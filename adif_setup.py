#!/usr/bin/env python3
"""
adif_setup.py — One-time setup for adif_map.py boundary data.

Downloads and caches boundary files used by adif_map.py overlays:

  ne_states.geojson   — Natural Earth 50m US states + Canadian provinces
                        (50m required: 110m omits NT, NU, PE, YT)
  us_counties.geojson — US Census TIGER 20m county boundaries (US only)

Run once; re-run to refresh.

Usage:
    python adif_setup.py

Output:
    ne_states.geojson   — US states + Canadian provinces/territories,
                          saved in the same directory as this script.

Dependencies:
    pip install requests pyshp
    (requests already required by qrz_common.py;
     pyshp needed to read Census/Natural Earth shapefile zips)
"""

import json
import sys
import zipfile
import io
from pathlib import Path

try:
    import shapefile as pyshp
    _PYSHP_AVAILABLE = True
except ImportError:
    _PYSHP_AVAILABLE = False

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR  = Path(__file__).parent
OUTPUT_FILE         = SCRIPT_DIR / "ne_states.geojson"
COUNTY_OUTPUT_FILE  = SCRIPT_DIR / "us_counties.geojson"

# Natural Earth admin-1 states/provinces boundary data.
#
# IMPORTANT: Use the 50m dataset, not 110m.
# At 110m resolution Natural Earth omits four Canadian provinces/territories
# (NT, NU, PE, YT) because they are too small or sparse at that scale.
# The 50m file includes all 13 CA provinces and territories.
#
# Source order: try each in sequence, stop on first success.
SOURCES = [
    {
        "label":    "Natural Earth CDN 50m (zip/shapefile)",
        "url":      "https://naciscdn.org/naturalearth/50m/cultural/"
                    "ne_50m_admin_1_states_provinces.zip",
        "format":   "shapefile_zip",
        "kind":     "states",
    },
    {
        "label":  "GitHub mirror 50m (geojson)",
        "url":    "https://raw.githubusercontent.com/nvkelso/"
                  "natural-earth-vector/master/geojson/"
                  "ne_50m_admin_1_states_provinces.geojson",
        "format": "geojson",
    },
    {
        "label":    "Natural Earth CDN 110m (zip/shapefile, fallback — missing NT/NU/PE/YT)",
        "url":      "https://naciscdn.org/naturalearth/110m/cultural/"
                    "ne_110m_admin_1_states_provinces.zip",
        "format":   "shapefile_zip",
        "kind":     "states",
    },
    {
        "label":  "GitHub mirror 110m (geojson, fallback — missing NT/NU/PE/YT)",
        "url":    "https://raw.githubusercontent.com/nvkelso/"
                  "natural-earth-vector/master/geojson/"
                  "ne_110m_admin_1_states_provinces.geojson",
        "format": "geojson",
    },
]

# Keep only US and Canada
KEEP_ISO = {"US", "CA"}

# ---------------------------------------------------------------------------
# County data configuration
# ---------------------------------------------------------------------------

# US Census TIGER 20m cartographic boundary file (county level).
# The zip contains a GeoJSON file alongside shapefiles.
# 20m resolution ≈ 2.5 MB zip — fine for choropleth use.
COUNTY_SOURCES = [
    {
        "label":    "Census TIGER 2023 county 20m (zip)",
        "url":      "https://www2.census.gov/geo/tiger/GENZ2023/shp/"
                    "cb_2023_us_county_20m.zip",
        "format":   "shapefile_zip",
        "kind":     "counties",
    },
    {
        "label":    "Census TIGER 2022 county 20m (zip, fallback)",
        "url":      "https://www2.census.gov/geo/tiger/GENZ2022/shp/"
                    "cb_2022_us_county_20m.zip",
        "format":   "shapefile_zip",
        "kind":     "counties",
    },
    {
        "label":    "Census TIGER 2021 county 20m (zip, fallback)",
        "url":      "https://www2.census.gov/geo/tiger/GENZ2021/shp/"
                    "cb_2021_us_county_20m.zip",
        "format":   "shapefile_zip",
        "kind":     "counties",
    },
]

# State FIPS code -> 2-letter postal abbreviation.
# Used to convert TIGER's STATEFP property to the state code stored in
# the ADIF CNTY field (e.g. STATEFP="53" + NAME="King" -> "WA,King").
FIPS_TO_ABBR: dict[str, str] = {
    "01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT",
    "10":"DE","11":"DC","12":"FL","13":"GA","15":"HI","16":"ID","17":"IL",
    "18":"IN","19":"IA","20":"KS","21":"KY","22":"LA","23":"ME","24":"MD",
    "25":"MA","26":"MI","27":"MN","28":"MS","29":"MO","30":"MT","31":"NE",
    "32":"NV","33":"NH","34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND",
    "39":"OH","40":"OK","41":"OR","42":"PA","44":"RI","45":"SC","46":"SD",
    "47":"TN","48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV",
    "55":"WI","56":"WY",
}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _shapefile_zip_to_raw_geojson(zip_bytes: bytes) -> dict:
    """
    Read a shapefile from a zip (in memory) and return a raw GeoJSON
    FeatureCollection with all original properties preserved.
    Requires pyshp.
    """
    if not _PYSHP_AVAILABLE:
        raise RuntimeError(
            "pyshp is required to read shapefile zips.\n"
            "  pip install pyshp"
        )
    zf   = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = zf.namelist()
    shp_name = next((n for n in names if n.endswith(".shp")), None)
    if not shp_name:
        raise ValueError(f"No .shp file found in zip. Contents: {names}")
    stem = shp_name[:-4]

    shp = io.BytesIO(zf.read(f"{stem}.shp"))
    dbf = io.BytesIO(zf.read(f"{stem}.dbf"))
    shx = io.BytesIO(zf.read(f"{stem}.shx")) if f"{stem}.shx" in names else None

    reader = pyshp.Reader(shp=shp, dbf=dbf, shx=shx)
    features = []
    for rec in reader.iterShapeRecords():
        props = dict(rec.record.as_dict())
        geom  = rec.shape.__geo_interface__
        features.append({
            "type":       "Feature",
            "properties": props,
            "geometry":   geom,
        })
    print(f"    Read {len(features)} features from shapefile.")
    return {"type": "FeatureCollection", "features": features}


def download(source: dict) -> dict | None:
    """
    Download and parse a GeoJSON FeatureCollection from a source dict.

    Supported formats:
        "geojson"        — response body is GeoJSON directly
        "shapefile_zip"  — zip contains .shp/.dbf/.shx (Census TIGER / NE CDN)

    Returns the parsed FeatureCollection dict, or None on failure.
    """
    label = source["label"]
    url   = source["url"]
    fmt   = source["format"]

    print(f"  Trying {label} ...")
    print(f"    {url}")

    try:
        r = requests.get(url, timeout=120,
                         headers={"User-Agent": "adif_setup/1.0"})
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"    FAILED: {exc}")
        return None

    print(f"    Downloaded {len(r.content):,} bytes")

    try:
        if fmt == "geojson":
            data = r.json()
        elif fmt == "shapefile_zip":
            data = _shapefile_zip_to_raw_geojson(r.content)
        else:
            print(f"    FAILED: unknown format {fmt!r}")
            return None
    except Exception as exc:
        print(f"    FAILED to parse: {exc}")
        return None

    if data.get("type") != "FeatureCollection":
        print(f"    FAILED: not a FeatureCollection (got {data.get('type')!r})")
        return None

    return data


# ---------------------------------------------------------------------------
# Filter and normalise
# ---------------------------------------------------------------------------

def normalise(data: dict) -> dict:
    """
    Filter to US + Canada only and normalise property keys so adif_map.py
    can rely on a consistent schema regardless of source version.

    Normalised properties on each feature:
        postal   — 2-letter abbreviation (WA, BC, ON …)
        name     — full English name
        iso_a2   — US or CA
        type_en  — State | Province | Territory
    """
    features_in  = data["features"]
    features_out = []

    for f in features_in:
        props = f.get("properties") or {}

        iso = (props.get("iso_a2") or props.get("ISO_A2") or "").upper().strip()
        if iso not in KEEP_ISO:
            continue

        # postal code — try several property name variants across NE versions
        postal = (
            props.get("postal")
            or props.get("POSTAL")
            or props.get("code_local")
            or props.get("abbrev")
            or props.get("adm1_code", "")[:2]
            or ""
        ).upper().strip()

        name = (
            props.get("name")
            or props.get("NAME")
            or props.get("name_en")
            or postal
        ).strip()

        type_en = (
            props.get("type_en")
            or props.get("TYPE_EN")
            or props.get("featurecla")
            or ("State" if iso == "US" else "Province")
        ).strip()

        features_out.append({
            "type": "Feature",
            "properties": {
                "postal":  postal,
                "name":    name,
                "iso_a2":  iso,
                "type_en": type_en,
            },
            "geometry": f["geometry"],
        })

    return {"type": "FeatureCollection", "features": features_out}


def normalise_counties(data: dict) -> dict:
    """
    Normalise Census TIGER county GeoJSON into a consistent schema.

    Normalised properties on each feature:
        adif_key  — ADIF CNTY match key, e.g. "WA,King"
        state     — 2-letter state abbreviation, e.g. "WA"
        name      — county name without suffix, e.g. "King"
        namelsad  — full name with suffix, e.g. "King County"

    Features whose STATEFP is not in FIPS_TO_ABBR (e.g. PR, VI) are dropped.
    """
    features_in  = data.get("features", [])
    features_out = []

    for f in features_in:
        props   = f.get("properties") or {}
        statefp = str(props.get("STATEFP") or props.get("statefp") or "").zfill(2)
        abbr    = FIPS_TO_ABBR.get(statefp)
        if not abbr:
            continue   # skip territories, PR, VI

        name    = (props.get("NAME") or props.get("name") or "").strip()
        namelsad= (props.get("NAMELSAD") or props.get("namelsad")
                   or f"{name} County").strip()
        adif_key = f"{abbr},{name}"

        features_out.append({
            "type": "Feature",
            "properties": {
                "adif_key": adif_key,
                "state":    abbr,
                "name":     name,
                "namelsad": namelsad,
            },
            "geometry": f["geometry"],
        })

    return {"type": "FeatureCollection", "features": features_out}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_states() -> bool:
    """Download and save ne_states.geojson. Returns True on success."""
    print("── States / Provinces ──────────────────────────────────────")
    if OUTPUT_FILE.exists():
        print(f"  Cache file already exists: {OUTPUT_FILE}")
        answer = input("  Re-download and overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("  Skipped.")
            return True

    raw = None
    for source in SOURCES:
        raw = download(source)
        if raw:
            break
        print()

    if raw is None:
        print()
        print("  ERROR: All states download sources failed.")
        return False

    normalised = normalise(raw)
    us_count = sum(1 for f in normalised["features"] if f["properties"]["iso_a2"] == "US")
    ca_count = sum(1 for f in normalised["features"] if f["properties"]["iso_a2"] == "CA")

    print()
    print(f"  Total features: {len(normalised['features'])}  "
          f"(US: {us_count}, CA: {ca_count})")

    # Spot-check CA coverage — 4 provinces absent from 110m fallback
    postals     = {f["properties"]["postal"] for f in normalised["features"]}
    ca_required = {"AB","BC","MB","NB","NL","NS","NT","NU","ON","PE","QC","SK","YT"}
    ca_missing  = ca_required - postals

    if ca_missing:
        print(f"  WARNING: missing CA provinces/territories: {ca_missing}")
        if {"NT","NU","PE","YT"} & ca_missing:
            print("  This usually means the 110m fallback was used instead of 50m.")
            print("  NT/NU/PE/YT are omitted from the 110m dataset.")
    else:
        print(f"  All 13 CA provinces/territories confirmed present.")

    OUTPUT_FILE.write_text(json.dumps(normalised, separators=(",", ":")),
                           encoding="utf-8")
    print(f"  Saved: {OUTPUT_FILE}  ({OUTPUT_FILE.stat().st_size // 1024} KB)")
    return True


def _run_counties() -> bool:
    """Download and save us_counties.geojson. Returns True on success."""
    print("── US Counties ─────────────────────────────────────────────")
    if COUNTY_OUTPUT_FILE.exists():
        print(f"  Cache file already exists: {COUNTY_OUTPUT_FILE}")
        answer = input("  Re-download and overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("  Skipped.")
            return True

    raw = None
    for source in COUNTY_SOURCES:
        raw = download(source)
        if raw:
            break
        print()

    if raw is None:
        print()
        print("  ERROR: All county download sources failed.")
        return False

    normalised = normalise_counties(raw)
    total = len(normalised["features"])

    # Spot-check
    keys   = {f["properties"]["adif_key"] for f in normalised["features"]}
    sample = {"WA,King", "TX,Travis", "NY,New York", "FL,Miami-Dade"}
    missing = sample - keys
    if missing:
        print(f"  WARNING: expected counties not found: {missing}")
    else:
        print(f"  {total} counties loaded. Spot-check OK.")

    COUNTY_OUTPUT_FILE.write_text(json.dumps(normalised, separators=(",", ":")),
                                  encoding="utf-8")
    print(f"  Saved: {COUNTY_OUTPUT_FILE}  "
          f"({COUNTY_OUTPUT_FILE.stat().st_size // 1024} KB)")
    return True


def main():
    print("adif_setup.py — downloading boundary data for adif_map.py")
    print()

    states_ok  = _run_states()
    print()
    counties_ok = _run_counties()
    print()

    if states_ok and counties_ok:
        print("Setup complete. You can now use --overlay states,counties,grids in adif_map.py.")
    else:
        if not states_ok:
            print("WARNING: states download failed — --overlay states will not work.")
        if not counties_ok:
            print("WARNING: counties download failed — --overlay counties will not work.")
        sys.exit(1)


if __name__ == "__main__":
    main()
