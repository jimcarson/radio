"""
qrz_common.py
=============
Shared library for QRZ logbook tools.

Provides:
  - ADIF file parsing and writing
  - QRZ API client (INSERT/REPLACE, DELETE, FETCH)
  - API key and config file loading (with callsign -> filename mapping)
  - Field value converters (CNTY, STATE, coordinates)
  - Maidenhead grid square utilities (latlon_to_grid, grid_to_latlon)
  - Match key building and QSO indexing
  - Field comparison utilities for reconciliation
  - Date/time normalisation (parse_qso_datetime) — handles both ADIF
    compact format (YYYYMMDD / HHMM) and human-readable (YYYY-MM-DD /
    HH:MM or YYYY-MM-DD HH:MM:SS), used by resolve and adif_extract.

Imported by:
  - resolve_qrz_discrepancies.py
  - adif_extract.py
  - reconcile_adif.py
"""

import configparser
import html
import logging
import re
import sys
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date / time normalisation
# ---------------------------------------------------------------------------

def parse_qso_datetime(date_val: str, time_val: str = "") -> tuple[str, str]:
    """
    Parse QSO date and time values into a canonical pair:
        date  -> "YYYYMMDD"   (ADIF storage format)
        time  -> "HHMM"       (ADIF storage format)

    Accepted date formats:
        "YYYYMMDD"            ADIF compact (QRZ ADIF export)
        "YYYY-MM-DD"          ISO date only
        "YYYY-MM-DD HH:MM"    ISO datetime
        "YYYY-MM-DD HH:MM:SS" ISO datetime with seconds

    Accepted time formats (when supplied separately):
        "HHMM"                ADIF compact (QRZ ADIF export)
        "HH:MM"               human-readable
        "HH:MM:SS"            with seconds (seconds are dropped)
        ""                    empty / not present

    When date_val already contains a time component (space-separated),
    time_val is ignored.

    Returns ("YYYYMMDD", "HHMM") — both strings.  On failure, returns
    the raw inputs unchanged and logs a warning.
    """
    date_s = str(date_val).strip()
    time_s = str(time_val).strip()

    # If date_val contains a space, split into date + time parts
    if " " in date_s:
        date_s, time_s = date_s.split(" ", 1)

    # --- Normalise date ---
    # Already YYYYMMDD (8 digits, no dashes)?
    if re.fullmatch(r"\d{8}", date_s):
        norm_date = date_s
    # ISO YYYY-MM-DD?
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_s):
        norm_date = date_s.replace("-", "")
    else:
        log.warning("parse_qso_datetime: unrecognised date %r", date_s)
        return date_s, time_s

    # --- Normalise time ---
    if not time_s:
        norm_time = ""
    elif re.fullmatch(r"\d{4}", time_s):          # HHMM
        norm_time = time_s
    elif re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", time_s):  # HH:MM or HH:MM:SS
        norm_time = time_s[:5].replace(":", "")
    else:
        log.warning("parse_qso_datetime: unrecognised time %r", time_s)
        norm_time = time_s

    return norm_date, norm_time


def format_qso_datetime(date_adif: str, time_adif: str) -> tuple[str, str]:
    """
    Convert ADIF-format date/time to human-readable form for CSV output.

        "20260328", "1635"  ->  "2026-03-28", "16:35"

    Accepts either ADIF compact or already-formatted values gracefully.
    """
    d = str(date_adif).strip()
    t = str(time_adif).strip()

    if re.fullmatch(r"\d{8}", d):
        d = f"{d[:4]}-{d[4:6]}-{d[6:]}"

    if re.fullmatch(r"\d{4}", t):
        t = f"{t[:2]}:{t[2:]}"

    return d, t


def open_text_file(path: Path, mode: str = "r") -> object:
    """
    Open a text file for reading with automatic encoding detection.

    Tries encodings in order:
      1. utf-8-sig  — UTF-8 with or without BOM (Excel's "Save as UTF-8 CSV")
      2. cp1252     — Windows Western European / Latin-1 superset; covers
                      accented characters saved by Windows apps like Excel

    On a successful fallback to cp1252 a WARNING is logged so the caller
    is aware the file was not UTF-8.  All files written by these tools use
    UTF-8, so the fallback only matters for files edited externally.

    Returns an open file object in the requested mode.  The caller is
    responsible for closing it (use as a context manager).
    """
    encodings = [("utf-8-sig", False), ("cp1252", True)]
    for encoding, warn in encodings:
        try:
            f = path.open(mode=mode, newline="", encoding=encoding, errors="strict")
            # For reading, consume the whole file to catch any decode error,
            # then rewind.  Files are small (CSV/text), so this is fine.
            if "r" in mode:
                f.read()
                f.seek(0)
            if warn:
                log.warning(
                    "%s does not appear to be UTF-8; re-reading as cp1252 "
                    "(Windows Latin-1). Consider saving as UTF-8 in Excel "
                    "via File → Save As → CSV UTF-8 (comma delimited).",
                    path.name,
                )
            return f
        except (UnicodeDecodeError, LookupError):
            try:
                f.close()
            except Exception:
                pass
            continue
    # Last resort: replace undecodable bytes rather than crashing
    log.error(
        "%s could not be decoded as UTF-8 or cp1252; "
        "opening with errors='replace'. Some characters may be lost.",
        path.name,
    )
    return path.open(mode=mode, newline="", encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QRZ_API_URL   = "https://logbook.qrz.com/api"
API_PAUSE_SEC = 1.0

# US DXCC entity code — used to gate STATE/CNTY comparisons
US_DXCC = "291"

# Confirmed LoTW QSO indicators
LOTW_CONFIRMED_FIELDS = {"APP_LOTW_2XQSL": "Y", "QSL_RCVD": "Y"}

# API key pattern: xxxx-xxxx-xxxx-xxxx (hex groups)
KEY_FILE_PATTERN = re.compile(
    r"^[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}$"
)

# Fields compared by reconcile_adif.py and their default rules
# Rules: lotw_wins | fill_blank | flag_only | skip
DEFAULT_FIELD_RULES: dict[str, str] = {
    "GRIDSQUARE":   "lotw_wins",
    "COUNTRY":      "fill_blank",
    "DXCC":         "lotw_wins",
    "CQZ":          "lotw_wins",
    "ITUZ":         "lotw_wins",
    "MODE":         "flag_only",
    "STATE":        "lotw_wins",
    "CNTY":         "lotw_wins",
    "MY_COUNTRY":   "lotw_wins",
    "MY_CQ_ZONE":   "lotw_wins",
    "MY_ITU_ZONE":  "lotw_wins",
    "MY_DXCC":      "lotw_wins",
    "MY_STATE":     "lotw_wins",
    "MY_CNTY":      "lotw_wins",
    "APP_LOTW_RXQSL": "fill_blank",
}

# Fields that should only be applied to US contacts (DXCC=291)
US_ONLY_FIELDS = {"STATE", "CNTY"}

# Fields compared as integers (strip leading zeros before comparing)
INTEGER_FIELDS = {"DXCC", "CQZ", "ITUZ", "MY_DXCC", "MY_CQ_ZONE", "MY_ITU_ZONE"}

# MY_COUNTRY normalisation — map LoTW verbose names to QRZ concise names
MY_COUNTRY_MAP: dict[str, str] = {
    "UNITED STATES OF AMERICA": "United States",
    "UNITED STATES":            "United States",
}

# ---------------------------------------------------------------------------
# Callsign -> filename mapping  (/ -> _)
# ---------------------------------------------------------------------------

def callsign_to_filename(callsign: str) -> str:
    """
    Convert a callsign to a safe filename stem.
    Replaces '/' with '_' so portable callsigns like TF/WT8P
    map to TF_WT8P (e.g. TF_WT8P.key, TF_WT8P.cfg).
    """
    return callsign.upper().replace("/", "_")


# ---------------------------------------------------------------------------
# API key file loader
# ---------------------------------------------------------------------------

def load_api_key(key_arg: Optional[str], callsign: str) -> str:
    """
    Return the QRZ API key from --key argument if provided, otherwise
    look for a file named <CALLSIGN>.key (with / replaced by _) in the
    current directory containing a single line: xxxx-xxxx-xxxx-xxxx
    """
    if key_arg:
        key = key_arg.strip()
        if not KEY_FILE_PATTERN.match(key):
            log.warning("--key value does not match expected format xxxx-xxxx-xxxx-xxxx")
        return key

    stem     = callsign_to_filename(callsign)
    key_file = Path(f"{stem}.key")
    if key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
        if KEY_FILE_PATTERN.match(key):
            log.info("API key loaded from %s", key_file)
            return key
        log.error("%s does not contain a valid API key (expected xxxx-xxxx-xxxx-xxxx)", key_file)
        sys.exit(1)

    log.error(
        "No API key provided. Supply --key YOUR-KEY or create %s.key "
        "containing your QRZ API key.", stem
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config file loader
# ---------------------------------------------------------------------------

def load_field_rules(callsign: str, config_path: Optional[Path] = None) -> dict[str, str]:
    """
    Load per-field reconciliation rules from a config file.

    Looks for (in order):
      1. The path supplied via --config
      2. <CALLSIGN>.cfg in the current directory (/ replaced by _)

    If neither exists, returns the hardcoded DEFAULT_FIELD_RULES.

    Config file format (INI):
        [fields]
        GRIDSQUARE   = lotw_wins
        COUNTRY      = fill_blank
        MODE         = flag_only
        STATE        = skip

    Valid rules: lotw_wins | fill_blank | flag_only | skip
    """
    VALID_RULES = {"lotw_wins", "fill_blank", "flag_only", "skip"}
    rules       = dict(DEFAULT_FIELD_RULES)  # start from defaults

    # Determine config file path
    if config_path is None:
        stem        = callsign_to_filename(callsign)
        config_path = Path(f"{stem}.cfg")

    if not config_path.exists():
        log.debug("No config file found at %s — using default field rules.", config_path)
        return rules

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    if "fields" not in parser:
        log.warning("Config file %s has no [fields] section — using defaults.", config_path)
        return rules

    for field, rule in parser["fields"].items():
        field = field.upper().strip()
        rule  = rule.lower().strip()
        if rule not in VALID_RULES:
            log.warning("Config: unknown rule %r for field %s — using default.", rule, field)
            continue
        rules[field] = rule
        log.debug("Config: %s = %s", field, rule)

    log.info("Field rules loaded from %s", config_path)
    return rules


# ---------------------------------------------------------------------------
# ADIF parser
# ---------------------------------------------------------------------------

def parse_adif_file(path: Path) -> list[dict]:
    """
    Parse a local ADIF export into a list of QSO dicts (keys upper-cased).
    Handles HTML-escaped angle brackets (&lt; &gt;) if present (QRZ API quirk).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    text = html.unescape(text)

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
            value  = text[tag_end: tag_end + length].strip()
            # Protect APP_QRZLOG_LOGID from float/scientific notation corruption
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


# ---------------------------------------------------------------------------
# QSO index builder
# ---------------------------------------------------------------------------

def build_index(records: list[dict],
                callsign_filter: Optional[str] = None) -> dict[tuple, dict]:
    """
    Index QSO records by (CALL, QSO_DATE, TIME_HHMM) for O(1) lookup.

    TIME_ON is normalised to HHMM (first 4 digits, strips seconds).
    CALL and STATION_CALLSIGN are upper-cased.

    If callsign_filter is supplied, only records whose STATION_CALLSIGN
    matches (case-insensitive) are indexed — used to filter a multi-callsign
    LoTW export down to a single logbook.
    """
    index: dict[tuple, dict] = {}
    skipped = 0

    for rec in records:
        if callsign_filter:
            station = rec.get("STATION_CALLSIGN", "").upper().strip()
            if station != callsign_filter.upper().strip():
                skipped += 1
                continue

        call     = rec.get("CALL", "").upper().strip()
        date     = rec.get("QSO_DATE", "").strip()
        time_raw = re.sub(r"\D", "", rec.get("TIME_ON", ""))[:4]

        if call and date:
            index[(call, date, time_raw)] = rec

    if callsign_filter and skipped:
        log.info("  Filtered out %d records not matching STATION_CALLSIGN=%s",
                 skipped, callsign_filter.upper())
    return index


def make_key(qso: dict) -> Optional[tuple]:
    """Build a match key tuple. Returns None if any required field is missing."""
    call = qso.get("CALL", "").upper().strip()
    date = qso.get("QSO_DATE", "").strip()
    time = re.sub(r"\D", "", qso.get("TIME_ON", ""))[:4]
    if call and date:
        return (call, date, time)
    return None


# ---------------------------------------------------------------------------
# ADIF writer
# ---------------------------------------------------------------------------

def adif_field_str(name: str, value: str) -> str:
    return f"<{name}:{len(value)}>{value}"


def build_adif(fields: dict) -> str:
    """Build an ADIF record string from a dict of field->value."""
    parts = [adif_field_str(k.upper(), str(v)) for k, v in fields.items() if v]
    return " ".join(parts) + " <eor>"


def write_adif_file(records: list[dict], path: Path) -> None:
    """Write a list of QSO dicts to an ADIF file."""
    lines = ["Generated by QRZ logbook tools", "<EOH>", ""]
    for qso in records:
        parts = [adif_field_str(k, v) for k, v in qso.items() if v]
        lines.append(" ".join(parts) + " <EOR>")
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote %d records to %s", len(records), path.name)


# ---------------------------------------------------------------------------
# QRZ API client
# ---------------------------------------------------------------------------

def qrz_post(api_key: str, callsign: str, action: str,
             extra: dict, user_agent: str = "QRZTools/1.0") -> dict:
    """
    POST to the QRZ Logbook API.
    Returns parsed response dict. Raises RuntimeError on failure.

    The QRZ API returns HTML-escaped ADIF in responses, which is unescaped
    before parsing.
    """
    payload = {"KEY": api_key, "ACTION": action, **extra}
    headers = {
        "User-Agent": f"{user_agent} ({callsign})",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        resp = requests.post(QRZ_API_URL, data=payload, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"HTTP error: {exc}") from exc

    raw    = resp.text
    result: dict = {}

    # ADIF value may contain & internally — isolate before splitting
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


def qrz_replace(api_key: str, callsign: str, qso: dict,
                user_agent: str = "QRZTools/1.0") -> dict:
    """
    Replace an existing QRZ record in place using ACTION=INSERT OPTION=REPLACE.
    The qso dict must contain APP_QRZLOG_LOGID.
    Returns the API response dict. Raises RuntimeError on failure.
    """
    adif_str = build_adif(qso)
    return qrz_post(api_key, callsign, "INSERT",
                    {"ADIF": adif_str, "OPTION": "REPLACE"}, user_agent)


# ---------------------------------------------------------------------------
# Field value converters
# ---------------------------------------------------------------------------

_STATE_FIXES: dict[str, str] = {
    "IND": "IN", "TEN": "TN", "CAL": "CA", "FLA": "FL", "TEX": "TX",
    "OHI": "OH", "PEN": "PA", "MAS": "MA", "NEV": "NV", "ORE": "OR",
    "WAS": "WA", "COL": "CO", "ARI": "AZ", "NEB": "NE", "MIN": "MN",
    "MIS": "MO", "ALA": "AL", "GEO": "GA", "VIR": "VA", "ILL": "IL",
    "WIS": "WI", "KAN": "KS", "ARK": "AR", "UTH": "UT", "IOW": "IA",
    "SCR": "SC", "NCR": "NC",
}


def convert_state(value: str) -> str:
    """Normalise a STATE value to the 2-letter ADIF abbreviation."""
    v = value.strip().upper()
    if len(v) == 2:
        return v
    return _STATE_FIXES.get(v, v)


def convert_cnty(value: str) -> str:
    """
    Convert QRZ's CNTY display format to ADIF format.
    'Hamilton County, TN' -> 'TN,Hamilton'
    'Anchorage Borough, AK' -> 'AK,Anchorage'  (Borough stripped for AK)
    """
    v = value.strip()
    if ", " not in v:
        return v

    parts      = v.rsplit(", ", 1)
    county_part = parts[0].strip()
    state_part  = parts[1].strip().upper()

    bare = re.sub(r"\s+County$", "", county_part, flags=re.IGNORECASE).strip()
    if state_part == "AK":
        bare = re.sub(r"\s+Borough\s*$", "", bare, flags=re.IGNORECASE).strip()
    elif state_part == "LA":
        bare = re.sub(r"\s+Parish\s*$", "", bare, flags=re.IGNORECASE).strip()

    return f"{state_part},{bare}"


def normalise_my_country(value: str) -> str:
    """Map LoTW verbose country names to QRZ concise equivalents."""
    return MY_COUNTRY_MAP.get(value.strip().upper(), value.strip())


# ---------------------------------------------------------------------------
# Coordinate validation and conversion
# ---------------------------------------------------------------------------

_COORD_RE   = re.compile(r"^[NSEWnsew]\d{3}\s+\d{2}\.\d{3}$")
_DECIMAL_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")


def _decimal_to_adif_coord(value: str, field: str) -> str:
    dec = float(value)
    if field == "MY_LAT":
        if not (-90.0 <= dec <= 90.0):
            raise ValueError(f"MY_LAT {dec} out of range (-90 to +90)")
        hemisphere = "N" if dec >= 0 else "S"
    else:
        if not (-180.0 <= dec <= 180.0):
            raise ValueError(f"MY_LON {dec} out of range (-180 to +180)")
        hemisphere = "E" if dec >= 0 else "W"
    abs_dec = abs(dec)
    degrees = int(abs_dec)
    minutes = (abs_dec - degrees) * 60.0
    return f"{hemisphere}{degrees:03d} {minutes:06.3f}"


def validate_coord(value: str, field: str) -> str:
    """
    Validate and normalise MY_LAT or MY_LON.
    Accepts decimal degrees (e.g. 47.5625, -122.958) or
    ADIF native format (e.g. N047 33.750, W122 57.480).
    Returns ADIF native format.
    """
    v = value.strip()
    if _DECIMAL_RE.match(v):
        return _decimal_to_adif_coord(v, field)

    v_upper = v.upper()
    if not _COORD_RE.match(v_upper):
        raise ValueError(
            f"{field} value {value!r} is not a recognised format.\n"
            f"  Decimal: 47.5625 or -122.958\n"
            f"  ADIF:    N047 33.750 or W122 57.480"
        )

    hemisphere = v_upper[0]
    deg_str, min_str = v_upper[1:].split()
    degrees = int(deg_str)
    minutes = float(min_str)

    if field == "MY_LAT":
        if hemisphere not in ("N", "S"):
            raise ValueError(f"MY_LAT hemisphere must be N or S, got {hemisphere!r}")
        if not (0 <= degrees <= 90):
            raise ValueError(f"MY_LAT degrees must be 0-90, got {degrees}")
    elif field == "MY_LON":
        if hemisphere not in ("E", "W"):
            raise ValueError(f"MY_LON hemisphere must be E or W, got {hemisphere!r}")
        if not (0 <= degrees <= 180):
            raise ValueError(f"MY_LON degrees must be 0-180, got {degrees}")

    if not (0 <= minutes < 60):
        raise ValueError(f"{field} minutes must be 0-59.999, got {minutes}")

    return f"{hemisphere}{degrees:03d} {minutes:06.3f}"


# ---------------------------------------------------------------------------
# Maidenhead grid square utilities
# ---------------------------------------------------------------------------

# Precision -> number of character pairs used
# 4 chars = field + square         (~55 km resolution)
# 6 chars = field + square + sub   (~460 m resolution)
# 8 chars = field + square + sub + extended  (~4 m resolution)
_GRID_PRECISIONS = {4, 6, 8}

# Character sets for each pair level
_UPPER  = "ABCDEFGHIJKLMNOPQRSTUVWX"   # field pair  (18 lon, 18 lat)
_DIGIT  = "0123456789"                  # square pair (10 lon, 10 lat)
_LOWER  = "abcdefghijklmnopqrstuvwx"   # subsquare   (24 lon, 24 lat)
_DIGIT2 = "0123456789"                  # extended    (10 lon, 10 lat)

# Sequence of (lon_div, lat_div, charset) for successive character pairs
_PAIR_STEPS = [
    (20.0,  10.0,  _UPPER),   # field     pair 1
    (2.0,   1.0,   _DIGIT),   # square    pair 2
    (5/60,  2.5/60, _LOWER),  # subsquare pair 3
    (0.5/60, 0.25/60, _DIGIT2),  # extended pair 4
]


def latlon_to_grid(lat: float, lon: float, precision: int = 6) -> str:
    """
    Convert decimal latitude/longitude to a Maidenhead grid locator.

    Args:
        lat:       Latitude  in decimal degrees (-90 to +90)
        lon:       Longitude in decimal degrees (-180 to +180)
        precision: Number of characters to return — 4, 6, or 8 (default 6)

    Returns:
        Grid locator string, e.g. 'CN87an' (6-char) or 'CN87an45' (8-char).
        First pair is always uppercase letters; second pair digits;
        third pair lowercase letters; fourth pair digits.

    Raises:
        ValueError: if lat/lon are out of range or precision is not 4/6/8.
    """
    if precision not in _GRID_PRECISIONS:
        raise ValueError(f"precision must be 4, 6, or 8 — got {precision}")
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"lat {lat} out of range (-90 to +90)")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"lon {lon} out of range (-180 to +180)")

    # Shift to positive origin: lon 0-360, lat 0-180
    rem_lon = lon + 180.0
    rem_lat = lat + 90.0

    grid = []
    n_pairs = precision // 2
    for lon_div, lat_div, charset in _PAIR_STEPS[:n_pairs]:
        lon_idx = int(rem_lon / lon_div)
        lat_idx = int(rem_lat / lat_div)
        # Clamp to charset length (handles exact upper boundary)
        lon_idx = min(lon_idx, len(charset) - 1)
        lat_idx = min(lat_idx, len(charset) - 1)
        grid.append(charset[lon_idx])
        grid.append(charset[lat_idx])
        rem_lon -= lon_idx * lon_div
        rem_lat -= lat_idx * lat_div

    return "".join(grid)


def grid_to_latlon(grid: str) -> tuple[float, float]:
    """
    Convert a Maidenhead grid locator to the decimal lat/lon of its centre.

    Args:
        grid: 4-, 6-, or 8-character Maidenhead locator.  The field pair is
              case-insensitive; subsquare pair may be upper or lower case.

    Returns:
        (latitude, longitude) in decimal degrees at the centre of the square.

    Raises:
        ValueError: if the grid string length or characters are invalid.
    """
    g = grid.strip()
    if len(g) not in _GRID_PRECISIONS:
        raise ValueError(
            f"Grid locator must be 4, 6, or 8 characters — got {len(g)}: {g!r}"
        )

    n_pairs = len(g) // 2
    lon = -180.0
    lat =  -90.0

    for p, (lon_div, lat_div, charset) in enumerate(_PAIR_STEPS[:n_pairs]):
        # Normalise character case to match the charset
        if charset in (_DIGIT, _DIGIT2):
            lon_ch = g[p * 2]
            lat_ch = g[p * 2 + 1]
        elif charset == _UPPER:
            lon_ch = g[p * 2].upper()
            lat_ch = g[p * 2 + 1].upper()
        else:
            lon_ch = g[p * 2].lower()
            lat_ch = g[p * 2 + 1].lower()

        if lon_ch not in charset or lat_ch not in charset:
            raise ValueError(
                f"Invalid character {g[p*2]!r}/{g[p*2+1]!r} at pair {p+1} "
                f"(expected characters from {charset!r})"
            )
        lon += charset.index(lon_ch) * lon_div
        lat += charset.index(lat_ch) * lat_div

    # Offset to centre of the finest square
    last_lon_div, last_lat_div, _ = _PAIR_STEPS[n_pairs - 1]
    lon += last_lon_div / 2.0
    lat += last_lat_div / 2.0

    # Round to 6 decimal places (~0.1 m precision)
    return round(lat, 6), round(lon, 6)


# ---------------------------------------------------------------------------
# Field comparison utilities
# ---------------------------------------------------------------------------

def normalise_for_compare(value: str, field: str) -> str:
    """
    Normalise a field value for comparison purposes:
      - Integer fields: strip leading zeros, compare as int string
      - Gridsquare: uppercase, truncate to 4 chars
      - All others: uppercase, strip whitespace
    """
    v = value.strip().upper()
    if field in INTEGER_FIELDS:
        try:
            return str(int(v))
        except ValueError:
            return v
    if field in ("GRIDSQUARE", "MY_GRIDSQUARE"):
        return v[:4]
    return v


def is_numeric_only(value: str) -> bool:
    """Return True if value contains only digits (e.g. Japanese prefecture codes)."""
    return value.strip().isdigit()


def fields_match(lotw_val: str, qrz_val: str, field: str) -> bool:
    """
    Return True if LoTW and QRZ values are considered equivalent for this field.
    Handles case, leading zeros, gridsquare prefix matching, and country
    name variants.
    """
    lv = normalise_for_compare(lotw_val, field)
    qv = normalise_for_compare(qrz_val,  field)

    if field == "MY_COUNTRY":
        lv = normalise_my_country(lotw_val).upper()
        qv = qrz_val.strip().upper()

    return lv == qv
