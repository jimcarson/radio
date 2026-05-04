#!/usr/bin/env python3
"""
import_gadm.py — Import GADM admin-1 region polygons into gsak_counties.db
============================================================================
Downloads GeoJSON admin-1 boundary data from GADM 4.1 for one or more
countries and imports the polygons into the counties table of an existing
gsak_counties.db, using the same flat-layout schema as Iceland/Norway/Czechia.

Each region is stored as:
    state_code  = ISO 2-letter country code             (e.g. 'NO', 'FI')
    state_name  = country display name                  (e.g. 'Norway')
    county_name = romanized region name from GADM NAME_1
    adif_key    = 'NO,Akershus', 'FI,Lappi', etc.

The coordinate-based lookup in adif_map.py (_cnty_key_fn coord fallback)
will then resolve contacts to the correct region automatically.

Country codes: ISO 2-letter codes are the primary keys (NO, FI, JP, KR ...).
Ham radio prefixes are accepted as aliases (LA->NO, OH->FI, JA->JP, HL->KR ...).
US states (WA, TX ...) and Canadian provinces (ON, BC ...) are a separate
namespace and do not conflict with the 2-letter ISO country codes used here.

GADM license: freely available for non-commercial use.
    See https://gadm.org/license.html

Usage:
    python import_gadm.py --db gsak_counties.db NO FI
    python import_gadm.py --db gsak_counties.db NO --dry-run
    python import_gadm.py --list

Dependencies: stdlib only (urllib, zipfile, json, sqlite3)
"""

__version__ = "1.1.3"  # add Indonesia (YB), India (VU); ID/IN aliases with collision-safe state_codes

import argparse
import io
import json
import sqlite3
import sys
import urllib.request
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Country table: lookup_code -> (ISO3 for GADM, ISO2 state_code, display name)
#
# Primary keys are ISO 2-letter codes.  Ham radio prefixes that differ from
# the ISO code are listed below as aliases pointing to the same ISO3/name,
# but using the *canonical ISO2* as the state_code stored in the DB.
#
# This keeps country codes in the same ISO namespace regardless of whether
# you type the ham prefix or the ISO code on the command line.
#
# US state codes (WA, TX ...) and Canadian province codes (ON, BC ...) are a
# separate namespace -- no collisions with any of the ISO2 codes here.
# ---------------------------------------------------------------------------
COUNTRY_TABLE: dict[str, tuple[str, str, str]] = {
    # lookup_code : (GADM ISO3,  DB state_code,  display name)
    #
    # ---- ISO 2-letter primary entries ----
    'NO': ('NOR', 'NO', 'Norway'),
    'FI': ('FIN', 'FI', 'Finland'),
    'IS': ('ISL', 'IS', 'Iceland'),
    'CZ': ('CZE', 'CZ', 'Czechia'),
    'SE': ('SWE', 'SE', 'Sweden'),
    'DK': ('DNK', 'DK', 'Denmark'),
    'NL': ('NLD', 'NL', 'Netherlands'),
    'BE': ('BEL', 'BE', 'Belgium'),
    'AT': ('AUT', 'AT', 'Austria'),
    'CH': ('CHE', 'CH', 'Switzerland'),
    'DE': ('DEU', 'DE', 'Germany'),
    'FR': ('FRA', 'FR', 'France'),
    'ES': ('ESP', 'ES', 'Spain'),
    'IT': ('ITA', 'IT', 'Italy'),
    'GB': ('GBR', 'GB', 'United Kingdom'),
    'AU': ('AUS', 'AU', 'Australia'),
    'NZ': ('NZL', 'NZ', 'New Zealand'),
    'JP': ('JPN', 'JP', 'Japan'),
    'KR': ('KOR', 'KR', 'South Korea'),
    'CN': ('CHN', 'CN', 'China'),
    'HK': ('HKG', 'HK', 'Hong Kong'),
    'YB': ('IDN', 'YB', 'Indonesia'),   # ID collides with Idaho; store as YB (ham prefix)
    'ZS': ('ZAF', 'ZA', 'South Africa'),
    'VU': ('IND', 'VU', 'India'),        # IN collides with Indiana; store as VU (ham prefix)
    'RU': ('RUS', 'RU', 'Russia'),
    'UA': ('UKR', 'UA', 'Ukraine'),
    'BY': ('BLR', 'BY', 'Belarus'),
    'AR': ('ARG', 'AR', 'Argentina'),
    'BR': ('BRA', 'BR', 'Brazil'),
    'MX': ('MEX', 'MX', 'Mexico'),
    'CL': ('CHL', 'CL', 'Chile'),
    'VE': ('VEN', 'VE', 'Venezuela'),
    'PE': ('PER', 'PE', 'Peru'),
    'PL': ('POL', 'PL', 'Poland'),
    'ZA': ('ZAF', 'ZA', 'South Africa'),
    #
    # ---- Ham radio prefix aliases (stored using ISO2 state_code) ----
    # Aliases are accepted on the command line but the DB always gets the ISO2.
    'LA': ('NOR', 'NO', 'Norway'),        # LA = Norway ham prefix
    'OH': ('FIN', 'FI', 'Finland'),       # OH = Finland ham prefix
    'TF': ('ISL', 'IS', 'Iceland'),       # TF = Iceland ham prefix
    'OK': ('CZE', 'CZ', 'Czechia'),       # OK = Czechia ham prefix
    'SM': ('SWE', 'SE', 'Sweden'),        # SM = Sweden ham prefix
    'OZ': ('DNK', 'DK', 'Denmark'),       # OZ = Denmark ham prefix
    'PA': ('NLD', 'NL', 'Netherlands'),   # PA = Netherlands ham prefix
    'OE': ('AUT', 'AT', 'Austria'),       # OE = Austria ham prefix
    'HB': ('CHE', 'CH', 'Switzerland'),   # HB = Switzerland ham prefix
    'DL': ('DEU', 'DE', 'Germany'),       # DL = Germany ham prefix
    'F':  ('FRA', 'FR', 'France'),        # F  = France ham prefix
    'EA': ('ESP', 'ES', 'Spain'),         # EA = Spain ham prefix
    'SP': ('POL', 'PL', 'Poland'),         # SP = Poland ham prefix (not Spain -- EA is Spain)
    'I':  ('ITA', 'IT', 'Italy'),         # I  = Italy ham prefix
    'G':  ('GBR', 'GB', 'United Kingdom'), # G = UK ham prefix
    'VK': ('AUS', 'AU', 'Australia'),     # VK = Australia ham prefix
    'ZL': ('NZL', 'NZ', 'New Zealand'),   # ZL = New Zealand ham prefix
    'JA': ('JPN', 'JP', 'Japan'),         # JA = Japan ham prefix
    'HL': ('KOR', 'KR', 'South Korea'),   # HL = South Korea ham prefix
    'ID': ('IDN', 'YB', 'Indonesia'),      # ID alias (ISO); stored as YB to avoid Idaho collision
    'IN': ('IND', 'VU', 'India'),          # IN alias (ISO); stored as VU to avoid Indiana collision
    'KH': ('KOR', 'KR', 'South Korea'),   # KH = South Korea (ADIF)
    'UR': ('RUS', 'RU', 'Russia'),        # UR = Russia (some ADIF)
    'RA': ('ARG', 'AR', 'Argentina'),     # RA = Argentina ham prefix
    'LU': ('ARG', 'AR', 'Argentina'),     # LU = Argentina (Buenos Aires)
    'PY': ('BRA', 'BR', 'Brazil'),        # PY = Brazil ham prefix
    'XE': ('MEX', 'MX', 'Mexico'),        # XE = Mexico ham prefix
    'CE': ('CHL', 'CL', 'Chile'),         # CE = Chile ham prefix
    'OA': ('PER', 'PE', 'Peru'),          # OA = Peru ham prefix
}

GADM_BASE_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json"


# ---------------------------------------------------------------------------
# Geometry helpers (stdlib only -- mirrors gsak_counties.py approach)
# ---------------------------------------------------------------------------

def _bbox(pts: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return min(lats), max(lats), min(lons), max(lons)


def _flatten_geometry(geometry: dict) -> list[list[tuple[float, float]]]:
    """
    Extract rings from a GeoJSON geometry as lists of (lat, lon) tuples.

    Handles Polygon, MultiPolygon, and GeometryCollection (recursively).
    Inner rings (holes) are discarded -- point-in-polygon over holes is rare
    at the region scale and complicates the schema.

    GeoJSON coords are [lon, lat]; we flip to (lat, lon) to match the
    gsak_counties.py convention.

    Returns an empty list if the geometry is null or an unsupported type.
    """
    if not geometry:
        return []

    rings = []
    gtype = geometry.get('type', '')

    if gtype == 'Polygon':
        coords_list = geometry.get('coordinates', [])
        if coords_list:
            rings.append([(lat, lon) for lon, lat in coords_list[0]])

    elif gtype == 'MultiPolygon':
        for poly in geometry.get('coordinates', []):
            if poly:
                rings.append([(lat, lon) for lon, lat in poly[0]])

    elif gtype == 'GeometryCollection':
        for sub_geom in geometry.get('geometries', []):
            rings.extend(_flatten_geometry(sub_geom))

    return rings


# ---------------------------------------------------------------------------
# GADM download
# ---------------------------------------------------------------------------

def _decode_json_bytes(raw: bytes) -> dict:
    """
    Decode raw bytes from a GADM zip into a parsed GeoJSON dict.

    GADM 4.1 JSON files are UTF-8.  If strict UTF-8 decoding fails, fall
    back to Latin-1 (cp1252) which covers older GADM vintages.
    Raises RuntimeError if neither encoding works.
    """
    for encoding in ('utf-8', 'latin-1'):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise RuntimeError("Could not decode JSON as UTF-8 or Latin-1.")


def _download_geojson(iso3: str, level: int = 1) -> dict:
    """
    Download and return the parsed GeoJSON FeatureCollection for a country
    at the given admin level from GADM 4.1.  Files are served as .json.zip.
    Level 1 is the top administrative division (states, regions, fylker).
    Level 2 gives finer subdivisions (e.g. Finnish maakunta, Norwegian kommuner).
    """
    url = f"{GADM_BASE_URL}/gadm41_{iso3}_{level}.json.zip"
    print(f"  Downloading {url} ...")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except Exception as exc:
        raise RuntimeError(f"Download failed for {iso3}: {exc}") from exc

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        json_name = next(n for n in zf.namelist() if n.endswith('.json'))
        geojson = _decode_json_bytes(zf.read(json_name))
    except RuntimeError:
        raise
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

def import_country(code: str, db_path: Path,
                   state_code_override: str | None = None,
                   level: int = 1,
                   dry_run: bool = False,
                   verbose: bool = False) -> int:
    """
    Download GADM admin-1 polygons for *code* and insert into db_path.

    *code* may be an ISO 2-letter code (NO, FI) or a ham prefix alias
    (LA, OH).  The DB always stores the canonical ISO2 state_code unless
    *state_code_override* is supplied.

    *level* selects the GADM admin level (default 1).  Use 2 for finer
    subdivisions, e.g. Finnish maakunta or Norwegian kommuner.
    The name field used is NAME_1 for level 1, NAME_2 for level 2, etc.

    Returns the number of polygon parts inserted.
    """
    code = code.upper()
    entry = COUNTRY_TABLE.get(code)
    if entry is None:
        print(f"  {code}: not in COUNTRY_TABLE -- skipping.")
        print(f"    (Add it to COUNTRY_TABLE in import_gadm.py to enable.)")
        return 0

    iso3, iso2, display_name = entry
    state_code = state_code_override.upper() if state_code_override else iso2

    if state_code != iso2:
        print(f"  {code} ({display_name}, ISO3={iso3})  "
              f"[state_code override: {iso2} -> {state_code}]")
    elif code != iso2:
        print(f"  {code} -> {iso2} ({display_name}, ISO3={iso3})")
    else:
        print(f"  {iso2} ({display_name}, ISO3={iso3})")

    name_field = f"NAME_{level}"

    try:
        geojson = _download_geojson(iso3, level=level)
    except RuntimeError as exc:
        print(f"  Error: {exc}")
        return 0

    features = geojson.get('features', [])
    print(f"  {len(features)} admin-1 features found.")

    rows = []
    skipped_no_name = 0
    skipped_no_geom: list[tuple[str, str]] = []

    for feat in features:
        props    = feat.get('properties', {})
        geometry = feat.get('geometry')

        name = (props.get(name_field) or '').strip()
        if not name:
            skipped_no_name += 1
            continue

        rings = _flatten_geometry(geometry or {})
        if not rings:
            geom_type = (geometry or {}).get('type', 'null') if geometry else 'null'
            skipped_no_geom.append((name, geom_type))
            if verbose:
                print(f"    Warning: '{name}' has no usable geometry "
                      f"(type={geom_type}) -- skipped.")
            continue

        adif_key = f"{state_code},{name}"

        for part_num, pts in enumerate(rings, 1):
            if len(pts) < 3:
                continue
            min_lat, max_lat, min_lon, max_lon = _bbox(pts)
            rows.append((
                state_code, display_name, name, adif_key,
                min_lat, max_lat, min_lon, max_lon,
                json.dumps(pts),
            ))
            if verbose:
                print(f"    {name} part {part_num}: {len(pts)} pts  "
                      f"key={adif_key}")

    if skipped_no_name:
        print(f"  {skipped_no_name} feature(s) skipped -- no {name_field}.")
    if skipped_no_geom:
        # Always report these regardless of --verbose
        print(f"  {len(skipped_no_geom)} feature(s) skipped -- no usable geometry:")
        for nm, gt in skipped_no_geom:
            print(f"    '{nm}'  (geometry type: {gt})")

    if dry_run:
        print(f"  [dry-run] Would insert {len(rows)} polygon part(s) "
              f"for {state_code} -- DB not modified.")
        return len(rows)

    conn = _open_rw(db_path)
    deleted = conn.execute(
        "DELETE FROM counties WHERE state_code = ?", (state_code,)
    ).rowcount
    if deleted:
        print(f"  Replaced {deleted} existing row(s) for {state_code}.")

    conn.executemany(
        "INSERT INTO counties "
        "(state_code, state_name, county_name, adif_key, "
        " min_lat, max_lat, min_lon, max_lon, polygon) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    print(f"  Inserted {len(rows)} polygon part(s) for {state_code} ({display_name}).")
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_list():
    from collections import defaultdict
    by_iso2: dict[str, list] = defaultdict(list)
    for code, (iso3, iso2, name) in sorted(COUNTRY_TABLE.items()):
        by_iso2[iso2].append((code, iso3, name))

    print(f"  {'DB code':<8} {'ISO3':<5} {'Country':<28}  Ham/other aliases")
    print('  ' + '-' * 72)
    for iso2 in sorted(by_iso2):
        entries = by_iso2[iso2]
        primary = next((e for e in entries if e[0] == iso2), entries[0])
        aliases = sorted(e[0] for e in entries if e[0] != iso2)
        alias_str = ', '.join(aliases) if aliases else ''
        _code, iso3, name = primary
        print(f"  {iso2:<8} {iso3:<5} {name:<28}  {alias_str}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Import GADM 4.1 admin-1 polygons into gsak_counties.db.\n"
            "Uses ISO 2-letter codes as the DB state_code (NO, FI, JP ...).\n"
            "Ham radio prefix aliases are also accepted (LA->NO, OH->FI, JA->JP ...).\n"
            "GADM data is freely available for non-commercial use: "
            "https://gadm.org/license.html"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python import_gadm.py --db gsak_counties.db NO FI\n"
            "  python import_gadm.py --db gsak_counties.db LA    # alias for NO\n"
            "  python import_gadm.py --db gsak_counties.db NO --dry-run\n"
            "  python import_gadm.py --list\n"
        ),
    )
    parser.add_argument(
        'codes', nargs='*', metavar='CODE',
        help='One or more ISO2 or ham-prefix codes to import (e.g. NO FI JP)',
    )
    parser.add_argument(
        '--db', default='gsak_counties.db',
        help='Path to gsak_counties.db (default: gsak_counties.db)',
    )
    parser.add_argument(
        '--state-code',
        help='Override the state_code stored in the DB (rarely needed)',
    )
    parser.add_argument(
        '--level', type=int, default=1, choices=[1, 2],
        help='GADM admin level: 1=top-level regions (default), 2=finer subdivisions '
             '(e.g. Finnish maakunta, Norwegian kommuner)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Download and parse but do not write to DB',
    )
    parser.add_argument(
        '--list', action='store_true',
        help='List supported codes and exit',
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

    if args.state_code and len(args.codes) > 1:
        sys.exit("--state-code can only be used when importing a single country.")

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
                           state_code_override=args.state_code,
                           level=args.level,
                           dry_run=args.dry_run,
                           verbose=args.verbose)
        total += n

    print(f"\nDone. {total} total polygon part(s) imported.")


if __name__ == '__main__':
    main()
