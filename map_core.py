#!/usr/bin/env python3
"""
map_core.py — Shared mapping engine
====================================
Common map infrastructure shared by adif_map.py and geocache_map.py.

Provides:
  - Theme loading (YAML color/style configuration)
  - Folium base map construction (tile layers)
  - Geometry utilities (_gc_points, _grid4_polygon)
  - Choropleth overlay builders (states/provinces, counties, grid squares)
  - Overlay legend
  - Generic point marker helper

Dependencies:
    pip install folium pyyaml
"""

from pathlib import Path

__version__ = "1.3.0"  # build_country_borders_overlay(); build_counties_overlay() intl DB path

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

try:
    import folium
except ImportError:
    import sys
    sys.exit("Missing dependency: run  pip install folium")

import math


# ---------------------------------------------------------------------------
# Theme defaults
# ---------------------------------------------------------------------------

THEME_DEFAULTS: dict = {
    "band_colors": {
        "160m": "#8B0000", "80m": "#CC2200", "60m": "#DD4400",
        "40m":  "#FF6600", "30m": "#FF9900", "20m": "#FFD700",
        "17m":  "#AACC00", "15m": "#44BB00", "12m": "#00AAAA",
        "10m":  "#0077DD", "6m":  "#5500DD", "2m":  "#AA00CC",
        "70cm": "#CC00AA",
    },
    "default_color": "#888888",
    "map_center_lon_offset": 0,
    "contact_dot": {
        "radius": 6,
        "fill_opacity": 0.85,
        "border_color": "white",
        "border_weight": 1.2,
    },
    "overlay": {
        "states": {
            "confirmed":        "#2ecc71",
            "worked":           "#f39c12",
            "border_confirmed": "#000000",
            "border_worked":    "#000000",
            "confirmed_weight": 2.0,
            "worked_weight":    2.0,
            "fill_opacity":     0.55,
            "unworked_fill":    "#ffffff",
            "unworked_border":  "#666666",
            "unworked_weight":  0.8,
            "unworked_opacity": 0.0,
        },
        "counties": {
            "confirmed":        "#1a8a4a",
            "worked":           "#c0720a",
            "border_confirmed": "#000000",
            "border_worked":    "#000000",
            "confirmed_weight": 0.8,
            "worked_weight":    0.8,
            "fill_opacity":     0.75,
            "unworked_fill":    "#ffffff",
            "unworked_border":  "#000000",
            "unworked_weight":  0.3,
            "unworked_opacity": 0.0,
        },
        "grids": {
            "confirmed":   "#27ae9e",
            "worked":      "#e8a020",
            "fill_opacity": 0.45,
        },
    },
}

# Module-level theme state — populated by load_theme()
BAND_COLORS:     dict  = {}
DEFAULT_COLOR:   str   = "#888888"
STATES_COLORS:   dict  = {}
COUNTIES_COLORS: dict  = {}
GRIDS_COLORS:    dict  = {}
MAP_LON_OFFSET:  float = 0.0
CONTACT_DOT:     dict  = {}


def load_theme(theme_path=None, script_dir: Path = None) -> None:
    """
    Load color theme from a YAML file into module-level color constants.
    Falls back to THEME_DEFAULTS if file not found or pyyaml not installed.

    theme_path  : Path/str to a .yaml file, or None to use theme_default.yaml
    script_dir  : directory to look in for theme_default.yaml (defaults to CWD)
    """
    global BAND_COLORS, DEFAULT_COLOR, STATES_COLORS, COUNTIES_COLORS
    global GRIDS_COLORS, MAP_LON_OFFSET, CONTACT_DOT

    import copy
    theme = copy.deepcopy(THEME_DEFAULTS)

    if theme_path is None:
        base = script_dir if script_dir else Path.cwd()
        theme_path = base / "theme_default.yaml"
    else:
        theme_path = Path(theme_path)

    if theme_path.exists():
        if not _YAML_AVAILABLE:
            print("  Warning: pyyaml not installed — using built-in defaults.")
            print("    pip install pyyaml")
        else:
            try:
                with theme_path.open(encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                if loaded:
                    if "map_center_lon_offset" in loaded:
                        theme["map_center_lon_offset"] = loaded["map_center_lon_offset"]
                    if "band_colors" in loaded:
                        theme["band_colors"].update(loaded["band_colors"])
                    if "default_color" in loaded:
                        theme["default_color"] = loaded["default_color"]
                    if "contact_dot" in loaded:
                        theme["contact_dot"].update(loaded["contact_dot"])
                    if "overlay" in loaded:
                        for key, vals in loaded["overlay"].items():
                            if key in theme["overlay"]:
                                theme["overlay"][key].update(vals)
                            else:
                                theme["overlay"][key] = vals
                print(f"  Theme loaded: {theme_path.name}")
            except Exception as exc:
                print(f"  Warning: could not load theme {theme_path}: {exc}")
    elif theme_path.name != "theme_default.yaml":
        print(f"  Warning: theme file not found: {theme_path}")

    BAND_COLORS     = theme["band_colors"]
    DEFAULT_COLOR   = theme["default_color"]
    STATES_COLORS   = theme["overlay"]["states"]
    COUNTIES_COLORS = theme["overlay"]["counties"]
    GRIDS_COLORS    = theme["overlay"]["grids"]
    MAP_LON_OFFSET  = float(theme.get("map_center_lon_offset", 0))
    CONTACT_DOT     = theme["contact_dot"]


# ---------------------------------------------------------------------------
# Base map construction
# ---------------------------------------------------------------------------

def build_base_map(center_lat: float, center_lon: float,
                   zoom_start: int = 3) -> folium.Map:
    """
    Create a folium Map with all standard tile layers and no initial tile.
    Applies MAP_LON_OFFSET to the initial center longitude.
    """
    m = folium.Map(
        location=(center_lat, center_lon + MAP_LON_OFFSET),
        zoom_start=zoom_start,
        tiles=None,
    )
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
    return m


# ---------------------------------------------------------------------------
# Geometry utilities
# ---------------------------------------------------------------------------

def gc_points(p1, p2, n: int = 32) -> list:
    """
    Return great-circle interpolated segments between p1 and p2.
    Handles antimeridian crossings by splitting into multiple segments.
    Each segment is a list of (lat, lon) tuples safe for Leaflet PolyLine.
    """
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    d = 2 * math.asin(math.sqrt(
        math.sin((lat2 - lat1) / 2) ** 2 +
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    ))
    if d < 1e-9:
        return [[p1, p2]]

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
        pts.append([lat, lon])

    for i in range(1, len(pts)):
        diff = pts[i][1] - pts[i - 1][1]
        if diff > 180:
            pts[i][1] -= 360
        elif diff < -180:
            pts[i][1] += 360

    segments = []
    current = []
    for pt in pts:
        lat, lon = pt
        norm_lon = lon
        while norm_lon > 180:
            norm_lon -= 360
        while norm_lon < -180:
            norm_lon += 360
        if current and abs(norm_lon - current[-1][1]) > 170:
            segments.append(current)
            current = []
        current.append((lat, norm_lon))
    if current:
        segments.append(current)

    return [s for s in segments if len(s) >= 2]


def grid4_polygon(grid4: str) -> list:
    """Return a closed GeoJSON ring for a 4-char Maidenhead grid square."""
    g = grid4.upper()
    lon = (ord(g[0]) - ord('A')) * 20 - 180
    lat = (ord(g[1]) - ord('A')) * 10 - 90
    lon += (ord(g[2]) - ord('0')) * 2
    lat += (ord(g[3]) - ord('0')) * 1
    return [
        [lon,   lat],
        [lon+2, lat],
        [lon+2, lat+1],
        [lon,   lat+1],
        [lon,   lat],
    ]


# ---------------------------------------------------------------------------
# Overlay data helpers
# ---------------------------------------------------------------------------

def classify_records(records: list, key_fn) -> tuple[dict, dict, dict, dict]:
    """
    Build status and per-state count dicts from a list of records.

    key_fn(record) -> str key (empty string = skip)
    Records must have '_confirmed' key (bool).

    Returns:
        status          : {key -> 'confirmed' | 'worked'}
        counts          : {key -> total int}
        confirmed_counts: {key -> confirmed int}
        worked_counts   : {key -> worked int}
    """
    status:           dict = {}
    counts:           dict = {}
    confirmed_counts: dict = {}
    worked_counts:    dict = {}
    for r in records:
        key = key_fn(r)
        if not key:
            continue
        confirmed = r.get('_confirmed', False)
        counts[key] = counts.get(key, 0) + 1
        if confirmed:
            status[key] = 'confirmed'
            confirmed_counts[key] = confirmed_counts.get(key, 0) + 1
        else:
            if key not in status:
                status[key] = 'worked'
            worked_counts[key] = worked_counts.get(key, 0) + 1
    return status, counts, confirmed_counts, worked_counts


def build_overlay_qso_data(records: list, key_fn,
                            group_fn, band_fn) -> tuple[dict, list, list]:
    """
    Build compact indexed QSO data for dynamic JS overlay updates.

    group_fn(record) -> str   (e.g. mode group label)
    band_fn(record)  -> str   (e.g. band or cache type)

    Returns:
        data       — {entity_key: [[grp_idx, band_idx, confirmed_int], ...]}
        grp_index  — list of group names   (index -> name)
        band_index — list of band/type names (index -> name)
    """
    grp_list, band_list = [], []
    grp_map,  band_map  = {}, {}

    def _grp_idx(g):
        if g not in grp_map:
            grp_map[g] = len(grp_list); grp_list.append(g)
        return grp_map[g]

    def _band_idx(b):
        if b not in band_map:
            band_map[b] = len(band_list); band_list.append(b)
        return band_map[b]

    data: dict = {}
    for r in records:
        key = key_fn(r)
        if not key:
            continue
        grp  = group_fn(r)
        band = band_fn(r)
        conf = 1 if r.get('_confirmed', False) else 0
        data.setdefault(key, []).append([_grp_idx(grp), _band_idx(band), conf])

    return data, grp_list, band_list


# ---------------------------------------------------------------------------
# State/style helpers
# ---------------------------------------------------------------------------

def _style_for_status(status: str | None, cfg: dict) -> dict:
    """Produce a Leaflet style dict for a given status string and theme config."""
    fill_op = cfg.get('fill_opacity', 0.45)
    if status == 'confirmed':
        return {
            'fillColor':   cfg.get('confirmed', '#2ecc71'),
            'color':       cfg.get('border_confirmed', '#000000'),
            'weight':      cfg.get('confirmed_weight', 2.0),
            'fillOpacity': fill_op,
        }
    if status == 'worked':
        return {
            'fillColor':   cfg.get('worked', '#f39c12'),
            'color':       cfg.get('border_worked', '#000000'),
            'weight':      cfg.get('worked_weight', 2.0),
            'fillOpacity': fill_op,
        }
    return {
        'fillColor':   cfg.get('unworked_fill',    '#ffffff'),
        'color':       cfg.get('unworked_border',  '#666666'),
        'weight':      cfg.get('unworked_weight',  0.5),
        'fillOpacity': cfg.get('unworked_opacity', 0.0),
    }


# ---------------------------------------------------------------------------
# Grid square choropleth
# ---------------------------------------------------------------------------

def build_grid_overlay(m: folium.Map, records: list,
                       key_fn=None, dynamic: bool = False,
                       group_fn=None, band_fn=None) -> dict:
    """
    Add a Maidenhead grid-square choropleth layer.

    key_fn(record) -> 4-char grid string (default: record['GRIDSQUARE'][:4])
    When dynamic=True, embed per-record data for JS recompute.
    """
    import json

    def _default_key(r):
        g = r.get('GRIDSQUARE', '')[:4].upper()
        return g if len(g) == 4 else ''

    kfn = key_fn or _default_key

    # Tag confirmed flag onto records for classify_records
    status, counts, conf_counts, work_counts = classify_records(records, kfn)
    if not status:
        print("  Grid overlay: no grid data found — skipping.")
        return {}

    features = []
    for grid, state in status.items():
        try:
            ring = grid4_polygon(grid)
        except Exception:
            continue
        nc = conf_counts.get(grid, 0)
        nw = work_counts.get(grid, 0)
        tip = f"{grid} — Confirmed: {nc} | Worked: {nw}"
        features.append({
            'type': 'Feature',
            'properties': {
                'grid': grid, 'key': grid, 'status': state,
                'count': nc + nw, 'tooltip': tip,
            },
            'geometry': {'type': 'Polygon', 'coordinates': [ring]},
        })

    geojson = {'type': 'FeatureCollection', 'features': features}
    cfg = GRIDS_COLORS

    def style_fn(feature):
        return _style_for_status(feature['properties']['status'], GRIDS_COLORS)

    fg  = folium.FeatureGroup(name='Overlay: Grid squares', show=True)
    gjl = folium.GeoJson(
        geojson,
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(fields=['tooltip'], aliases=[''], labels=False),
    )
    gjl.add_to(fg)
    fg.add_to(m)

    confirmed_n = sum(1 for s in status.values() if s == 'confirmed')
    worked_n    = sum(1 for s in status.values() if s == 'worked')
    print(f"  Grid overlay: {confirmed_n} confirmed, {worked_n} worked-only squares.")
    _add_state_borders(m)

    if dynamic and group_fn and band_fn:
        dyn_data, grp_index, band_index = build_overlay_qso_data(
            records, kfn, group_fn, band_fn)
        return {'var_name': gjl.get_name(), 'data': dyn_data,
                'grp_index': grp_index, 'band_index': band_index}
    return {}



# ---------------------------------------------------------------------------
# State border lines overlay (thin black lines, no fill, non-interactive)
# Automatically added whenever any choropleth overlay is active.
# ---------------------------------------------------------------------------

_state_borders_added: set = set()   # track per-map object id to avoid duplicates


def _add_state_borders(m: folium.Map, cache_path: Path = None) -> None:
    """
    Add a thin state/province boundary line layer to the map.
    Uses ne_states.geojson (already required for the states choropleth).
    Safe to call multiple times on the same map — adds only once.
    No fill, no tooltip — purely for visual geographic orientation.
    """
    import json
    map_id = id(m)
    if map_id in _state_borders_added:
        return
    _state_borders_added.add(map_id)

    geo_path = cache_path or _STATES_CACHE
    if not geo_path.exists():
        return  # silently skip — caller already warns if file is missing

    try:
        geojson = json.loads(geo_path.read_text(encoding="utf-8"))
    except Exception:
        return

    def border_style(_feature):
        return {
            'fillColor':   '#000000',
            'fillOpacity': 0.0,
            'color':       '#333333',
            'weight':      1.2,
        }

    fg = folium.FeatureGroup(name='State/Province borders', show=True)
    folium.GeoJson(
        geojson,
        style_function=border_style,
        tooltip=None,
    ).add_to(fg)
    fg.add_to(m)


# ---------------------------------------------------------------------------
# Country border lines overlay
# ---------------------------------------------------------------------------

_country_borders_added: set = set()   # track per-map object id to avoid duplicates


def build_country_borders_overlay(
        m: folium.Map,
        db_path,
        country_names: list | None = None) -> None:
    """
    Add a country border lines layer sourced from the country_polygons table.

    country_names : if provided, only render borders for these countries
                    (pass the list of countries represented in the cache data
                    to avoid rendering all 200+ countries at once).
    Renders as thin lines with no fill — for geographic orientation only.
    Safe to call multiple times on the same map (deduplicates by map id).
    Does NOT call _add_state_borders — the caller decides whether to add
    US/CA state borders separately.
    """
    import json as _json
    import sqlite3 as _sqlite3

    map_id = id(m)
    if map_id in _country_borders_added:
        return
    _country_borders_added.add(map_id)

    db_path = str(db_path)
    try:
        conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = _sqlite3.Row
    except _sqlite3.OperationalError as exc:
        print(f"  Country borders: could not open DB {db_path}: {exc}")
        return

    try:
        if country_names:
            placeholders = ','.join('?' * len(country_names))
            rows = conn.execute(
                f"SELECT country_name, polygon FROM country_polygons "
                f"WHERE country_name IN ({placeholders})",
                country_names,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT country_name, polygon FROM country_polygons"
            ).fetchall()
    except _sqlite3.OperationalError as exc:
        print(f"  Country borders: query failed (table missing?): {exc}")
        conn.close()
        return
    conn.close()

    if not rows:
        print("  Country borders: no matching rows found — skipping.")
        return

    features = []
    for row in rows:
        pts = _json.loads(row['polygon'])
        # pts is [[lat, lon], ...] — GeoJSON wants [[lon, lat], ...]
        coords = [[lon, lat] for lat, lon in pts]
        features.append({
            'type': 'Feature',
            'properties': {'country_name': row['country_name']},
            'geometry': {
                'type': 'Polygon',
                'coordinates': [coords],
            },
        })

    geojson = {'type': 'FeatureCollection', 'features': features}

    def border_style(_feature):
        return {
            'fillColor':   '#000000',
            'fillOpacity': 0.0,
            'color':       '#444444',
            'weight':      1.5,
        }

    fg = folium.FeatureGroup(name='Overlay: Country borders', show=True)
    folium.GeoJson(
        geojson,
        style_function=border_style,
        tooltip=folium.GeoJsonTooltip(
            fields=['country_name'],
            aliases=['Country'],
            localize=True,
        ),
    ).add_to(fg)
    fg.add_to(m)
    print(f"  Country borders: {len(features)} polygon parts "
          f"({len({r['country_name'] for r in rows})} countries).")


# ---------------------------------------------------------------------------
# States / provinces choropleth
# ---------------------------------------------------------------------------

_STATES_CACHE = Path(__file__).parent / "ne_states.geojson"


def build_states_overlay(m: folium.Map, records: list,
                         us_key_fn=None, ca_key_fn=None,
                         dynamic: bool = False,
                         group_fn=None, band_fn=None,
                         cache_path: Path = None) -> dict:
    """
    Add a US states + Canadian provinces choropleth layer.

    us_key_fn(record) -> 2-char US state postal code or ''
    ca_key_fn(record) -> 2-char CA province code or ''
    cache_path        : override for ne_states.geojson location
    """
    import json
    geo_path = cache_path or _STATES_CACHE

    if not geo_path.exists():
        print(
            f"  States overlay: {geo_path.name} not found.\n"
            "  Run  python adif_setup.py  once to download boundary data."
        )
        return {}

    try:
        geojson = json.loads(geo_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  States overlay: could not read {geo_path.name}: {exc}")
        return {}

    def _default_us(r): return ''
    def _default_ca(r): return ''

    us_kfn = us_key_fn or _default_us
    ca_kfn = ca_key_fn or _default_ca

    us_status, us_counts, us_conf_c, us_work_c = classify_records(records, us_kfn)
    ca_status, ca_counts, ca_conf_c, ca_work_c = classify_records(records, ca_kfn)
    us_status.pop('', None); us_counts.pop('', None)
    ca_status.pop('', None); ca_counts.pop('', None)
    us_conf_c.pop('', None); us_work_c.pop('', None)
    ca_conf_c.pop('', None); ca_work_c.pop('', None)

    lookup = {}
    for code, state in {**us_status, **ca_status}.items():
        cc = (us_conf_c if code in us_status else ca_conf_c).get(code, 0)
        wc = (us_work_c if code in us_status else ca_work_c).get(code, 0)
        lookup[code] = {'status': state, 'conf': cc, 'work': wc}

    # Embed tooltip into GeoJSON properties so GeoJsonTooltip can access it
    for feat in geojson['features']:
        postal = (feat['properties'].get('postal') or '').upper()
        info   = lookup.get(postal)
        if info:
            feat['properties']['_tip'] = (
                f"Confirmed: {info['conf']} | Worked: {info['work']}")
        else:
            feat['properties']['_tip'] = ''

    us_conf = sum(1 for c, s in lookup.items() if s['status']=='confirmed' and c in us_status)
    us_work = sum(1 for c, s in lookup.items() if s['status']=='worked'    and c in us_status)
    ca_conf = sum(1 for c, s in lookup.items() if s['status']=='confirmed' and c in ca_status)
    ca_work = sum(1 for c, s in lookup.items() if s['status']=='worked'    and c in ca_status)
    print(f"  States overlay: US {us_conf} confirmed, {us_work} worked | "
          f"CA {ca_conf} confirmed, {ca_work} worked")

    def style_fn(feature):
        postal = (feature['properties'].get('postal') or '').upper()
        info   = lookup.get(postal)
        return _style_for_status(info['status'] if info else None, STATES_COLORS)

    fg  = folium.FeatureGroup(name='Overlay: States & Provinces', show=True)
    gjl = folium.GeoJson(
        geojson,
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=['postal', 'name', '_tip'],
            aliases=['Code', 'Name', 'Counts'],
            localize=True,
        ),
    )
    gjl.add_to(fg)
    fg.add_to(m)
    _add_state_borders(m)

    if dynamic and group_fn and band_fn:
        def combined_key(r):
            return us_kfn(r) or ca_kfn(r)
        dyn_data, grp_index, band_index = build_overlay_qso_data(
            records, combined_key, group_fn, band_fn)
        return {'var_name': gjl.get_name(), 'data': dyn_data,
                'grp_index': grp_index, 'band_index': band_index}
    return {}


# ---------------------------------------------------------------------------
# County choropleth
# ---------------------------------------------------------------------------

_COUNTIES_CACHE = Path(__file__).parent / "us_counties.geojson"


def build_counties_overlay(m: folium.Map, records: list,
                            key_fn=None,
                            dynamic: bool = False,
                            group_fn=None, band_fn=None,
                            cache_path: Path = None,
                            db_path=None) -> dict:
    """
    Add a county/district choropleth layer for US, CA, and international regions.

    key_fn(record) -> "ST,Name" ADIF-style key, or ''
    cache_path     : override for us_counties.geojson location
    db_path        : path to gsak_counties.db for international region polygons.
                     Keys whose state_code is not a US/CA postal code are looked
                     up in the DB counties table and merged into one layer.
                     If None, only US/CA GeoJSON keys are rendered.
    """
    import json as _json
    import sqlite3 as _sqlite3

    # Determine which 2-letter codes are US/CA so we can split keys
    _us_ca_codes: set[str] = set()
    try:
        from gsak_counties import _POSTAL_STATE as _ps
        _us_ca_codes = set(_ps.keys())
    except ImportError:
        pass   # no gsak_counties — treat everything as US/CA (legacy behaviour)

    def _is_us_ca(adif_key: str) -> bool:
        code = adif_key.split(',', 1)[0] if ',' in adif_key else ''
        return (not _us_ca_codes) or (code in _us_ca_codes)

    # -----------------------------------------------------------------------
    # US / CA path — load GeoJSON
    # -----------------------------------------------------------------------
    geo_path = cache_path or _COUNTIES_CACHE
    geojson_features: list = []

    if geo_path.exists():
        try:
            geojson = _json.loads(geo_path.read_text(encoding="utf-8"))
            geojson_features = geojson.get('features', [])
        except Exception as exc:
            print(f"  County overlay: could not read {geo_path.name}: {exc}")
    else:
        print(
            f"  County overlay: {geo_path.name} not found.\n"
            "  Run  python adif_setup.py  to download boundary data."
        )

    def _default_key(r): return ''
    kfn = key_fn or _default_key

    status, counts, conf_counts, work_counts = classify_records(records, kfn)
    status.pop('', None); counts.pop('', None)
    conf_counts.pop('', None); work_counts.pop('', None)

    # Split keys into US/CA (handled by GeoJSON) and international (handled by DB)
    us_ca_keys = {k for k in status if _is_us_ca(k)}
    intl_keys  = {k for k in status if not _is_us_ca(k)}

    # -----------------------------------------------------------------------
    # Embed tooltip counts into GeoJSON features (US/CA only)
    # -----------------------------------------------------------------------
    for feat in geojson_features:
        key = feat['properties'].get('adif_key', '')
        nc  = conf_counts.get(key, 0)
        nw  = work_counts.get(key, 0)
        feat['properties']['_tip'] = (
            f"Confirmed: {nc} | Worked: {nw}" if (nc or nw) else '')
        # Ensure international tooltip fields exist (empty) so tooltip schema is uniform
        feat['properties'].setdefault('county_name', feat['properties'].get('namelsad', ''))
        feat['properties'].setdefault('state_code',  feat['properties'].get('state', ''))

    # -----------------------------------------------------------------------
    # International path — query DB for polygon rows
    # -----------------------------------------------------------------------
    db_features: list = []
    if intl_keys and db_path:
        try:
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = _sqlite3.Row
            placeholders = ','.join('?' * len(intl_keys))
            rows = conn.execute(
                f"SELECT adif_key, county_name, state_code, polygon "
                f"FROM counties WHERE adif_key IN ({placeholders})",
                list(intl_keys),
            ).fetchall()
            conn.close()
            for row in rows:
                pts  = _json.loads(row['polygon'])
                # pts stored as [[lat,lon],...]; GeoJSON needs [[lon,lat],...]
                coords = [[lon, lat] for lat, lon in pts]
                key = row['adif_key']
                nc  = conf_counts.get(key, 0)
                nw  = work_counts.get(key, 0)
                db_features.append({
                    'type': 'Feature',
                    'properties': {
                        'adif_key':    key,
                        'county_name': row['county_name'],
                        'state_code':  row['state_code'],
                        'namelsad':    row['county_name'],   # uniform schema
                        'state':       row['state_code'],
                        '_tip': f"Confirmed: {nc} | Worked: {nw}" if (nc or nw) else '',
                    },
                    'geometry': {
                        'type': 'Polygon',
                        'coordinates': [coords],
                    },
                })
        except _sqlite3.Error as exc:
            print(f"  County overlay: DB query failed: {exc}")
    elif intl_keys and not db_path:
        print(f"  County overlay: {len(intl_keys)} international region(s) skipped "
              f"(no --db path provided).")

    # -----------------------------------------------------------------------
    # Merge and render as a single layer
    # -----------------------------------------------------------------------
    all_features = geojson_features + db_features
    if not all_features:
        print("  County overlay: no features to render — skipping.")
        return {}

    merged = {'type': 'FeatureCollection', 'features': all_features}

    confirmed_n = sum(1 for s in status.values() if s == 'confirmed')
    worked_n    = sum(1 for s in status.values() if s == 'worked')
    intl_rendered = len(db_features)
    print(f"  County overlay: {confirmed_n} confirmed, {worked_n} worked-only "
          f"({intl_rendered} international region(s) from DB).")

    def style_fn(feature):
        key  = feature['properties'].get('adif_key', '')
        info = status.get(key)
        return _style_for_status(info if isinstance(info, str) else None, COUNTIES_COLORS)

    fg  = folium.FeatureGroup(name='Overlay: Counties', show=True)
    gjl = folium.GeoJson(
        merged,
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=['namelsad', 'state', 'adif_key', '_tip'],
            aliases=['County', 'State', 'ADIF key', 'Counts'],
            localize=True,
        ),
    )
    gjl.add_to(fg)
    fg.add_to(m)
    _add_state_borders(m)

    if dynamic and group_fn and band_fn:
        dyn_data, grp_index, band_index = build_overlay_qso_data(
            records, kfn, group_fn, band_fn)
        return {'var_name': gjl.get_name(), 'data': dyn_data,
                'grp_index': grp_index, 'band_index': band_index}
    return {}


# ---------------------------------------------------------------------------
# Overlay legend  (bottom-right)
# ---------------------------------------------------------------------------

def add_overlay_legend(m: folium.Map, overlays: list,
                       extra_rows: list = None) -> None:
    """
    Add the overlay color-key legend at bottom-right of map.

    overlays   : list of active overlay type strings ('states','counties','grids')
    extra_rows : optional list of (label, confirmed_color, worked_color) tuples
                 for caller-specific overlay types (e.g. geocache types)
    """
    if not overlays and not extra_rows:
        return

    OVERLAY_LEGEND_ROWS = {
        'states':   ('States/Provinces', STATES_COLORS.get('confirmed','#2ecc71'),
                                         STATES_COLORS.get('worked','#f39c12')),
        'counties': ('Counties',         COUNTIES_COLORS.get('confirmed','#1a8a4a'),
                                         COUNTIES_COLORS.get('worked','#c0720a')),
        'grids':    ('Grid Squares',     GRIDS_COLORS.get('confirmed','#27ae9e'),
                                         GRIDS_COLORS.get('worked','#e8a020')),
    }

    def swatch(color, border='#555'):
        return (f'<span style="display:inline-block;width:12px;height:12px;'
                f'border-radius:2px;background:{color};border:1px solid {border};'
                'opacity:0.85;margin-right:3px"></span>')

    def row(label, c_conf, c_worked):
        return (
            f'<div style="display:flex;align-items:center;gap:4px;margin:3px 0">'
            + swatch(c_conf) + swatch(c_worked)
            + f'<span style="margin-left:2px">{label}</span></div>'
        )

    header = (
        '<div style="display:flex;gap:16px;font-size:11px;color:#666;margin-bottom:4px">'
        + '<span>' + swatch('#555') + 'Confirmed</span>'
        + '<span>' + swatch('#aaa') + 'Worked</span>'
        + '</div>'
    )

    items = header
    for key in ['states', 'counties', 'grids']:
        if key in overlays:
            label, cc, cw = OVERLAY_LEGEND_ROWS[key]
            items += row(label, cc, cw)

    for extra in (extra_rows or []):
        label, cc, cw = extra
        items += row(label, cc, cw)

    items += (
        '<div style="display:flex;align-items:center;gap:4px;margin:3px 0">'
        '<span style="display:inline-block;width:12px;height:12px;border-radius:2px;'
        'background:#fff;border:1px solid #999;margin-right:3px"></span>'
        '<span>Not worked (outline only)</span></div>'
    )

    html = """
    <div style="position:fixed;bottom:30px;right:10px;z-index:9999;
                background:rgba(255,255,255,0.92);padding:10px 14px;
                border-radius:8px;border:1px solid #aaa;font-size:13px;
                font-family:sans-serif;box-shadow:2px 2px 6px rgba(0,0,0,0.3)">
      <b>Overlay</b><br>""" + items + """
    </div>
    """
    m.get_root().html.add_child(folium.Element(html))


# ---------------------------------------------------------------------------
# Theme colors serialised for JS (used by both adif_map and geocache_map)
# ---------------------------------------------------------------------------

def theme_colors_js_dict() -> dict:
    """Return a dict suitable for json.dumps() for the JS recompute engine."""
    return {
        'states': {
            'confirmed':        STATES_COLORS.get('confirmed',        '#2ecc71'),
            'worked':           STATES_COLORS.get('worked',           '#f39c12'),
            'border_confirmed': STATES_COLORS.get('border_confirmed', '#000000'),
            'border_worked':    STATES_COLORS.get('border_worked',    '#000000'),
            'confirmed_weight': STATES_COLORS.get('confirmed_weight', 2.0),
            'worked_weight':    STATES_COLORS.get('worked_weight',    2.0),
            'fill_opacity':     STATES_COLORS.get('fill_opacity',     0.55),
            'unworked_fill':    STATES_COLORS.get('unworked_fill',    '#ffffff'),
            'unworked_border':  STATES_COLORS.get('unworked_border',  '#666666'),
            'unworked_weight':  STATES_COLORS.get('unworked_weight',  0.8),
        },
        'counties': {
            'confirmed':        COUNTIES_COLORS.get('confirmed',        '#1a8a4a'),
            'worked':           COUNTIES_COLORS.get('worked',           '#c0720a'),
            'border_confirmed': COUNTIES_COLORS.get('border_confirmed', '#000000'),
            'border_worked':    COUNTIES_COLORS.get('border_worked',    '#000000'),
            'confirmed_weight': COUNTIES_COLORS.get('confirmed_weight', 0.8),
            'worked_weight':    COUNTIES_COLORS.get('worked_weight',    0.8),
            'fill_opacity':     COUNTIES_COLORS.get('fill_opacity',     0.75),
            'unworked_fill':    COUNTIES_COLORS.get('unworked_fill',    '#ffffff'),
            'unworked_border':  COUNTIES_COLORS.get('unworked_border',  '#000000'),
            'unworked_weight':  COUNTIES_COLORS.get('unworked_weight',  0.3),
        },
        'grids': {
            'confirmed':    GRIDS_COLORS.get('confirmed',    '#27ae9e'),
            'worked':       GRIDS_COLORS.get('worked',       '#e8a020'),
            'fill_opacity': GRIDS_COLORS.get('fill_opacity', 0.45),
        },
    }
