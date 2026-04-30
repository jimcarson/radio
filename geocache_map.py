#!/usr/bin/env python3
"""
geocache_map.py — Geocache GPX map viewer
==========================================
Parses a GPX file exported from GSAK (or geocaching.com) and renders
caches on an interactive Leaflet map via folium.

Shares map infrastructure with adif_map.py via map_core.py.

Dependencies:
    pip install folium pyyaml

Usage:
    python geocache_map.py caches.gpx [options]

Options:
    --type LIST         Filter by cache type(s), comma-separated
                        Types: Traditional, Mystery, Multi, Earth, Virtual,
                               Letterbox, Wherigo, CITO, Mega, Giga
                        (case-insensitive prefix match — e.g. "trad,earth")
    --difficulty MIN-MAX  Difficulty range, e.g. --difficulty 1-3.5
    --terrain MIN-MAX     Terrain range,    e.g. --terrain 2-5
    --found             Show only found caches (sym contains "Found")
    --not-found         Show only unfound caches
    --overlay LIST      Comma-separated overlays: states, counties, grids
                        (requires adif_setup.py boundary files)
    --theme FILE        Color theme YAML (default: theme_default.yaml)
    --show-filters      Show collapsible type/D/T filter panel
    --output FILE       Output HTML filename (default: map_output.html)
    --verbose           Show cache type breakdown and skipped count
"""

__version__ = "1.1.0"  # --db, intl county shading, country borders, _cnty_key rewrite

import argparse
import sys
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import folium
except ImportError:
    sys.exit("Missing dependency: run  pip install folium")

try:
    import map_core
    from map_core import (
        load_theme, build_base_map,
        build_grid_overlay, build_states_overlay, build_counties_overlay,
        build_country_borders_overlay,
        add_overlay_legend, theme_colors_js_dict,
        BAND_COLORS, DEFAULT_COLOR, STATES_COLORS, COUNTIES_COLORS,
        GRIDS_COLORS, MAP_LON_OFFSET, CONTACT_DOT,
    )
except ImportError:
    sys.exit(
        "Missing map_core.py — ensure it is in the same directory as geocache_map.py."
    )

try:
    from gsak_counties import GSAK_NAME_TO_ISO, ISO_TO_GSAK_NAME
    _GSAK_COUNTIES_AVAILABLE = True
except ImportError:
    GSAK_NAME_TO_ISO = {}
    ISO_TO_GSAK_NAME = {}
    _GSAK_COUNTIES_AVAILABLE = False


def _strip_accents(s: str) -> str:
    """
    Transliterate accented and special characters to their ASCII equivalents.

    Two-pass approach:
      1. Substitute characters that NFD cannot decompose (ø→o, ð→d, þ→th,
         æ→ae, å→a) — common in Norse, Faroese, and Icelandic names.
      2. NFD-decompose and drop remaining combining accent marks (Mn category),
         covering é→e, č→c, ü→u, ñ→n, etc.
    """
    import unicodedata as _ud
    _SUBST = {
        'ø': 'o', 'Ø': 'O',
        'ð': 'd', 'Ð': 'D',
        'þ': 'th', 'Þ': 'Th',
        'æ': 'ae', 'Æ': 'Ae',
        'å': 'a', 'Å': 'A',
    }
    for char, repl in _SUBST.items():
        s = s.replace(char, repl)
    return ''.join(
        c for c in _ud.normalize('NFD', s)
        if _ud.category(c) != 'Mn'
    )


def resolve_db_path(db_arg: str | None) -> str | None:
    """
    Resolve the path to gsak_counties.db.

    Priority:
      1. Explicit --db argument
      2. gsak_counties.db beside geocache_map.py
      3. gsak_counties.db in the current working directory

    Returns the resolved path string if the file exists, else None
    (with a warning printed).
    """
    import os
    candidates = []
    if db_arg:
        candidates.append(Path(db_arg).expanduser().resolve())
    candidates.append(Path(__file__).parent / "gsak_counties.db")
    candidates.append(Path.cwd() / "gsak_counties.db")

    for p in candidates:
        if p.exists():
            return str(p)

    if db_arg:
        print(f"  Warning: --db path not found: {db_arg} — country/county DB features disabled.")
    else:
        print("  Note: gsak_counties.db not found — country borders and international "
              "county overlays will be skipped.")
    return None


def countries_to_gsak_names(country_strings: set[str]) -> list[str]:
    """
    Convert a set of country strings from GPX <country> fields to GSAK
    country names suitable for build_country_borders_overlay().

    GPX country fields are free-text (e.g. 'United States', 'Iceland').
    We resolve via ISO_TO_GSAK_NAME: first map the GPX string to an ISO
    code via GSAK_NAME_TO_ISO (which covers the common spellings), then
    map the ISO code back to the canonical GSAK name.

    Falls back to the raw string if no mapping is found — it may still
    match a country_polygons row by name.
    """
    gsak_names: list[str] = []
    seen: set[str] = set()
    for country_str in country_strings:
        if not country_str:
            continue
        # Try direct GSAK name lookup first
        iso = GSAK_NAME_TO_ISO.get(country_str)
        if iso:
            gsak_name = ISO_TO_GSAK_NAME.get(iso, country_str)
        else:
            # Try case-insensitive scan as fallback
            lower = country_str.lower()
            gsak_name = next(
                (n for n in GSAK_NAME_TO_ISO if n.lower() == lower),
                country_str,
            )
        if gsak_name not in seen:
            seen.add(gsak_name)
            gsak_names.append(gsak_name)
    return gsak_names


# ---------------------------------------------------------------------------
# GPX namespaces
# ---------------------------------------------------------------------------

_NS = {
    'gpx':        'http://www.topografix.com/GPX/1/0',
    'groundspeak': 'http://www.groundspeak.com/cache/1/0/1',
    'gsak':       'http://www.gsak.net/xmlv1/6',
}

# Also try /1/0/2 variant (some exports)
_NS_GS_ALT = 'http://www.groundspeak.com/cache/1/0/2'


# ---------------------------------------------------------------------------
# Cache type configuration
# ---------------------------------------------------------------------------

# Display name -> (dot color, short label for filter panel)
CACHE_TYPE_COLORS: dict = {
    'Traditional Cache': ('#2ecc71', 'Traditional'),
    'Mystery Cache':     ('#3498db', 'Mystery'),
    'Unknown Cache':     ('#3498db', 'Mystery'),      # alias
    'Multi-cache':       ('#e67e22', 'Multi'),
    'Earthcache':        ('#8B4513', 'Earth'),
    'Virtual Cache':     ('#9b59b6', 'Virtual'),
    'Letterbox Hybrid':  ('#1abc9c', 'Letterbox'),
    'Wherigo Cache':     ('#16a085', 'Wherigo'),
    'Cache In Trash Out Event': ('#f39c12', 'CITO'),
    'Mega-Event Cache':  ('#e74c3c', 'Mega'),
    'Giga-Event Cache':  ('#c0392b', 'Giga'),
    'Event Cache':       ('#e74c3c', 'Event'),
}
_DEFAULT_CACHE_COLOR = '#888888'

# Canonical prefix map for --type matching (lowercase prefix -> canonical name)
_TYPE_PREFIX_MAP: dict = {
    t.lower()[:3]: canonical
    for canonical, _ in CACHE_TYPE_COLORS.items()
    for t in [canonical]
}
# More explicit mappings
_TYPE_ALIASES: dict = {
    'traditional': 'Traditional Cache',
    'trad':        'Traditional Cache',
    'mystery':     'Mystery Cache',
    'unknown':     'Mystery Cache',
    'myst':        'Mystery Cache',
    'multi':       'Multi-cache',
    'earth':       'Earthcache',
    'ec':          'Earthcache',
    'virtual':     'Virtual Cache',
    'virt':        'Virtual Cache',
    'letterbox':   'Letterbox Hybrid',
    'lb':          'Letterbox Hybrid',
    'wherigo':     'Wherigo Cache',
    'wh':          'Wherigo Cache',
    'cito':        'Cache In Trash Out Event',
    'mega':        'Mega-Event Cache',
    'giga':        'Giga-Event Cache',
    'event':       'Event Cache',
}


def resolve_cache_type(raw_type: str) -> str:
    """Normalise a raw cache type string to a canonical key."""
    t = raw_type.strip()
    if t in CACHE_TYPE_COLORS:
        return t
    # Try alias lookup
    lower = t.lower()
    if lower in _TYPE_ALIASES:
        return _TYPE_ALIASES[lower]
    # Prefix match on canonical names
    for canonical in CACHE_TYPE_COLORS:
        if canonical.lower().startswith(lower):
            return canonical
    return t  # unknown — keep as-is


def cache_color(cache_type: str) -> str:
    return CACHE_TYPE_COLORS.get(cache_type, (_DEFAULT_CACHE_COLOR,))[0] \
        if isinstance(CACHE_TYPE_COLORS.get(cache_type), tuple) \
        else CACHE_TYPE_COLORS.get(cache_type, _DEFAULT_CACHE_COLOR)  # type: ignore


def _color_for(cache_type: str) -> str:
    entry = CACHE_TYPE_COLORS.get(cache_type)
    if isinstance(entry, tuple):
        return entry[0]
    return entry or _DEFAULT_CACHE_COLOR


def _label_for(cache_type: str) -> str:
    entry = CACHE_TYPE_COLORS.get(cache_type)
    if isinstance(entry, tuple):
        return entry[1]
    # Fall back to first word of type name
    return cache_type.split()[0] if cache_type else 'Other'


# ---------------------------------------------------------------------------
# GPX parsing
# ---------------------------------------------------------------------------

def _find_gs(wpt, tag: str):
    """Find a groundspeak: child element, trying both namespace variants."""
    el = wpt.find(f'groundspeak:cache/groundspeak:{tag}', _NS)
    if el is None:
        # Try alt namespace
        el = wpt.find(f'{{{_NS_GS_ALT}}}cache/{{{_NS_GS_ALT}}}{tag}')
    return el


def parse_gpx(gpx_path: Path) -> list[dict]:
    """
    Parse a GPX file and return a list of cache dicts with keys:
        gc_code, name, lat, lon, type, difficulty, terrain,
        found, placed_by, container, country, state, county,
        sym, url, _confirmed (=found, for map_core overlay compat)
    """
    try:
        tree = ET.parse(str(gpx_path))
    except ET.ParseError as exc:
        sys.exit(f"GPX parse error: {exc}")

    root = tree.getroot()
    # Strip namespace from root tag if needed
    ns_uri = _NS['gpx']

    caches = []
    for wpt in root.findall(f'{{{ns_uri}}}wpt'):
        try:
            lat = float(wpt.get('lat', 0))
            lon = float(wpt.get('lon', 0))
        except ValueError:
            continue

        gc_code = wpt.findtext(f'{{{ns_uri}}}n') or ''

        def _gs_text(tag: str, default: str = '') -> str:
            """Get text of a groundspeak child element; '' if absent or empty."""
            el = _find_gs(wpt, tag)
            return el.text.strip() if el is not None and el.text else default

        name     = _gs_text('name') or wpt.findtext(f'{{{ns_uri}}}urlname') or gc_code
        raw_type = _gs_text('type')
        # Also try the <type> wpt-level field: "Geocache|Traditional Cache|Found"
        if not raw_type:
            wpt_type = wpt.findtext(f'{{{ns_uri}}}type') or ''
            parts = wpt_type.split('|')
            raw_type = parts[1].strip() if len(parts) >= 2 else wpt_type

        cache_type = resolve_cache_type(raw_type)

        try:
            difficulty = float(_gs_text('difficulty') or 0)
        except ValueError:
            difficulty = 0.0
        try:
            terrain = float(_gs_text('terrain') or 0)
        except ValueError:
            terrain = 0.0

        placed_by = _gs_text('placed_by')
        container = _gs_text('container')
        country   = _gs_text('country')
        state     = _gs_text('state')

        sym  = wpt.findtext(f'{{{ns_uri}}}sym') or ''
        url  = wpt.findtext(f'{{{ns_uri}}}url') or ''
        found = 'found' in sym.lower()

        # GSAK county extension
        county = ''
        gsak_ext = wpt.find(f'gsak:wptExtension/gsak:County', _NS)
        if gsak_ext is None:
            gsak_ext = wpt.find(
                f'{{{_NS["gsak"]}}}wptExtension/{{{_NS["gsak"]}}}County')
        if gsak_ext is not None and gsak_ext.text:
            county = gsak_ext.text.strip()

        # Skip non-cache waypoints (Final Location, Parking, etc.)
        wpt_type_raw = wpt.findtext(f'{{{ns_uri}}}type') or ''
        if wpt_type_raw and not wpt_type_raw.startswith('Geocache'):
            continue
        if sym == 'Final Location':
            continue

        caches.append({
            'gc_code':    gc_code,
            'name':       name,
            'lat':        lat,
            'lon':        lon,
            'type':       cache_type,
            'difficulty': difficulty,
            'terrain':    terrain,
            'found':      found,
            'placed_by':  placed_by,
            'container':  container,
            'country':    country,
            'state':      state,
            'county':     county,
            'sym':        sym,
            'url':        url,
            '_confirmed': found,  # map_core overlay compatibility
        })

    return caches


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _parse_range(s: str) -> tuple[float, float]:
    """Parse "MIN-MAX" string into (min, max) floats. Raises ValueError on bad input."""
    parts = s.split('-')
    if len(parts) == 2:
        return float(parts[0]), float(parts[1])
    raise ValueError(f"Expected MIN-MAX format, got: {s!r}")


def _resolve_type_filter(raw: str) -> set[str]:
    """Resolve comma-separated --type argument to a set of canonical cache type names."""
    result = set()
    for tok in raw.split(','):
        tok = tok.strip()
        if not tok:
            continue
        canonical = _TYPE_ALIASES.get(tok.lower())
        if canonical:
            result.add(canonical)
            # Mystery/Unknown are aliases for the same dot color
            if canonical == 'Mystery Cache':
                result.add('Unknown Cache')
            continue
        # Prefix match
        for full_name in CACHE_TYPE_COLORS:
            if full_name.lower().startswith(tok.lower()):
                result.add(full_name)
                break
        else:
            print(f"  Warning: unknown cache type filter token '{tok}' — ignored.")
    return result


def apply_filters(caches: list, args) -> list:
    out = []
    type_filter = _resolve_type_filter(args.type or '') if args.type else set()
    diff_range = _parse_range(args.difficulty) if args.difficulty else None
    terr_range = _parse_range(args.terrain)    if args.terrain    else None

    for c in caches:
        if type_filter and c['type'] not in type_filter:
            continue
        if diff_range and not (diff_range[0] <= c['difficulty'] <= diff_range[1]):
            continue
        if terr_range and not (terr_range[0] <= c['terrain'] <= terr_range[1]):
            continue
        if args.found and not c['found']:
            continue
        if args.not_found and c['found']:
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Map building
# ---------------------------------------------------------------------------

def build_map(center: tuple, caches: list,
              m: folium.Map = None) -> tuple[folium.Map, int]:
    """
    Build the geocache map. Returns (map, plotted_count).
    Caches are grouped by type into FeatureGroups with MarkerCluster.

    m : if provided, dots are added to this existing map (allows overlays to
        be added first so they render beneath the cache markers in Leaflet).
        If None, a new base map is created.
    """
    from folium.plugins import MarkerCluster

    if m is None:
        m = build_base_map(center[0], center[1], zoom_start=7)

    cluster_icon_fn = """
        function(cluster) {
            var count = cluster.getChildCount();
            var size  = count < 10 ? 28 : count < 100 ? 36 : 44;
            return L.divIcon({
                html: '<div title="' + count + ' caches"'
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

    type_fgs:      dict = {}
    type_clusters: dict = {}
    plotted = 0
    skipped = 0

    for c in caches:
        ctype = c['type']
        color = _color_for(ctype)
        label = _label_for(ctype)

        dt_str = ''
        if c['difficulty'] > 0 or c['terrain'] > 0:
            dt_str = f" | D{c['difficulty']}/T{c['terrain']}"
        found_str = ' ✓ Found' if c['found'] else ''
        url_html  = f' <a href="{c["url"]}" target="_blank">↗</a>' if c['url'] else ''
        tooltip_text = (
            f"{c['gc_code']} — {c['name']}{dt_str}{found_str}"
        )
        popup_html = (
            f"<b>{c['gc_code']}</b>{url_html}<br>"
            f"{c['name']}<br>"
            f"Type: {ctype}<br>"
            f"D/T: {c['difficulty']}/{c['terrain']}<br>"
            f"By: {c['placed_by']}"
            + (f"<br>County: {c['county']}" if c['county'] else '')
            + (f"<br><b>Found</b>" if c['found'] else '')
        )

        if ctype not in type_fgs:
            fg = folium.FeatureGroup(name=f"Type: {label}", show=True)
            type_fgs[ctype] = fg
            type_clusters[ctype] = MarkerCluster(
                icon_create_function=cluster_icon_fn,
                options={"maxClusterRadius": 40, "disableClusteringAtZoom": 12},
            ).add_to(fg)

        dot = CONTACT_DOT
        folium.CircleMarker(
            location=(c['lat'], c['lon']),
            radius=dot.get('radius', 6),
            color=dot.get('border_color', 'white'),
            weight=dot.get('border_weight', 1.2),
            fill=True,
            fill_color=color,
            fill_opacity=dot.get('fill_opacity', 0.85),
            tooltip=tooltip_text,
            popup=folium.Popup(popup_html, max_width=280),
        ).add_to(type_clusters[ctype])
        plotted += 1

    for fg in type_fgs.values():
        fg.add_to(m)

    if skipped:
        print(f"  Note: {skipped} waypoint(s) skipped — no usable coordinates.")

    return m, plotted


# ---------------------------------------------------------------------------
# Type color legend  (top-left, static — always visible)
# ---------------------------------------------------------------------------

def add_type_legend(m: folium.Map, types_present: set) -> None:
    """Add a cache-type color legend (bottom-left of map)."""
    items = ''
    for ctype, entry in CACHE_TYPE_COLORS.items():
        if ctype not in types_present:
            continue
        color = entry[0] if isinstance(entry, tuple) else entry
        label = entry[1] if isinstance(entry, tuple) else ctype.split()[0]
        items += (
            f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0">'
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'border-radius:50%;background:{color};border:1px solid #555"></span>'
            f'<span>{label}</span></div>\n'
        )

    html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
                background:rgba(255,255,255,0.9);padding:10px 14px;
                border-radius:8px;border:1px solid #aaa;font-size:13px;
                font-family:sans-serif;box-shadow:2px 2px 6px rgba(0,0,0,0.3)">
      <b>Cache Type</b><br>{items}
    </div>
    """
    m.get_root().html.add_child(folium.Element(html))


# ---------------------------------------------------------------------------
# Filter panel  (type checkboxes + D/T sliders)
# ---------------------------------------------------------------------------

def inject_filter_panel(m: folium.Map, caches: list,
                        type_fgs: dict, types_present: set) -> None:
    """
    Inject a collapsible filter panel (top-left, starts collapsed).
    Contains:
      - Cache type checkboxes (toggle FeatureGroup layers)
      - Difficulty slider (min/max)
      - Terrain slider (min/max)
    """
    import json

    map_var = m.get_name()

    # Build type -> FeatureGroup JS var name mapping
    type_fg_names = {ct: fg.get_name() for ct, fg in type_fgs.items()}
    type_fg_names_js = json.dumps(type_fg_names)

    # Build type color map for swatches
    type_colors = {ct: _color_for(ct) for ct in types_present}
    type_labels = {ct: _label_for(ct) for ct in types_present}
    type_colors_js = json.dumps(type_colors)
    type_labels_js = json.dumps(type_labels)

    # Build per-cache data for D/T filtering: {gc_code: [lat, lon, type, diff, terr]}
    cache_data = {
        c['gc_code']: [c['lat'], c['lon'], c['type'], c['difficulty'], c['terrain']]
        for c in caches
    }
    cache_data_js = json.dumps(cache_data)

    panel_html = f"""
<style>
#gc-fp {{
    position:fixed; top:80px; left:10px; z-index:1000;
    background:rgba(255,255,255,0.95); border:1px solid #aaa;
    border-radius:8px; box-shadow:2px 2px 8px rgba(0,0,0,0.25);
    font-family:sans-serif; font-size:12px;
    min-width:170px; max-width:230px;
    user-select:none; cursor:default;
}}
#gc-fp .pt {{
    padding:6px 10px; font-weight:bold; font-size:13px;
    border-bottom:1px solid #ddd; display:flex;
    justify-content:space-between; align-items:center;
    cursor:pointer; color:#333;
}}
#gc-fp .sh {{
    padding:5px 10px 3px; font-weight:bold; color:#555; cursor:pointer;
    display:flex; justify-content:space-between; align-items:center;
    border-top:1px solid #eee; font-size:11px;
    text-transform:uppercase; letter-spacing:0.05em;
}}
#gc-fp .sh:hover {{ background:#f5f5f5; }}
#gc-fp .sb {{ padding:2px 8px 6px 10px; }}
#gc-fp .tr {{ display:flex; align-items:center; gap:6px; padding:2px 0; }}
#gc-fp .tr:hover {{ background:#f8f8f8; border-radius:3px; padding:2px 2px; margin:0 -2px; }}
#gc-fp .sw {{
    display:inline-block; width:10px; height:10px; border-radius:50%;
    border:1px solid rgba(0,0,0,0.2); flex-shrink:0;
}}
#gc-fp .chv {{ font-size:10px; color:#999; transition:transform 0.15s; display:inline-block; }}
#gc-fp .chv.col {{ transform:rotate(-90deg); }}
#gc-fp .sl-row {{ padding:3px 10px 6px; }}
#gc-fp .sl-lbl {{ display:flex; justify-content:space-between; font-size:11px; color:#555; margin-bottom:2px; }}
#gc-fp input[type=range] {{ width:100%; margin:0; }}
</style>
<div id="gc-fp">
  <div class="pt" id="gc-ptitle">Filters <span id="gc-pchev">▶</span></div>
  <div id="gc-pbody" style="display:none">

    <div class="sh" id="gc-thead">
      Cache Types <span id="gc-tchev" class="chv">▼</span>
    </div>
    <div class="sb" id="gc-types-body"></div>

    <div class="sh" id="gc-dhead">
      Difficulty <span id="gc-dchev" class="chv">▼</span>
    </div>
    <div id="gc-diff-body" class="sl-row">
      <div class="sl-lbl">
        <span>Min: <b id="gc-dmin-v">1</b></span>
        <span>Max: <b id="gc-dmax-v">5</b></span>
      </div>
      <input type="range" id="gc-dmin" min="1" max="5" step="0.5" value="1">
      <input type="range" id="gc-dmax" min="1" max="5" step="0.5" value="5">
    </div>

    <div class="sh" id="gc-thead2">
      Terrain <span id="gc-tchev2" class="chv">▼</span>
    </div>
    <div id="gc-terr-body" class="sl-row">
      <div class="sl-lbl">
        <span>Min: <b id="gc-tmin-v">1</b></span>
        <span>Max: <b id="gc-tmax-v">5</b></span>
      </div>
      <input type="range" id="gc-tmin" min="1" max="5" step="0.5" value="1">
      <input type="range" id="gc-tmax" min="1" max="5" step="0.5" value="5">
    </div>

  </div>
</div>
<script>
setTimeout(function() {{
    var mapObj       = {map_var};
    var typeFgNames  = {type_fg_names_js};
    var typeColors   = {type_colors_js};
    var typeLabels   = {type_labels_js};

    var activeTypes = new Set(Object.keys(typeFgNames));
    var diffMin = 1, diffMax = 5, terrMin = 1, terrMax = 5;

    function getLayer(n) {{ return window[n] || null; }}

    // ── Build type checkboxes ─────────────────────────────────
    var typesBody = document.getElementById('gc-types-body');
    if (typesBody) {{
        Object.keys(typeFgNames).forEach(function(ct) {{
            var row = document.createElement('div');
            row.className = 'tr';
            var cb = document.createElement('input');
            cb.type='checkbox'; cb.id='cb-ct-'+ct; cb.checked=true;
            cb.addEventListener('change', function() {{ gcTypeToggle(ct, this.checked); }});
            var sw = document.createElement('span');
            sw.className='sw'; sw.style.background = typeColors[ct] || '#888';
            var lb = document.createElement('label');
            lb.htmlFor='cb-ct-'+ct;
            lb.textContent = typeLabels[ct] || ct.split(' ')[0];
            lb.style.cursor='pointer';
            row.appendChild(cb); row.appendChild(sw); row.appendChild(lb);
            typesBody.appendChild(row);
        }});
    }}

    // ── Type toggle ───────────────────────────────────────────
    window.gcTypeToggle = function(ct, checked) {{
        var layer = getLayer(typeFgNames[ct]);
        if (layer) {{
            if (checked) {{ if (!mapObj.hasLayer(layer)) layer.addTo(mapObj); }}
            else         {{ if (mapObj.hasLayer(layer))  layer.remove(); }}
        }}
        if (checked) activeTypes.add(ct); else activeTypes.delete(ct);
    }};

    // ── D/T slider wiring ─────────────────────────────────────
    function wireSlider(minId, maxId, minValId, maxValId, onchange) {{
        var sMin = document.getElementById(minId);
        var sMax = document.getElementById(maxId);
        var vMin = document.getElementById(minValId);
        var vMax = document.getElementById(maxValId);
        if (!sMin || !sMax) return;
        function update() {{
            var lo = parseFloat(sMin.value);
            var hi = parseFloat(sMax.value);
            if (lo > hi) {{ sMax.value = lo; hi = lo; }}
            if (vMin) vMin.textContent = lo;
            if (vMax) vMax.textContent = hi;
            onchange(lo, hi);
        }}
        sMin.addEventListener('input', update);
        sMax.addEventListener('input', update);
    }}

    wireSlider('gc-dmin','gc-dmax','gc-dmin-v','gc-dmax-v', function(lo, hi) {{
        diffMin = lo; diffMax = hi;
    }});
    wireSlider('gc-tmin','gc-tmax','gc-tmin-v','gc-tmax-v', function(lo, hi) {{
        terrMin = lo; terrMax = hi;
    }});

    // ── Collapse wiring ───────────────────────────────────────
    function wireClick(id, fn) {{
        var el = document.getElementById(id);
        if (el) el.addEventListener('click', fn);
    }}
    wireClick('gc-ptitle', function() {{
        var body = document.getElementById('gc-pbody');
        var chev = document.getElementById('gc-pchev');
        if (!body) return;
        var h = body.style.display === 'none';
        body.style.display = h ? '' : 'none';
        if (chev) chev.textContent = h ? '▼' : '▶';
    }});
    function wireSection(bodyId, chevId, headId) {{
        wireClick(headId, function() {{
            var body = document.getElementById(bodyId);
            var chev = document.getElementById(chevId);
            if (!body) return;
            var h = body.style.display === 'none';
            body.style.display = h ? '' : 'none';
            if (chev) chev.classList.toggle('col', !h);
        }});
    }}
    wireSection('gc-types-body', 'gc-tchev',  'gc-thead');
    wireSection('gc-diff-body',  'gc-dchev',  'gc-dhead');
    wireSection('gc-terr-body',  'gc-tchev2', 'gc-thead2');

    // Wire Leaflet event isolation
    var fp = document.getElementById('gc-fp');
    if (fp && window.L) {{
        L.DomEvent.disableClickPropagation(fp);
        L.DomEvent.disableScrollPropagation(fp);
    }}

}}, 300);
</script>
"""
    m.get_root().html.add_child(folium.Element(panel_html))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Visualise geocaches from a GPX file on an interactive map."
    )
    parser.add_argument("gpx", help="Path to GPX file (GSAK or geocaching.com export)")
    parser.add_argument("--type",
                        help="Cache type filter, comma-separated (e.g. Traditional,Earth,Mystery)")
    parser.add_argument("--difficulty",
                        help="Difficulty range, e.g. --difficulty 1-3.5")
    parser.add_argument("--terrain",
                        help="Terrain range, e.g. --terrain 2-5")
    parser.add_argument("--found",     action="store_true",
                        help="Show only found caches")
    parser.add_argument("--not-found", dest="not_found", action="store_true",
                        help="Show only unfound caches")
    parser.add_argument("--overlay",
                        help="Comma-separated overlays: states, counties, grids")
    parser.add_argument("--theme",
                        help="Color theme YAML (default: theme_default.yaml)")
    parser.add_argument("--show-filters", dest="show_filters", action="store_true",
                        help="Show collapsible type/D/T filter panel")
    parser.add_argument("--output",
                        help="Output HTML path (default: map_output.html beside GPX)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show cache type breakdown")
    parser.add_argument("--db",
                        help="Path to gsak_counties.db for country borders and "
                             "international county overlays "
                             "(default: auto-detect beside script or in CWD)")
    args = parser.parse_args()

    gpx_path = Path(args.gpx).expanduser().resolve()
    if not gpx_path.exists():
        sys.exit(f"File not found: {gpx_path}")

    load_theme(args.theme, script_dir=Path(__file__).parent)

    # Resolve DB path early so we can report its status before building
    db_path = resolve_db_path(getattr(args, 'db', None))

    print(f"Parsing {gpx_path.name} ...")
    all_caches = parse_gpx(gpx_path)
    print(f"  {len(all_caches)} geocaches found.")

    filtered = apply_filters(all_caches, args)
    print(f"  {len(filtered)} caches after filtering.")

    if not filtered:
        sys.exit("No caches to plot after filtering.")

    # Collect country names present in filtered data for border overlay
    represented_countries = {c['country'] for c in filtered if c.get('country')}

    if args.verbose:
        from collections import Counter
        type_counts = Counter(c['type'] for c in filtered)
        for t, n in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"    {t}: {n}")

    # Map center: average lat/lon of all filtered caches
    avg_lat = sum(c['lat'] for c in filtered) / len(filtered)
    avg_lon = sum(c['lon'] for c in filtered) / len(filtered)
    center  = (avg_lat, avg_lon)

    print("Building map ...")
    center = (avg_lat, avg_lon)

    # Create base map first so overlays can be added before cache dot layers.
    # Leaflet renders FeatureGroups in insertion order — overlays added here
    # will sit beneath the cache markers added by build_map() below.
    m = map_core.build_base_map(center[0], center[1], zoom_start=7)

    # Overlays (reuse map_core builders; key functions work on geocache dicts)
    overlay_meta = {}
    overlays = [o.strip().lower() for o in (args.overlay or '').split(',') if o.strip()]

    def _us_key(c): return c.get('state','').upper().strip()[:2] if c.get('country','').lower() in ('united states','us','usa') else ''

    # Pre-build the set of known US/CA postal codes for fast lookup
    try:
        from gsak_counties import _POSTAL_STATE as _ps, lookup_county as _lookup_county
        _us_ca_codes: set = set(_ps.keys())
    except ImportError:
        _us_ca_codes = set()
        _lookup_county = None

    # Coordinate → (state, county) lookup cache — avoids redundant DB hits
    # for multiple caches in the same county
    _coord_county_cache: dict = {}

    def _cnty_key(c):
        import re as _re
        state  = c.get('state',  '').strip()
        county = c.get('county', '').strip()

        # Strip common admin suffixes from county field (US/CA only meaningful,
        # but safe to apply universally since intl names won't have them)
        def _clean_county(s):
            return _re.sub(
                r'\s+(County|Parish|Borough|Census Area|Municipality)\s*$',
                '', s, flags=_re.IGNORECASE,
            ).strip()

        if state in _us_ca_codes:
            # US or CA: state field IS the 2-letter postal code
            if county:
                return f"{state},{_clean_county(county)}"
            # No county in GPX — do a coordinate-based DB lookup
            if _lookup_county is None or not db_path:
                return ''
            lat, lon = c.get('lat', 0.0), c.get('lon', 0.0)
            cache_key = (round(lat, 5), round(lon, 5))
            if cache_key not in _coord_county_cache:
                sc, cn = _lookup_county(lat, lon, db_path=db_path,
                                        state_hint=state)
                _coord_county_cache[cache_key] = (sc, cn)
            sc, cn = _coord_county_cache[cache_key]
            if sc and cn:
                return f"{sc},{cn}"
            return ''

        # International cache: state field is a region/province name
        country_str = c.get('country', '')
        iso = GSAK_NAME_TO_ISO.get(country_str) or GSAK_NAME_TO_ISO.get(
            next((n for n in GSAK_NAME_TO_ISO
                  if n.lower() == country_str.lower()), ''), '')
        if not iso:
            return ''   # unknown country — skip

        if county:
            # GSAK populated the county field (e.g. CZ district name) — prefer it.
            # Strip accents to match DB filenames (Hlavní → Hlavni, etc.)
            return f"{iso},{_strip_accents(_clean_county(county))}"

        # No county field: try state field as region name first (works for flat
        # countries like Iceland, Norway where state == district).
        if not state:
            return ''
        key_from_state = f"{iso},{_strip_accents(state)}"

        # For countries like CZ where the GPX state is a *region* (Ústecký kraj)
        # but the DB has *districts* (Decin, Litomerice, ...), the state-derived
        # key won't match. Fall back to a coordinate lookup in that case.
        # We always try the state key first via the overlay's status dict; if it
        # misses there, the coordinate lookup will find the right district row.
        # We pre-populate the cache here so build_counties_overlay sees a hit.
        if _lookup_county is not None and db_path:
            lat, lon = c.get('lat', 0.0), c.get('lon', 0.0)
            coord_key = (round(lat, 5), round(lon, 5))
            if coord_key not in _coord_county_cache:
                sc, cn = _lookup_county(lat, lon, db_path=db_path,
                                        state_hint=iso)
                _coord_county_cache[coord_key] = (sc, cn)
            sc, cn = _coord_county_cache[coord_key]
            if sc and cn:
                return f"{sc},{cn}"   # e.g. 'CZ,Decin'

        return key_from_state

    def _grid_key(c): return ''   # GPX doesn't carry grid squares
    def _grp_fn(c): return c.get('type','Other')
    def _band_fn(c): return c.get('type','Other')  # reuse band slot for type

    # Country borders first — renders beneath county shading and cache dots
    if overlays and db_path:
        print("Building country borders overlay ...")
        gsak_names = countries_to_gsak_names(represented_countries)
        build_country_borders_overlay(m, db_path, country_names=gsak_names or None)

    if 'states' in overlays:
        print("Building states overlay ...")
        result = build_states_overlay(m, filtered, us_key_fn=_us_key)
        if result:
            overlay_meta['states'] = result
    if 'counties' in overlays:
        print("Building counties overlay ...")
        result = build_counties_overlay(m, filtered, key_fn=_cnty_key,
                                        db_path=db_path)
        if result:
            overlay_meta['counties'] = result
    if overlays:
        add_overlay_legend(m, [o for o in overlays if o in ('states','counties','grids')])

    # Add cache dots on top of all overlays
    m, plotted = build_map(center, filtered, m=m)

    # Get type_fgs for the filter panel
    types_present = {c['type'] for c in filtered}
    type_fg_refs: dict = {}
    for layer in m._children.values():
        if isinstance(layer, folium.FeatureGroup):
            name = layer.layer_name or ''
            if name.startswith('Type: '):
                label = name[6:]
                for ct in types_present:
                    if _label_for(ct) == label:
                        type_fg_refs[ct] = layer
                        break

    add_type_legend(m, types_present)

    if args.show_filters:
        inject_filter_panel(m, filtered, type_fg_refs, types_present)

    folium.LayerControl(collapsed=False).add_to(m)

    # Fit bounds to all plotted caches
    lats = [c['lat'] for c in filtered]
    lons = [c['lon'] for c in filtered]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
    else:
        out_path = gpx_path.parent / "map_output.html"

    m.save(str(out_path))
    print(f"  Map saved → {out_path}")
    print(f"  Plotted {plotted} caches.")

    webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()
