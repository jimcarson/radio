"""
resolve_qrz_discrepancies.py
============================
Reads the QRZ-generated discrepancy Excel file (Grids, State, County sheets),
skips rows flagged "Bad Data", then for each remaining discrepancy:

  1. Looks up the matching QSO in your exported QRZ ADIF file to get its logid.
  2. Re-INSERTs it with OPTION=REPLACE and the corrected field value.

NOTE: ACTION=INSERT with OPTION=REPLACE and APP_QRZLOG_LOGID in the ADIF
payload replaces the existing record in place. This works on both unconfirmed
and confirmed/award-locked records — unlike ACTION=DELETE which fails silently
on locked records. QRZ returns RESULT=REPLACE on success.

This approach is fast: the ADIF export is parsed locally, so no bulk API fetch
of 36,000 records is needed. The API is only called for the small number of
records that actually need correcting.

Usage
-----
    python resolve_qrz_discrepancies.py \
        --xlsx  qrz_errors.xlsx \
        --adif  qrz_export.adi \
        --key   YOUR-QRZ-API-KEY \
        --call  WT8P \
        [--dry-run]                    # preview without writing to QRZ
        [--output-csv resolved_log.csv]

Requirements
------------
    pip install pandas openpyxl requests

QRZ API endpoint : https://logbook.qrz.com/api
Rate limiting    : 1 s pause between API write calls to be polite
"""

import argparse
import csv
import html
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
QRZ_API_URL   = "https://logbook.qrz.com/api"
API_PAUSE_SEC = 1.0

# Mapping from Excel sheet name -> ADIF field (other party's fields)
SHEET_TO_ADIF_FIELD = {
    "Grid":  "GRIDSQUARE",
    "Grids":  "GRIDSQUARE",
    "State":  "STATE",
    "States":  "STATE",
    "County": "CNTY",
    "Counties": "CNTY",
}

# Mapping from Excel sheet name -> ADIF field (my station's fields)
SHEET_TO_MY_ADIF_FIELD = {
    "Grid":  "MY_GRIDSQUARE",
    "Grids":  "MY_GRIDSQUARE",
    "State":  "MY_STATE",
    "States":  "MY_STATE",
    "County": "MY_CNTY",
    "Counties": "MY_CNTY",
}

# All valid ADIF field names across both modes
ALL_ADIF_FIELDS = set(SHEET_TO_ADIF_FIELD.values()) | set(SHEET_TO_MY_ADIF_FIELD.values())


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
    status:      str   # updated | dry_run | no_change | skipped_bad_data | no_match | error
    error_msg:   str = ""


# ---------------------------------------------------------------------------
# Excel loader
# ---------------------------------------------------------------------------

def _parse_date_time(dt_val) -> tuple[str, str]:
    if isinstance(dt_val, pd.Timestamp):
        return dt_val.strftime("%Y%m%d"), dt_val.strftime("%H%M")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d %H%M"):
        try:
            dt = datetime.strptime(str(dt_val).strip(), fmt)
            return dt.strftime("%Y%m%d"), dt.strftime("%H%M")
        except ValueError:
            continue
    log.warning("Could not parse date/time: %r", dt_val)
    return str(dt_val), ""


def _row_to_discrepancy(adif_field: str, sheet_name: str, row_date, qso_with: str,
                         your_val: str, other_val: str, note: str) -> Optional[Discrepancy]:
    """Shared conversion logic used by both the Excel and CSV loaders."""
    bad_data  = note.strip().lower() == "bad data"
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


def load_discrepancies(xlsx_path: Path, my_station: bool = False) -> list[Discrepancy]:
    field_map = SHEET_TO_MY_ADIF_FIELD if my_station else SHEET_TO_ADIF_FIELD
    xl        = pd.ExcelFile(xlsx_path)
    results: list[Discrepancy] = []

    for sheet_name, adif_field in field_map.items():
        if sheet_name not in xl.sheet_names:
            log.warning("Sheet '%s' not found — skipping.", sheet_name)
            continue

        df = xl.parse(sheet_name)
        # Normalise column headers — match on prefix so "You Entered county",
        # "You Entered grid" etc. all resolve correctly
        col_map = {}
        for col in df.columns:
            col_lower = str(col).strip().lower()
            if col_lower.startswith("you entered"):
                col_map["you_entered"] = col
            elif col_lower.startswith("other party entered"):
                col_map["other_party_entered"] = col

        for _, row in df.iterrows():
            d = _row_to_discrepancy(
                adif_field=adif_field,
                sheet_name=sheet_name,
                row_date=row["QSO Date"],
                qso_with=str(row["QSO With"]),
                your_val=str(row.get(col_map.get("you_entered", "You Entered"), "")),
                other_val=str(row.get(col_map.get("other_party_entered", "Other Party Entered"), "")),
                note=str(row.get("Note", "")),
            )
            if d:
                results.append(d)

    bad = sum(1 for d in results if d.bad_data)
    mode = "MY_ fields" if my_station else "other party fields"
    log.info("Loaded %d discrepancy rows [%s] (%d Bad Data skipped, %d to process)",
             len(results), mode, bad, len(results) - bad)
    return results


def load_discrepancies_csv(csv_path: Path, my_station: bool = False) -> list[Discrepancy]:
    """
    Load discrepancies from a flat CSV file instead of the Excel workbook.

    Required columns:
        field             : GRIDSQUARE, STATE, or CNTY
                            (or MY_GRIDSQUARE, MY_STATE, MY_CNTY — but --my-station
                            flag is the cleaner way to handle this)
        qso_date          : e.g. 2025-08-11 02:22:00  or  20250811
        qso_with          : other party's callsign
        other_party_entered : value to apply

    Optional columns:
        you_entered       : your current value (informational)
        de                : your callsign (informational)
        note              : "Bad Data" to skip this row

    Example CSV (other party mode):
        field,qso_date,qso_with,de,you_entered,other_party_entered,note
        GRIDSQUARE,2024-07-06 20:28:00,VE5URQ,WT8P,DO62,DN69,
        STATE,2017-10-28 15:14:00,WA4JS,WT8P,TN,TEN,Bad Data
        CNTY,2025-08-11 02:22:00,KL4RL,WT8P,,Anchorage Borough AK,

    Example CSV (my station mode, --my-station flag):
        field,qso_date,qso_with,new_value
        GRIDSQUARE,2025-08-11 02:22:00,KL4RL,CN87
        STATE,2025-09-10 19:13:00,K5ZD,WA
        CNTY,2025-08-11 02:22:00,KL4RL,WA,King
    """
    COL_ALIASES = {
        "field":                "field",
        "adif_field":           "field",
        "qso_date":             "qso_date",
        "date":                 "qso_date",
        "qso_with":             "qso_with",
        "call":                 "qso_with",
        "you_entered":          "you_entered",
        "your_value":           "you_entered",
        "other_party_entered":  "other_party_entered",
        "other_value":          "other_party_entered",
        "other":                "other_party_entered",
        "new_value":            "other_party_entered",  # alias for my-station mode
        "note":                 "note",
        "notes":                "note",
        "de":                   "de",
    }

    # In my_station mode, accept bare field names and map them to MY_ variants
    MY_FIELD_UPGRADE = {
        "GRIDSQUARE": "MY_GRIDSQUARE",
        "STATE":      "MY_STATE",
        "CNTY":       "MY_CNTY",
    }

    results: list[Discrepancy] = []

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            log.error("CSV file appears empty: %s", csv_path)
            return results

        col_map = {col: COL_ALIASES.get(col.strip().lower()) for col in reader.fieldnames}
        missing = {"field", "qso_date", "qso_with", "other_party_entered"} - set(col_map.values())
        if missing:
            log.error("CSV is missing required columns: %s", missing)
            log.error("Required: field, qso_date, qso_with, other_party_entered (or new_value)")
            sys.exit(1)

        for i, raw_row in enumerate(reader, start=2):
            row = {canonical: raw_row[orig]
                   for orig, canonical in col_map.items()
                   if canonical and orig in raw_row}

            adif_field = row.get("field", "").strip().upper()

            # If --my-station, upgrade bare field names to MY_ variants
            if my_station and adif_field in MY_FIELD_UPGRADE:
                adif_field = MY_FIELD_UPGRADE[adif_field]

            if adif_field not in ALL_ADIF_FIELDS:
                log.warning("Row %d: unknown field %r — skipping (valid: %s)",
                            i, adif_field, sorted(ALL_ADIF_FIELDS))
                continue

            # Derive sheet name from field
            reverse_map = {v: k for k, v in {**SHEET_TO_ADIF_FIELD, **SHEET_TO_MY_ADIF_FIELD}.items()}
            sheet_name  = reverse_map.get(adif_field, adif_field)

            d = _row_to_discrepancy(
                adif_field=adif_field,
                sheet_name=sheet_name,
                row_date=row.get("qso_date", ""),
                qso_with=row.get("qso_with", ""),
                your_val=row.get("you_entered", ""),
                other_val=row.get("other_party_entered", ""),
                note=row.get("note", ""),
            )
            if d:
                results.append(d)

    bad  = sum(1 for d in results if d.bad_data)
    mode = "MY_ fields" if my_station else "other party fields"
    log.info("Loaded %d discrepancy rows from CSV [%s] (%d Bad Data skipped, %d to process)",
             len(results), mode, bad, len(results) - bad)
    return results
    """
    Load discrepancies from a flat CSV file instead of the Excel workbook.

    Required columns:
        field             : GRIDSQUARE, STATE, or CNTY
        qso_date          : e.g. 2025-08-11 02:22:00  or  20250811
        qso_with          : other party's callsign
        other_party_entered : value to apply

    Optional columns:
        you_entered       : your current value (informational)
        de                : your callsign (informational)
        note              : "Bad Data" to skip this row

    Example CSV:
        field,qso_date,qso_with,de,you_entered,other_party_entered,note
        GRIDSQUARE,2024-07-06 20:28:00,VE5URQ,WT8P,DO62,DN69,
        STATE,2017-10-28 15:14:00,WA4JS,WT8P,TN,TEN,Bad Data
        CNTY,2025-08-11 02:22:00,KL4RL,WT8P,,Anchorage Borough AK,
    """
    # Map of accepted column name variants (lowercase) -> canonical name
    COL_ALIASES = {
        "field":                "field",
        "adif_field":           "field",
        "qso_date":             "qso_date",
        "date":                 "qso_date",
        "qso_with":             "qso_with",
        "call":                 "qso_with",
        "you_entered":          "you_entered",
        "your_value":           "you_entered",
        "other_party_entered":  "other_party_entered",
        "other_value":          "other_party_entered",
        "other":                "other_party_entered",
        "note":                 "note",
        "notes":                "note",
        "de":                   "de",
    }

    VALID_FIELDS = set(SHEET_TO_ADIF_FIELD.values())  # GRIDSQUARE, STATE, CNTY

    results: list[Discrepancy] = []

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Normalise column names via aliases
        if reader.fieldnames is None:
            log.error("CSV file appears empty: %s", csv_path)
            return results

        col_map = {col: COL_ALIASES.get(col.strip().lower()) for col in reader.fieldnames}
        missing = {"field", "qso_date", "qso_with", "other_party_entered"} - set(col_map.values())
        if missing:
            log.error("CSV is missing required columns: %s", missing)
            log.error("Required: field, qso_date, qso_with, other_party_entered")
            sys.exit(1)

        for i, raw_row in enumerate(reader, start=2):  # start=2 accounts for header row
            # Remap to canonical column names
            row = {canonical: raw_row[orig]
                   for orig, canonical in col_map.items()
                   if canonical and orig in raw_row}

            adif_field = row.get("field", "").strip().upper()
            if adif_field not in VALID_FIELDS:
                log.warning("Row %d: unknown field %r — skipping (must be one of %s)",
                            i, adif_field, sorted(VALID_FIELDS))
                continue

            # sheet name is just the field for CSV input
            sheet_name = {v: k for k, v in SHEET_TO_ADIF_FIELD.items()}.get(adif_field, adif_field)

            d = _row_to_discrepancy(
                adif_field=adif_field,
                sheet_name=sheet_name,
                row_date=row.get("qso_date", ""),
                qso_with=row.get("qso_with", ""),
                your_val=row.get("you_entered", ""),
                other_val=row.get("other_party_entered", ""),
                note=row.get("note", ""),
            )
            if d:
                results.append(d)

    bad = sum(1 for d in results if d.bad_data)
    log.info("Loaded %d discrepancy rows from CSV (%d Bad Data skipped, %d to process)",
             len(results), bad, len(results) - bad)
    return results


# ---------------------------------------------------------------------------
# Local ADIF parser
# ---------------------------------------------------------------------------

def parse_adif_file(path: Path) -> list[dict]:
    """
    Parse a local ADIF export into a list of QSO dicts (keys upper-cased).
    Handles HTML-escaped angle brackets (&lt; &gt;) if present.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    text = html.unescape(text)

    # Skip header section
    eoh = re.search(r"<EOH>", text, re.IGNORECASE)
    pos = eoh.end() if eoh else 0

    pattern = re.compile(r"<([^:>]+)(?::(\d+)(?::[^>]*)?)?>", re.IGNORECASE)
    records: list[dict] = []
    current: dict       = {}

    while pos < len(text):
        m = pattern.search(text, pos)
        if not m:
            break
        tag_end  = m.end()
        raw_name = m.group(1).upper()
        len_str  = m.group(2)

        if raw_name == "EOR":
            if current:
                records.append(current)
            current = {}
            pos = tag_end
        elif raw_name == "EOH":
            pos = tag_end
        elif len_str:
            length = int(len_str)
            value = text[tag_end: tag_end + length].strip()
            # APP_QRZLOG_LOGID must stay as a clean integer string — never allow
            # float formatting (e.g. 1.00752e+09) to corrupt it during later use
            if raw_name == "APP_QRZLOG_LOGID" and value:
                try:
                    value = str(int(float(value)))
                except ValueError:
                    pass
            current[raw_name] = value
            pos = tag_end + length
        else:
            pos = tag_end

    log.info("Parsed %d QSO records from %s", len(records), path.name)
    return records


def build_index(records: list[dict]) -> dict[tuple, dict]:
    """Index by (CALL, QSO_DATE, TIME_HHMM)."""
    index: dict[tuple, dict] = {}
    for rec in records:
        call     = rec.get("CALL", "").upper().strip()
        date     = rec.get("QSO_DATE", "").strip()
        time_raw = re.sub(r"\D", "", rec.get("TIME_ON", ""))[:4]
        if call and date:
            index[(call, date, time_raw)] = rec
    return index


# ---------------------------------------------------------------------------
# Field value converters: QRZ display format -> ADIF format
# ---------------------------------------------------------------------------

# QRZ STATE discrepancy export sometimes uses non-standard abbreviations
# Map any known bad ones to the correct 2-letter ADIF value
_STATE_FIXES: dict[str, str] = {
    "IND": "IN",
    "TEN": "TN",
    "CAL": "CA",
    "FLA": "FL",
    "TEX": "TX",
    "OHI": "OH",
    "PEN": "PA",
    "MAS": "MA",
    "NEV": "NV",
    "ORE": "OR",
    "WAS": "WA",
    "COL": "CO",
    "ARI": "AZ",
    "NEB": "NE",
    "MIN": "MN",
    "MIS": "MO",  # ambiguous but MIS more often Missouri than Mississippi(MS)
    "ALA": "AL",
    "GEO": "GA",
    "VIR": "VA",
    "ILL": "IL",
    "WIS": "WI",
    "KAN": "KS",
    "ARK": "AR",
    "UTH": "UT",
    "IOW": "IA",
    "SCR": "SC",  # SCR not standard; SC is
    "NCR": "NC",
}


def _convert_state(qrz_value: str) -> str:
    """
    Normalise QRZ's STATE value to the 2-letter ADIF abbreviation.
    If it's already 2 letters, pass it through unchanged.
    """
    v = qrz_value.strip().upper()
    if len(v) == 2:
        return v
    return _STATE_FIXES.get(v, v)   # return fixed value or original if unknown


def _convert_cnty(qrz_value: str) -> str:
    """
    Convert QRZ's CNTY display format to ADIF format.

    QRZ display : 'Hamilton County, TN'  or  'Anchorage Borough, AK'
    ADIF format : 'TN,Hamilton'          or  'AK,Anchorage'

    The rule: ADIF enumeration uses bare names like 'TN,Hamilton' not 
    'TN,Hamilton County').  split on the last ', ', swap.  
    Later, we will drop the words County, Borough (Alaska) or Parish 
    (Louisiana) or County (all other states and territories) if present.  
    """
    v = qrz_value.strip()
    # Split on last ', ' to separate the state abbreviation
    if ", " not in v:
        return v   # can't parse, return as-is

    # last token after ', ' should be the 2-letter state
    parts = v.rsplit(", ", 1)
    county_part = parts[0].strip()
    state_part  = parts[1].strip().upper()

    # Strip trailing " County" 
    bare = re.sub(r"\s+County$", "", county_part, flags=re.IGNORECASE).strip()
    # Strip trailing " Borough" from Alaska county names — ADIF enumeration
    # uses bare names e.g. "AK,Anchorage" not "AK,Anchorage Borough"
    if state_part == "AK":
        bare = re.sub(r"\s+Borough\s*$", "", bare, flags=re.IGNORECASE).strip()
    elif state_part == "LA":
        bare = re.sub(r"\s+Parish\s*$", "", bare, flags=re.IGNORECASE).strip()

    return f"{state_part},{bare}"

def _adif_field_str(name: str, value: str) -> str:
    return f"<{name}:{len(value)}>{value}"


def _build_adif(fields: dict) -> str:
    parts = [_adif_field_str(k.upper(), str(v)) for k, v in fields.items() if v]
    return " ".join(parts) + " <eor>"


def _post(api_key: str, callsign: str, action: str, extra: dict) -> dict:
    payload = {"KEY": api_key, "ACTION": action, **extra}
    headers = {
        "User-Agent": f"QRZDiscrepancyResolver/1.0 ({callsign})",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        resp = requests.post(QRZ_API_URL, data=payload, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"HTTP error: {exc}") from exc

    raw    = resp.text
    result: dict = {}

    # ADIF value may contain & internally — isolate it before splitting
    adif_match = re.search(r"(?:^|&|\n)ADIF=", raw, re.IGNORECASE)
    if adif_match:
        for token in re.split(r"[&\n]", raw[:adif_match.start()]):
            if "=" in token:
                k, _, v = token.partition("=")
                result[k.strip().upper()] = v.strip()
        result["ADIF"] = html.unescape(raw[adif_match.end():])
    else:
        for token in re.split(r"[&\n]", raw):
            if "=" in token:
                k, _, v = token.partition("=")
                result[k.strip().upper()] = v.strip()

    if result.get("RESULT") == "FAIL":
        reason = result.get("REASON", "")
        raise RuntimeError(f"QRZ API FAIL: {reason if reason else repr(raw[:200])}")
    if result.get("RESULT") == "AUTH":
        raise RuntimeError("QRZ API AUTH error — check your API key.")

    return result


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

        logid     = qso.get("APP_QRZLOG_LOGID", "")
        # Guard against float formatting (e.g. '1.00752e+09') from any upstream source
        if logid:
            try:
                logid = str(int(float(logid)))
            except ValueError:
                pass
        old_value = qso.get(d.adif_field, "")

        # Convert QRZ display format to ADIF format before applying
        # Handles both other-party fields (CNTY, STATE) and my-station fields (MY_CNTY, MY_STATE)
        if d.adif_field in ("CNTY", "MY_CNTY"):
            new_value = _convert_cnty(d.other_value)
        elif d.adif_field in ("STATE", "MY_STATE"):
            new_value = _convert_state(d.other_value)
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
            log.info("No-op    logid=%-8s  %-12s  %s: value already %r — skipping",
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

        # Use INSERT with OPTION=REPLACE — QRZ replaces the existing record
        # in place when APP_QRZLOG_LOGID is present in the ADIF payload.
        # This works on both unconfirmed and confirmed/award-locked records,
        # unlike ACTION=DELETE which fails on locked records.
        updated = dict(qso)
        updated[d.adif_field] = new_value
        adif_str = _build_adif(updated)

        try:
            result = _post(api_key, callsign, "INSERT", {"ADIF": adif_str, "OPTION": "REPLACE"})
            if result.get("RESULT") == "REPLACE":
                log.info("Replaced logid=%-8s  %-12s  %s: %r -> %r",
                         logid, d.qso_with, d.adif_field, old_value, new_value)
                resolutions.append(Resolution(
                    discrepancy=d, logid=logid,
                    old_value=old_value, new_value=new_value,
                    status="updated",
                ))
            else:
                log.error("Unexpected result logid=%-8s  %s", logid, result)
                resolutions.append(Resolution(
                    discrepancy=d, logid=logid,
                    old_value=old_value, new_value=new_value,
                    status="error", error_msg=f"Unexpected RESULT: {result.get('RESULT')}",
                ))
        except RuntimeError as exc:
            log.error("Failed   logid=%-8s  %s", logid, exc)
            resolutions.append(Resolution(
                discrepancy=d, logid=logid,
                old_value=old_value, new_value=new_value,
                status="error", error_msg=str(exc),
            ))

        time.sleep(API_PAUSE_SEC)

    # Record bad-data skips in output
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
        description=(
            "Resolve QRZ discrepancies: parse a local ADIF export for logids, "
            "then update only the changed records via the QRZ API."
        )
    )
    input_group = p.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--xlsx",
                   help="QRZ discrepancy Excel file (e.g. qrz_errors.xlsx)")
    input_group.add_argument("--input-csv",
                   help=(
                       "Flat CSV file instead of Excel. Required columns: "
                       "field, qso_date, qso_with, other_party_entered. "
                       "Optional: you_entered, note, de."
                   ))
    p.add_argument("--adif", required=True,
                   help="Your QRZ ADIF export — must contain APP_QRZLOG_LOGID field")
    p.add_argument("--key",  required=True,
                   help="QRZ API key")
    p.add_argument("--call", required=True,
                   help="Your callsign (e.g. WT8P)")
    p.add_argument("--my-station", action="store_true",
                   help=(
                       "Correct your own station's fields (MY_GRIDSQUARE, MY_STATE, MY_CNTY) "
                       "instead of the other party's fields. The input file format is identical; "
                       "bare field names (GRIDSQUARE, STATE, CNTY) are automatically mapped to "
                       "their MY_ equivalents when this flag is set."
                   ))
    p.add_argument("--dry-run", action="store_true",
                   help="Preview changes without writing to QRZ")
    p.add_argument("--output-csv", default="resolved_log.csv",
                   help="Output CSV log (default: resolved_log.csv)")
    return p


def main() -> None:
    args    = build_parser().parse_args()
    adif    = Path(args.adif)
    out_csv = Path(args.output_csv)

    if not adif.exists():
        log.error("File not found: %s", adif)
        sys.exit(1)

    # Load discrepancies from whichever input format was specified
    if args.xlsx:
        input_path = Path(args.xlsx)
        if not input_path.exists():
            log.error("File not found: %s", input_path)
            sys.exit(1)
        log.info("=== QRZ Discrepancy Resolver ===")
        log.info("Excel    : %s", input_path)
        discrepancies = load_discrepancies(input_path, my_station=args.my_station)
    else:
        input_path = Path(args.input_csv)
        if not input_path.exists():
            log.error("File not found: %s", input_path)
            sys.exit(1)
        log.info("=== QRZ Discrepancy Resolver ===")
        log.info("CSV      : %s", input_path)
        discrepancies = load_discrepancies_csv(input_path, my_station=args.my_station)

    log.info("ADIF     : %s", adif)
    log.info("Callsign : %s", args.call.upper())
    log.info("Mode     : %s", "MY_ station fields" if args.my_station else "other party fields")
    log.info("Dry run  : %s", args.dry_run)

    if not discrepancies:
        log.info("No discrepancies to process.")
        return

    qso_records = parse_adif_file(adif)
    qso_index   = build_index(qso_records)
    log.info("QSO index built with %d entries.", len(qso_index))

    resolutions = resolve(discrepancies, qso_index, args.key, args.call.upper(), args.dry_run)

    write_csv(resolutions, out_csv)
    log.info("=== Done. ===")


if __name__ == "__main__":
    main()
