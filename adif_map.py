#!/usr/bin/env python3
"""
adif_map.py — Ham radio contact map viewer
Parses an ADIF log file and renders contacts on an interactive Leaflet map via folium.

Part of the QRZ Logbook Tools suite. Requires qrz_common.py in the same directory.
Run adif_setup.py once before using --overlay states.

Dependencies:
    pip install folium
    (qrz_common.py also requires: pip install requests)

Usage:
    python adif_map.py contacts.adi [options]

Options:
    --band BAND             Filter by band (e.g. 40m, 20m, 2m)
    --mode MODE             Filter by single mode (kept for compatibility)
    --modes LIST            Filter by multiple modes (e.g. --modes CW,SSB,FT8)
    --date-from DATE        Filter QSOs on or after date (YYYYMMDD or YYYY-MM-DD)
    --date-to DATE          Filter QSOs on or before date (YYYYMMDD or YYYY-MM-DD)
    --confirmed             Only show confirmed QSOs (LoTW or QSL card received)
    --include-null-grid     Include JJ00 contacts (default: excluded — JJ00 is a
                            placeholder grid for contacts with no location data)
    --overlays-only         Hide contact dots; show only overlay choropleth.
                            Unworked cells render as ghost polygons (transparent
                            fill, visible border) so you can hover to identify them.
                            Requires land_grids.txt (run build_land_grids.py once).
    --show-arcs             Show great-circle arc lines (default: off — slow on large logs)
    --show-filters          Show collapsible band/mode filter panel (top-left corner)
    --overlay LIST          Comma-separated overlays: grids, states, counties
    --theme FILE            Color theme YAML file (default: theme_default.yaml)
    --verbose               Detailed console output: all station locations, band breakdown
    --output FILE           Output HTML filename (default: map_output.html next to input file)
"""

__version__ = "1.2.7"  # Canadian/international county overlay: --db arg, coord-based fallback lookup via gsak_counties

import argparse
import sys
import webbrowser
from pathlib import Path

try:
    import folium
except ImportError:
    sys.exit("Missing dependency: run  pip install folium")

try:
    import qrz_common
    from qrz_common import (
        parse_adif_with_header,
        adif_latlon_to_decimal,
        grid_to_latlon,
        parse_qso_datetime,
    )
except ImportError:
    sys.exit(
        "Missing qrz_common.py — ensure it is in the same directory as adif_map.py."
    )

try:
    import map_core
    from map_core import (
        load_theme, build_base_map, gc_points, grid4_polygon,
        build_grid_overlay, build_states_overlay, build_counties_overlay,
        add_overlay_legend, theme_colors_js_dict,
        BAND_COLORS, DEFAULT_COLOR, STATES_COLORS, COUNTIES_COLORS,
        GRIDS_COLORS, MAP_LON_OFFSET, CONTACT_DOT,
    )
except ImportError:
    sys.exit(
        "Missing map_core.py — ensure it is in the same directory as adif_map.py."
    )

try:
    from location_mapping import DXCC_US, DXCC_CA
except ImportError:
    DXCC_US = '291'   # United States
    DXCC_CA = '1'     # Canada

try:
    from gsak_counties import lookup_county_adif_key as _gsak_lookup
except ImportError:
    _gsak_lookup = None



# ---------------------------------------------------------------------------
# Coordinate resolution  (parsing delegated to qrz_common)
# ---------------------------------------------------------------------------
def resolve_coords(record: dict):
    """Return (lat, lon) or None for a QSO record."""
    lat_raw = record.get('LAT') or record.get('MY_LAT')
    lon_raw = record.get('LON') or record.get('MY_LON')
    if lat_raw and lon_raw:
        lat = adif_latlon_to_decimal(lat_raw)
        lon = adif_latlon_to_decimal(lon_raw)
        if lat is not None and lon is not None:
            return (lat, lon)
    grid = record.get('GRIDSQUARE') or record.get('GRID')
    if grid:
        try:
            return grid_to_latlon(grid)
        except (ValueError, Exception):
            return None
    return None


def resolve_my_coords(header: dict, records: list):
    """
    Determine a representative station coordinate for map centering.
    Returns the average of all unique per-record origins, so the initial
    view is centred sensibly even for portable/multi-location operations.
    """
    fallback = None
    lat_raw = header.get('MY_LAT')
    lon_raw = header.get('MY_LON')
    if lat_raw and lon_raw:
        lat = adif_latlon_to_decimal(lat_raw)
        lon = adif_latlon_to_decimal(lon_raw)
        if lat is not None and lon is not None:
            fallback = (lat, lon)
    if fallback is None:
        grid = header.get('MY_GRIDSQUARE')
        if grid:
            try:
                fallback = grid_to_latlon(grid)
            except (ValueError, Exception):
                pass

    origins_seen = set()
    origins = []
    for r in records:
        c = _resolve_my_coords_for_record(r)
        if c and c not in origins_seen:
            origins_seen.add(c)
            origins.append(c)

    if origins:
        avg_lat = sum(c[0] for c in origins) / len(origins)
        avg_lon = sum(c[1] for c in origins) / len(origins)
        return (avg_lat, avg_lon)
    return fallback


def _resolve_my_coords_for_record(record: dict):
    """Return (lat, lon) for the operator's location at the time of this QSO."""
    lat_raw = record.get('MY_LAT')
    lon_raw = record.get('MY_LON')
    if lat_raw and lon_raw:
        lat = adif_latlon_to_decimal(lat_raw)
        lon = adif_latlon_to_decimal(lon_raw)
        if lat is not None and lon is not None:
            return (lat, lon)
    grid = record.get('MY_GRIDSQUARE')
    if grid:
        try:
            return grid_to_latlon(grid)
        except (ValueError, Exception):
            return None
    return None



# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
def is_confirmed(record: dict):
    lotw = record.get('LOTW_QSL_RCVD', '').upper()
    qsl  = record.get('QSLRCD', '').upper()
    qsl2 = record.get('QSL_RCVD', '').upper()   # standard ADIF field used by LoTW exports
    return lotw == 'Y' or qsl == 'Y' or qsl2 == 'Y'


# JJ00 bounding box: 0°–2° N latitude, 0°–2° W longitude (Atlantic, prime meridian)
_JJ00_LAT_MIN, _JJ00_LAT_MAX =  0.0,  2.0
_JJ00_LON_MIN, _JJ00_LON_MAX = -2.0,  0.0

# AA00 bounding box: 90°–89° S latitude, 180°–178° W longitude (near south pole / antimeridian)
_AA00_LAT_MIN, _AA00_LAT_MAX = -90.0, -89.0
_AA00_LON_MIN, _AA00_LON_MAX = -180.0, -178.0

def is_null_grid(record: dict) -> bool:
    """
    Return True if this record resolves to a known null grid placeholder.

    Two null grids are recognised:
      JJ00 — 0°–2° N, 0°–2° W (mid-Atlantic near prime meridian/equator).
              The most common placeholder for stations with no location data.
      AA00 — 90°–89° S, 180°–178° W (near south pole / antimeridian).
              Another common default/unset grid value.

    Detection covers two cases for each:
      1. GRIDSQUARE field starts with 'JJ00' or 'AA00' (case-insensitive),
         regardless of any further sub-square characters.
      2. Explicit LAT/LON coordinates that fall within either bounding box.
    """
    grid = (record.get('GRIDSQUARE') or record.get('GRID') or '').strip().upper()
    if grid.startswith('JJ00') or grid.startswith('AA00'):
        return True

    lat_raw = record.get('LAT') or record.get('MY_LAT')
    lon_raw = record.get('LON') or record.get('MY_LON')
    if lat_raw and lon_raw:
        lat = adif_latlon_to_decimal(lat_raw)
        lon = adif_latlon_to_decimal(lon_raw)
        if lat is not None and lon is not None:
            if (_JJ00_LAT_MIN <= lat <= _JJ00_LAT_MAX and
                    _JJ00_LON_MIN <= lon <= _JJ00_LON_MAX):
                return True
            if (_AA00_LAT_MIN <= lat <= _AA00_LAT_MAX and
                    _AA00_LON_MIN <= lon <= _AA00_LON_MAX):
                return True

    return False


def _build_mode_filter(args) -> set:
    """
    Build the set of modes to include from --mode and/or --modes.
    Returns an empty set if no mode filter is active (all modes pass).
    """
    modes = set()
    if getattr(args, 'mode', None):
        modes.add(args.mode.upper().strip())
    for m in (getattr(args, 'modes', None) or '').split(','):
        m = m.strip().upper()
        if m:
            modes.add(m)
    return modes


def apply_filters(records, args):
    # Normalise date bounds to YYYYMMDD (accepts YYYYMMDD or YYYY-MM-DD)
    date_from  = parse_qso_datetime(args.date_from)[0] if args.date_from else None
    date_to    = parse_qso_datetime(args.date_to)[0]   if args.date_to   else None
    mode_filter = _build_mode_filter(args)

    out = []
    for r in records:
        if args.band and r.get('BAND', '').lower() != args.band.lower():
            continue
        if mode_filter and r.get('MODE', '').upper() not in mode_filter:
            continue
        date = r.get('QSO_DATE', '')
        if date_from and date < date_from:
            continue
        if date_to and date > date_to:
            continue
        if args.confirmed and not is_confirmed(r):
            continue
        if not getattr(args, 'include_null_grid', False) and is_null_grid(r):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Map building
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Mode grouping for the browser-side toggle panel
# ---------------------------------------------------------------------------
MODE_GROUPS = [
    ("CW",      {"CW"}),
    ("SSB",     {"SSB", "USB", "LSB", "AM", "FM"}),
    ("Digital", {"FT8", "FT4", "FT2", "DATA", "RTTY", "JT65", "JT9",
                 "PSK31", "PSK63", "WSPR", "JS8", "MSK144", "Q65"}),
    ("Other",   None),   # None = catch-all for anything not matched above
]

# Band grouping for the browser-side filter panel.
# Each tuple: (group_label, set_of_band_strings).
# None = catch-all for bands not matched above.
BAND_GROUPS = [
    ("HF",  {"160m", "80m", "60m", "40m", "30m", "20m", "17m",
              "15m", "12m", "10m", "6m"}),
    ("VHF+", {"2m", "1.25m", "70cm", "33cm", "23cm", "13cm"}),
    ("Other", None),
]


# ---------------------------------------------------------------------------
# Arc decimation
# ---------------------------------------------------------------------------

def _select_arcs(records: list, arc_max: int = 1000,
                 arc_cell_max: int = 2) -> list:
    """
    Return a decimated list of records to draw as arcs.

    Strategy:
      1. Deduplicate by callsign — one arc per unique contact regardless of
         how many bands/modes were worked.
      2. Assign each contact to a 5x5 degree geographic cell based on its
         coordinates.
      3. Prioritise rare DX: fill cells with the fewest contacts first so
         isolated grid cells always get an arc before dense clusters.
      4. Apply per-cell cap (arc_cell_max) then global cap (arc_max).
    """
    # Step 1: deduplicate by callsign, keep first record seen
    seen_calls: set = set()
    unique: list = []
    for r in records:
        call = r.get('CALL', '').upper().strip()
        if not call or call in seen_calls:
            continue
        coords = resolve_coords(r)
        if coords is None:
            continue
        seen_calls.add(call)
        unique.append((r, coords))

    if not unique:
        return []

    # Step 2: assign to 5x5 degree cells
    cell_map: dict = {}
    for r, coords in unique:
        cell = (int(coords[1] // 5), int(coords[0] // 5))
        cell_map.setdefault(cell, []).append(r)

    # Step 3: sort cells by population — rarest first (rare DX gets priority)
    cells_by_rarity = sorted(cell_map.items(), key=lambda kv: len(kv[1]))

    # Step 4: select up to arc_cell_max per cell, arc_max total
    selected = []
    for _cell, entries in cells_by_rarity:
        for r in entries[:arc_cell_max]:
            selected.append(r)
            if len(selected) >= arc_max:
                return selected

    return selected


def build_map(my_coords, records, show_arcs: bool,
              arc_max: int = 1000, arc_cell_max: int = 2,
              overlays_only: bool = False, verbose: bool = False):
    from folium.plugins import MarkerCluster

    m = build_base_map(my_coords[0], my_coords[1], verbose=verbose)

    # Operating location markers — one per unique MY_ position
    seen_origins = {}   # coords -> info dict for dedup
    MY_DISPLAY_FIELDS = [
        'MY_GRIDSQUARE', 'MY_CITY', 'MY_STATE', 'MY_CNTY',
        'MY_COUNTRY', 'MY_CQ_ZONE', 'MY_ITU_ZONE', 'MY_NAME',
    ]
    for r in records:
        origin = _resolve_my_coords_for_record(r)
        if origin is None or origin in seen_origins:
            continue
        callsign = (r.get('STATION_CALLSIGN') or r.get('MY_CALL')
                    or r.get('OPERATOR') or '?')
        info = {'callsign': callsign}
        for f in MY_DISPLAY_FIELDS:
            v = r.get(f, '').strip()
            if v:
                info[f] = v
        seen_origins[origin] = info

    # Fallback: if no per-record origins found, use the map-centre coords
    if not seen_origins:
        seen_origins[my_coords] = {'callsign': 'My Station'}

    for origin, info in seen_origins.items():
        callsign = info.get('callsign', '?')
        parts = [callsign]
        # Ordered MY_ fields to display if populated
        display_fields = [
            ('MY_GRIDSQUARE', None),
            ('MY_CITY',       None),
            ('MY_STATE',      None),
            ('MY_CNTY',       None),
            ('MY_COUNTRY',    None),
            ('MY_CQ_ZONE',    'CQ'),
            ('MY_ITU_ZONE',   'ITU'),
            ('MY_NAME',       None),
        ]
        for field, label in display_fields:
            val = info.get(field, '').strip()
            if val:
                parts.append(f"{label}: {val}" if label else val)
        parts.append(f"{origin[0]:.4f}, {origin[1]:.4f}")
        folium.Marker(
            location=origin,
            tooltip=" | ".join(parts),
            icon=folium.Icon(color="red", icon="home", prefix="fa"),
        ).add_to(m)

    # ------------------------------------------------------------------
    # Clustering setup
    #
    # Mode A (default): one shared MarkerCluster inside a single
    #   FeatureGroup so the layer toggle still works.
    #
    # Mode B (--cluster-by-band): one MarkerCluster per band, each
    #   inside its own FeatureGroup so bands can be toggled individually.
    # ------------------------------------------------------------------
    # One FeatureGroup + MarkerCluster per mode group.
    # These are real Leaflet layers — the mode toggle panel calls
    # .addTo(map) / .remove() on them directly, which is reliable.
    # The native layer control only shows overlay layers (not these).
    # ------------------------------------------------------------------
    cluster_icon_fn = """
        function(cluster) {
            var count = cluster.getChildCount();
            var size  = count < 10 ? 28 : count < 100 ? 36 : 44;
            return L.divIcon({
                html: '<div title="' + count + ' contacts"'
                    + ' style="background:rgba(80,80,80,0.75);color:#fff;'
                    + 'border-radius:50%;width:' + size + 'px;height:' + size + 'px;'
                    + 'display:flex;align-items:center;justify-content:center;'
                    + 'font-weight:bold;font-size:12px;border:2px solid #fff;">'
                    + count + '</div>',
                className: '',
                iconSize: [size, size]
            });
        }
    """

    catch_all_label = next((g[0] for g in MODE_GROUPS if g[1] is None), "Other")
    def _mode_to_group(mode: str) -> str:
        for label, members in MODE_GROUPS:
            if members is not None and mode.upper() in members:
                return label
        return catch_all_label

    mode_fgs      = {}   # group_label -> FeatureGroup
    mode_clusters = {}   # group_label -> MarkerCluster
    band_groups   = {}   # band -> None  (for legend tracking only)

    skipped = 0

    if not overlays_only:
        for r in records:
            coords = resolve_coords(r)
            if coords is None:
                skipped += 1
                continue

            band   = r.get('BAND', 'unknown').lower()
            mode   = r.get('MODE', '').upper()
            call   = r.get('CALL', '?')
            date   = r.get('QSO_DATE', '')
            color  = BAND_COLORS.get(band, DEFAULT_COLOR)
            conf   = "✓ confirmed" if is_confirmed(r) else ""
            origin = _resolve_my_coords_for_record(r) or my_coords
            grp    = _mode_to_group(mode)
            tooltip_text = f"{call} | {band} {mode} | {date} {conf}"

            if grp not in mode_fgs:
                fg = folium.FeatureGroup(name=f"Mode: {grp}", show=True)
                mode_fgs[grp] = fg
                mode_clusters[grp] = MarkerCluster(
                    icon_create_function=cluster_icon_fn,
                    options={"maxClusterRadius": 40, "disableClusteringAtZoom": 9},
                ).add_to(fg)

            _dot = CONTACT_DOT
            marker = folium.CircleMarker(
                location=coords,
                radius=_dot.get("radius", 6),
                color=_dot.get("border_color", "white"),
                weight=_dot.get("border_weight", 1.2),
                fill=True,
                fill_color=color,
                fill_opacity=_dot.get("fill_opacity", 0.85),
                tooltip=tooltip_text,
            )
            marker.add_to(mode_clusters[grp])
            band_groups[band] = None

        for fg in mode_fgs.values():
            fg.add_to(m)
    else:
        # overlays_only: still collect band_groups for legend/layer_meta
        for r in records:
            band = r.get('BAND', 'unknown').lower()
            band_groups[band] = None

    # Draw decimated arcs after markers so they don't block tooltips
    if show_arcs and not overlays_only:
        arc_records = _select_arcs(records, arc_max=arc_max,
                                   arc_cell_max=arc_cell_max)
        arc_fg = folium.FeatureGroup(name="Arcs", show=True)
        for r in arc_records:
            coords = resolve_coords(r)
            if coords is None:
                continue
            origin = _resolve_my_coords_for_record(r) or my_coords
            band   = r.get('BAND', 'unknown').lower()
            mode   = r.get('MODE', '').upper()
            call   = r.get('CALL', '?')
            date   = r.get('QSO_DATE', '')
            color  = BAND_COLORS.get(band, DEFAULT_COLOR)
            conf   = "✓ confirmed" if is_confirmed(r) else ""
            tip    = f"{call} | {band} {mode} | {date} {conf}"
            # _gc_points returns a list of segments (split at antimeridian)
            for seg in gc_points(origin, coords):
                folium.PolyLine(
                    locations=seg,
                    color=color,
                    weight=1,
                    opacity=0.45,
                    tooltip=tip,
                    dash_array="6 4",
                ).add_to(arc_fg)
        arc_fg.add_to(m)
        print(f"  Arcs: {len(arc_records)} drawn from "
              f"{len({r.get('CALL','') for r in records})} unique contacts "
              f"({len(records)} QSOs total)")

    if skipped and verbose:
        print(f"  Note: {skipped} QSO(s) skipped — no usable coordinates "
              f"(missing LAT/LON and GRIDSQUARE). Check with adif_extract.py --country.")

    # Return Leaflet JS variable names for the mode FeatureGroups
    layer_meta = {
        "bands_present": sorted(band_groups.keys()),
        "mode_fg_names": {grp: fg.get_name() for grp, fg in mode_fgs.items()},
        "mode_fg_modes": {},
    }
    for r in records:
        mode = r.get('MODE', '').upper()
        grp  = _mode_to_group(mode)
        if grp in layer_meta["mode_fg_names"]:
            layer_meta["mode_fg_modes"].setdefault(grp, set()).add(mode)
    layer_meta["mode_fg_modes"] = {
        g: sorted(s) for g, s in layer_meta["mode_fg_modes"].items()
    }

    return m, len(records) - skipped, layer_meta



# ---------------------------------------------------------------------------
# Custom collapsible band / mode toggle panel
# ---------------------------------------------------------------------------

def inject_toggle_panel(m, filtered_records: list, layer_meta: dict) -> None:
    """
    Inject a collapsible filter panel (top-left, starts collapsed).

    Contains:
      - Modes section: toggles whole mode-group FeatureGroup layers on/off
      - Bands section: filters overlay choropleth recompute (not dot visibility)

    When overlay_meta is present in layer_meta, mode AND band toggles also
    call recomputeOverlays() which updates GeoJSON fill colors dynamically.
    """
    import json

    map_var        = m.get_name()
    mode_fg_names  = layer_meta.get("mode_fg_names", {})
    mode_fg_modes  = layer_meta.get("mode_fg_modes", {})
    overlay_meta   = layer_meta.get("overlay_meta", {})
    bands_present  = layer_meta.get("bands_present_sorted",
                                    layer_meta.get("bands_present", []))

    if not mode_fg_names:
        return

    mode_fg_names_js = json.dumps(mode_fg_names)
    mode_fg_modes_js = json.dumps(mode_fg_modes)
    active_groups    = json.dumps(sorted(mode_fg_names.keys()))
    bands_js         = json.dumps(bands_present)

    # Band group structure for grouped checkbox rendering
    catch_all_band = next((g[0] for g in BAND_GROUPS if g[1] is None), 'Other')
    band_group_of = {}
    for b in bands_present:
        assigned = catch_all_band
        for lbl, members in BAND_GROUPS:
            if members is not None and b in members:
                assigned = lbl
                break
        band_group_of[b] = assigned
    # active groups in order, only those present in log
    seen = set()
    band_group_order = []
    for lbl, _ in BAND_GROUPS:
        if lbl not in seen and any(band_group_of.get(b)==lbl for b in bands_present):
            seen.add(lbl); band_group_order.append(lbl)
    band_group_members = {g: [b for b in bands_present if band_group_of.get(b)==g]
                          for g in band_group_order}
    band_group_of_js    = json.dumps(band_group_of)
    band_group_order_js = json.dumps(band_group_order)
    band_group_members_js = json.dumps(band_group_members)

    # Serialize overlay dynamic data
    # overlay_dyn: {type: {varName, data, grpIndex, bandIndex}}
    overlay_dyn = {}
    for ov_type, meta in overlay_meta.items():
        overlay_dyn[ov_type] = {
            'varName':   meta.get('var_name', ''),
            'data':      meta.get('data', {}),
            'grpIndex':  meta.get('grp_index', []),
            'bandIndex': meta.get('band_index', []),
        }
    overlay_dyn_js = json.dumps(overlay_dyn)

    # Theme colors for JS recompute
    colors_js = json.dumps(theme_colors_js_dict())

    band_colors_js = json.dumps({b: BAND_COLORS.get(b, DEFAULT_COLOR)
                                  for b in bands_present})

    panel_html = f"""
<style>
#adif-fp {{
    position:fixed; top:80px; left:10px; z-index:1000;
    background:rgba(255,255,255,0.95); border:1px solid #aaa;
    border-radius:8px; box-shadow:2px 2px 8px rgba(0,0,0,0.25);
    font-family:sans-serif; font-size:12px;
    min-width:160px; max-width:220px;
    user-select:none; cursor:default;
}}
#adif-fp .pt {{
    padding:6px 10px; font-weight:bold; font-size:13px;
    border-bottom:1px solid #ddd; display:flex;
    justify-content:space-between; align-items:center;
    cursor:pointer; color:#333;
}}
#adif-fp .sh {{
    padding:5px 10px 3px; font-weight:bold; color:#555; cursor:pointer;
    display:flex; justify-content:space-between; align-items:center;
    border-top:1px solid #eee; font-size:11px;
    text-transform:uppercase; letter-spacing:0.05em;
}}
#adif-fp .sh:hover {{ background:#f5f5f5; }}
#adif-fp .sb {{ padding:2px 8px 6px 10px; }}
#adif-fp .note {{ padding:2px 10px 4px; font-size:10px; color:#999; font-style:italic; }}
#adif-fp .tr {{ display:flex; align-items:center; gap:6px; padding:2px 0; }}
#adif-fp .tr:hover {{ background:#f8f8f8; border-radius:3px; padding:2px 2px; margin:0 -2px; }}
#adif-fp .sw {{
    display:inline-block; width:10px; height:10px; border-radius:50%;
    border:1px solid rgba(0,0,0,0.2); flex-shrink:0;
}}
#adif-fp .chv {{ font-size:10px; color:#999; transition:transform 0.15s; display:inline-block; }}
#adif-fp .chv.col {{ transform:rotate(-90deg); }}
#adif-fp .sa {{ font-size:10px; color:#888; cursor:pointer; padding:1px 4px; border-radius:3px; }}
#adif-fp .sa:hover {{ background:#eee; color:#333; }}
</style>
<div id="adif-fp">
  <div class="pt" id="adif-ptitle">Filters <span id="adif-pchev">▶</span></div>
  <div id="adif-pbody" style="display:none">

    <div class="sh" id="adif-mhead">
      Modes
      <span>
        <!-- all/none suppressed — use native layer control for mode groups
        <span class="sa" id="adif-mall">all</span>
        <span class="sa" id="adif-mnone">none</span>
        -->
        <span id="adif-mchev" class="chv">▼</span>
      </span>
    </div>
    <div class="sb" id="adif-modes-body"></div>

    <div class="sh" id="adif-bhead">
      Bands
      <span>
        <!-- all/none suppressed
        <span class="sa" id="adif-ball">all</span>
        <span class="sa" id="adif-bnone">none</span>
        -->
        <span id="adif-bchev" class="chv">▼</span>
      </span>
    </div>
    <div class="sb" id="adif-bands-body"></div>
    <div class="note" id="adif-band-note">Affects overlay colors only</div>

  </div>
</div>
<script>
setTimeout(function() {{
    var mapObj       = {map_var};
    var modeFgNames  = {mode_fg_names_js};
    var modeFgModes  = {mode_fg_modes_js};
    var activeGroups = {active_groups};
    var bandsPresent    = {bands_js};
    var bandGroupOf     = {band_group_of_js};
    var bandGroupOrder  = {band_group_order_js};
    var bandGroupMembers= {band_group_members_js};
    var bandColors   = {band_colors_js};
    var overlayDyn   = {overlay_dyn_js};
    var themeColors  = {colors_js};

    function getLayer(n) {{ return window[n] || null; }}

    var activeModes = new Set();
    activeGroups.forEach(function(g) {{
        (modeFgModes[g]||[]).forEach(function(m) {{ activeModes.add(m); }});
    }});
    var activeBands = new Set(bandsPresent);

    // ── Build mode checkboxes ──────────────────────────────────
    var modesBody = document.getElementById('adif-modes-body');
    if (modesBody) {{
        activeGroups.forEach(function(grp) {{
            var modes = modeFgModes[grp] || [];
            var grpRow = document.createElement('div');
            grpRow.className = 'tr'; grpRow.style.marginTop = '3px';
            var gcb = document.createElement('input');
            gcb.type='checkbox'; gcb.id='cb-mgrp-'+grp; gcb.checked=true;
            gcb.addEventListener('change', function() {{ adifGrpToggle(grp, this.checked); }});
            var glb = document.createElement('label');
            glb.htmlFor = 'cb-mgrp-'+grp;
            glb.textContent = grp + (modes.length===1 ? ' ('+modes[0]+')' : '');
            glb.style.cssText = 'cursor:pointer;font-weight:bold';
            grpRow.appendChild(gcb); grpRow.appendChild(glb);
            modesBody.appendChild(grpRow);
            if (modes.length > 1) {{
                modes.forEach(function(mode) {{
                    var mRow = document.createElement('div');
                    mRow.className='tr'; mRow.style.paddingLeft='16px';
                    var mcb = document.createElement('input');
                    mcb.type='checkbox'; mcb.id='cb-mode-'+mode; mcb.checked=true;
                    mcb.addEventListener('change', function() {{ adifModeToggle(grp, mode, this.checked); }});
                    var mlb = document.createElement('label');
                    mlb.htmlFor='cb-mode-'+mode; mlb.textContent=mode;
                    mlb.style.cssText='cursor:pointer;color:#555';
                    mRow.appendChild(mcb); mRow.appendChild(mlb);
                    modesBody.appendChild(mRow);
                }});
            }}
        }});
    }}

    // ── Build band checkboxes (grouped by HF/VHF+/Other) ──────
    var bandsBody = document.getElementById('adif-bands-body');
    if (bandsBody) {{
        bandGroupOrder.forEach(function(grp) {{
            var members = bandGroupMembers[grp] || [];
            // Group header
            var ghdr = document.createElement('div');
            ghdr.className = 'tr'; ghdr.style.marginTop = '3px';
            var gcb = document.createElement('input');
            gcb.type='checkbox'; gcb.id='cb-bgrp-'+grp; gcb.checked=true;
            gcb.addEventListener('change', function() {{ adifBandGroupToggle(grp, this.checked); }});
            var glb = document.createElement('label');
            glb.htmlFor='cb-bgrp-'+grp; glb.textContent=grp;
            glb.style.cssText='cursor:pointer;font-weight:bold';
            ghdr.appendChild(gcb); ghdr.appendChild(glb);
            bandsBody.appendChild(ghdr);
            // Individual bands in group
            members.forEach(function(band) {{
                var color = bandColors[band] || '#888';
                var row   = document.createElement('div');
                row.className = 'tr'; row.style.paddingLeft = '16px';
                var cb = document.createElement('input');
                cb.type='checkbox'; cb.id='cb-band-'+band; cb.checked=true;
                cb.addEventListener('change', function() {{ adifBandToggle(band, this.checked); }});
                var sw = document.createElement('span');
                sw.className='sw'; sw.style.background=color;
                var lb = document.createElement('label');
                lb.htmlFor='cb-band-'+band; lb.textContent=band;
                lb.style.cursor='pointer';
                row.appendChild(cb); row.appendChild(sw); row.appendChild(lb);
                bandsBody.appendChild(row);
            }});
        }});
    }}

    // ── Overlay recompute ──────────────────────────────────────
    function computeStatus(qsos, grpIdx, bndIdx) {{
        var hasConf = false, hasWork = false;
        for (var i=0; i<qsos.length; i++) {{
            var q    = qsos[i];
            var grp  = grpIdx[q[0]];
            var band = bndIdx[q[1]];
            var conf = q[2];
            var gcb  = document.getElementById('cb-mgrp-'+grp);
            if (!gcb || !gcb.checked) continue;
            if (!activeBands.has(band)) continue;
            if (conf) hasConf = true; else hasWork = true;
        }}
        if (hasConf) return 'confirmed';
        if (hasWork) return 'worked';
        return null;
    }}

    function styleForStatus(status, tc) {{
        var op = tc.fill_opacity != null ? tc.fill_opacity : 0.45;
        if (status === 'confirmed') return {{
            fillColor: tc.confirmed, color: tc.border_confirmed || tc.confirmed,
            weight: tc.confirmed_weight || 2.0, fillOpacity: op,
        }};
        if (status === 'worked') return {{
            fillColor: tc.worked, color: tc.border_worked || tc.worked,
            weight: tc.worked_weight || 2.0, fillOpacity: op,
        }};
        return {{
            fillColor: tc.unworked_fill || '#ffffff',
            color: tc.unworked_border || '#aaaaaa',
            weight: tc.unworked_weight || 0.5, fillOpacity: 0.0,
        }};
    }}

    function recomputeOverlay(ovType) {{
        var ov = overlayDyn[ovType];
        if (!ov || !ov.varName) return;
        var layer = getLayer(ov.varName);
        if (!layer || !layer.eachLayer) return;
        var grpIdx = ov.grpIndex;
        var bndIdx = ov.bandIndex;
        var data   = ov.data;
        var tc     = themeColors[ovType] || themeColors['states'];

        layer.eachLayer(function(path) {{
            if (!path.feature || !path.feature.properties) return;
            var props  = path.feature.properties;
            var key    = props.key || props.postal || props.adif_key || props.grid || '';
            var status = computeStatus(data[key] || [], grpIdx, bndIdx);
            path.setStyle(styleForStatus(status, tc));
        }});
    }}

    function recomputeAllOverlays() {{
        Object.keys(overlayDyn).forEach(function(ovType) {{
            recomputeOverlay(ovType);
        }});
    }}

    // ── Toggle handlers ────────────────────────────────────────
    window.adifGrpToggle = function(grp, checked) {{
        var layer = getLayer(modeFgNames[grp]);
        if (layer) {{
            if (checked) {{ if (!mapObj.hasLayer(layer)) layer.addTo(mapObj); }}
            else         {{ if (mapObj.hasLayer(layer))  layer.remove(); }}
        }}
        (modeFgModes[grp]||[]).forEach(function(m) {{
            if (checked) activeModes.add(m); else activeModes.delete(m);
            var cb = document.getElementById('cb-mode-'+m);
            if (cb) cb.checked = checked;
        }});
        recomputeAllOverlays();
    }};

    window.adifModeToggle = function(grp, mode, checked) {{
        if (checked) activeModes.add(mode); else activeModes.delete(mode);
        var anyOn = (modeFgModes[grp]||[]).some(function(m) {{ return activeModes.has(m); }});
        var gcb = document.getElementById('cb-mgrp-'+grp);
        if (gcb) gcb.checked = anyOn;
        var layer = getLayer(modeFgNames[grp]);
        if (layer) {{
            if (anyOn) {{ if (!mapObj.hasLayer(layer)) layer.addTo(mapObj); }}
            else       {{ if (mapObj.hasLayer(layer))  layer.remove(); }}
        }}
        recomputeAllOverlays();
    }};

    window.adifBandGroupToggle = function(grp, checked) {{
        var members = bandGroupMembers[grp] || [];
        members.forEach(function(band) {{
            if (checked) activeBands.add(band); else activeBands.delete(band);
            var cb = document.getElementById('cb-band-'+band);
            if (cb) cb.checked = checked;
        }});
        recomputeAllOverlays();
    }};

    window.adifBandToggle = function(band, checked) {{
        if (checked) activeBands.add(band); else activeBands.delete(band);
        // Update group checkbox state
        var grp = bandGroupOf[band];
        var members = bandGroupMembers[grp] || [];
        var anyOn = members.some(function(b) {{ return activeBands.has(b); }});
        var gcb = document.getElementById('cb-bgrp-'+grp);
        if (gcb) gcb.checked = anyOn;
        recomputeAllOverlays();
    }};

    window.adifSelectModes = function(checked) {{
        activeGroups.forEach(function(grp) {{
            var gcb = document.getElementById('cb-mgrp-'+grp);
            if (gcb) gcb.checked = checked;
            adifGrpToggle(grp, checked);
        }});
    }};

    window.adifSelectBands = function(checked) {{
        bandsPresent.forEach(function(b) {{
            if (checked) activeBands.add(b); else activeBands.delete(b);
            var cb = document.getElementById('cb-band-'+b);
            if (cb) cb.checked = checked;
        }});
        recomputeAllOverlays();
    }};

    // ── Collapse wiring ────────────────────────────────────────
    function wireClick(id, fn) {{
        var el = document.getElementById(id);
        if (el) el.addEventListener('click', fn);
    }}
    wireClick('adif-ptitle', function() {{
        var body = document.getElementById('adif-pbody');
        var chev = document.getElementById('adif-pchev');
        if (!body) return;
        var h = body.style.display === 'none';
        body.style.display = h ? '' : 'none';
        if (chev) chev.textContent = h ? '▼' : '▶';
    }});
    function wireSection(bodyId, chevId, headId) {{
        wireClick(headId, function(e) {{
            if (e.target.classList.contains('sa')) return;
            var body = document.getElementById(bodyId);
            var chev = document.getElementById(chevId);
            if (!body) return;
            var h = body.style.display === 'none';
            body.style.display = h ? '' : 'none';
            if (chev) chev.classList.toggle('col', !h);
        }});
    }}
    wireSection('adif-modes-body', 'adif-mchev', 'adif-mhead');
    wireSection('adif-bands-body', 'adif-bchev', 'adif-bhead');
    // all/none wireClicks suppressed
    // wireClick('adif-mall',  ...adifSelectModes(true));
    // wireClick('adif-mnone', ...adifSelectModes(false));
    // wireClick('adif-ball',  ...adifSelectBands(true));
    // wireClick('adif-bnone', ...adifSelectBands(false));

    // Hide band section note if no overlays are active
    var note = document.getElementById('adif-band-note');
    if (note && Object.keys(overlayDyn).length === 0) note.style.display = 'none';

    // Wire Leaflet event isolation
    var fp = document.getElementById('adif-fp');
    if (fp && window.L) {{
        L.DomEvent.disableClickPropagation(fp);
        L.DomEvent.disableScrollPropagation(fp);
    }}

}}, 300);
</script>
"""
    m.get_root().html.add_child(folium.Element(panel_html))


def add_legend(m, band_groups, hidden: bool = False):
    """
    Add the band color legend to the map.
    When hidden=True (e.g. --show-filters is active), the legend is rendered
    with display:none so it does not appear — the filter panel already shows
    band swatches and colors.
    """
    items = ""
    for band in sorted(band_groups.keys()):
        color = BAND_COLORS.get(band, DEFAULT_COLOR)
        items += (
            f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0">'
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'border-radius:50%;background:{color};border:1px solid #555"></span>'
            f'<span>{band}</span></div>\n'
        )
    display = "none" if hidden else "block"
    legend_html = f"""
    <div id="adif-band-legend"
         style="display:{display};position:fixed;bottom:30px;left:30px;z-index:9999;
                background:rgba(255,255,255,0.9);padding:10px 14px;
                border-radius:8px;border:1px solid #aaa;font-size:13px;
                font-family:sans-serif;box-shadow:2px 2px 6px rgba(0,0,0,0.3)">
      <b>Band</b><br>{items}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Visualise ham radio ADIF contacts on an interactive map."
    )
    parser.add_argument("adif", help="Path to ADIF log file")
    parser.add_argument("--band",      help="Filter by band (e.g. 40m, 20m)")
    parser.add_argument("--mode",      help="Filter by single mode (e.g. SSB, CW, FT8) — kept for compatibility")
    parser.add_argument("--modes",     help="Filter by multiple modes, comma-separated (e.g. --modes CW,SSB,FT8)")
    parser.add_argument("--date-from", dest="date_from",
                        help="Start date filter — YYYYMMDD or YYYY-MM-DD (inclusive)")
    parser.add_argument("--date-to",   dest="date_to",
                        help="End date filter — YYYYMMDD or YYYY-MM-DD (inclusive)")
    parser.add_argument("--confirmed", action="store_true",
                        help="Only show confirmed QSOs (LoTW or QSL received)")
    parser.add_argument("--include-null-grid", dest="include_null_grid", action="store_true",
                        help="Include JJ00 contacts (excluded by default — JJ00 is a placeholder for contacts with no location)")
    parser.add_argument("--overlays-only", dest="overlays_only", action="store_true",
                        help="Hide contact dots; show overlay choropleth only. Unworked cells rendered as ghost polygons (requires land_grids.txt — run build_land_grids.py once).")
    parser.add_argument("--show-arcs", dest="show_arcs", action="store_true",
                        help="Draw great-circle arc lines (default: off — slow on large logs)")
    parser.add_argument("--arc-max", dest="arc_max", type=int, default=1000,
                        help="Maximum total arcs to draw when --show-arcs is active (default: 1000)")
    parser.add_argument("--arc-cell-max", dest="arc_cell_max", type=int, default=2,
                        help="Maximum arcs per 5°x5° geographic cell — limits dense clusters (default: 3)")
    parser.add_argument("--cluster-by-band", dest="cluster_by_band", action="store_true",
                        help="Separate cluster bubble per band, toggleable via layer control (default: all bands together)")
    parser.add_argument("--show-filters", dest="show_filters", action="store_true",
                        help="Show in-browser collapsible band/mode filter panel (top-left of map)")
    parser.add_argument("--overlay",
                        help="Comma-separated overlays: grids, states, counties")
    parser.add_argument("--db",
                        help="Path to gsak_counties.db for Canadian/international county polygons "
                             "(default: gsak_counties.db beside this script)")
    parser.add_argument("--theme",
                        help="Color theme YAML file (default: theme_default.yaml beside this script)")
    parser.add_argument("--verbose",   action="store_true",
                        help="Detailed console output: all station locations, band breakdown")
    parser.add_argument("--output",    help="Output HTML path (default: map_output.html beside input)")
    args = parser.parse_args()

    adif_path = Path(args.adif).expanduser().resolve()
    if not adif_path.exists():
        sys.exit(f"File not found: {adif_path}")

    load_theme(args.theme, script_dir=Path(__file__).parent)
    print(f"Parsing {adif_path.name} ...")
    header, records = parse_adif_with_header(adif_path)
    print(f"  {len(records)} QSO records found.")

    my_coords = resolve_my_coords(header, records)
    if my_coords is None:
        sys.exit(
            "Could not determine your station coordinates.\n"
            "Ensure MY_LAT/MY_LON or MY_GRIDSQUARE is present in the ADIF header or records."
        )
    if args.verbose:
        # Print all unique operating locations
        seen_locs = {}
        for r in records:
            c = _resolve_my_coords_for_record(r)
            if c and c not in seen_locs:
                cs   = (r.get('STATION_CALLSIGN') or r.get('MY_CALL') or '?')
                grid = r.get('MY_GRIDSQUARE', '')
                city = r.get('MY_CITY', '')
                st   = r.get('MY_STATE', '')
                loc_parts = [cs]
                if grid: loc_parts.append(grid)
                if city: loc_parts.append(city)
                if st:   loc_parts.append(st)
                loc_parts.append(f"{c[0]:.4f}, {c[1]:.4f}")
                seen_locs[c] = " | ".join(loc_parts)
        print(f"  Operating locations ({len(seen_locs)}):")
        for coords, desc in seen_locs.items():
            print(f"    {desc}")
    else:
        print(f"  Station location: {my_coords[0]:.4f}, {my_coords[1]:.4f}")

    filtered = apply_filters(records, args)
    print(f"  {len(filtered)} QSOs after filtering.")
    if not args.include_null_grid:
        null_count = sum(1 for r in records if is_null_grid(r))
        if null_count:
            print(f"  {null_count} null-grid contact(s) excluded (JJ00/AA00) — use --include-null-grid to show them.")
    if args.overlays_only:
        print("  --overlays-only: contact dots suppressed; unworked overlay cells shown as ghost polygons.")

    from collections import Counter
    mode_counts = Counter(r.get('MODE', 'unknown').upper() for r in filtered)
    band_counts = Counter(r.get('BAND', 'unknown').lower() for r in filtered)

    # Always show a one-line mode summary
    mode_line = ", ".join(
        f"{m} ({n})" for m, n in sorted(mode_counts.items(), key=lambda x: -x[1])
    )
    print(f"  Modes: {mode_line}")

    if args.verbose:
        # Detailed band breakdown
        band_line = ", ".join(
            f"{b} ({n})" for b, n in sorted(band_counts.items(), key=lambda x: -x[1])
        )
        print(f"  Bands: {band_line}")

    if not filtered:
        sys.exit("No contacts to plot after filtering.")

    print("Building map ...")
    m, plotted, layer_meta = build_map(my_coords, filtered,
                                       show_arcs=args.show_arcs,
                                       arc_max=args.arc_max,
                                       arc_cell_max=args.arc_cell_max,
                                       overlays_only=args.overlays_only,
                                       verbose=args.verbose)
    # Collect band groups that were actually used (for legend)
    used_bands = {r.get('BAND', 'unknown').lower() for r in filtered if resolve_coords(r)}
    add_legend(m, {b: None for b in used_bands}, hidden=args.show_filters)
    # Build mode_group_fn from MODE_GROUPS for dynamic overlay data
    catch_all_lbl = next((g[0] for g in MODE_GROUPS if g[1] is None), "Other")
    def _mgfn(mode: str) -> str:
        for lbl, members in MODE_GROUPS:
            if members is not None and mode.upper() in members:
                return lbl
        return catch_all_lbl

    # Tag _confirmed flag onto each record for map_core overlay builders
    for r in filtered:
        r['_confirmed'] = is_confirmed(r)

    # Key functions for overlay builders
    def _us_key(r): return r.get('STATE','').upper().strip()[:2] if r.get('DXCC','') == DXCC_US else ''
    def _ca_key(r): return r.get('STATE','').upper().strip()[:2] if r.get('DXCC','') == DXCC_CA else ''
    # Resolve db_path once — used by _cnty_key_fn fallback and build_counties_overlay
    if args.db:
        db_path = Path(args.db).expanduser().resolve()
    else:
        db_path = Path(__file__).parent / "gsak_counties.db"
    if not db_path.exists():
        db_path = None   # silently suppress — overlay will work US-only

    # Coord-based county lookup cache: (round(lat,3), round(lon,3)) -> adif_key|''
    # Avoids repeated DB point-in-polygon tests for contacts in the same area.
    _coord_key_cache: dict = {}

    def _cnty_key_fn(r):
        import re as _re
        cnty = r.get('CNTY','').strip()
        if cnty and ',' in cnty:
            state, name = cnty.split(',',1)
            name = _re.sub(r'\s+(County|Parish|Borough|Census Area|Municipality)\s*$','',name.strip(),flags=_re.IGNORECASE).strip()
            # Normalise to title case — LoTW exports county names in ALL CAPS
            name = name.title()
            # Normalise known LoTW/GSAK spelling differences.
            # LoTW uses "De Kalb", "De Soto" (TX/FL/MS), "De Witt" as two words;
            # GSAK stores them as one word. Apply per-state to avoid breaking
            # Louisiana "De Soto" (Parish) which correctly stays two words.
            _DEFIX = {
                ('AL','De Kalb'):('AL','DeKalb'), ('GA','De Kalb'):('GA','DeKalb'),
                ('IL','De Kalb'):('IL','DeKalb'), ('IN','De Kalb'):('IN','DeKalb'),
                ('MO','De Kalb'):('MO','DeKalb'), ('TN','De Kalb'):('TN','DeKalb'),
                ('FL','De Soto'):('FL','DeSoto'), ('MS','De Soto'):('MS','DeSoto'),
                ('TX','De Witt'):('TX','DeWitt'),
            }
            st = state.upper()
            fix = _DEFIX.get((st, name))
            if fix:
                st, name = fix
            return f"{st},{name}"

        # No CNTY field — fall back to coordinate-based DB lookup.
        # Used for Canadian and other international contacts where LoTW
        # does not populate CNTY.
        if not db_path or not _gsak_lookup:
            return ''
        coords = resolve_coords(r)
        if not coords:
            return ''
        cache_key = (round(coords[0], 3), round(coords[1], 3))
        if cache_key not in _coord_key_cache:
            _coord_key_cache[cache_key] = _gsak_lookup(
                coords[0], coords[1], db_path=db_path) or ''
        return _coord_key_cache[cache_key]
    def _grid_key(r):
        g = r.get('GRIDSQUARE','')[:4].upper()
        return g if len(g)==4 else ''
    def _grp_fn(r): return _mgfn(r.get('MODE','').upper())
    def _band_fn(r): return r.get('BAND','unknown').lower()

    # Overlays — pass dynamic=True when mode filter panel is active
    overlay_meta = {}   # overlay type -> {var_name, data, grp_index, band_index}
    overlays = [o.strip().lower() for o in (args.overlay or "").split(",") if o.strip()]
    if "states" in overlays:
        print("Building states/provinces overlay ...")
        result = build_states_overlay(m, filtered,
                                      us_key_fn=_us_key, ca_key_fn=_ca_key,
                                      dynamic=args.show_filters,
                                      group_fn=_grp_fn, band_fn=_band_fn,
                                      overlays_only=args.overlays_only)
        if result:
            overlay_meta['states'] = result
    if "counties" in overlays:
        print("Building county overlay ...")
        result = build_counties_overlay(m, filtered,
                                        key_fn=_cnty_key_fn,
                                        dynamic=args.show_filters,
                                        group_fn=_grp_fn, band_fn=_band_fn,
                                        overlays_only=args.overlays_only,
                                        db_path=db_path)
        if result:
            overlay_meta['counties'] = result
    if "grids" in overlays:
        print("Building grid square overlay ...")
        result = build_grid_overlay(m, filtered,
                                    key_fn=_grid_key,
                                    dynamic=args.show_filters,
                                    group_fn=_grp_fn, band_fn=_band_fn,
                                    overlays_only=args.overlays_only)
        if result:
            overlay_meta['grids'] = result
    if overlays:
        add_overlay_legend(m, overlays)

    # Auto-frame to US + Canada when states or counties overlay is active.
    # Bounds: SW corner (24°N, 170°W) to NE corner (84°N, 50°W)
    # Alaska pulls the northwest far enough to include most of Canada.
    if ('states' in overlays or 'counties' in overlays) and overlays != ['grids']:
        m.fit_bounds([[24, -170], [66, -50]])

    # Inject mode filter panel (must come after overlays so var names exist)
    if args.show_filters:
        layer_meta['overlay_meta'] = overlay_meta
        layer_meta['bands_present_sorted'] = sorted(
            {r.get('BAND','unknown').lower() for r in filtered})
        inject_toggle_panel(m, filtered, layer_meta)

    # Single LayerControl added after all layers (including overlays) are built
    folium.LayerControl(collapsed=False).add_to(m)

    # Output path
    if args.output:
        out_path = Path(args.output).expanduser().resolve()
    else:
        out_path = adif_path.parent / "map_output.html"

    m.save(str(out_path))
    print(f"  Map saved → {out_path}")
    if args.overlays_only:
        print(f"  {plotted} contacts included in overlay choropleth (dots hidden).")
    else:
        print(f"  Plotted {plotted} contacts.")

    webbrowser.open(out_path.as_uri())



if __name__ == "__main__":
    main()
