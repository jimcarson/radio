#!/usr/bin/env python3
"""
build_land_grids.py — Generate land_grids.txt for adif_map.py / map_core.py
============================================================================
One-time setup script.  Fetches the Natural Earth 110m land polygon dataset,
buffers it by 2 Maidenhead grid-widths (~4° lon), and writes every grid4
square whose centre falls within that buffer to land_grids.txt.

map_core.py reads land_grids.txt at runtime to restrict ghost (unworked) cell
rendering in --overlays-only mode to land-adjacent grids only, dramatically
reducing browser load.  If land_grids.txt is absent at runtime, map_core.py
falls back to the full bounding-box ghost set (no land filtering).

Usage:
    python build_land_grids.py [--buffer N] [--output FILE] [--verbose]
    python build_land_grids.py --input ne_110m_land.geojson   # skip download

Options:
    --buffer N      Buffer distance in degrees (default: 4.0 — approx 2 grid-widths)
    --input FILE    Use a local GeoJSON file instead of fetching from the web.
                    If omitted, the script checks for ne_110m_land.geojson beside
                    itself; if not found, fetches from Natural Earth via GitHub.
    --output FILE   Output path (default: land_grids.txt beside this script)
    --verbose       Print every accepted grid square

Dependencies (one-time only, not needed at runtime):
    pip install shapely

Data source:
    Natural Earth 110m land polygons — public domain
    https://www.naturalearthdata.com/
"""

__version__ = "1.0.0"  # Initial release

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

LAND_GEOJSON_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
    "/master/geojson/ne_110m_land.geojson"
)

TOTAL_GRIDS = 32_400   # 18 fields × 10 squares × 18 fields × 10 squares


def _all_grid4() -> list[tuple[str, float, float]]:
    """Yield (grid4, center_lon, center_lat) for every valid Maidenhead grid4."""
    result = []
    for field_col in range(18):          # field letter A–R (lon)
        for field_row in range(18):      # field letter A–R (lat)
            for sq_col in range(10):     # square digit 0–9 (lon)
                for sq_row in range(10): # square digit 0–9 (lat)
                    grid = (
                        chr(ord('A') + field_col) +
                        chr(ord('A') + field_row) +
                        str(sq_col) +
                        str(sq_row)
                    )
                    lon_sw = field_col * 20 - 180 + sq_col * 2
                    lat_sw = field_row * 10 -  90 + sq_row * 1
                    result.append((grid, lon_sw + 1.0, lat_sw + 0.5))
    return result


_LOCAL_GEOJSON_NAME = "ne_110m_land.geojson"


def load_land_geojson(input_path: Path | None) -> dict:
    """
    Load Natural Earth 110m land GeoJSON.

    Priority:
      1. --input FILE argument (if provided)
      2. ne_110m_land.geojson beside this script (if it exists)
      3. Fetch from Natural Earth via GitHub
    """
    # Explicit --input
    if input_path is not None:
        print(f"Loading land GeoJSON from {input_path} ...")
        try:
            data = json.loads(input_path.read_text(encoding="utf-8"))
        except Exception as exc:
            sys.exit(f"  Could not read {input_path}: {exc}")
        print(f"  {len(data.get('features', []))} land polygon features loaded.")
        return data

    # Local file beside the script
    local = Path(__file__).parent / _LOCAL_GEOJSON_NAME
    if local.exists():
        print(f"Loading land GeoJSON from local file {local.name} ...")
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
        except Exception as exc:
            sys.exit(f"  Could not read {local}: {exc}")
        print(f"  {len(data.get('features', []))} land polygon features loaded.")
        return data

    # Fetch from web
    print(f"Fetching Natural Earth 110m land data ...")
    print(f"  URL: {LAND_GEOJSON_URL}")
    print(f"  (Save as {_LOCAL_GEOJSON_NAME} beside this script to skip download next time)")
    try:
        req = urllib.request.Request(
            LAND_GEOJSON_URL, headers={"User-Agent": "build_land_grids/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
    except Exception as exc:
        sys.exit(
            f"  Failed to fetch land data: {exc}\n"
            f"  Download manually from:\n  {LAND_GEOJSON_URL}\n"
            f"  then re-run with:  python build_land_grids.py --input ne_110m_land.geojson"
        )
    n = len(data.get("features", []))
    print(f"  {n} land polygon features fetched.")
    return data


def build_buffered_land(geojson: dict, buffer_deg: float):
    try:
        from shapely.geometry import shape
        from shapely.ops import unary_union
    except ImportError:
        sys.exit(
            "Missing dependency: run  pip install shapely\n"
            "(shapely is only needed for this one-time generation step)"
        )

    print(f"Building buffered land union (buffer={buffer_deg}°) ...")
    t0 = time.time()
    polys = []
    for feat in geojson.get("features", []):
        geom = feat.get("geometry")
        if geom:
            try:
                polys.append(shape(geom))
            except Exception:
                pass
    if not polys:
        sys.exit("No valid land geometries found in GeoJSON.")

    land    = unary_union(polys)
    buffered = land.buffer(buffer_deg)
    print(f"  Done in {time.time() - t0:.1f}s  ({len(polys)} polygons merged)")
    return buffered


def classify_grids(buffered, verbose: bool) -> list[str]:
    try:
        from shapely.geometry import Point
    except ImportError:
        sys.exit("Missing dependency: run  pip install shapely")

    print(f"Testing all {TOTAL_GRIDS:,} grid4 squares ...")
    t0 = time.time()
    near_land = []
    all_grids = _all_grid4()

    for i, (grid, lon_c, lat_c) in enumerate(all_grids):
        if buffered.contains(Point(lon_c, lat_c)):
            near_land.append(grid)
            if verbose:
                print(f"    {grid}  ({lon_c:.1f}, {lat_c:.1f})")
        if (i + 1) % 5000 == 0:
            pct = 100 * (i + 1) / TOTAL_GRIDS
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (TOTAL_GRIDS - i - 1)
            print(f"  {i+1:,}/{TOTAL_GRIDS:,} ({pct:.0f}%)  ETA {eta:.0f}s", end="\r")

    elapsed = time.time() - t0
    print(f"  {len(near_land):,} of {TOTAL_GRIDS:,} grids are land-adjacent "
          f"({100*len(near_land)/TOTAL_GRIDS:.1f}%) — "
          f"{100*(1-len(near_land)/TOTAL_GRIDS):.0f}% eliminated  [{elapsed:.1f}s]")
    return near_land


def write_output(near_land: list[str], out_path: Path, buffer_deg: float) -> None:
    import datetime
    today = datetime.date.today().isoformat()
    lines = [
        f"# land_grids.txt — generated by build_land_grids.py v{__version__}",
        f"# Date: {today}",
        f"# Buffer: {buffer_deg}° (approx {buffer_deg/2:.0f} grid-widths)",
        f"# Source: Natural Earth 110m land polygons (public domain)",
        f"# Grids: {len(near_land)} of {TOTAL_GRIDS} total grid4 squares",
        f"# Used by map_core.py build_grid_overlay() to restrict ghost cell",
        f"# rendering in --overlays-only mode to land-adjacent grids only.",
        "",
    ] + near_land

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Written → {out_path}  ({out_path.stat().st_size // 1024} KB)")


def main():
    parser = argparse.ArgumentParser(
        description="Generate land_grids.txt — land-adjacent Maidenhead grid4 whitelist."
    )
    parser.add_argument(
        "--buffer", type=float, default=4.0,
        help="Buffer distance in degrees (default: 4.0 ≈ 2 grid-widths)"
    )
    parser.add_argument(
        "--input",
        help=(f"Local Natural Earth GeoJSON file to use instead of fetching from web. "
              f"If omitted, checks for {_LOCAL_GEOJSON_NAME} beside this script, "
              f"then fetches from GitHub.")
    )
    parser.add_argument(
        "--output",
        help="Output path (default: land_grids.txt beside this script)"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print every accepted grid square"
    )
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve() if args.input else None
    out_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else Path(__file__).parent / "land_grids.txt"
    )

    print(f"build_land_grids.py v{__version__}")
    print(f"  Buffer:  {args.buffer}°  (≈ {args.buffer/2:.0f} Maidenhead grid-widths)")
    print(f"  Output:  {out_path}")
    print()

    geojson  = load_land_geojson(input_path)
    buffered = build_buffered_land(geojson, args.buffer)
    grids    = classify_grids(buffered, args.verbose)
    write_output(grids, out_path, args.buffer)

    print()
    print("Done.  Run adif_map.py with --overlays-only --overlay grids to use it.")


if __name__ == "__main__":
    main()
