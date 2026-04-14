"""
adif_extract.py
===============
Extract and inspect QSOs from a QRZ ADIF export, with optional date-range
filtering.  Produces an inspection CSV suitable for review in Excel and,
after filling in the `field` and `new_value` columns, direct use as input
to resolve_qrz_discrepancies.py (--input-csv).

Usage
-----
    # Extract all QSOs to CSV
    python adif_extract.py --adif qrz_export.adi

    # Single activation date (shorthand for --after DATE --before DATE)
    python adif_extract.py --adif qrz_export.adi --date 2026-03-28

    # Filter to a date range
    python adif_extract.py --adif qrz_export.adi --after 2026-03-01 --before 2026-04-01

    # Custom output filename
    python adif_extract.py --adif qrz_export.adi --date 20260328 --output-csv pota_scenic.csv

Date formats accepted by --after / --before / --date
-----------------------------------------------------
    YYYY-MM-DD   (ISO, e.g. 2026-03-28)
    YYYYMMDD     (ADIF compact, e.g. 20260328)

--date is a shorthand for --after DATE --before DATE (single-day extract).
It is mutually exclusive with --after and --before.

Output CSV columns
------------------
    field          — blank; fill in the ADIF field name to correct, e.g.:
                       MY_GRIDSQUARE, MY_LAT, MY_LON, MY_STATE, MY_CNTY,
                       MY_CITY, MY_COUNTRY, MY_CQ_ZONE, MY_ITU_ZONE,
                       MY_DXCC, MY_NAME, COMMENT
    qso_date       — YYYY-MM-DD (human-readable)
    time_on        — HH:MM      (human-readable)
    call           — contacted station callsign
    MY_GRIDSQUARE  — your station's grid square as logged
    MY_LAT         — your station latitude (ADIF format)
    MY_LON         — your station longitude (ADIF format)
    MY_STATE       — your station state
    MY_CNTY        — your station county (ADIF format, e.g. WA,King)
    MY_CITY        — your station city
    MY_COUNTRY     — your station country
    MY_CQ_ZONE     — your CQ zone
    MY_ITU_ZONE    — your ITU zone
    MY_DXCC        — your DXCC entity code
    MY_NAME        — your name as logged
    COMMENT        — QRZ logbook comment
    new_value      — blank; fill in the corrected value

The `field` and `new_value` columns are intentionally blank so you can
duplicate rows in Excel for multiple fields, or delete rows you don't need
to correct, then feed the result back to resolve_qrz_discrepancies.py.

Requirements
------------
    pip install requests      (via qrz_common)

See qrz_common.py for shared ADIF parsing, date normalisation, and grid
utilities.

2026-04-14 Jim Carson (WT8P)
"""

import argparse
import csv
import logging
import re
import sys
from pathlib import Path

import qrz_common as qrz

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixed MY_ inspection columns (in display order)
# ---------------------------------------------------------------------------
MY_INSPECT_FIELDS = [
    "MY_GRIDSQUARE",
    "MY_LAT",
    "MY_LON",
    "MY_STATE",
    "MY_CNTY",
    "MY_CITY",
    "MY_COUNTRY",
    "MY_CQ_ZONE",
    "MY_ITU_ZONE",
    "MY_DXCC",
    "MY_NAME",
    "COMMENT",
]

# CSV column order — field and new_value are placeholders for corrections
CSV_COLUMNS = (
    ["field", "qso_date", "time_on", "call"]
    + MY_INSPECT_FIELDS
    + ["new_value"]
)


# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------

def _normalise_date_arg(value: str, argname: str) -> str:
    """
    Accept YYYY-MM-DD or YYYYMMDD and return YYYYMMDD for comparison
    against QSO_DATE values from the ADIF file.
    """
    v = value.strip()
    if re.fullmatch(r"\d{8}", v):
        return v
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v.replace("-", "")
    log.error("--%s value %r is not a recognised date (use YYYY-MM-DD or YYYYMMDD)",
              argname, value)
    sys.exit(1)


def _in_range(qso_date: str, after: str, before: str) -> bool:
    """
    Return True if qso_date (YYYYMMDD) falls within [after, before] inclusive.
    Empty bound means unbounded on that side.
    """
    if after and qso_date < after:
        return False
    if before and qso_date > before:
        return False
    return True


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract(
    records: list[dict],
    after: str = "",
    before: str = "",
) -> list[dict]:
    """
    Filter records by date range and return a list of row dicts ready for
    CSV output.  Dates are converted to human-readable YYYY-MM-DD / HH:MM.
    """
    rows: list[dict] = []

    for rec in records:
        qso_date_raw = rec.get("QSO_DATE", "").strip()
        time_on_raw  = rec.get("TIME_ON",  "").strip()

        # Normalise to YYYYMMDD/HHMM for filtering
        adif_date, adif_time = qrz.parse_qso_datetime(qso_date_raw, time_on_raw)

        if not _in_range(adif_date, after, before):
            continue

        # Convert to human-readable for the CSV
        hr_date, hr_time = qrz.format_qso_datetime(adif_date, adif_time)

        row: dict = {
            "field":    "",
            "qso_date": hr_date,
            "time_on":  hr_time,
            "call":     rec.get("CALL", "").upper().strip(),
            "new_value": "",
        }
        for field in MY_INSPECT_FIELDS:
            row[field] = rec.get(field, "")

        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote %d rows to %s", len(rows), path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Extract QSOs from a QRZ ADIF export to an inspection CSV. "
            "The CSV includes MY_ fields and COMMENT for spot-checking, "
            "plus blank 'field' and 'new_value' columns so it can be used "
            "directly as input to resolve_qrz_discrepancies.py after editing."
        )
    )
    p.add_argument("--adif", required=True,
        help="QRZ ADIF export file (e.g. qrz_export.adi)")

    date_group = p.add_mutually_exclusive_group()
    date_group.add_argument("--date", default="",
        metavar="DATE",
        help="Extract a single date — shorthand for --after DATE --before DATE "
             "(YYYY-MM-DD or YYYYMMDD)")
    date_group.add_argument("--after", default="",
        metavar="DATE",
        help="Include QSOs on or after this date (YYYY-MM-DD or YYYYMMDD, inclusive)")

    p.add_argument("--before", default="",
        metavar="DATE",
        help="Include QSOs on or before this date (YYYY-MM-DD or YYYYMMDD, inclusive). "
             "Not used with --date.")
    p.add_argument("--output-csv", default="adif_extract.csv",
        help="Output CSV filename (default: adif_extract.csv)")
    return p


def main() -> None:
    args    = build_parser().parse_args()
    adif    = Path(args.adif)
    out_csv = Path(args.output_csv)

    if not adif.exists():
        log.error("File not found: %s", adif)
        sys.exit(1)

    # --date is shorthand for --after DATE --before DATE
    if args.date:
        if args.before:
            log.error("--before cannot be used with --date")
            sys.exit(1)
        date_norm = _normalise_date_arg(args.date, "date")
        after  = date_norm
        before = date_norm
    else:
        after  = _normalise_date_arg(args.after,  "after")  if args.after  else ""
        before = _normalise_date_arg(args.before, "before") if args.before else ""

    if after and before and after > before:
        log.error("--after (%s) is later than --before (%s)", after, before)
        sys.exit(1)

    log.info("=== ADIF Extract ===")
    log.info("ADIF       : %s", adif)
    if args.date:
        log.info("Date       : %s", f"{after[:4]}-{after[4:6]}-{after[6:]}")
    else:
        if after:
            log.info("After      : %s", f"{after[:4]}-{after[4:6]}-{after[6:]}")
        if before:
            log.info("Before     : %s", f"{before[:4]}-{before[4:6]}-{before[6:]}")

    records = qrz.parse_adif_file(adif)
    rows    = extract(records, after=after, before=before)

    if not rows:
        log.warning("No QSOs matched the specified criteria.")
        sys.exit(0)

    log.info("Matched    : %d QSOs", len(rows))
    write_csv(rows, out_csv)
    log.info("=== Done. ===")
    log.info(
        "Fill in 'field' and 'new_value' columns, then pass to "
        "resolve_qrz_discrepancies.py with --input-csv %s --my-station "
        "[--derive-coords]", out_csv
    )


if __name__ == "__main__":
    main()
