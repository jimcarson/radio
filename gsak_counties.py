#!/usr/bin/env python3
"""
gsak_counties.py — GSAK county polygon database
=================================================
Builds a SQLite database from GSAK's county polygon files and provides
fast point-in-polygon lookup for geocache coordinate → county assignment.

County polygon data credit:
    The polygon boundary files used by this module were distributed with
    GSAK (Geocaching Swiss Army Knife), written by Clyde Findlay, and
    released as freeware after he was no longer able to maintain it.
    The polygon data itself is a community effort — contributed and
    refined by GSAK users over many years.

    GSAK website : https://gsak.net
    License      : Freeware

    We are grateful to Clyde and the GSAK community for making this
    data freely available.

    Polygon files are located in the GSAK installation directory under
    Data/Counties/{country}/{State}/{County}.txt
    Each file contains one lat/lon pair per line (whitespace- or
    comma-separated), forming a closed polygon.

Usage — build the database once:
    python gsak_counties.py build --gsak-dir "C:/GSAK/Data/Counties" --db gsak_counties.db

Usage — lookup from another script:
    from gsak_counties import lookup_county
    state, county = lookup_county(47.56, -122.03, db_path="gsak_counties.db")
    # Returns e.g. ('WA', 'King') or (None, None) if not found / DB absent

CLI lookup (for testing):
    python gsak_counties.py lookup 47.56 -122.03 --db gsak_counties.db

Dependencies: stdlib only (sqlite3, json, pathlib, argparse)
"""

__version__ = "1.5.0"  # fix CA county name underscore→space conversion

import argparse
import json
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# State name → 2-letter postal code
# ---------------------------------------------------------------------------

_STATE_POSTAL: dict[str, str] = {
    # US states — standard spaced names
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
    'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT',
    'Delaware': 'DE', 'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI',
    'Idaho': 'ID', 'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA',
    'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME',
    'Maryland': 'MD', 'Massachusetts': 'MA', 'Michigan': 'MI',
    'Minnesota': 'MN', 'Mississippi': 'MS', 'Missouri': 'MO',
    'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
    'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM',
    'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND',
    'Ohio': 'OH', 'Oklahoma': 'OK', 'Oregon': 'OR', 'Pennsylvania': 'PA',
    'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD',
    'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT',
    'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV',
    'Wisconsin': 'WI', 'Wyoming': 'WY',
    'District of Columbia': 'DC',
    # US — GSAK run-together directory names (no spaces or separators)
    'NewHampshire': 'NH', 'NewJersey': 'NJ', 'NewMexico': 'NM',
    'NewYork': 'NY', 'NorthCarolina': 'NC', 'NorthDakota': 'ND',
    'RhodeIsland': 'RI', 'SouthCarolina': 'SC', 'SouthDakota': 'SD',
    'WestVirginia': 'WV', 'DistrictofColumbia': 'DC',
    # Canadian provinces and territories — directory names use spaces
    'Alberta': 'AB', 'British Columbia': 'BC', 'Manitoba': 'MB',
    'New Brunswick': 'NB', 'Newfoundland and Labrador': 'NL',
    'Nova Scotia': 'NS', 'Nunavut': 'NU', 'Ontario': 'ON',
    'Prince Edward Island': 'PE', 'Saskatchewan': 'SK',
    'Yukon': 'YT', 'Northwest Territories': 'NT',
    # Quebec — accented (canonical), unaccented (filesystem variant)
    'Québec': 'QC', 'Quebec': 'QC',
    # Canadian province dirs after gsak_rename.py (spaces → underscores)
    'British_Columbia': 'BC', 'New_Brunswick': 'NB',
    'Newfoundland_and_Labrador': 'NL', 'Nova_Scotia': 'NS',
    'Prince_Edward_Island': 'PE', 'Northwest_Territories': 'NT',
}

def _dir_to_postal(dir_name: str) -> str | None:
    """
    Resolve a GSAK state/province directory name to a 2-letter postal code.
    Handles accented characters by also trying a Unicode-normalised fallback.
    """
    code = _STATE_POSTAL.get(dir_name)
    if code:
        return code
    # Strip accents and retry (catches Québec → Quebec on some filesystems)
    import unicodedata
    stripped = ''.join(
        c for c in unicodedata.normalize('NFD', dir_name)
        if unicodedata.category(c) != 'Mn'
    )
    return _STATE_POSTAL.get(stripped)

# Reverse map: postal → full name (for display)
_POSTAL_STATE: dict[str, str] = {v: k for k, v in _STATE_POSTAL.items()}


# ---------------------------------------------------------------------------
# Polygon parsing
# ---------------------------------------------------------------------------


# Virginia independent cities that end in "City" in their GSAK stem.
# These get the " City" suffix stripped for the ADIF key (e.g. "VA,Alexandria").
# Genuine counties whose name contains "City" (Charles City, James City) are
# NOT in this set — their stems don't have a standalone "_City" at the end
# that would be ambiguous, but we whitelist to be safe.
_VA_CITY_STEMS: frozenset = frozenset({
    'Alexandria_City', 'Bristol_City', 'Buena_Vista_City',
    'Charlottesville_City', 'Chesapeake_City', 'Colonial_Heights_City',
    'Covington_City', 'Danville_City', 'Emporia_City', 'Fairfax_City',
    'Falls_Church_City', 'Franklin_City', 'Fredericksburg_City',
    'Galax_City', 'Hampton_City', 'Harrisonburg_City', 'Hopewell_City',
    'Lexington_City', 'Lynchburg_City', 'Manassas_City',
    'Manassas_Park_City', 'Martinsville_City', 'Newport_News_City',
    'Norfolk_City', 'Norton_City', 'Petersburg_City', 'Poquoson_City',
    'Portsmouth_City', 'Radford_City', 'Richmond_City', 'Roanoke_City',
    'Salem_City', 'Staunton_City', 'Suffolk_City', 'Virginia_Beach_City',
    'Waynesboro_City', 'Williamsburg_City', 'Winchester_City',
    # Charles_City and James_City are genuine counties — NOT in this set
})


def stem_to_county_name(stem: str, state_code: str) -> str:
    """
    Convert a GSAK filename stem (without .txt) to a canonical county name
    matching the adif_key format used in us_counties.geojson and ADIF CNTY.

    Transformations applied:
      1. Underscores → spaces        San_Augustine     → San Augustine
      2. Double-spaces collapsed      St._Clair         → St. Clair  (dot keeps space)
      3. Louisiana _Parish stripped   Acadia_Parish     → Acadia
         (ADIF CNTY field omits Parish; Louisiana uses parish not county anyway)
      4. Virginia independent cities  Alexandria_City   → Alexandria
         (whitelist-based — Charles_City and James_City are real counties, kept as-is)
      5. Hyphens preserved            Miami-Dade        → Miami-Dade
      6. Result matches title-cased adif_key convention

    For Canadian provinces (AB, BC, MB, NB, NL, NS, NU, ON, PE, QC, SK,
    YT, NT) no suffix stripping is applied — the stem is simply
    de-underscored to produce the display name.
    """
    import re as _re

    # Canadian provinces: simple underscore → space, no suffix stripping
    _CA = {'AB','BC','MB','NB','NL','NS','NU','ON','PE','QC','SK','YT','NT'}
    if state_code in _CA:
        return stem.replace('_', ' ')

    # 1. Underscores → spaces (hyphens left alone)
    name = stem.replace('_', ' ')

    # 2. Collapse any accidental double-spaces (e.g. "St.  Clair" can't happen
    #    since "St._Clair" → "St. Clair" cleanly, but be defensive)
    name = _re.sub(r' {2,}', ' ', name).strip()

    # 3. Louisiana: strip trailing " Parish"
    if state_code == 'LA':
        name = _re.sub(r' Parish$', '', name, flags=_re.IGNORECASE).strip()

    # 4. Virginia: strip trailing " City" only for known independent cities
    if state_code == 'VA' and stem in _VA_CITY_STEMS:
        name = _re.sub(r' City$', '', name, flags=_re.IGNORECASE).strip()

    return name


def stem_to_adif_key(stem: str, state_code: str) -> str:
    """Return the ADIF-style key 'ST,County' for a GSAK filename stem."""
    return f"{state_code},{stem_to_county_name(stem, state_code)}"


def stem_to_county_name_ca(stem: str) -> str:
    """
    Convert a Canadian GSAK filename stem (post-gsak_rename.py) to a display name.
    After renaming, spaces have been converted to underscores, so we reverse that.
    Hyphens are preserved as-is (they are part of the official region name).

    Examples (post-rename stems):
        'Banff_National_Park'  → 'Banff National Park'
        'Lac_Ste._Anne'        → 'Lac Ste. Anne'
        'Special_Area_No._2'   → 'Special Area No. 2'
        'St._Georges'          → 'St. Georges'
        'Alberni-Clayoquot'    → 'Alberni-Clayoquot'
        'City_of_Winnipeg'     → 'City of Winnipeg'
        'Saguenay-Lac_St_Jean' → 'Saguenay-Lac St Jean'
    """
    return stem.replace('_', ' ').strip()

def _parse_polygon(path: Path) -> list[tuple[float, float]]:
    """
    Parse a GSAK county .txt file into a list of (lat, lon) tuples.
    Handles two separator styles found in the wild:
      - Whitespace-separated: "51.2707  -106.8707"  (US and most CA files)
      - Comma-separated:      "51.2707,-106.8707"   (Saskatchewan files)
    """
    pts = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        # Try comma separator first, then whitespace
        parts = line.split(',') if ',' in line else line.split()
        if len(parts) < 2:
            continue
        try:
            lat, lon = float(parts[0].strip()), float(parts[1].strip())
            pts.append((lat, lon))
        except ValueError:
            continue
    return pts


def _bbox(pts: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lon, max_lon) bounding box."""
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return min(lats), max(lats), min(lons), max(lons)


# ---------------------------------------------------------------------------
# Point-in-polygon (ray casting, pure Python)
# ---------------------------------------------------------------------------

def _point_in_polygon(lat: float, lon: float,
                      pts: list[tuple[float, float]]) -> bool:
    """
    Ray-casting point-in-polygon test.
    pts: list of (lat, lon) tuples forming a closed polygon.
    Returns True if (lat, lon) is inside the polygon.
    """
    n = len(pts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i][1], pts[i][0]   # lon, lat
        xj, yj = pts[j][1], pts[j][0]
        x, y   = lon, lat
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS counties (
    id          INTEGER PRIMARY KEY,
    state_code  TEXT NOT NULL,          -- e.g. 'WA'
    state_name  TEXT NOT NULL,          -- e.g. 'Washington'
    county_name TEXT NOT NULL,          -- e.g. 'King'
    adif_key    TEXT NOT NULL,          -- e.g. 'WA,King'
    min_lat     REAL NOT NULL,
    max_lat     REAL NOT NULL,
    min_lon     REAL NOT NULL,
    max_lon     REAL NOT NULL,
    polygon     TEXT NOT NULL           -- JSON array of [lat, lon] pairs
);
CREATE INDEX IF NOT EXISTS idx_bbox
    ON counties (min_lat, max_lat, min_lon, max_lon);
CREATE INDEX IF NOT EXISTS idx_state
    ON counties (state_code);
"""


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------------------
# Build database
# ---------------------------------------------------------------------------

def build_db(gsak_dir: Path, db_path: Path,
             country: str = 'usa', verbose: bool = False) -> int:
    """
    Walk gsak_dir/{country}/{State}/{County}.txt and build SQLite DB.

    Returns the number of counties inserted.
    """
    gsak_dir = Path(gsak_dir)
    db_path  = Path(db_path)
    country_dir = gsak_dir / country

    if not country_dir.exists():
        # Try case-insensitive search
        for d in gsak_dir.iterdir():
            if d.name.lower() == country.lower():
                country_dir = d
                break
        else:
            raise FileNotFoundError(
                f"Country directory '{country}' not found under {gsak_dir}")

    conn = _open_db(db_path)
    conn.executescript(_SCHEMA)

    # Determine which state codes belong to this country so we can
    # clear only those rows when rebuilding (avoids wiping US when rebuilding CA)
    is_canada = country.lower() in ('ca', 'canada')
    _CA_CODES = {'AB','BC','MB','NB','NL','NS','NU','ON','PE','QC','SK','YT','NT'}
    _US_CODES = set(_STATE_POSTAL.values()) - _CA_CODES
    country_codes = _CA_CODES if is_canada else _US_CODES
    placeholders = ','.join('?' * len(country_codes))
    conn.execute(
        f"DELETE FROM counties WHERE state_code IN ({placeholders})",
        list(country_codes)
    )
    conn.commit()
    inserted = 0
    skipped  = 0
    state_dirs = sorted(d for d in country_dir.iterdir() if d.is_dir())

    for state_dir in state_dirs:
        state_name = state_dir.name

        # Support two directory structures:
        #   Full name:   gsak/US/Washington/King.txt   (state_name = 'Washington')
        #   Postal code: gsak/US/WA/King.txt           (state_name = 'WA')
        # If the directory name is already a 2-letter postal code, use it directly.
        if len(state_name) == 2 and state_name.upper() in _POSTAL_STATE:
            state_code = state_name.upper()
        elif len(state_name) == 2 and state_name.upper() in set(_STATE_POSTAL.values()):
            state_code = state_name.upper()
        else:
            state_code = _dir_to_postal(state_name)

        if state_code is None:
            if verbose:
                print(f"  Warning: no postal code for '{state_name}' — skipped.")
            skipped += 1
            continue

        # Canadian files use spaces in names; US files use underscores
        txt_files = sorted(state_dir.glob('*.txt'))
        # Skip version.ver (GSAK metadata)
        txt_files = [t for t in txt_files
                     if t.stem.lower() != 'version'
                     and not t.stem.lower().startswith('version')]

        if not txt_files:
            if verbose:
                print(f"  {state_code} ({state_name}): no polygon files — skipped.")
            continue

        if verbose:
            print(f"  {state_code} ({state_name}): {len(txt_files)} regions")

        rows = []
        for txt in txt_files:
            # Name derivation differs by country
            if is_canada:
                county_name = stem_to_county_name_ca(txt.stem)
            else:
                county_name = stem_to_county_name(txt.stem, state_code)

            pts = _parse_polygon(txt)
            if len(pts) < 3:
                if verbose:
                    print(f"    Warning: {txt.name} has < 3 points — skipped.")
                continue
            min_lat, max_lat, min_lon, max_lon = _bbox(pts)
            adif_key = f"{state_code},{county_name}"
            polygon_json = json.dumps(pts)
            rows.append((
                state_code, state_name, county_name, adif_key,
                min_lat, max_lat, min_lon, max_lon, polygon_json,
            ))

        conn.executemany(
            "INSERT INTO counties "
            "(state_code, state_name, county_name, adif_key, "
            " min_lat, max_lat, min_lon, max_lon, polygon) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        inserted += len(rows)

    conn.close()
    print(f"  DB built: {inserted} regions from {len(state_dirs)} "
          f"{'province' if is_canada else 'state'} directories.")
    if skipped:
        print(f"  {skipped} director(y/ies) skipped — no postal code mapping.")
    return inserted


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

# Module-level connection cache — one per db_path
_conn_cache: dict[str, sqlite3.Connection] = {}


def _get_conn(db_path: str) -> sqlite3.Connection | None:
    if db_path not in _conn_cache:
        p = Path(db_path)
        if not p.exists():
            return None
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        _conn_cache[db_path] = conn
    return _conn_cache[db_path]


def lookup_county(lat: float, lon: float,
                  db_path: str | Path = 'gsak_counties.db',
                  state_hint: str = None) -> tuple[str | None, str | None]:
    """
    Find the US county containing (lat, lon) using the GSAK polygon DB.

    db_path    : path to gsak_counties.db (default: beside this script)
    state_hint : 2-letter state code — narrows candidates if known

    Returns (state_code, county_name) e.g. ('WA', 'King'),
            or (None, None) if not found or DB not present.
    """
    db_path = str(Path(db_path).expanduser().resolve())
    conn = _get_conn(db_path)
    if conn is None:
        return None, None

    # Bbox pre-filter
    query = (
        "SELECT state_code, county_name, adif_key, polygon "
        "FROM counties "
        "WHERE min_lat <= ? AND max_lat >= ? "
        "  AND min_lon <= ? AND max_lon >= ?"
    )
    params = [lat, lat, lon, lon]
    if state_hint:
        query += " AND state_code = ?"
        params.append(state_hint.upper())

    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return None, None

    for row in rows:
        pts = json.loads(row['polygon'])
        if _point_in_polygon(lat, lon, pts):
            return row['state_code'], row['county_name']

    return None, None


def lookup_county_adif_key(lat: float, lon: float,
                            db_path: str | Path = 'gsak_counties.db',
                            state_hint: str = None) -> str | None:
    """
    Convenience wrapper — returns 'ST,County' string or None.
    """
    state, county = lookup_county(lat, lon, db_path=db_path,
                                  state_hint=state_hint)
    if state and county:
        return f"{state},{county}"
    return None


def batch_lookup(coords: list[tuple[float, float]],
                 db_path: str | Path = 'gsak_counties.db') -> list[tuple]:
    """
    Look up multiple (lat, lon) pairs. Returns list of (state, county) tuples.
    More efficient than calling lookup_county() in a loop since the DB
    connection is reused.
    """
    return [lookup_county(lat, lon, db_path=db_path) for lat, lon in coords]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_build(args):
    gsak_dir = Path(args.gsak_dir)
    db_path  = Path(args.db)
    print(f"Building county DB from {gsak_dir} ...")
    n = build_db(gsak_dir, db_path, country=args.country, verbose=args.verbose)
    print(f"Done. {n} counties written to {db_path}")


def _cmd_lookup(args):
    lat, lon = float(args.lat), float(args.lon)
    db_path = args.db
    state, county = lookup_county(lat, lon, db_path=db_path)
    if state:
        print(f"  ({lat}, {lon}) → {state},{county}  (adif_key: {state},{county})")
    else:
        print(f"  ({lat}, {lon}) → not found (DB: {db_path})")


def _cmd_stats(args):
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT state_code, COUNT(*) as n FROM counties "
        "GROUP BY state_code ORDER BY state_code"
    ).fetchall()
    total = sum(r[1] for r in rows)
    print(f"{'State':6} {'Counties':>8}")
    print('-' * 16)
    for r in rows:
        print(f"  {r[0]:4}   {r[1]:>6}")
    print('-' * 16)
    print(f"  {'TOTAL':4}   {total:>6}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="GSAK county polygon database builder and lookup tool.\n"
                    "Polygon data © Groundspeak / GSAK (www.gsak.net), "
                    "used under GSAK freeware license.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    # build
    p_build = sub.add_parser('build', help='Build SQLite DB from GSAK polygon files')
    p_build.add_argument('--gsak-dir', required=True,
                         help='Root GSAK Counties directory (contains usa/ subdir)')
    p_build.add_argument('--db', default='gsak_counties.db',
                         help='Output DB path (default: gsak_counties.db)')
    p_build.add_argument('--country', default='usa',
                         help='Country subdirectory to load: usa or ca (default: usa). '
                              'Run twice to load both into the same DB.')
    p_build.add_argument('--verbose', action='store_true')
    p_build.set_defaults(func=_cmd_build)

    # lookup
    p_look = sub.add_parser('lookup', help='Look up county for a lat/lon point')
    p_look.add_argument('lat', help='Latitude (decimal degrees)')
    p_look.add_argument('lon', help='Longitude (decimal degrees)')
    p_look.add_argument('--db', default='gsak_counties.db')
    p_look.set_defaults(func=_cmd_lookup)

    # stats
    p_stat = sub.add_parser('stats', help='Show county counts per state in DB')
    p_stat.add_argument('--db', default='gsak_counties.db')
    p_stat.set_defaults(func=_cmd_stats)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
