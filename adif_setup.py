#!/usr/bin/env python3
"""
adif_setup.py — One-time setup for adif_map.py boundary data.

Downloads and caches the Natural Earth state/province boundary file
used by the --overlay states option.  Run this once; re-run only if
the cache file is deleted or you want to refresh the data.

Usage:
    python adif_setup.py

Output:
    ne_states.geojson   — US states + Canadian provinces/territories,
                          saved in the same directory as this script.

Dependencies:
    pip install requests
    (Already required by qrz_common.py)
"""

import json
import sys
import zipfile
import io
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR  = Path(__file__).parent
OUTPUT_FILE = SCRIPT_DIR / "ne_states.geojson"

# Natural Earth 110m admin-1 states/provinces
# Primary: official Natural Earth CDN (zip containing shapefile + geojson)
# Fallback: GitHub mirror of the geojson directly
SOURCES = [
    {
        "label":  "Natural Earth CDN (zip)",
        "url":    "https://naciscdn.org/naturalearth/110m/cultural/"
                  "ne_110m_admin_1_states_provinces.zip",
        "format": "zip",
        "zip_name": "ne_110m_admin_1_states_provinces.geojson",
    },
    {
        "label":  "GitHub mirror (geojson)",
        "url":    "https://raw.githubusercontent.com/nvkelso/"
                  "natural-earth-vector/master/geojson/"
                  "ne_110m_admin_1_states_provinces.geojson",
        "format": "geojson",
    },
    {
        "label":  "GitHub mirror (geojson, alternate branch)",
        "url":    "https://raw.githubusercontent.com/nvkelso/"
                  "natural-earth-vector/v5.1.2/geojson/"
                  "ne_110m_admin_1_states_provinces.geojson",
        "format": "geojson",
    },
]

# Keep only US and Canada
KEEP_ISO = {"US", "CA"}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download(source: dict) -> dict | None:
    """
    Download and parse a GeoJSON FeatureCollection from a source dict.
    Returns the parsed dict or None on failure.
    """
    label = source["label"]
    url   = source["url"]
    fmt   = source["format"]

    print(f"  Trying {label} ...")
    print(f"    {url}")

    try:
        r = requests.get(url, timeout=60,
                         headers={"User-Agent": "adif_setup/1.0"})
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"    FAILED: {exc}")
        return None

    print(f"    Downloaded {len(r.content):,} bytes")

    try:
        if fmt == "zip":
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            target = source.get("zip_name", "")
            names  = zf.namelist()
            # Find the geojson member (name may vary by version)
            match = next(
                (n for n in names
                 if n.endswith(".geojson") and "admin_1" in n),
                None
            )
            if not match and target:
                match = next((n for n in names if target in n), None)
            if not match:
                # Try any geojson in the zip
                match = next((n for n in names if n.endswith(".geojson")), None)
            if not match:
                print(f"    FAILED: no .geojson found in zip. Contents: {names[:10]}")
                return None
            print(f"    Reading {match} from zip ...")
            data = json.loads(zf.read(match))
        else:
            data = r.json()
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("adif_setup.py — downloading boundary data for adif_map.py")
    print()

    if OUTPUT_FILE.exists():
        print(f"Cache file already exists: {OUTPUT_FILE}")
        answer = input("Re-download and overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Nothing to do.")
            return

    raw = None
    for source in SOURCES:
        raw = download(source)
        if raw:
            break
        print()

    if raw is None:
        print()
        print("ERROR: All download sources failed.")
        print("Check your internet connection and try again.")
        sys.exit(1)

    total_in = len(raw["features"])
    normalised = normalise(raw)
    total_out  = len(normalised["features"])

    us_count = sum(1 for f in normalised["features"]
                   if f["properties"]["iso_a2"] == "US")
    ca_count = sum(1 for f in normalised["features"]
                   if f["properties"]["iso_a2"] == "CA")

    print()
    print(f"  Total features in source:  {total_in}")
    print(f"  After filtering to US+CA:  {total_out}")
    print(f"    US states/DC:            {us_count}")
    print(f"    CA provinces/territories:{ca_count}")

    # Spot-check that key postal codes are present
    postals = {f["properties"]["postal"] for f in normalised["features"]}
    missing = {"WA", "ON", "BC", "TX", "FL"} - postals
    if missing:
        print(f"  WARNING: expected postal codes not found: {missing}")
        print("  The overlay may not match QSO records correctly.")
    else:
        print(f"  Spot-check postal codes: OK (WA, ON, BC, TX, FL all present)")

    OUTPUT_FILE.write_text(
        json.dumps(normalised, separators=(",", ":")),
        encoding="utf-8"
    )
    size_kb = OUTPUT_FILE.stat().st_size // 1024
    print()
    print(f"Saved: {OUTPUT_FILE}  ({size_kb} KB)")
    print()
    print("Setup complete. You can now use --overlay states in adif_map.py.")


if __name__ == "__main__":
    main()
