"""
resolve_qrz_discrepancies.py
============================
Resolves location field discrepancies in your QRZ logbook by reading the
discrepancy report exported from QRZ's Awards pages and applying the other
party's values via the QRZ API.

For each discrepancy:
  1. Looks up the QSO in your exported QRZ ADIF file to obtain its logid.
  2. Applies the correction via ACTION=INSERT OPTION=REPLACE, which works on
     both unconfirmed and confirmed/award-locked records.

Dry-run mode is the DEFAULT — no changes are written to QRZ unless you pass
--update explicitly.

Usage
-----
    # Preview changes (dry-run is default)
    python resolve_qrz_discrepancies.py \
        --xlsx  qrz_errors.xlsx \
        --adif  qrz_export.adi \
        --call  WT8P \
        [--key  YOUR-API-KEY]

    # Apply changes
    python resolve_qrz_discrepancies.py \
        --xlsx  qrz_errors.xlsx \
        --adif  qrz_export.adi \
        --call  WT8P \
        --update

    # Correct your own station's fields from a CSV
    python resolve_qrz_discrepancies.py \
        --input-csv my_corrections.csv \
        --adif  qrz_export.adi \
        --call  WT8P \
        --my-station \
        [--update]

    # Derive coordinates from a grid square and update all three MY_ fields
    python resolve_qrz_discrepancies.py \
        --input-csv my_corrections.csv \
        --adif  qrz_export.adi \
        --call  WT8P \
        --my-station \
        --derive-coords \
        [--grid-precision 6]

CSV field keywords (--input-csv)
---------------------------------
    GRIDSQUARE, STATE, CNTY              — other party's fields
    MY_GRIDSQUARE, MY_STATE, MY_CNTY    — your station's fields (or use
                                           bare names with --my-station)
    MY_LAT, MY_LON                       — your station coordinates
    MY_LOC                               — lat+lon on one row: "lat,lon"
                                           (quoted in CSV), e.g.:
                                           MY_LOC,...,"47.5625,-122.058"
                                           With --derive-coords also updates
                                           MY_GRIDSQUARE.
    MY_GRIDSQUARE (with --derive-coords) — also updates MY_LAT and MY_LON
                                           from the grid centre.
    COMMENT                              — QRZ logbook comment (free text);
                                           valid in both --my-station and
                                           other-party modes.

    # API key can also be stored in WT8P.key (or TF_WT8P.key for TF/WT8P)

Requirements
------------
    pip install pandas openpyxl requests

See qrz_common.py for shared ADIF parsing, API, and field conversion logic.

2026-03-19 Jim Carson (WT8P)
"""

import argparse
import csv
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

import qrz_common as qrz

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — resolve-specific field mappings
# ---------------------------------------------------------------------------

# Excel sheet name -> ADIF field (other party's fields)
SHEET_TO_ADIF_FIELD = {
    "Grids":  "GRIDSQUARE",
    "State":  "STATE",
    "County": "CNTY",
}

# Excel sheet name -> ADIF field (my station's fields)
SHEET_TO_MY_ADIF_FIELD = {
    "Grids":  "MY_GRIDSQUARE",
    "State":  "MY_STATE",
    "County": "MY_CNTY",
}

# MY_ coordinate fields (CSV only, no Excel sheet equivalent)
# MY_LOC is a virtual field: "lat,lon" in one quoted CSV cell — expanded
# to MY_LAT + MY_LON (and optionally MY_GRIDSQUARE) during CSV loading.
MY_COORD_FIELDS = {"MY_LAT", "MY_LON", "MY_LOC"}

# COMMENT is a standalone field, valid in both --my-station and other-party
# modes.  It is not prefixed MY_ and has no Excel sheet equivalent.
COMMENT_FIELDS = {"COMMENT"}

# All valid ADIF fields across both modes
ALL_ADIF_FIELDS = (
    set(SHEET_TO_ADIF_FIELD.values())
    | set(SHEET_TO_MY_ADIF_FIELD.values())
    | MY_COORD_FIELDS
    | COMMENT_FIELDS
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Discrepancy:
    sheet:       str
    adif_field:  str
    qso_date:    str   # YYYYMMDD
    time_on:     str   # HHMM
    qso_with:    str
    your_value:  str
    other_value: str
    bad_data:    bool = False


@dataclass
class Resolution:
    discrepancy: Discrepancy
    logid:       Optional[str]
    old_value:   str
    new_value:   str
    status:      str   # updated|dry_run|no_change|skipped_bad_data|no_match|error
    error_msg:   str = ""


# ---------------------------------------------------------------------------
# Date/time parser
# ---------------------------------------------------------------------------

def _parse_date_time(dt_val) -> tuple[str, str]:
    """
    Parse a date/time value from the Excel discrepancy report.
    Delegates to qrz.parse_qso_datetime which handles both ADIF compact
    (YYYYMMDD / HHMM) and human-readable (YYYY-MM-DD HH:MM[:SS]) formats.
    pandas Timestamps are converted to ISO string first.
    """
    if isinstance(dt_val, pd.Timestamp):
        return qrz.parse_qso_datetime(dt_val.strftime("%Y-%m-%d"),
                                      dt_val.strftime("%H:%M"))
    return qrz.parse_qso_datetime(str(dt_val).strip())


def _row_to_discrepancy(adif_field: str, sheet_name: str, row_date,
                         qso_with: str, your_val: str,
                         other_val: str, note: str) -> Optional[Discrepancy]:
    bad_data  = (note or "").strip().lower() == "bad data"
    other_val = other_val.strip()
    your_val  = your_val.strip()
    if not other_val or other_val.lower() in ("nan", "none", ""):
        return None
    qso_date, time_on = _parse_date_time(row_date)
    return Discrepancy(
        sheet=sheet_name,
        adif_field=adif_field,
        qso_date=qso_date,
        time_on=time_on,
        qso_with=qso_with.strip().upper(),
        your_value=your_val if your_val.lower() not in ("nan", "none", "") else "",
        other_value=other_val,
        bad_data=bad_data,
    )


# ---------------------------------------------------------------------------
# Excel loader
# ---------------------------------------------------------------------------

def load_discrepancies(xlsx_path: Path, my_station: bool = False) -> list[Discrepancy]:
    field_map = SHEET_TO_MY_ADIF_FIELD if my_station else SHEET_TO_ADIF_FIELD
    xl        = pd.ExcelFile(xlsx_path)
    results: list[Discrepancy] = []

    for sheet_name, adif_field in field_map.items():
        if sheet_name not in xl.sheet_names:
            log.warning("Sheet '%s' not found — skipping.", sheet_name)
            continue

        df = xl.parse(sheet_name)
        # Match column headers by prefix to handle "You Entered county" etc.
        col_map = {}
        for col in df.columns:
            cl = str(col).strip().lower()
            if cl.startswith("you entered"):
                col_map["you_entered"] = col
            elif cl.startswith("other party entered"):
                col_map["other_party_entered"] = col

        for _, row in df.iterrows():
            d = _row_to_discrepancy(
                adif_field=adif_field,
                sheet_name=sheet_name,
                row_date=row["QSO Date"],
                qso_with=str(row["QSO With"]),
                your_val=str(row.get(col_map.get("you_entered", "You Entered"), "")),
                other_val=str(row.get(col_map.get("other_party_entered",
                                                   "Other Party Entered"), "")),
                note=str(row.get("Note", "")),
            )
            if d:
                results.append(d)

    bad  = sum(1 for d in results if d.bad_data)
    mode = "MY_ fields" if my_station else "other party fields"
    log.info("Loaded %d discrepancy rows [%s] (%d Bad Data, %d to process)",
             len(results), mode, bad, len(results) - bad)
    return results


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def load_discrepancies_csv(csv_path: Path,
                            my_station: bool = False,
                            derive_coords: bool = False,
                            grid_precision: int = 6) -> list[Discrepancy]:
    """
    Load discrepancies from a flat CSV file.

    Required columns : field, qso_date, qso_with, new_value
    Optional columns : you_entered, de, note

    Blank lines and lines whose first field starts with '#' are silently
    skipped, allowing comments and section separators in the CSV.

    'field' values   : GRIDSQUARE, STATE, CNTY (promoted to MY_ if --my-station)
                       MY_GRIDSQUARE, MY_STATE, MY_CNTY, MY_LAT, MY_LON
                       MY_LOC  — lat+lon together as a quoted "lat,lon" value;
                                 expands to MY_LAT + MY_LON rows, and with
                                 --derive-coords also MY_GRIDSQUARE.
                       MY_GRIDSQUARE + --derive-coords also emits MY_LAT/MY_LON.
                       COMMENT — QRZ logbook comment field; accepted in both
                                 --my-station and other-party modes.

    CNTY / MY_CNTY   : Supply the QRZ display format, quoted because it contains
                       a comma: "Hartford County, CT".  The word 'County' (and
                       'Borough' for Alaska, 'Parish' for Louisiana) is stripped
                       and the value is converted to ADIF format (CT,Hartford)
                       before being written to QRZ.

    Example:
        field,qso_date,qso_with,new_value
        # Other party's fields
        GRIDSQUARE,2024-07-06 20:28:00,VE5URQ,DN69
        STATE,2017-10-28 15:14:00,WA4JS,TEN
        CNTY,2025-08-11 02:22:00,VE5URQ,"Polk County, MN"
        # Own station fields
        MY_LOC,2025-08-11 02:22:00,KL4RL,"47.5625,-122.058"
        MY_GRIDSQUARE,2025-08-11 02:22:00,KL4RL,CN87xn
        MY_CNTY,2025-08-11 02:22:00,KL4RL,"King County, WA"
    """
    COL_ALIASES = {
        "field": "field", "adif_field": "field",
        "qso_date": "qso_date", "date": "qso_date",
        "qso_with": "qso_with", "call": "qso_with",
        "you_entered": "you_entered", "your_value": "you_entered",
        "other_party_entered": "other_party_entered",
        "other_value": "other_party_entered",
        "other": "other_party_entered",
        "new_value": "other_party_entered",
        "note": "note", "notes": "note", "de": "de",
    }
    MY_UPGRADE = {"GRIDSQUARE": "MY_GRIDSQUARE",
                  "STATE": "MY_STATE", "CNTY": "MY_CNTY"}
    REVERSE_MAP = {v: k for k, v in {**SHEET_TO_ADIF_FIELD,
                                      **SHEET_TO_MY_ADIF_FIELD}.items()}
    results: list[Discrepancy] = []

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            log.error("CSV appears empty: %s", csv_path)
            return results

        col_map = {col: COL_ALIASES.get(col.strip().lower())
                   for col in reader.fieldnames}
        missing = {"field", "qso_date", "qso_with",
                   "other_party_entered"} - set(col_map.values())
        if missing:
            log.error("CSV missing required columns: %s", missing)
            sys.exit(1)

        for i, raw_row in enumerate(reader, start=2):
            # Skip blank rows and comment rows (first field starts with #)
            first_val = (raw_row.get(reader.fieldnames[0]) or "").strip()
            if not first_val or first_val.startswith("#"):
                continue

            row = {canonical: raw_row[orig]
                   for orig, canonical in col_map.items()
                   if canonical and orig in raw_row}

            adif_field = row.get("field", "").strip().upper()
            if my_station and adif_field in MY_UPGRADE:
                adif_field = MY_UPGRADE[adif_field]

            if adif_field not in ALL_ADIF_FIELDS:
                log.warning("Row %d: unknown field %r — skipping", i, adif_field)
                continue

            raw_value  = row.get("other_party_entered", "").strip()
            sheet_name = REVERSE_MAP.get(adif_field, adif_field)
            common_kw  = dict(
                adif_field=adif_field,
                sheet_name=sheet_name,
                row_date=row.get("qso_date", ""),
                qso_with=row.get("qso_with", ""),
                your_val=row.get("you_entered", ""),
                note=row.get("note", ""),
            )

            # ------------------------------------------------------------------
            # MY_LOC: "lat,lon" in one quoted cell → expand to MY_LAT + MY_LON
            # and optionally MY_GRIDSQUARE (--derive-coords)
            # ------------------------------------------------------------------
            if adif_field == "MY_LOC":
                parts = [p.strip() for p in raw_value.split(",", 1)]
                if len(parts) != 2:
                    log.warning(
                        "Row %d: MY_LOC value %r must be 'lat,lon' "
                        "(tip: quote the cell in CSV)", i, raw_value
                    )
                    continue
                lat_str, lon_str = parts
                for sub_field, sub_val in (("MY_LAT", lat_str),
                                            ("MY_LON", lon_str)):
                    d = _row_to_discrepancy(
                        **{**common_kw,
                           "adif_field": sub_field,
                           "sheet_name": sub_field,
                           "other_val":  sub_val}
                    )
                    if d:
                        results.append(d)
                if derive_coords:
                    try:
                        lat = float(lat_str)
                        lon = float(lon_str)
                        grid = qrz.latlon_to_grid(lat, lon, grid_precision)
                    except ValueError as exc:
                        log.warning("Row %d: cannot derive grid from MY_LOC: %s", i, exc)
                    else:
                        d = _row_to_discrepancy(
                            **{**common_kw,
                               "adif_field": "MY_GRIDSQUARE",
                               "sheet_name": "MY_GRIDSQUARE",
                               "other_val":  grid}
                        )
                        if d:
                            results.append(d)
                continue

            # ------------------------------------------------------------------
            # MY_GRIDSQUARE + --derive-coords: also emit MY_LAT and MY_LON
            # ------------------------------------------------------------------
            if adif_field == "MY_GRIDSQUARE" and derive_coords:
                d = _row_to_discrepancy(**{**common_kw, "other_val": raw_value})
                if d:
                    results.append(d)
                try:
                    lat, lon = qrz.grid_to_latlon(raw_value)
                except ValueError as exc:
                    log.warning("Row %d: cannot derive coords from %r: %s",
                                i, raw_value, exc)
                else:
                    for sub_field, sub_val in (("MY_LAT", str(lat)),
                                                ("MY_LON", str(lon))):
                        d = _row_to_discrepancy(
                            **{**common_kw,
                               "adif_field": sub_field,
                               "sheet_name": sub_field,
                               "other_val":  sub_val}
                        )
                        if d:
                            results.append(d)
                continue

            # ------------------------------------------------------------------
            # Standard single-field row
            # ------------------------------------------------------------------
            d = _row_to_discrepancy(**{**common_kw, "other_val": raw_value})
            if d:
                results.append(d)

    bad  = sum(1 for d in results if d.bad_data)
    mode = "MY_ fields" if my_station else "other party fields"
    log.info("Loaded %d rows from CSV [%s] (%d Bad Data, %d to process)",
             len(results), mode, bad, len(results) - bad)
    return results


# ---------------------------------------------------------------------------
# Core resolution logic
# ---------------------------------------------------------------------------

def resolve(
    discrepancies: list[Discrepancy],
    qso_index:     dict[tuple, dict],
    api_key:       str,
    callsign:      str,
    dry_run:       bool,
) -> list[Resolution]:

    actionable = [d for d in discrepancies if not d.bad_data]
    log.info("Processing %d actionable discrepancies…", len(actionable))
    resolutions: list[Resolution] = []

    for d in actionable:
        key = (d.qso_with, d.qso_date, d.time_on)
        qso = qso_index.get(key)

        if qso is None:
            log.warning("No match : %-12s  %s %s", d.qso_with, d.qso_date, d.time_on)
            resolutions.append(Resolution(
                discrepancy=d, logid=None,
                old_value=d.your_value, new_value=d.other_value,
                status="no_match",
            ))
            continue

        logid = qso.get("APP_QRZLOG_LOGID", "")
        if logid:
            try:
                logid = str(int(float(logid)))
            except ValueError:
                pass

        old_value = qso.get(d.adif_field, "")

        # Convert display format to ADIF format
        if d.adif_field in ("CNTY", "MY_CNTY"):
            new_value = qrz.convert_cnty(d.other_value)
        elif d.adif_field in ("STATE", "MY_STATE"):
            new_value = qrz.convert_state(d.other_value)
        elif d.adif_field in ("MY_LAT", "MY_LON"):
            try:
                new_value = qrz.validate_coord(d.other_value, d.adif_field)
            except ValueError as exc:
                log.error("Invalid coordinate logid=%s %s: %s", logid, d.adif_field, exc)
                resolutions.append(Resolution(
                    discrepancy=d, logid=logid,
                    old_value=old_value, new_value=d.other_value,
                    status="error", error_msg=str(exc),
                ))
                continue
        else:
            new_value = d.other_value

        if not logid:
            log.warning("No logid : %-12s  %s %s", d.qso_with, d.qso_date, d.time_on)
            resolutions.append(Resolution(
                discrepancy=d, logid=None,
                old_value=old_value, new_value=new_value,
                status="error", error_msg="APP_QRZLOG_LOGID missing from ADIF export",
            ))
            continue

        if old_value == new_value:
            log.info("No-op    logid=%-8s  %-12s  %s: already %r",
                     logid, d.qso_with, d.adif_field, new_value)
            resolutions.append(Resolution(
                discrepancy=d, logid=logid,
                old_value=old_value, new_value=new_value,
                status="no_change",
            ))
            continue

        if dry_run:
            log.info("[DRY-RUN] logid=%-8s  %-12s  %s: %r -> %r",
                     logid, d.qso_with, d.adif_field, old_value, new_value)
            resolutions.append(Resolution(
                discrepancy=d, logid=logid,
                old_value=old_value, new_value=new_value,
                status="dry_run",
            ))
            continue

        # Apply correction via INSERT OPTION=REPLACE
        updated = dict(qso)
        updated[d.adif_field] = new_value

        try:
            result = qrz.qrz_replace(api_key, callsign, updated,
                                      user_agent="QRZDiscrepancyResolver/2.0")
            if result.get("RESULT") == "REPLACE":
                log.info("Replaced logid=%-8s  %-12s  %s: %r -> %r",
                         logid, d.qso_with, d.adif_field, old_value, new_value)
                resolutions.append(Resolution(
                    discrepancy=d, logid=logid,
                    old_value=old_value, new_value=new_value,
                    status="updated",
                ))
                # Update the index so subsequent corrections for the same QSO
                # accumulate rather than each overwriting the previous field.
                qso_index[key][d.adif_field] = new_value
            else:
                log.error("Unexpected result logid=%s: %s", logid, result)
                resolutions.append(Resolution(
                    discrepancy=d, logid=logid,
                    old_value=old_value, new_value=new_value,
                    status="error",
                    error_msg=f"Unexpected RESULT: {result.get('RESULT')}",
                ))
        except RuntimeError as exc:
            log.error("Failed   logid=%-8s  %s", logid, exc)
            resolutions.append(Resolution(
                discrepancy=d, logid=logid,
                old_value=old_value, new_value=new_value,
                status="error", error_msg=str(exc),
            ))

        time.sleep(qrz.API_PAUSE_SEC)

    # Record bad-data skips
    for d in discrepancies:
        if d.bad_data:
            resolutions.append(Resolution(
                discrepancy=d, logid=None,
                old_value=d.your_value, new_value=d.other_value,
                status="skipped_bad_data",
            ))

    return resolutions


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

CSV_HEADERS = [
    "sheet", "adif_field", "qso_with", "qso_date", "time_on",
    "logid", "old_value", "new_value", "status", "error_msg",
]


def write_csv(resolutions: list[Resolution], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for r in resolutions:
            writer.writerow({
                "sheet":      r.discrepancy.sheet,
                "adif_field": r.discrepancy.adif_field,
                "qso_with":   r.discrepancy.qso_with,
                "qso_date":   r.discrepancy.qso_date,
                "time_on":    r.discrepancy.time_on,
                "logid":      r.logid or "",
                "old_value":  r.old_value,
                "new_value":  r.new_value,
                "status":     r.status,
                "error_msg":  r.error_msg,
            })
    counts: dict[str, int] = {}
    for r in resolutions:
        counts[r.status] = counts.get(r.status, 0) + 1
    log.info("Results written to %s", path)
    for status, n in sorted(counts.items()):
        log.info("  %-24s : %d", status, n)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve QRZ logbook discrepancies via the QRZ API. "
                    "Dry-run mode is the default — pass --update to write changes."
    )
    input_group = p.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--xlsx",
        help="QRZ discrepancy Excel file (e.g. qrz_errors.xlsx)")
    input_group.add_argument("--input-csv",
        help="Flat CSV input — columns: field, qso_date, qso_with, "
             "new_value. Optional: you_entered, note, de.")
    p.add_argument("--adif", required=True,
        help="Your QRZ ADIF export (must contain APP_QRZLOG_LOGID)")
    p.add_argument("--call", required=True,
        help="Your callsign (e.g. WT8P or TF/WT8P)")
    p.add_argument("--key", default=None,
        help="QRZ API key. Optional if <CALLSIGN>.key file exists "
             "(use _ for / in callsign, e.g. TF_WT8P.key)")
    p.add_argument("--my-station", action="store_true",
        help="Correct your own station fields (MY_GRIDSQUARE, MY_STATE, "
             "MY_CNTY, MY_LAT, MY_LON) instead of the other party's fields")
    p.add_argument("--update", action="store_true",
        help="Apply changes to QRZ (default is dry-run — preview only)")
    p.add_argument("--derive-coords", action="store_true",
        help="Derive related fields automatically: MY_LOC also updates "
             "MY_GRIDSQUARE; MY_GRIDSQUARE also updates MY_LAT and MY_LON "
             "(from grid centre). Only applies to --input-csv.")
    p.add_argument("--grid-precision", type=int, default=6,
        choices=[4, 6, 8],
        help="Maidenhead grid precision when deriving a grid from coordinates "
             "(4, 6, or 8 chars; default 6 ≈ 460 m). Only used with "
             "--derive-coords.")
    p.add_argument("--output-csv", default="resolved_log.csv",
        help="Output CSV log (default: resolved_log.csv)")
    return p


def main() -> None:
    args    = build_parser().parse_args()
    adif    = Path(args.adif)
    out_csv = Path(args.output_csv)
    dry_run = not args.update   # dry-run is the default

    if not adif.exists():
        log.error("File not found: %s", adif)
        sys.exit(1)

    if args.xlsx:
        input_path = Path(args.xlsx)
        if not input_path.exists():
            log.error("File not found: %s", input_path)
            sys.exit(1)
        log.info("=== QRZ Discrepancy Resolver ===")
        log.info("Excel    : %s", input_path)
        discrepancies = load_discrepancies(input_path, my_station=args.my_station)
        if args.derive_coords:
            log.warning("--derive-coords has no effect with --xlsx input")
    else:
        input_path = Path(args.input_csv)
        if not input_path.exists():
            log.error("File not found: %s", input_path)
            sys.exit(1)
        log.info("=== QRZ Discrepancy Resolver ===")
        log.info("CSV      : %s", input_path)
        discrepancies = load_discrepancies_csv(
            input_path,
            my_station=args.my_station,
            derive_coords=args.derive_coords,
            grid_precision=args.grid_precision,
        )

    log.info("ADIF     : %s", adif)
    log.info("Callsign : %s", args.call.upper())
    log.info("Mode     : %s", "MY_ station fields" if args.my_station
             else "other party fields")
    log.info("Dry run  : %s", dry_run)
    if args.derive_coords:
        log.info("Derive coords: yes (grid precision=%d)", args.grid_precision)

    api_key = qrz.load_api_key(args.key, args.call)

    if not discrepancies:
        log.info("No discrepancies to process.")
        return

    qso_records = qrz.parse_adif_file(adif)
    qso_index   = qrz.build_index(qso_records)
    log.info("QSO index built with %d entries.", len(qso_index))

    resolutions = resolve(discrepancies, qso_index, api_key,
                          args.call.upper(), dry_run)

    write_csv(resolutions, out_csv)
    log.info("=== Done. ===")
    if dry_run:
        log.info("(Dry-run mode — re-run with --update to apply changes)")


if __name__ == "__main__":
    main()
