#!/usr/bin/env python3
"""
gsak_build_geojson.py — Generate us_counties.geojson from GSAK polygon DB
==========================================================================
Reads the US county polygons stored in gsak_counties.db and writes a
GeoJSON FeatureCollection suitable for use as the county choropleth layer
in adif_map.py and geocache_map.py.

The output replaces the Census/Natural Earth-derived us_counties.geojson
with higher-fidelity GSAK polygon boundaries.

Feature properties per county:
    adif_key  : "WA,King"                (state,name — matches ADIF CNTY field)
    name      : "King"                   (bare county name)
    namelsad  : "King County"            (display name with proper suffix)
    state     : "WA"                     (2-letter postal code)

namelsad suffix rules:
    Louisiana          → "{name} Parish"
    Alaska             → varies (see _ALASKA_SUFFIXES below)
    Virginia cities    → "{name} (Ind. City)"
    All others         → "{name} County"

Usage:
    python gsak_build_geojson.py
    python gsak_build_geojson.py --db gsak_counties.db --out us_counties.geojson
    python gsak_build_geojson.py --db gsak_counties.db --out us_counties.geojson --simplify 0.001

Dependencies: stdlib only (sqlite3, json, pathlib, argparse)
"""

__version__ = "1.2.0"  # IDL fix: normalize longitudes, MultiPolygon for island chains

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# namelsad suffix rules
# ---------------------------------------------------------------------------

# Virginia independent cities — same whitelist as gsak_counties.py
_VA_INDEPENDENT_CITIES: frozenset = frozenset({
    'Alexandria', 'Bristol', 'Buena Vista', 'Charlottesville', 'Chesapeake',
    'Colonial Heights', 'Covington', 'Danville', 'Emporia', 'Fairfax',
    'Falls Church', 'Franklin', 'Fredericksburg', 'Galax', 'Hampton',
    'Harrisonburg', 'Hopewell', 'Lexington', 'Lynchburg', 'Manassas',
    'Manassas Park', 'Martinsville', 'Newport News', 'Norfolk', 'Norton',
    'Petersburg', 'Poquoson', 'Portsmouth', 'Radford', 'Richmond', 'Roanoke',
    'Salem', 'Staunton', 'Suffolk', 'Virginia Beach', 'Waynesboro',
    'Williamsburg', 'Winchester',
})

# Alaska — each borough/census area/city has its own suffix type.
# Anything not listed defaults to "Borough".
_ALASKA_SUFFIXES: dict[str, str] = {
    # City and Borough
    'Juneau':           'City and Borough',
    'Sitka':            'City and Borough',
    'Wrangell':         'City and Borough',
    'Yakutat':          'City and Borough',
    # Census Areas (unorganized)
    'Aleutians East':         'Borough',
    'Aleutians West':         'Census Area',
    'Bethel':                 'Census Area',
    'Bristol Bay':            'Borough',
    'Denali':                 'Borough',
    'Dillingham':             'Census Area',
    'Fairbanks North Star':   'Borough',
    'Haines':                 'Borough',
    'Hoonah-Angoon':          'Census Area',
    'Kenai Peninsula':        'Borough',
    'Ketchikan Gateway':      'Borough',
    'Kodiak Island':          'Borough',
    'Kusilvak':               'Census Area',
    'Lake and Peninsula':     'Borough',
    'Matanuska-Susitna':      'Borough',
    'Nome':                   'Census Area',
    'North Slope':            'Borough',
    'Northwest Arctic':       'Borough',
    'Petersburg':             'Borough',
    'Prince of Wales-Hyder':  'Census Area',
    'Skagway':                'Municipality',
    'Southeast Fairbanks':    'Census Area',
    'Valdez-Cordova':         'Census Area',
    'Yukon-Koyukuk':          'Census Area',
    # Anchorage is a Municipality
    'Anchorage':              'Municipality',
}

# DC is its own thing
_DC_NAMELSAD = 'District of Columbia'


def make_namelsad(name: str, state: str) -> str:
    """
    Construct the full display name (namelsad) for a county/parish/borough.
    Mirrors the Census Bureau NAMELSAD field convention.
    """
    if state == 'DC':
        return _DC_NAMELSAD
    if state == 'LA':
        return f"{name} Parish"
    if state == 'AK':
        suffix = _ALASKA_SUFFIXES.get(name, 'Borough')
        return f"{name} {suffix}"
    if state == 'VA' and name in _VA_INDEPENDENT_CITIES:
        return f"{name} (Ind. City)"
    return f"{name} County"


# ---------------------------------------------------------------------------
# Optional polygon simplification (Ramer-Douglas-Peucker)
# ---------------------------------------------------------------------------

def _rdp(pts: list, epsilon: float) -> list:
    """
    Ramer-Douglas-Peucker polyline simplification.
    pts     : list of (lat, lon) tuples
    epsilon : maximum distance tolerance in degrees
    Returns a reduced list of points.
    """
    if len(pts) < 3:
        return pts

    # Find the point with maximum distance from the line start→end
    start, end = pts[0], pts[-1]
    max_dist = 0.0
    max_idx  = 0

    for i in range(1, len(pts) - 1):
        # Perpendicular distance from pts[i] to line segment start→end
        # Using the cross-product formula in 2D (lat/lon as x/y)
        dx = end[1] - start[1]
        dy = end[0] - start[0]
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            dist = ((pts[i][0] - start[0]) ** 2 +
                    (pts[i][1] - start[1]) ** 2) ** 0.5
        else:
            t = ((pts[i][0] - start[0]) * dy +
                 (pts[i][1] - start[1]) * dx) / length_sq
            t = max(0.0, min(1.0, t))
            proj_lat = start[0] + t * dy
            proj_lon = start[1] + t * dx
            dist = ((pts[i][0] - proj_lat) ** 2 +
                    (pts[i][1] - proj_lon) ** 2) ** 0.5
        if dist > max_dist:
            max_dist = dist
            max_idx  = i

    if max_dist > epsilon:
        left  = _rdp(pts[:max_idx + 1], epsilon)
        right = _rdp(pts[max_idx:],     epsilon)
        return left[:-1] + right
    return [start, end]


def simplify_polygon(pts: list, epsilon: float) -> list:
    """Apply RDP simplification, ensuring the polygon stays closed."""
    if epsilon <= 0 or len(pts) < 4:
        return pts
    simplified = _rdp(pts, epsilon)
    # Ensure closed ring
    if simplified[0] != simplified[-1]:
        simplified.append(simplified[0])
    return simplified


# ---------------------------------------------------------------------------
# International Date Line helpers
# ---------------------------------------------------------------------------

def normalize_lon(lon: float) -> float:
    """
    Shift East longitudes > 170° into the Western hemisphere by subtracting
    360°.  This keeps island-chain polygons (e.g. Aleutians West) entirely
    in negative-longitude space so renderers don't draw cross-globe lines.

    170° is chosen as the threshold because no US territory sits between
    170°E and 180°E on the Western side — everything east of 170°E that
    belongs to Alaska is geographically west of the IDL and should be
    expressed as a negative longitude.
    """
    return lon - 360.0 if lon > 170.0 else lon


def split_sub_polygons(pts: list) -> list[list]:
    """
    A GSAK polygon entry may concatenate multiple closed rings end-to-end
    (each ring closes back to its own first point before the next ring
    begins).  Split them into separate lists of (lat, lon) tuples.

    Returns a list of rings; each ring is a closed list of (lat, lon) tuples.
    If no closing point is found for a ring the remaining points are returned
    as a single unclosed ring (the GeoJSON builder will close it).
    """
    if not pts:
        return []

    rings = []
    start = 0

    while start < len(pts):
        first = tuple(pts[start])
        closed = False
        for i in range(start + 1, len(pts)):
            if tuple(pts[i]) == first:
                rings.append(pts[start : i + 1])
                start = i + 1
                closed = True
                break
        if not closed:
            # No closing point found — treat the remainder as one ring
            remainder = pts[start:]
            if len(remainder) >= 3:
                rings.append(remainder)
            break

    return rings if rings else [pts]


# ---------------------------------------------------------------------------
# GeoJSON generation
# ---------------------------------------------------------------------------

def build_geojson(db_path: Path, epsilon: float = 0.0,
                  verbose: bool = False) -> dict:
    """
    Read all US counties from gsak_counties.db and return a GeoJSON
    FeatureCollection dict.

    epsilon : RDP simplification tolerance in degrees (0 = no simplification)
              Suggested values: 0.001 (moderate), 0.0005 (light), 0 (none)
    """
    if not db_path.exists():
        sys.exit(f"DB not found: {db_path}\n"
                 "Run: python gsak_counties.py build --gsak-dir <dir> --country US")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # US-only: exclude Canadian province codes
    _CA_CODES = ('AB','BC','MB','NB','NL','NS','NU','ON','PE','QC','SK','YT','NT')
    placeholders = ','.join('?' * len(_CA_CODES))
    rows = conn.execute(
        f"SELECT state_code, county_name, adif_key, polygon "
        f"FROM counties "
        f"WHERE state_code NOT IN ({placeholders}) "
        f"ORDER BY state_code, county_name",
        _CA_CODES,
    ).fetchall()
    conn.close()

    if not rows:
        sys.exit("No US counties found in DB. "
                 "Run: python gsak_counties.py build --gsak-dir <dir> --country US")

    features = []
    skipped  = 0
    orig_pts_total = 0
    simp_pts_total = 0

    for row in rows:
        state    = row['state_code']
        name     = row['county_name']
        adif_key = row['adif_key']
        pts      = json.loads(row['polygon'])  # list of [lat, lon]

        if len(pts) < 3:
            if verbose:
                print(f"  Warning: {adif_key} has < 3 points — skipped.")
            skipped += 1
            continue

        orig_pts_total += len(pts)

        # Split concatenated sub-polygons (e.g. Aleutians West island chain)
        rings_raw = split_sub_polygons(pts)

        # Simplify each ring, normalize IDL longitudes, convert to [lon, lat]
        rings_geo = []
        for ring in rings_raw:
            simp = simplify_polygon(ring, epsilon) if epsilon > 0 else ring
            simp_pts_total += len(simp)
            # Normalize East longitudes > 170° → negative (Western hemisphere)
            coords = [[normalize_lon(pt[1]), pt[0]] for pt in simp]
            # Ensure closed ring
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            if len(coords) >= 4:  # GeoJSON requires at least 4 positions
                rings_geo.append(coords)

        if not rings_geo:
            if verbose:
                print(f"  Warning: {adif_key} produced no valid rings — skipped.")
            skipped += 1
            continue

        namelsad = make_namelsad(name, state)

        # Use MultiPolygon when there are multiple islands/sub-regions
        if len(rings_geo) == 1:
            geometry = {
                'type':        'Polygon',
                'coordinates': [rings_geo[0]],
            }
        else:
            geometry = {
                'type':        'MultiPolygon',
                # Each element of MultiPolygon coordinates is [[outer_ring]]
                'coordinates': [[ring] for ring in rings_geo],
            }

        features.append({
            'type': 'Feature',
            'properties': {
                'adif_key': adif_key,
                'name':     name,
                'namelsad': namelsad,
                'state':    state,
            },
            'geometry': geometry,
        })

    if verbose and epsilon > 0:
        reduction = 100 * (1 - simp_pts_total / max(orig_pts_total, 1))
        print(f"  Simplification (ε={epsilon}): "
              f"{orig_pts_total:,} → {simp_pts_total:,} points "
              f"({reduction:.1f}% reduction)")

    return {
        'type':     'FeatureCollection',
        'features': features,
        'metadata': {
            'source':    'GSAK community polygon data (https://gsak.net)',
            'credit':    'Clyde Findlay (GSAK author) and GSAK community contributors',
            'license':   'Freeware — freely redistributable with attribution',
            'generated': time.strftime('%Y-%m-%d'),
            'counties':  len(features),
            'skipped':   skipped,
            'epsilon':   epsilon,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate us_counties.geojson from gsak_counties.db.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default — light simplification, ~12MB, recommended
  python gsak_build_geojson.py

  # Full fidelity — ~26MB, for comparison or archival
  python gsak_build_geojson.py --full
  python gsak_build_geojson.py --simplify 0

  # Moderate simplification — ~8MB, if 12MB is still too large
  python gsak_build_geojson.py --simplify 0.001

  # Custom paths
  python gsak_build_geojson.py --db path/to/gsak_counties.db --out path/to/us_counties.geojson
""",
    )
    parser.add_argument(
        '--db', default='gsak_counties.db',
        help='Path to gsak_counties.db (default: gsak_counties.db)',
    )
    parser.add_argument(
        '--out', default='us_counties.geojson',
        help='Output GeoJSON path (default: us_counties.geojson)',
    )
    parser.add_argument(
        '--simplify', type=float, default=0.0005, metavar='EPSILON',
        help='RDP simplification tolerance in degrees '
             '(default: 0.0005 — light simplification, ~12MB output). '
             'Use 0 for full fidelity (~26MB). 0.001 for moderate (~8MB).',
    )
    parser.add_argument(
        '--full', action='store_true',
        help='Shorthand for --simplify 0 (full fidelity, ~26MB output)',
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Show per-state progress and simplification stats',
    )
    args = parser.parse_args()

    db_path  = Path(args.db).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()

    print(f"Reading from: {db_path}")
    if args.full:
        args.simplify = 0.0

    if args.simplify > 0:
        print(f"Simplification: ε={args.simplify} (RDP)")
    else:
        print("Simplification: none (full fidelity)")

    t0 = time.time()
    geojson = build_geojson(db_path, epsilon=args.simplify, verbose=args.verbose)
    t1 = time.time()

    n = len(geojson['features'])
    print(f"  Built {n:,} county features in {t1-t0:.1f}s")

    # Spot-check a few namelsad values
    if args.verbose:
        spot = {f['properties']['adif_key']: f['properties']['namelsad']
                for f in geojson['features']
                if f['properties']['adif_key'] in
                   ('WA,King','LA,Acadia','AK,Anchorage','VA,Alexandria',
                    'AK,Kusilvak','AK,Juneau','VA,Charles City','DC,District of Columbia')}
        if spot:
            print("  namelsad spot-check:")
            for k, v in sorted(spot.items()):
                print(f"    {k:30s} → {v!r}")

    print(f"Writing: {out_path}")
    t2 = time.time()
    out_path.write_text(
        json.dumps(geojson, separators=(',', ':')),
        encoding='utf-8',
    )
    t3 = time.time()

    size_mb = out_path.stat().st_size / 1_048_576
    print(f"  Written in {t3-t2:.1f}s  —  {size_mb:.1f} MB")
    print("Done.")


if __name__ == '__main__':
    main()
