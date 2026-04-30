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

Usage — build the database (run once per country, all share the same DB):
    python gsak_counties.py build --gsak-dir "C:/GSAK/Data/Counties" --db gsak_counties.db --country usa
    python gsak_counties.py build --gsak-dir "C:/GSAK/Data/Counties" --db gsak_counties.db --country ca
    python gsak_counties.py build --gsak-dir "C:/GSAK/Data/Counties" --db gsak_counties.db --country cz
    python gsak_counties.py build --gsak-dir "C:/GSAK/Data/Counties" --db gsak_counties.db --country is

Two directory layouts are supported and auto-detected:
    Hierarchical (US, CA): gsak_dir/US/WA/King.txt  — state_code = postal code
    Flat (all others):     gsak_dir/CZ/Decin.txt    — state_code = country code

Usage — lookup from another script:
    from gsak_counties import lookup_county
    state, county = lookup_county(47.56, -122.03, db_path="gsak_counties.db")
    # US:  ('WA', 'King')
    # CZ:  ('CZ', 'Hlavni mesto Praha')
    # IS:  ('IS', 'Hofudborgarsvaedi')
    # Returns (None, None) if not found or DB absent

CLI lookup (for testing):
    python gsak_counties.py lookup 47.56 -122.03 --db gsak_counties.db
    python gsak_counties.py stats  --db gsak_counties.db

Dependencies: stdlib only (sqlite3, json, pathlib, argparse)
"""

__version__ = "1.6.0"  # country_polygons table, build-countries/list-countries subcommands, lookup_country()

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from location_mapping import GSAK_NAME_TO_ISO, ISO_TO_GSAK_NAME, _POSTAL_STATE, _STATE_POSTAL, _dir_to_postal, CA_CODES, _VA_CITY_STEMS

# ---------------------------------------------------------------------------
# Polygon parsing
# ---------------------------------------------------------------------------


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
    if state_code in CA_CODES:
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

def _read_polygon_text(path: Path) -> str:
    """
    Read a GSAK polygon file, trying UTF-8 first then cp1252.
    cp1252 (Windows Western European) is common for files exported from
    GSAK on Windows, particularly those with accented region names in
    the #gsakname= header line.
    """
    try:
        return path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return path.read_text(encoding='cp1252', errors='replace')


def _parse_polygon(path: Path) -> tuple[list[tuple[float, float]], str | None]:
    """
    Parse a GSAK polygon .txt file into (points, gsakname).

    points   : list of (lat, lon) tuples forming the polygon
    gsakname : value of the '#gsakname=' header line if present, else None

    Handles:
      - Blank lines (skipped)
      - Comment / header lines starting with '#' (parsed for #gsakname=)
      - Whitespace-separated coords: "51.2707  -106.8707"  (US, most CA)
      - Comma-separated coords:      "51.2707,-106.8707"   (Saskatchewan, IS)
      - UTF-8 and cp1252 encodings
    """
    pts: list[tuple[float, float]] = []
    gsakname: str | None = None

    for line in _read_polygon_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        # Header / comment lines
        if line.startswith('#'):
            if line.lower().startswith('#gsakname='):
                gsakname = line[len('#gsakname='):].strip()
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

    return pts, gsakname


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

CREATE TABLE IF NOT EXISTS country_polygons (
    id           INTEGER PRIMARY KEY,
    country_name TEXT NOT NULL,   -- from #GsakName=, e.g. 'Iceland'
    iso_code     TEXT NOT NULL,   -- 2-letter ISO, e.g. 'IS' ('' if unknown)
    part_num     INTEGER NOT NULL DEFAULT 1,
    min_lat      REAL NOT NULL,
    max_lat      REAL NOT NULL,
    min_lon      REAL NOT NULL,
    max_lon      REAL NOT NULL,
    polygon      TEXT NOT NULL    -- JSON [[lat,lon], ...]
);
CREATE INDEX IF NOT EXISTS idx_country_name
    ON country_polygons (country_name);
CREATE INDEX IF NOT EXISTS idx_country_iso
    ON country_polygons (iso_code);
"""


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)   # idempotent — CREATE IF NOT EXISTS
    return conn


# ---------------------------------------------------------------------------
# Build database
# ---------------------------------------------------------------------------

# Country code -> display name for flat (non US/CA) countries.
# Extend as you add more countries to the DB.
_COUNTRY_NAMES: dict[str, str] = {
    'AR': 'Argentina',
    'AU': 'Australia',
    'CZ': 'Czechia',
    'IS': 'Iceland',
    'IT': 'Italy',
    'NO': 'Norway',
    'NZ': 'New Zealand',
    'UK': 'United Kingdom',
}

# Two-letter codes that use the two-level US/CA hierarchy.
# Everything else is treated as flat (regions directly in country dir).
_HIERARCHICAL_COUNTRIES = {'us', 'usa', 'ca', 'canada'}


def _country_code_from_dir(country: str) -> str:
    """
    Normalise a country argument to an uppercase 2-letter code.
    Accepts 'usa'->'US', 'ca'->'CA', 'cz'->'CZ', etc.
    'usa' and 'canada' are special-cased to their ISO codes.
    """
    low = country.lower()
    if low in ('usa', 'us'):
        return 'US'
    if low in ('ca', 'canada'):
        return 'CA'
    return country.upper()[:2]


def build_db(gsak_dir: Path, db_path: Path,
             country: str = 'usa', verbose: bool = False) -> int:
    """
    Build (or update) the SQLite region DB from GSAK polygon files.

    Two directory layouts are supported, detected automatically:

    Hierarchical (US and CA):
        gsak_dir/US/WA/King.txt
        gsak_dir/CA/Ontario/Toronto.txt
        state_code = province/state postal code ('WA', 'ON', ...)

    Flat (all other countries — CZ, IS, AU, UK, NO, ...):
        gsak_dir/CZ/Decin.txt
        gsak_dir/IS/Hofudborgarsvaedi.txt
        state_code = 2-letter country code ('CZ', 'IS', ...)
        county_name = region name derived from filename stem
                      (or #gsakname= header for single-char/digit stems)

    Run once per country; multiple countries can share the same DB.
    Returns the number of regions inserted.
    """
    gsak_dir = Path(gsak_dir)
    db_path  = Path(db_path)
    country_dir = gsak_dir / country

    if not country_dir.exists():
        # Case-insensitive search
        for d in gsak_dir.iterdir():
            if d.name.lower() == country.lower():
                country_dir = d
                break
        else:
            raise FileNotFoundError(
                f"Country directory '{country}' not found under {gsak_dir}")

    conn = _open_db(db_path)

    is_hierarchical = country.lower() in _HIERARCHICAL_COUNTRIES
    is_canada = country.lower() in ('ca', 'canada')
    country_code = _country_code_from_dir(country)

    # -----------------------------------------------------------------------
    # Hierarchical layout (US / CA): iterate state subdirectories
    # -----------------------------------------------------------------------
    if is_hierarchical:
        _US_CODES = set(_STATE_POSTAL.values()) - CA_CODES
        country_codes = CA_CODES if is_canada else _US_CODES
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

            txt_files = sorted(state_dir.glob('*.txt'))
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
                if is_canada:
                    county_name = stem_to_county_name_ca(txt.stem)
                else:
                    county_name = stem_to_county_name(txt.stem, state_code)

                pts, _ = _parse_polygon(txt)
                if len(pts) < 3:
                    if verbose:
                        print(f"    Warning: {txt.name} has < 3 points — skipped.")
                    continue
                min_lat, max_lat, min_lon, max_lon = _bbox(pts)
                adif_key = f"{state_code},{county_name}"
                rows.append((
                    state_code, state_name, county_name, adif_key,
                    min_lat, max_lat, min_lon, max_lon, json.dumps(pts),
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

    # -----------------------------------------------------------------------
    # Flat layout (CZ, IS, AU, UK, NO, ...): .txt files directly in country dir
    # -----------------------------------------------------------------------
    country_name = _COUNTRY_NAMES.get(country_code, country_code)

    # Delete existing rows for this country code before rebuilding
    conn.execute("DELETE FROM counties WHERE state_code = ?", (country_code,))
    conn.commit()

    txt_files = sorted(
        t for t in country_dir.glob('*.txt')
        if t.stem.lower() not in ('version',) and not t.stem.lower().startswith('version')
    )

    if not txt_files:
        conn.close()
        print(f"  No polygon files found in {country_dir}")
        return 0

    if verbose:
        print(f"  {country_code} ({country_name}): {len(txt_files)} region files")

    rows = []
    for txt in txt_files:
        pts, gsakname = _parse_polygon(txt)

        # Derive region name: prefer #gsakname= header for trivially short
        # stems (single char or pure digits — e.g. 'h' for Hofudborgarsvaedi).
        # Otherwise use the stem with underscores→spaces.
        stem = txt.stem
        if gsakname and (len(stem) <= 1 or stem.isdigit()):
            county_name = gsakname
        else:
            county_name = stem.replace('_', ' ').strip()

        if len(pts) < 3:
            if verbose:
                print(f"  Warning: {txt.name} ({county_name}) has < 3 points — skipped.")
            continue

        min_lat, max_lat, min_lon, max_lon = _bbox(pts)
        adif_key = f"{country_code},{county_name}"
        rows.append((
            country_code, country_name, county_name, adif_key,
            min_lat, max_lat, min_lon, max_lon, json.dumps(pts),
        ))
        if verbose:
            print(f"    {county_name}: {len(pts)} points")

    conn.executemany(
        "INSERT INTO counties "
        "(state_code, state_name, county_name, adif_key, "
        " min_lat, max_lat, min_lon, max_lon, polygon) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    print(f"  DB built: {len(rows)} regions for {country_code} ({country_name})")
    return len(rows)


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
# Country polygon database — build and lookup
# ---------------------------------------------------------------------------

import re as _re


def _gsak_name_from_stem(stem: str) -> str:
    """
    Derive a country name from a GSAK Countries filename stem when the file
    has no #GsakName= header.  Strips trailing run of digits (and optional
    separator) from the stem.

    Examples:
        'Belgium23'              → 'Belgium'
        'Iceland2'               → 'Iceland'
        'Canada_2'               → 'Canada'
        'Bosnia and Herzegovina1'→ 'Bosnia and Herzegovina'
        'Czechia'                → 'Czechia'
        'United States4'         → 'United States'
    """
    return _re.sub(r'[_\s]*\d+$', '', stem).strip()


def build_countries_db(gsak_dir: Path, db_path: Path,
                       verbose: bool = False) -> tuple[int, int]:
    """
    Walk gsak_dir/Countries/*.txt and populate the country_polygons table.

    Returns (num_countries, num_parts).
    Full rebuild: all existing rows are deleted before insert.
    """
    gsak_dir = Path(gsak_dir)
    db_path  = Path(db_path)

    # Find the Countries subdirectory case-insensitively
    countries_dir: Path | None = None
    for d in gsak_dir.iterdir():
        if d.is_dir() and d.name.lower() == 'countries':
            countries_dir = d
            break
    if countries_dir is None:
        raise FileNotFoundError(
            f"No 'Countries' subdirectory found under {gsak_dir}")

    # Collect all .txt files (case-insensitive), skip version.ver etc.
    txt_files = sorted(
        t for t in countries_dir.iterdir()
        if t.suffix.lower() == '.txt'
        and t.stem.lower() not in ('version',)
        and not t.stem.lower().startswith('version')
    )
    if not txt_files:
        print(f"  No polygon files found in {countries_dir}")
        return 0, 0

    conn = _open_db(db_path)

    # First pass: determine which country_names are present so we can
    # delete-all-then-reinsert (full rebuild per the spec).
    # We need to peek at gsakname before we can group — do a quick scan.
    name_set: set[str] = set()
    for txt in txt_files:
        _, gsakname = _parse_polygon(txt)
        cname = gsakname.strip() if gsakname else _gsak_name_from_stem(txt.stem)
        name_set.add(cname)

    placeholders = ','.join('?' * len(name_set))
    conn.execute(
        f"DELETE FROM country_polygons WHERE country_name IN ({placeholders})",
        list(name_set),
    )
    conn.commit()

    # Second pass: parse and insert, tracking part_num per country_name
    part_counts: dict[str, int] = {}   # country_name → parts inserted so far
    rows = []

    for txt in txt_files:
        pts, gsakname = _parse_polygon(txt)
        cname = gsakname.strip() if gsakname else _gsak_name_from_stem(txt.stem)
        iso   = GSAK_NAME_TO_ISO.get(cname, '')

        if len(pts) < 3:
            if verbose:
                print(f"  Warning: {txt.name} ({cname}) has < 3 points — skipped.")
            continue

        part_num = part_counts.get(cname, 0) + 1
        part_counts[cname] = part_num

        min_lat, max_lat, min_lon, max_lon = _bbox(pts)
        rows.append((
            cname, iso, part_num,
            min_lat, max_lat, min_lon, max_lon,
            json.dumps(pts),
        ))
        if verbose:
            print(f"    {cname} part {part_num}: {len(pts)} points  iso={iso or '?'}")

    conn.executemany(
        "INSERT INTO country_polygons "
        "(country_name, iso_code, part_num, "
        " min_lat, max_lat, min_lon, max_lon, polygon) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    num_countries = len(part_counts)
    num_parts     = len(rows)
    print(f"  Countries DB built: {num_countries} countries, "
          f"{num_parts} polygon parts total.")
    return num_countries, num_parts


def lookup_country(lat: float, lon: float,
                   db_path: str | Path = 'gsak_counties.db') -> str | None:
    """
    Return the country_name for the given coordinates, or None.
    Uses bbox pre-filter then point-in-polygon on stored parts.
    """
    db_path = str(Path(db_path).expanduser().resolve())
    conn = _get_conn(db_path)
    if conn is None:
        return None

    # Ensure country_polygons table exists (DB may have been opened read-only
    # before the table was created; if so, we won't find rows but won't crash).
    try:
        rows = conn.execute(
            "SELECT country_name, polygon "
            "FROM country_polygons "
            "WHERE min_lat <= ? AND max_lat >= ? "
            "  AND min_lon <= ? AND max_lon >= ?",
            [lat, lat, lon, lon],
        ).fetchall()
    except sqlite3.OperationalError:
        return None   # table doesn't exist yet

    for row in rows:
        pts = json.loads(row['polygon'])
        if _point_in_polygon(lat, lon, pts):
            return row['country_name']

    return None


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
        "SELECT state_code, state_name, COUNT(*) as n FROM counties "
        "GROUP BY state_code ORDER BY state_code"
    ).fetchall()
    total = sum(r[2] for r in rows)
    print(f"{'Code':<6} {'Regions':>7}  Name")
    print('-' * 36)
    for r in rows:
        code, name, n = r[0], r[1], r[2]
        print(f"  {code:<4}   {n:>6}  {name}")
    print('-' * 36)
    print(f"  {'TOTAL':<4}   {total:>6}")
    conn.close()


def _cmd_build_countries(args):
    gsak_dir = Path(args.gsak_dir)
    db_path  = Path(args.db)
    print(f"Building country borders DB from {gsak_dir}/Countries ...")
    n_countries, n_parts = build_countries_db(gsak_dir, db_path,
                                              verbose=args.verbose)
    print(f"Done. {n_countries} countries, {n_parts} parts → {db_path}")


def _cmd_list_countries(args):
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if args.country:
        rows = conn.execute(
            "SELECT part_num, iso_code, country_name, "
            "       min_lat, max_lat, min_lon, max_lon "
            "FROM country_polygons "
            "WHERE country_name = ? "
            "ORDER BY part_num",
            (args.country,),
        ).fetchall()
        if not rows:
            print(f"No parts found for country '{args.country}'.")
        else:
            print(f"{args.country}  (ISO: {rows[0]['iso_code'] or '?'})")
            for r in rows:
                print(f"  Part {r['part_num']:>3}: "
                      f"lat [{r['min_lat']:.4f}, {r['max_lat']:.4f}]  "
                      f"lon [{r['min_lon']:.4f}, {r['max_lon']:.4f}]")
    else:
        rows = conn.execute(
            "SELECT country_name, iso_code, COUNT(*) as parts "
            "FROM country_polygons "
            "GROUP BY country_name "
            "ORDER BY country_name"
        ).fetchall()
        if not rows:
            print("country_polygons table is empty — run build-countries first.")
        else:
            print(f"{'Country':<40} {'ISO':<5} {'Parts':>5}")
            print('-' * 52)
            for r in rows:
                print(f"  {r['country_name']:<38} {r['iso_code'] or '?':<5} "
                      f"{r['parts']:>5}")
            print(f"\n  {len(rows)} countries total.")
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
                         help='Country subdirectory to load, e.g.: usa, ca, cz, is, au, uk, no. '
                              '(default: usa). Run once per country to load multiple into the same DB.')
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

    # build-countries
    p_bc = sub.add_parser('build-countries',
                          help='Build country_polygons table from gsak/Countries/')
    p_bc.add_argument('--gsak-dir', required=True,
                      help='Root GSAK directory (contains Countries/ subdir)')
    p_bc.add_argument('--db', default='gsak_counties.db',
                      help='DB path (default: gsak_counties.db)')
    p_bc.add_argument('--verbose', action='store_true')
    p_bc.set_defaults(func=_cmd_build_countries)

    # list-countries
    p_lc = sub.add_parser('list-countries',
                          help='List countries in country_polygons table')
    p_lc.add_argument('--db', default='gsak_counties.db')
    p_lc.add_argument('--country', default=None,
                      help='Show parts for a specific country name')
    p_lc.set_defaults(func=_cmd_list_countries)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
