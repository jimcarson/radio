#!/usr/bin/env python3
"""
adif_map.py — Ham radio contact map viewer
Parses an ADIF log file and renders contacts on an interactive Leaflet map via folium.

Part of the QRZ Logbook Tools suite. Requires qrz_common.py in the same directory.

Dependencies:
    pip install folium
    (qrz_common.py also requires: pip install requests)

Usage:
    python adif_map.py contacts.adi [options]

Options:
    --band BAND             Filter by band (e.g. 40m, 20m, 2m)
    --mode MODE             Filter by mode (e.g. SSB, CW, FT8)
    --date-from DATE        Filter QSOs on or after date (YYYYMMDD or YYYY-MM-DD)
    --date-to DATE          Filter QSOs on or before date (YYYYMMDD or YYYY-MM-DD)
    --confirmed             Only show confirmed QSOs (LoTW or QSL card received)
    --no-arcs               Suppress great-circle arc lines
    --cluster-by-band       Separate cluster bubble per band (default: all bands together)
    --output FILE           Output HTML filename (default: map_output.html next to input file)
"""

import argparse
import math
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


# ---------------------------------------------------------------------------
# Band colour palette (Leaflet-compatible colour names or hex)
# ---------------------------------------------------------------------------
BAND_COLORS = {
    "160m": "#8B0000",
    "80m":  "#CC2200",
    "60m":  "#DD4400",
    "40m":  "#FF6600",
    "30m":  "#FF9900",
    "20m":  "#FFD700",
    "17m":  "#AACC00",
    "15m":  "#44BB00",
    "12m":  "#00AAAA",
    "10m":  "#0077DD",
    "6m":   "#5500DD",
    "2m":   "#AA00CC",
    "70cm": "#CC00AA",
}
DEFAULT_COLOR = "#888888"


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

    origins = []
    for r in records:
        c = _resolve_my_coords_for_record(r)
        if c and c not in origins:
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
# Great-circle arc interpolation
# ---------------------------------------------------------------------------
def _gc_points(p1, p2, n=60):
    """Return n+1 (lat,lon) points along the great-circle between p1 and p2."""
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    d = 2 * math.asin(math.sqrt(
        math.sin((lat2 - lat1) / 2) ** 2 +
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    ))
    if d < 1e-9:
        return [p1, p2]
    pts = []
    for i in range(n + 1):
        f = i / n
        A = math.sin((1 - f) * d) / math.sin(d)
        B = math.sin(f * d) / math.sin(d)
        x = A * math.cos(lat1) * math.cos(lon1) + B * math.cos(lat2) * math.cos(lon2)
        y = A * math.cos(lat1) * math.sin(lon1) + B * math.cos(lat2) * math.sin(lon2)
        z = A * math.sin(lat1) + B * math.sin(lat2)
        lat = math.degrees(math.atan2(z, math.sqrt(x**2 + y**2)))
        lon = math.degrees(math.atan2(y, x))
        pts.append((lat, lon))
    return pts


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
def is_confirmed(record: dict):
    lotw = record.get('LOTW_QSL_RCVD', '').upper()
    qsl  = record.get('QSLRCD', '').upper()
    return lotw == 'Y' or qsl == 'Y'


def apply_filters(records, args):
    # Normalise date bounds to YYYYMMDD (accepts YYYYMMDD or YYYY-MM-DD)
    date_from = parse_qso_datetime(args.date_from)[0] if args.date_from else None
    date_to   = parse_qso_datetime(args.date_to)[0]   if args.date_to   else None

    out = []
    for r in records:
        if args.band and r.get('BAND', '').lower() != args.band.lower():
            continue
        if args.mode and r.get('MODE', '').upper() != args.mode.upper():
            continue
        date = r.get('QSO_DATE', '')
        if date_from and date < date_from:
            continue
        if date_to and date > date_to:
            continue
        if args.confirmed and not is_confirmed(r):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Map building
# ---------------------------------------------------------------------------
def build_map(my_coords, records, show_arcs: bool, cluster_by_band: bool = False):
    from folium.plugins import MarkerCluster

    m = folium.Map(
        location=my_coords,
        zoom_start=3,
        tiles=None,
    )

    # Base tile layers — no API key required; all appear in the layer control.
    # OSM omitted: their tile CDN blocks requests without a Referer header,
    # which browsers cannot set on image tile fetches.
    folium.TileLayer("CartoDB positron",    name="CartoDB Light").add_to(m)
    folium.TileLayer("CartoDB dark_matter", name="CartoDB Dark").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Esri Topo",
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri / National Geographic", name="Esri NatGeo",
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Esri Satellite",
    ).add_to(m)

    # Operating location markers — one per unique MY_ position
    seen_origins = {}   # coords -> (callsign, grid) for dedup
    for r in records:
        origin = _resolve_my_coords_for_record(r)
        if origin is None or origin in seen_origins:
            continue
        callsign = r.get('STATION_CALLSIGN') or r.get('MY_CALL') or r.get('OPERATOR') or '?'
        grid     = r.get('MY_GRIDSQUARE', '')
        seen_origins[origin] = (callsign, grid)

    # Fallback: if no per-record origins found, use the map-centre coords
    if not seen_origins:
        seen_origins[my_coords] = ('My Station', '')

    for origin, (callsign, grid) in seen_origins.items():
        tip = callsign
        if grid:
            tip += f" | {grid}"
        tip += f" | {origin[0]:.4f}, {origin[1]:.4f}"
        folium.Marker(
            location=origin,
            tooltip=tip,
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
    cluster_icon_fn = """
        function(cluster) {
            var count = cluster.getChildCount();
            var size  = count < 10 ? 28 : count < 100 ? 36 : 44;
            return L.divIcon({
                html: '<div style="background:rgba(80,80,80,0.75);color:#fff;'
                    + 'border-radius:50%;width:' + size + 'px;height:' + size + 'px;'
                    + 'display:flex;align-items:center;justify-content:center;'
                    + 'font-weight:bold;font-size:12px;border:2px solid #fff;">'
                    + count + '</div>',
                className: '',
                iconSize: [size, size]
            });
        }
    """

    band_groups   = {}   # band -> FeatureGroup
    band_clusters = {}   # band -> MarkerCluster  (cluster_by_band mode)
    shared_fg      = None
    shared_cluster = None

    if not cluster_by_band:
        shared_fg = folium.FeatureGroup(name="Contacts", show=True)
        shared_cluster = MarkerCluster(
            icon_create_function=cluster_icon_fn,
            options={"maxClusterRadius": 40, "disableClusteringAtZoom": 9},
        ).add_to(shared_fg)

    skipped = 0

    for r in records:
        coords = resolve_coords(r)
        if coords is None:
            skipped += 1
            continue

        band   = r.get('BAND', 'unknown').lower()
        mode   = r.get('MODE', '')
        call   = r.get('CALL', '?')
        date   = r.get('QSO_DATE', '')
        color  = BAND_COLORS.get(band, DEFAULT_COLOR)
        conf   = "✓ confirmed" if is_confirmed(r) else ""
        origin = _resolve_my_coords_for_record(r) or my_coords
        tooltip_text = f"{call} | {band} {mode} | {date} {conf}"

        # Circle marker with a thin white border for legibility
        marker = folium.CircleMarker(
            location=coords,
            radius=6,
            color="white",        # border colour
            weight=1.2,           # border width
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            tooltip=tooltip_text,
        )

        if cluster_by_band:
            if band not in band_groups:
                fg = folium.FeatureGroup(name=f"Band: {band}", show=True)
                band_groups[band] = fg
                band_clusters[band] = MarkerCluster(
                    icon_create_function=cluster_icon_fn,
                    options={"maxClusterRadius": 40, "disableClusteringAtZoom": 9},
                ).add_to(fg)
            marker.add_to(band_clusters[band])
        else:
            marker.add_to(shared_cluster)
            # Track bands used (for legend) — reuse band_groups as a set proxy
            band_groups[band] = band_groups.get(band, None)

        # Great-circle arc — added directly to map (not inside cluster)
        if show_arcs:
            arc_pts = _gc_points(origin, coords)
            folium.PolyLine(
                locations=arc_pts,
                color=color,
                weight=1,
                opacity=0.45,
                tooltip=tooltip_text,
                dash_array="6 4",
            ).add_to(m)

    # Attach all groups to map
    if cluster_by_band:
        for fg in band_groups.values():
            fg.add_to(m)
    else:
        shared_fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    if skipped:
        print(f"  Note: {skipped} QSO(s) skipped — no usable coordinates found.")

    return m, len(records) - skipped


# ---------------------------------------------------------------------------
# Legend HTML
# ---------------------------------------------------------------------------
def add_legend(m, band_groups):
    items = ""
    for band in sorted(band_groups.keys()):
        color = BAND_COLORS.get(band, DEFAULT_COLOR)
        items += (
            f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0">'
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'border-radius:50%;background:{color};border:1px solid #555"></span>'
            f'<span>{band}</span></div>\n'
        )
    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
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
    parser.add_argument("--mode",      help="Filter by mode (e.g. SSB, CW, FT8)")
    parser.add_argument("--date-from", dest="date_from",
                        help="Start date filter — YYYYMMDD or YYYY-MM-DD (inclusive)")
    parser.add_argument("--date-to",   dest="date_to",
                        help="End date filter — YYYYMMDD or YYYY-MM-DD (inclusive)")
    parser.add_argument("--confirmed", action="store_true",
                        help="Only show confirmed QSOs (LoTW or QSL received)")
    parser.add_argument("--no-arcs",   dest="no_arcs", action="store_true",
                        help="Suppress great-circle arc lines")
    parser.add_argument("--cluster-by-band", dest="cluster_by_band", action="store_true",
                        help="Separate cluster bubble per band, toggleable via layer control (default: all bands together)")
    parser.add_argument("--output",    help="Output HTML path (default: map_output.html beside input)")
    args = parser.parse_args()

    adif_path = Path(args.adif).expanduser().resolve()
    if not adif_path.exists():
        sys.exit(f"File not found: {adif_path}")

    print(f"Parsing {adif_path.name} ...")
    header, records = parse_adif_with_header(adif_path)
    print(f"  {len(records)} QSO records found.")

    my_coords = resolve_my_coords(header, records)
    if my_coords is None:
        sys.exit(
            "Could not determine your station coordinates.\n"
            "Ensure MY_LAT/MY_LON or MY_GRIDSQUARE is present in the ADIF header or records."
        )
    print(f"  Station location: {my_coords[0]:.4f}, {my_coords[1]:.4f}")

    filtered = apply_filters(records, args)
    print(f"  {len(filtered)} QSOs after filtering.")

    if not filtered:
        sys.exit("No contacts to plot after filtering.")

    print("Building map ...")
    m, plotted = build_map(my_coords, filtered, show_arcs=not args.no_arcs,
                             cluster_by_band=args.cluster_by_band)

    # Collect band groups that were actually used (for legend)
    used_bands = {r.get('BAND', 'unknown').lower() for r in filtered if resolve_coords(r)}
    add_legend(m, {b: None for b in used_bands})

    # Output path
    if args.output:
        out_path = Path(args.output).expanduser().resolve()
    else:
        out_path = adif_path.parent / "map_output.html"

    m.save(str(out_path))
    print(f"  Map saved → {out_path}")
    print(f"  Plotted {plotted} contacts.")

    webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()
