#!/usr/bin/env python3
"""
import_gadm.py — Import GADM admin-1 region polygons into gsak_counties.db
============================================================================
Downloads GeoJSON admin-1 boundary data from GADM 4.1 for one or more
countries and imports the polygons into the counties table of an existing
gsak_counties.db, using the same flat-layout schema as Iceland/Norway/Czechia.

Each region is stored as:
    state_code  = 2-letter ADIF/ham radio country code  (e.g. 'JA', 'HL')
    state_name  = country display name                  (e.g. 'Japan')
    county_name = romanized region name from GADM NAME_1
    adif_key    = 'JA,Aomori', 'HL,Gyeonggi-do', etc.

The coordinate-based lookup in adif_map.py (_cnty_key_fn coord fallback) will
then resolve Japanese and South Korean contacts to the correct prefecture/
province automatically.

GADM license: freely available for non-commercial use.
    See https://gadm.org/license.html

Usage:
    python import_gadm.py --db gsak_counties.db JA KR
    python import_gadm.py --db gsak_counties.db JA KR --dry-run
    python import_gadm.py --list

Dependencies: stdlib only (urllib, zipfile, json, sqlite3)
"""

__version__ = "1.0.0"  # Initial release

import argparse
import io
import json
import math
import sqlite3
import sys
import urllib.request
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Country table: ADIF/ham code -> (ISO3 for GADM, display name)
# Extend freely as you add more countries.
# ---------------------------------------------------------------------------
COUNTRY_TABLE: dict[str, tuple[str, str]] = {
    # ADIF  ISO3    Display name
    'JA': ('JPN', 'Japan'),
    'KH': ('KOR', 'South Korea'),   # HL is the ITU prefix; ADIF uses KH for South Korea
    'HL': ('KOR', 'South Korea'),   # accept HL as alias
    'SP': ('ESP', 'Spain'),
    'I':  ('ITA', 'Italy'),
    'IT': ('ITA', 'Italy'),         # alias
    'DL': ('DEU', 'Germany'),
    'F':  ('FRA', 'France'),
    'G':  ('GBR', 'United Kingdom'),
    'VK': ('AUS', 'Australia'),
    'ZL': ('NZL', 'New Zealand'),
    'LU': ('ARG', 'Argentina'),
    'PY': ('BRA', 'Brazil'),        # Brazil — municipality level, probably skip
    'XE': ('MEX', 'Mexico'),
    'BY': ('BLR', 'Belarus'),
    'RA': ('ARG', 'Argentina'),
    'UA': ('UKR', 'Ukraine'),
    'UR': ('RUS', 'Russia'),
    'OZ': ('DNK', 'Denmark'),
    'PA': ('NLD', 'Netherlands'),
    'HB': ('CHE', 'Switzerland'),
    'OE': ('AUT', 'Austria'),
    'OK': ('CZE', 'Czechia'),
    'LA': ('NOR', 'Norway'),
    'SM': ('SWE', 'Sweden'),
    'OH': ('FIN', 'Finland'),
    'TF': ('ISL', 'Iceland'),
    'VE': ('VEN', 'Venezuela'),
    'CE': ('CHL', 'Chile'),
    'OA': ('PER', 'Peru'),
    'HK': ('HKG', 'Hong Kong'),
    'BY': ('CHN', 'China'),
    'BY9': ('CHN', 'China'),
}

GADM_BASE_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json"


# ---------------------------------------------------------------------------
# Geometry helpers (stdlib only — mirrors gsak_counties.py approach)
# ---------------------------------------------------------------------------

def _bbox(pts: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return min(lats), max(lats), min(lons), max(lons)


def _flatten_geometry(geometry: dict) -> list[list[tuple[float, float]]]:
    """
    Extract rings from a GeoJSON geometry as lists of (lat, lon) tuples.

    Handles Polygon and MultiPolygon. Returns one list of points per
    outer ring — inner rings (holes) are discarded since point-in-polygon
    over holes is rare at the prefecture scale and complicates the schema.

    GeoJSON coords are [lon, lat]; we flip to (lat, lon) to match the
    gsak_counties.py convention.
    """
    rings = []
    gtype = geometry.get('type', '')

    if gtype == 'Polygon':
        coords_list = geometry.get('coordinates', [])
        if coords_list:
            # First ring = outer boundary
            rings.append([(lat, lon) for lon, lat in coords_list[0]])

    elif gtype == 'MultiPolygon':
        for poly in geometry.get('coordinates', []):
            if poly:
                rings.append([(lat, lon) for lon, lat in poly[0]])

    return rings


# ---------------------------------------------------------------------------
# GADM download
# ---------------------------------------------------------------------------

def _download_geojson(iso3: str) -> dict:
    """
    Download and return the parsed GeoJSON FeatureCollection for a country
    at admin level 1 from GADM 4.1.  Files are served as .json.zip.
    """
    url = f"{GADM_BASE_URL}/gadm41_{iso3}_1.json.zip"
    print(f"  Downloading {url} ...")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except Exception as exc:
        raise RuntimeError(f"Download failed for {iso3}: {exc}") from exc

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        # The zip contains a single .json file
        json_name = next(n for n in zf.namelist() if n.endswith('.json'))
        geojson = json.loads(zf.read(json_name))
    except Exception as exc:
        raise RuntimeError(f"Could not parse zip/JSON for {iso3}: {exc}") from exc

    return geojson


# ---------------------------------------------------------------------------
# DB helpers (mirrors gsak_counties._open_db read-write pattern)
# ---------------------------------------------------------------------------

_SCHEMA_ENSURE = """
CREATE TABLE IF NOT EXISTS counties (
    id          INTEGER PRIMARY KEY,
    state_code  TEXT NOT NULL,
    state_name  TEXT NOT NULL,
    county_name TEXT NOT NULL,
    adif_key    TEXT NOT NULL,
    min_lat     REAL NOT NULL,
    max_lat     REAL NOT NULL,
    min_lon     REAL NOT NULL,
    max_lon     REAL NOT NULL,
    polygon     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bbox
    ON counties (min_lat, max_lat, min_lon, max_lon);
CREATE INDEX IF NOT EXISTS idx_state
    ON counties (state_code);
"""


def _open_rw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA_ENSURE)
    return conn


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_country(adif_code: str, db_path: Path,
                   dry_run: bool = False, verbose: bool = False) -> int:
    """
    Download GADM admin-1 polygons for adif_code and insert into db_path.

    Returns the number of polygon parts inserted (MultiPolygon countries
    produce more parts than features).
    """
    adif_code = adif_code.upper()
    entry = COUNTRY_TABLE.get(adif_code)
    if entry is None:
        print(f"  {adif_code}: not in COUNTRY_TABLE — skipping.")
        print(f"    (Add it to COUNTRY_TABLE in import_gadm.py to enable.)")
        return 0

    iso3, display_name = entry
    print(f"  {adif_code} ({display_name}, ISO3={iso3})")

    try:
        geojson = _download_geojson(iso3)
    except RuntimeError as exc:
        print(f"  Error: {exc}")
        return 0

    features = geojson.get('features', [])
    print(f"  {len(features)} admin-1 features found.")

    rows = []
    skipped = 0
    for feat in features:
        props    = feat.get('properties', {})
        geometry = feat.get('geometry', {})

        # GADM uses NAME_1 for the primary romanized name.
        # VARNAME_1 holds pipe-separated alternate names — we don't need it.
        name = (props.get('NAME_1') or '').strip()
        if not name:
            skipped += 1
            continue

        rings = _flatten_geometry(geometry)
        if not rings:
            skipped += 1
            if verbose:
                print(f"    Warning: {name} has no usable geometry — skipped.")
            continue

        adif_key = f"{adif_code},{name}"

        for part_num, pts in enumerate(rings, 1):
            if len(pts) < 3:
                continue
            min_lat, max_lat, min_lon, max_lon = _bbox(pts)
            rows.append((
                adif_code, display_name, name, adif_key,
                min_lat, max_lat, min_lon, max_lon,
                json.dumps(pts),
            ))
            if verbose:
                print(f"    {name} part {part_num}: {len(pts)} points  "
                      f"key={adif_key}")

    if skipped:
        print(f"  {skipped} feature(s) skipped (no name or geometry).")

    if dry_run:
        print(f"  [dry-run] Would insert {len(rows)} polygon part(s) "
              f"for {adif_code} — DB not modified.")
        return len(rows)

    conn = _open_rw(db_path)
    # Full replace: delete existing rows for this state_code before reinserting
    deleted = conn.execute(
        "DELETE FROM counties WHERE state_code = ?", (adif_code,)
    ).rowcount
    if deleted:
        print(f"  Replaced {deleted} existing row(s) for {adif_code}.")

    conn.executemany(
        "INSERT INTO counties "
        "(state_code, state_name, county_name, adif_key, "
        " min_lat, max_lat, min_lon, max_lon, polygon) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    print(f"  Inserted {len(rows)} polygon part(s) for {adif_code} ({display_name}).")
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_list():
    print(f"{'ADIF':<6} {'ISO3':<5} {'Country'}")
    print('-' * 40)
    seen_iso3 = set()
    for adif, (iso3, name) in sorted(COUNTRY_TABLE.items()):
        alias = ' (alias)' if iso3 in seen_iso3 else ''
        print(f"  {adif:<4}  {iso3:<5} {name}{alias}")
        seen_iso3.add(iso3)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Import GADM 4.1 admin-1 polygons into gsak_counties.db.\n"
            "GADM data is freely available for non-commercial use: "
            "https://gadm.org/license.html"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python import_gadm.py --db gsak_counties.db JA HL\n"
            "  python import_gadm.py --db gsak_counties.db JA --dry-run\n"
            "  python import_gadm.py --list\n"
        ),
    )
    parser.add_argument(
        'codes', nargs='*', metavar='CODE',
        help='One or more ADIF country codes to import (e.g. JA HL)',
    )
    parser.add_argument(
        '--db', default='gsak_counties.db',
        help='Path to gsak_counties.db (default: gsak_counties.db)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Download and parse but do not write to DB',
    )
    parser.add_argument(
        '--list', action='store_true',
        help='List supported ADIF codes and exit',
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Print each region and polygon part as it is processed',
    )
    args = parser.parse_args()

    if args.list:
        _cmd_list()
        return

    if not args.codes:
        parser.print_help()
        sys.exit(1)

    db_path = Path(args.db).expanduser().resolve()
    if not args.dry_run and not db_path.exists():
        sys.exit(
            f"DB not found: {db_path}\n"
            "Run gsak_counties.py build first to create it, or use --dry-run."
        )

    total = 0
    for code in args.codes:
        print(f"\nImporting {code.upper()} ...")
        n = import_country(code, db_path,
                           dry_run=args.dry_run, verbose=args.verbose)
        total += n

    print(f"\nDone. {total} total polygon part(s) imported.")


if __name__ == '__main__':
    main()
