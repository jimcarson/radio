#!/usr/bin/env python3
"""
gsak_rename.py — Normalize GSAK polygon filenames to underscore convention
===========================================================================
Renames Canadian county/district polygon files from GSAK's space-based
naming to the underscore convention used by the US files, so that
gsak_counties.py can process both with a single code path.

Transformation rules (applied in order):
  1. Spaces          → underscores      "Big Lakes"         → "Big_Lakes"
  2. Apostrophes     → stripped         "St. George's"      → "St._Georges"
  3. Dots kept       as-is              "Lac Ste. Anne"     → "Lac_Ste._Anne"
  4. Dashes kept     as-is              "Saguenay-Lac_St_Jean" (after spaces→_)
  5. Accented chars  → ASCII equivalent "Québec"            → "Quebec"
     (directory names only — applied to dir scan, not needed for files
      since Québec directory has no polygon files)

Usage:
    # Dry run — show what would be renamed, make no changes
    python gsak_rename.py --gsak-dir "C:/GSAK/Data/Counties" --country ca --dry-run

    # Apply renames
    python gsak_rename.py --gsak-dir "C:/GSAK/Data/Counties" --country ca

    # Verbose dry run
    python gsak_rename.py --gsak-dir "C:/GSAK/Data/Counties" --country ca --dry-run --verbose

The script is idempotent — already-normalized files are skipped silently.
Run --dry-run first to review before committing.
"""

__version__ = "1.0.0"  # initial release

import argparse
import sys
import unicodedata
from pathlib import Path


# ---------------------------------------------------------------------------
# Character normalization
# ---------------------------------------------------------------------------

def strip_accents(s: str) -> str:
    """Convert accented characters to their ASCII base equivalents."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def normalize_stem(stem: str) -> str:
    """
    Apply all rename transformations to a filename stem (no extension).

    Transformations:
      - Accents stripped         (Québec → Quebec)
      - Spaces → underscores    (Big Lakes → Big_Lakes)
      - Apostrophes stripped    (George's → Georges)
      - Dots, dashes preserved  (Lac Ste. Anne → Lac_Ste._Anne)
    """
    name = strip_accents(stem)
    name = name.replace("'", "")     # strip apostrophes before spaces→_
    name = name.replace(' ', '_')    # spaces to underscores
    return name


# ---------------------------------------------------------------------------
# Rename logic
# ---------------------------------------------------------------------------

def collect_renames(country_dir: Path,
                    verbose: bool = False) -> list[tuple[Path, Path]]:
    """
    Walk all state/province subdirectories under country_dir and collect
    (old_path, new_path) pairs for files that need renaming.

    Returns list of (src, dst) Path pairs where src != dst.
    """
    renames = []
    skipped_dirs = []

    subdirs = sorted(d for d in country_dir.iterdir() if d.is_dir())
    if not subdirs:
        print(f"  Warning: no subdirectories found under {country_dir}")
        return []

    for subdir in subdirs:
        txt_files = sorted(subdir.glob('*.txt'))
        for src in txt_files:
            if src.stem.lower() == 'version':
                continue
            new_stem = normalize_stem(src.stem)
            if new_stem == src.stem:
                if verbose:
                    print(f"  skip  [{subdir.name}] {src.name}")
                continue
            dst = src.with_name(new_stem + src.suffix)
            if dst.exists() and dst != src:
                print(f"  WARNING: target already exists, skipping: "
                      f"{src.name} → {dst.name}")
                continue
            renames.append((src, dst))

    return renames


def apply_renames(renames: list[tuple[Path, Path]],
                  dry_run: bool = True,
                  verbose: bool = False) -> tuple[int, int]:
    """
    Apply or preview renames. Returns (renamed_count, skipped_count).
    """
    renamed = 0
    skipped = 0

    # Group by parent for cleaner output
    by_dir: dict[Path, list] = {}
    for src, dst in renames:
        by_dir.setdefault(src.parent, []).append((src, dst))

    for parent in sorted(by_dir.keys()):
        pairs = by_dir[parent]
        print(f"\n  [{parent.name}]  {len(pairs)} file(s)")
        for src, dst in pairs:
            action = "would rename" if dry_run else "renaming"
            print(f"    {action}: {src.name!r}")
            print(f"         → {dst.name!r}")
            if not dry_run:
                try:
                    src.rename(dst)
                    renamed += 1
                except OSError as exc:
                    print(f"    ERROR: {exc}")
                    skipped += 1
            else:
                renamed += 1   # count as "would rename" for summary

    return renamed, skipped


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Normalize GSAK polygon filenames to underscore convention.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview changes for Canadian files
  python gsak_rename.py --gsak-dir "C:/GSAK/Data/Counties" --country ca --dry-run

  # Apply renames
  python gsak_rename.py --gsak-dir "C:/GSAK/Data/Counties" --country ca

  # Also normalize US files (already correct, should show 0 renames)
  python gsak_rename.py --gsak-dir "C:/GSAK/Data/Counties" --country usa --dry-run
""",
    )
    parser.add_argument(
        '--gsak-dir', required=True,
        help='Root GSAK Counties directory (contains usa/ and/or ca/ subdirs)',
    )
    parser.add_argument(
        '--country', default='ca',
        help='Country subdirectory to process (default: ca)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Preview renames without making any changes',
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Show files that are already correctly named (skipped)',
    )
    args = parser.parse_args()

    gsak_dir    = Path(args.gsak_dir).expanduser().resolve()
    country_dir = gsak_dir / args.country

    if not gsak_dir.exists():
        sys.exit(f"Directory not found: {gsak_dir}")

    # Case-insensitive country dir search
    if not country_dir.exists():
        for d in gsak_dir.iterdir():
            if d.is_dir() and d.name.lower() == args.country.lower():
                country_dir = d
                break
        else:
            sys.exit(
                f"Country directory '{args.country}' not found under {gsak_dir}\n"
                f"Available: {[d.name for d in gsak_dir.iterdir() if d.is_dir()]}"
            )

    mode = "DRY RUN — no files will be changed" if args.dry_run else "LIVE — files will be renamed"
    print(f"gsak_rename.py  [{mode}]")
    print(f"Directory: {country_dir}")

    renames = collect_renames(country_dir, verbose=args.verbose)

    if not renames:
        print("\nNo files need renaming — all already normalized.")
        return

    print(f"\nFiles to rename: {len(renames)}")
    renamed, skipped = apply_renames(renames, dry_run=args.dry_run,
                                     verbose=args.verbose)

    print()
    if args.dry_run:
        print(f"Dry run complete: {renamed} file(s) would be renamed.")
        print("Run without --dry-run to apply changes.")
    else:
        print(f"Done: {renamed} renamed, {skipped} skipped (errors).")


if __name__ == '__main__':
    main()
