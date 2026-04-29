"""
gpx_extract.py
==============
Extract and filter geocaches from a Groundspeak or GSAK GPX file.

Detects source format automatically (looks for <gsak:...> elements).
Filters and emits a clean GPX (minus long_description, encoded_hints,
and other-finder logs by default) plus a pipe-delimited CSV.

Typical workflow
----------------
    # All your finds, default outputs
    python gpx_extract.py --gpx myfinds.gpx

    # Single-day found filter (--date / --after / --before are synonyms for --found-*)
    python gpx_extract.py --gpx myfinds.gpx --date 2009-08-01
    python gpx_extract.py --gpx myfinds.gpx --found-date 2009-08-01

    # Found date range
    python gpx_extract.py --gpx myfinds.gpx --found-after 2009-01-01 --found-before 2009-12-31
    python gpx_extract.py --gpx myfinds.gpx --after 2009-01-01 --before 2009-12-31

    # Placed (wpt) date range -- when the cache was listed, not when you found it
    python gpx_extract.py --gpx myfinds.gpx --placed-after 2005-01-01 --placed-before 2007-12-31

    # Difficulty / terrain (comma-list or range)
    python gpx_extract.py --gpx myfinds.gpx --difficulty 1,1.5,2 --terrain 3-5

    # Cache type filter (alias or comma-separated aliases)
    python gpx_extract.py --gpx myfinds.gpx --cache-type traditional
    python gpx_extract.py --gpx myfinds.gpx --cache-type event,cito
    python gpx_extract.py --gpx myfinds.gpx --cache-type mystery,earth

    # Country / state filter
    python gpx_extract.py --gpx myfinds.gpx --country "United States" --state Washington

    # Specific GC codes
    python gpx_extract.py --gpx myfinds.gpx --gccode GCMYJK,GC13J6A

    # List all distinct cache types in the file (diagnostic)
    python gpx_extract.py --gpx myfinds.gpx --list-types

    # Custom outputs
    python gpx_extract.py --gpx myfinds.gpx --output-gpx filtered.gpx --output-csv filtered.csv

gc.key
------
Place gc.key in the same directory as the script, or in ~/.gc.key.
Format (simple key=value, no section header):

    finder=jim_carson
    finder_id=1730833

finder_id (numeric) is preferred when matching logs; finder (username) is
the fallback.  If someone has changed their handle, the numeric ID remains
stable.  --finder on the CLI overrides both (accepts either the username
or a numeric ID).

Finder filtering
----------------
By default, only logs belonging to YOU (identified via gc.key or --finder)
are retained in the GPX output.  If no finder identity is configured,
all logs are retained and a warning is issued.

Suppressed by default (GPX output)
------------------------------------
    <groundspeak:long_description>   (use --include-longdesc to restore)
    <groundspeak:encoded_hints>      (use --include-hints to restore)
    Logs by other finders            (use --include-all-logs to restore)

CSV output
----------
Delimiter: pipe | (override with --delimiter)
Columns: gccode | name | lat | lon | found_date | difficulty | terrain |
         cache_type | container | country | state | county | placed_by |
         archived | available | short_description | source

long_description, encoded_hints and log text are never written to CSV
to avoid delimiter collisions.

Date filters
------------
Found date (your log):   --found-after / --found-before / --found-date
                         --after / --before / --date  (synonyms for found)
Placed date (wpt time):  --placed-after / --placed-before / --placed-date
Both date pairs accept YYYY-MM-DD or YYYYMMDD.  --*-date is shorthand for
setting both --*-after and --*-before to the same day.

Cache type filter (--cache-type)
----------------------------------
Accepts one or more comma-separated aliases (case-insensitive).
" Cache" suffix is stripped before matching, so "Traditional Cache" -> "Traditional".

  Alias        Matches raw type (substring, case-insensitive)
  -----------  -----------------------------------------------
  traditional  Traditional, Project APE, Groundspeak HQ
  multi        Multi
  mystery      Mystery, Unknown
  earth        Earth
  letterbox    Letterbox
  wherigo      Wherigo
  event        Event, Mega-Event, Giga-Event, Community Celebration,
               Geocaching HQ Celebration, Geocaching HQ Block Party,
               GPS Adventures Exhibit
  cito         Cache In Trash Out
  virtual      Virtual, Locationless, Lab
  webcam       Webcam

Use --list-types to see all raw cache types present in a given GPX file.

Attribute filter (--attribute)
--------------------------------
Comma-separated list of terms; ALL must match (AND logic).
Prefix with ! to require the attribute be absent.
Each term is either a numeric ID or a case-insensitive text substring.
Text is matched against decoded names (HTML entities resolved).

  Examples:
    --attribute "medium hike"          has medium hike attribute (inc=1)
    --attribute "!dogs"                dogs attribute explicitly absent
    --attribute "scenic view,!ticks"   scenic AND no ticks
    --attribute 56                     same as "medium hike" (ID 56)
    --attribute "56,!1"                medium hike AND no dogs (mixed)

  Use --list-attributes to see all attributes in a file with counts and IDs.

Requirements
------------
    Python 3.9+ (standard library only - no pip installs needed)

2026-04-28  Jim Carson (WT8P)
"""

import argparse
import csv
import logging
import re
import sys
from pathlib import Path
from datetime import date, datetime, timezone
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# XML namespaces
# ---------------------------------------------------------------------------
NS_GPX         = "http://www.topografix.com/GPX/1/0"
NS_GROUNDSPEAK = "http://www.groundspeak.com/cache/1/0/1"
NS_GSAK        = "http://www.gsak.net/xmlv1/6"
NS_EXTRACT     = "urn:gpx-extract"          # our own metadata tag

# Register for ElementTree serialisation (prefix -> uri)
ET.register_namespace("",            NS_GPX)
ET.register_namespace("groundspeak", NS_GROUNDSPEAK)
ET.register_namespace("gsak",        NS_GSAK)
ET.register_namespace("gpx_extract", NS_EXTRACT)

def _gs(tag: str) -> str:
    """Qualified name in the groundspeak namespace."""
    return f"{{{NS_GROUNDSPEAK}}}{tag}"

def _gsak(tag: str) -> str:
    return f"{{{NS_GSAK}}}{tag}"

def _gpx(tag: str) -> str:
    return f"{{{NS_GPX}}}{tag}"

# ---------------------------------------------------------------------------
# gc.key loader
# ---------------------------------------------------------------------------

def load_gc_key(script_dir: Path) -> dict[str, str]:
    """
    Look for gc.key in (1) script directory, (2) ~/.gc.key.
    Returns dict with keys: finder, finder_id  (both optional).
    """
    candidates = [script_dir / "gc.key", Path.home() / ".gc.key"]
    for path in candidates:
        if path.exists():
            cfg: dict[str, str] = {}
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    cfg[k.strip().lower()] = v.strip()
            log.info("gc.key : loaded from %s", path)
            return cfg
    return {}


def resolve_finder(args_finder: str, gc_cfg: dict[str, str]) -> tuple[str, str]:
    """
    Return (finder_name, finder_id) from CLI arg or gc.key.
    Either may be empty string if not known.
    CLI --finder overrides gc.key; if --finder looks numeric, treat as id.
    """
    if args_finder:
        if re.fullmatch(r"\d+", args_finder):
            return "", args_finder          # numeric ID provided on CLI
        return args_finder, ""              # username provided on CLI
    return gc_cfg.get("finder", ""), gc_cfg.get("finder_id", "")


def log_belongs_to_me(log_elem: ET.Element,
                      finder_name: str, finder_id: str) -> bool:
    """Return True if this <groundspeak:log> was written by the configured finder."""
    finder_el = log_elem.find(_gs("finder"))
    if finder_el is None:
        return False
    # Prefer numeric ID match (stable across handle changes)
    if finder_id:
        el_id = finder_el.get("id", "")
        if el_id == finder_id:
            return True
    if finder_name:
        el_text = (finder_el.text or "").strip().lower()
        if el_text == finder_name.lower():
            return True
    return False

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _normalise_date_arg(value: str, argname: str) -> str:
    """Accept YYYY-MM-DD or YYYYMMDD; return YYYYMMDD for comparisons."""
    v = value.strip()
    if re.fullmatch(r"\d{8}", v):
        return v
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v.replace("-", "")
    log.error("--%s value %r is not a recognised date (YYYY-MM-DD or YYYYMMDD)", argname, value)
    sys.exit(1)


def _gpx_time_to_compact(iso_str: str) -> str:
    """
    Convert ISO 8601 wpt time (e.g. '2009-06-14T19:00:00Z') to YYYYMMDD.
    Returns '' on parse failure.
    """
    if not iso_str:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(iso_str.strip(), fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return ""


def _in_range(compact_date: str, after: str, before: str) -> bool:
    if after  and compact_date < after:  return False
    if before and compact_date > before: return False
    return True

# ---------------------------------------------------------------------------
# Difficulty / terrain value-set parser
# ---------------------------------------------------------------------------

def _parse_rating_filter(spec: str, argname: str) -> set[float]:
    """
    Parse a difficulty or terrain filter spec into a set of allowed float values.

    Accepts:
        "3,3.5,4.0"     -> {3.0, 3.5, 4.0}
        "3-4.5"         -> {3.0, 3.5, 4.0, 4.5}
        "1,2-3,4.5"     -> {1.0, 2.0, 2.5, 3.0, 4.5}  (mixed ok)
    """
    result: set[float] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-", 1)
            try:
                lo, hi = float(parts[0]), float(parts[1])
            except ValueError:
                log.error("--%s: invalid range %r", argname, token)
                sys.exit(1)
            if lo > hi:
                lo, hi = hi, lo
            v = lo
            while v <= hi + 1e-9:
                result.add(round(v, 1))
                v += 0.5
        else:
            try:
                result.add(float(token))
            except ValueError:
                log.error("--%s: invalid value %r", argname, token)
                sys.exit(1)
    return result


def _passes_rating(element_text: str | None, allowed: set[float]) -> bool:
    """Return True if the element's float value is in allowed (or allowed is empty)."""
    if not allowed:
        return True
    if not element_text:
        return False
    try:
        return round(float(element_text.strip()), 1) in allowed
    except ValueError:
        return False

# ---------------------------------------------------------------------------
# Cache type groups
# ---------------------------------------------------------------------------
# Maps user-facing alias -> list of substrings matched against the raw
# cache_type field *after* stripping a trailing " Cache" suffix (case-insensitive).
# Substrings are themselves matched case-insensitively.
CACHE_TYPE_GROUPS: dict[str, list[str]] = {
    "traditional": ["traditional", "project ape", "groundspeak hq"],
    "multi":       ["multi"],
    "mystery":     ["mystery", "unknown"],
    "earth":       ["earth"],
    "letterbox":   ["letterbox"],
    "wherigo":     ["wherigo"],
    "event":       [
        "event",                        # catches Event, Mega-Event, Giga-Event
        "community celebration",
        "geocaching hq celebration",
        "geocaching hq block party",
        "gps adventures exhibit",
    ],
    "cito":        ["cache in trash out"],
    "virtual":     ["virtual", "locationless", "lab"],
    "webcam":      ["webcam"],
}

_CACHE_SUFFIX_RE = re.compile(r"\s*cache\s*$", re.IGNORECASE)


def _normalise_cache_type(raw: str) -> str:
    """Strip trailing ' Cache' suffix and lowercase for matching."""
    return _CACHE_SUFFIX_RE.sub("", raw).strip().lower()


def parse_cache_type_filter(spec: str) -> list[str]:
    """
    Parse a comma-separated list of type aliases, validate each against
    CACHE_TYPE_GROUPS, and return the list of recognised aliases.
    Exits with an error if any alias is unknown.
    """
    aliases = [t.strip().lower() for t in spec.split(",") if t.strip()]
    unknown = [a for a in aliases if a not in CACHE_TYPE_GROUPS]
    if unknown:
        log.error(
            "--cache-type: unknown alias(es): %s.  Valid aliases: %s",
            ", ".join(unknown),
            ", ".join(sorted(CACHE_TYPE_GROUPS)),
        )
        sys.exit(1)
    return aliases


def _passes_cache_type(cache_type_raw: str, aliases: list[str]) -> bool:
    """Return True if cache_type_raw matches any of the requested aliases."""
    if not aliases:
        return True
    normalised = _normalise_cache_type(cache_type_raw)
    for alias in aliases:
        for substring in CACHE_TYPE_GROUPS[alias]:
            if substring in normalised:
                return True
    return False



# ---------------------------------------------------------------------------
# Attribute definitions
# ---------------------------------------------------------------------------
# Sourced from https://www.geocaching.com/about/icons.aspx plus observed data.
# Key: numeric attribute ID (as string, matching the id= attribute in GPX).
# Value: canonical display name (HTML entities already decoded).
#
# inc="1" means the attribute IS present/positive.
# inc="0" means the attribute is explicitly ABSENT/negative.
# Both polarities share the same ID and name; the inc value distinguishes them.
#
# To add new IDs: append to the dict — no other changes needed.
ATTRIBUTE_NAMES: dict[str, str] = {
    "1":  "Dogs",
    "2":  "Access/parking fee",
    "3":  "Climbing gear required",
    "4":  "Boat required",
    "5":  "Scuba gear required",
    "6":  "Recommended for kids",
    "7":  "Takes less than one hour",
    "8":  "Scenic view",
    "9":  "Significant hike",
    "10": "Difficult climb",
    "11": "May require wading",
    "12": "May require swimming",
    "13": "Available 24/7",
    "14": "Recommended at night",
    "15": "Available in winter",
    "17": "Poisonous plants",
    "18": "Dangerous animals",
    "19": "Ticks",
    "20": "Abandoned mine",
    "21": "Cliff/falling rocks",
    "22": "Hunting area",
    "23": "Dangerous area",
    "24": "Wheelchair accessible",
    "25": "Parking nearby",
    "26": "Public transportation nearby",
    "27": "Drinking water nearby",
    "28": "Public restrooms nearby",
    "29": "Telephone nearby",
    "30": "Picnic tables nearby",
    "31": "Camping nearby",
    "32": "Bicycles",
    "33": "Motorcycles",
    "34": "Quads",
    "35": "Off-road vehicles",
    "36": "Snowmobiles",
    "37": "Horses",
    "38": "Campfires",
    "39": "Thorns",
    "40": "Stealth required",
    "41": "Stroller accessible",
    "42": "Needs maintenance",
    "43": "Livestock nearby",
    "44": "Flashlight required",
    "45": "Lost and Found tour",
    "46": "Trucks/RVs",
    "47": "Field puzzle",
    "48": "UV light required",
    "49": "May require snowshoes",
    "50": "May require cross country skis",
    "51": "Special tool required",
    "52": "Night cache",
    "53": "Park and grab",
    "54": "Abandoned structure",
    "55": "Short hike (<1 km)",
    "56": "Medium hike (1 km-10 km)",
    "57": "Long hike (>10 km)",
    "58": "Fuel nearby",
    "59": "Food nearby",
    "60": "Wireless beacon",
    "61": "Partnership cache",
    "62": "Seasonal access",
    "63": "Recommended for tourists",
    "64": "Tree climbing required",
    "65": "Yard (private residence)",
    "66": "Teamwork cache",
    "67": "GeoTour",
    "69": "Bonus cache",
    "70": "Power trail",
    "71": "Challenge cache",
    "72": "Geocaching.com solution checker",
    # Additional types observed in practice
    "85": "Groundspeak HQ",
    "86": "Lab cache",
}

# HTML entities that appear in attribute text from GPX files
_HTML_ENTITIES = {"&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&#39;": "'"}
_ENTITY_RE = re.compile("|".join(re.escape(k) for k in _HTML_ENTITIES))


def _decode_entities(text: str) -> str:
    """Replace common HTML entities with their character equivalents."""
    return _ENTITY_RE.sub(lambda m: _HTML_ENTITIES[m.group()], text)


# ---------------------------------------------------------------------------
# Attribute filter
# ---------------------------------------------------------------------------

class AttrTerm:
    """A single parsed attribute filter term."""
    __slots__ = ("negate", "is_id", "id_str", "text_lower")

    def __init__(self, negate: bool, is_id: bool,
                 id_str: str = "", text_lower: str = ""):
        self.negate     = negate
        self.is_id      = is_id
        self.id_str     = id_str        # numeric ID string, e.g. "56"
        self.text_lower = text_lower    # lowercase substring for text match

    def __repr__(self):
        polarity = "!" if self.negate else "+"
        spec = self.id_str if self.is_id else repr(self.text_lower)
        return f"AttrTerm({polarity}{spec})"


def parse_attribute_filter(spec: str) -> list[AttrTerm]:
    """
    Parse a comma-separated attribute filter spec into AttrTerm objects.

    Syntax per token:
        "medium hike"     positive text match (case-insensitive substring)
        "!dogs"           negative text match (attribute must NOT be present inc=1)
        "56"              positive numeric ID match
        "!56"             negative numeric ID match

    Numeric IDs are matched against the id= attribute in <groundspeak:attribute>.
    Text is matched as a case-insensitive substring against the decoded attribute
    name (HTML entities resolved).

    Unknown numeric IDs produce a warning but are not fatal (future-proofing).
    """
    terms: list[AttrTerm] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        negate = raw.startswith("!")
        token  = raw[1:].strip() if negate else raw

        if re.fullmatch(r"\d+", token):
            # Numeric ID
            if token not in ATTRIBUTE_NAMES:
                log.warning(
                    "--attribute: ID %r not in known attribute table; "
                    "will match by ID anyway.", token
                )
            terms.append(AttrTerm(negate=negate, is_id=True, id_str=token))
        else:
            terms.append(AttrTerm(negate=negate, is_id=False,
                                  text_lower=token.lower()))

    return terms


def _passes_attributes(cache_attrs: list[dict], terms: list[AttrTerm]) -> bool:
    """
    Return True if the cache's attribute list satisfies ALL terms (AND logic).

    For positive terms  (negate=False): at least one inc="1" attribute must match.
    For negative terms  (negate=True):  no   inc="1" attribute may match
                                        (inc="0" entries are irrelevant to negation).

    Matching:
        is_id=True  -> compare term.id_str  to attr["id"]
        is_id=False -> compare term.text_lower as substring of
                       decoded attr["text"].lower()
    """
    if not terms:
        return True

    # Pre-build a set of positive-inc (inc="1") attribute IDs and decoded
    # lowercase names for efficient lookup.
    positive_ids:   set[str] = set()
    positive_texts: list[str] = []
    for a in cache_attrs:
        if a["inc"] == "1":
            positive_ids.add(a["id"])
            positive_texts.append(_decode_entities(a["text"]).lower())

    for term in terms:
        if term.is_id:
            matched = term.id_str in positive_ids
        else:
            matched = any(term.text_lower in t for t in positive_texts)

        # Positive term: must match; negative term: must NOT match
        if term.negate and matched:
            return False
        if not term.negate and not matched:
            return False

    return True


def _describe_attr_terms(terms: list[AttrTerm]) -> str:
    """Human-readable summary of attribute terms for log output."""
    parts = []
    for t in terms:
        polarity = "NOT " if t.negate else ""
        if t.is_id:
            name = ATTRIBUTE_NAMES.get(t.id_str, f"ID:{t.id_str}")
            parts.append(f"{polarity}{t.id_str} ({name})")
        else:
            parts.append(f"{polarity}'{t.text_lower}'")
    return ", ".join(parts)


def detect_source(tree: ET.ElementTree) -> str:
    """Return 'gsak' if any gsak: element is found, else 'groundspeak'."""
    root = tree.getroot()
    for elem in root.iter():
        if elem.tag.startswith(f"{{{NS_GSAK}}}"):
            return "gsak"
    return "groundspeak"

# ---------------------------------------------------------------------------
# Core GPX parsing
# ---------------------------------------------------------------------------

def parse_gpx(gpx_path: Path) -> ET.ElementTree:
    """
    Parse a GPX file.  If ET raises a ParseError, attempt a one-shot repair
    by appending a missing </gpx> closing tag (common with truncated exports).
    """
    try:
        return ET.parse(gpx_path)
    except ET.ParseError as exc:
        log.warning("GPX parse error (%s) -- attempting repair (missing </gpx>?)", exc)
        content = gpx_path.read_bytes().rstrip()
        if not content.endswith(b"</gpx>"):
            content += b"\n</gpx>\n"
            try:
                tree = ET.ElementTree(ET.fromstring(content))
                log.info("GPX repair succeeded.")
                return tree
            except ET.ParseError as exc2:
                log.error("GPX repair failed: %s", exc2)
        else:
            log.error("Failed to parse GPX file %s: %s", gpx_path, exc)
        sys.exit(1)


def get_text(parent: ET.Element | None, tag: str, default: str = "") -> str:
    """Safely retrieve .text of a child element."""
    if parent is None:
        return default
    el = parent.find(tag)
    return (el.text or "").strip() if el is not None else default


def extract_wpt_data(wpt: ET.Element) -> dict:
    """
    Extract all useful fields from a <wpt> element into a plain dict.
    Returns None if this wpt has no <groundspeak:cache> child.
    """
    cache = wpt.find(_gs("cache"))
    if cache is None:
        return None                     # not a geocache waypoint

    d = {}
    d["lat"]        = wpt.get("lat", "")
    d["lon"]        = wpt.get("lon", "")
    d["wpt_time"]   = get_text(wpt, _gpx("time"))
    d["gccode"]     = get_text(wpt, _gpx("name"))
    d["desc"]       = get_text(wpt, _gpx("desc"))
    d["url"]        = get_text(wpt, _gpx("url"))
    d["urlname"]    = get_text(wpt, _gpx("urlname"))
    d["sym"]        = get_text(wpt, _gpx("sym"))
    d["wpt_type"]   = get_text(wpt, _gpx("type"))

    d["cache_id"]   = cache.get("id", "")
    d["archived"]   = cache.get("archived", "")
    d["available"]  = cache.get("available", "")
    d["name"]       = get_text(cache, _gs("name"))
    d["placed_by"]  = get_text(cache, _gs("placed_by"))
    d["owner"]      = get_text(cache, _gs("owner"))
    d["cache_type"] = get_text(cache, _gs("type"))
    d["container"]  = get_text(cache, _gs("container"))
    d["difficulty"] = get_text(cache, _gs("difficulty"))
    d["terrain"]    = get_text(cache, _gs("terrain"))
    d["country"]    = get_text(cache, _gs("country"))
    d["state"]      = get_text(cache, _gs("state"))
    d["short_description"] = get_text(cache, _gs("short_description"))
    d["long_description"]  = get_text(cache, _gs("long_description"))
    d["encoded_hints"]     = get_text(cache, _gs("encoded_hints"))

    # GSAK-specific county (may be absent)
    gsak_wpt = wpt.find(_gsak("wptExtension"))     # common GSAK wrapper
    if gsak_wpt is not None:
        d["county"] = get_text(gsak_wpt, _gsak("County"))
    else:
        d["county"] = ""

    # Attributes list
    attrs_el = cache.find(_gs("attributes"))
    d["attributes"] = []
    if attrs_el is not None:
        for attr in attrs_el.findall(_gs("attribute")):
            d["attributes"].append({
                "id":  attr.get("id", ""),
                "inc": attr.get("inc", ""),
                "text": (attr.text or "").strip(),
            })

    # Logs - keep raw elements for later filtering
    logs_el = cache.find(_gs("logs"))
    d["logs"] = []
    if logs_el is not None:
        d["logs"] = list(logs_el.findall(_gs("log")))

    # Found date: take date from the user's own log (first matching)
    d["found_date"] = ""

    return d

# ---------------------------------------------------------------------------
# Filter logic
# ---------------------------------------------------------------------------

def apply_filters(
    wpts_data:        list[dict],
    found_after:      str,
    found_before:     str,
    placed_after:     str,
    placed_before:    str,
    difficulty_set:   set[float],
    terrain_set:      set[float],
    countries:        list[str],
    states:           list[str],
    gccodes:          set[str],
    cache_type_aliases: list[str],
    attr_terms:       list,          # list[AttrTerm]
    finder_name:      str,
    finder_id:        str,
) -> list[dict]:
    """
    Apply all active filters.  All conditions are AND.
    Also sets d["found_date"] and d["my_logs"] for each passing record.

    Note: found_date filtering requires log-scanning to derive the date first,
    so log splitting always happens before the found-date gate.
    """
    results = []
    for d in wpts_data:
        # -- GC code filter (fast exit)
        if gccodes and d["gccode"].upper() not in gccodes:
            continue

        # -- Cache type filter
        if not _passes_cache_type(d["cache_type"], cache_type_aliases):
            continue

        # -- Attribute filter
        if not _passes_attributes(d["attributes"], attr_terms):
            continue

        # -- Placed date filter (wpt <time>)
        placed_compact = _gpx_time_to_compact(d["wpt_time"])
        if not _in_range(placed_compact, placed_after, placed_before):
            continue

        # -- Difficulty / terrain filters
        if not _passes_rating(d["difficulty"], difficulty_set):
            continue
        if not _passes_rating(d["terrain"], terrain_set):
            continue

        # -- Country / state filters (case-insensitive substring)
        if countries:
            c_lower = d["country"].lower()
            if not any(c.lower() in c_lower for c in countries):
                continue
        if states:
            s_lower = d["state"].lower()
            if not any(s.lower() in s_lower for s in states):
                continue

        # -- Split logs into mine vs others (needed for found_date derivation)
        have_finder = bool(finder_name or finder_id)
        my_logs    = []
        other_logs = []
        for log_el in d["logs"]:
            if have_finder and log_belongs_to_me(log_el, finder_name, finder_id):
                my_logs.append(log_el)
            else:
                other_logs.append(log_el)

        d["my_logs"]    = my_logs
        d["other_logs"] = other_logs

        # Derive found_date from first of my logs (chronologically earliest)
        found_compact = ""
        for log_el in my_logs:
            date_el = log_el.find(_gs("date"))
            if date_el is not None:
                found_compact = _gpx_time_to_compact(date_el.text or "")
                break
        d["found_date"] = found_compact

        # -- Found date filter (applied after log scanning)
        if found_after or found_before:
            if not _in_range(found_compact, found_after, found_before):
                continue

        results.append(d)

    return results


# ---------------------------------------------------------------------------
# GPX output
# ---------------------------------------------------------------------------

_GPX_HEADER = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<gpx xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'version="1.0" '
    'xsi:schemaLocation="http://www.topografix.com/GPX/1/0 '
    'http://www.topografix.com/GPX/1/0/gpx.xsd '
    'http://www.groundspeak.com/cache/1/0/1 '
    'http://www.groundspeak.com/cache/1/0/1/cache.xsd" '
    'creator="gpx_extract" '
    'xmlns="http://www.topografix.com/GPX/1/0">\n'
)


def _sub(parent: ET.Element, tag: str, text: str = "", **attribs) -> ET.Element:
    el = ET.SubElement(parent, tag, **attribs)
    if text:
        el.text = text
    return el


def build_wpt_element(d: dict,
                      include_longdesc:  bool,
                      include_hints:     bool,
                      include_all_logs:  bool,
                      source:            str) -> ET.Element:
    """Build a clean <wpt> ET element from an extracted data dict."""

    wpt = ET.Element("wpt", lat=d["lat"], lon=d["lon"])

    _sub(wpt, _gpx("time"),    d["wpt_time"])
    _sub(wpt, _gpx("name"),    d["gccode"])
    _sub(wpt, _gpx("desc"),    d["desc"])
    _sub(wpt, _gpx("url"),     d["url"])
    _sub(wpt, _gpx("urlname"), d["urlname"])
    _sub(wpt, _gpx("sym"),     d["sym"])
    _sub(wpt, _gpx("type"),    d["wpt_type"])

    cache_attribs = {
        "id":       d["cache_id"],
        "archived": d["archived"],
        "available": d["available"],
        f"xmlns:groundspeak": NS_GROUNDSPEAK,
    }
    cache = _sub(wpt, _gs("cache"), **cache_attribs)

    _sub(cache, _gs("name"),       d["name"])
    _sub(cache, _gs("placed_by"),  d["placed_by"])
    owner_el = _sub(cache, _gs("owner"), d["owner"])
    _sub(cache, _gs("type"),       d["cache_type"])
    _sub(cache, _gs("container"),  d["container"])

    # Attributes block
    if d["attributes"]:
        attrs_el = _sub(cache, _gs("attributes"))
        for a in d["attributes"]:
            attr_el = _sub(attrs_el, _gs("attribute"), a["text"],
                           id=a["id"], inc=a["inc"])

    _sub(cache, _gs("difficulty"), d["difficulty"])
    _sub(cache, _gs("terrain"),    d["terrain"])
    _sub(cache, _gs("country"),    d["country"])
    _sub(cache, _gs("state"),      d["state"])
    _sub(cache, _gs("short_description"), d["short_description"])

    if include_longdesc:
        _sub(cache, _gs("long_description"), d["long_description"])

    if include_hints:
        _sub(cache, _gs("encoded_hints"), d["encoded_hints"])

    # GSAK county passthrough
    if source == "gsak" and d.get("county"):
        gsak_ext = _sub(cache, _gsak("wptExtension"))
        _sub(gsak_ext, _gsak("County"), d["county"])

    # Logs
    logs_to_write = d.get("my_logs", [])
    if include_all_logs:
        logs_to_write = d.get("my_logs", []) + d.get("other_logs", [])

    if logs_to_write:
        logs_el = _sub(cache, _gs("logs"))
        for log_el in logs_to_write:
            logs_el.append(log_el)

    # Source metadata element (our addition)
    _sub(cache, f"{{{NS_EXTRACT}}}source", source)

    return wpt


def write_gpx(wpts_data: list[dict],
              output_path: Path,
              source: str,
              include_longdesc: bool,
              include_hints: bool,
              include_all_logs: bool) -> None:
    """Write a clean filtered GPX file."""
    lines = [_GPX_HEADER]

    for d in wpts_data:
        wpt_el = build_wpt_element(
            d, include_longdesc, include_hints, include_all_logs, source
        )
        # Indent for readability
        ET.indent(wpt_el, space="  ")
        lines.append("  " + ET.tostring(wpt_el, encoding="unicode") + "\n")

    lines.append("</gpx>\n")

    output_path.write_text("".join(lines), encoding="utf-8")
    log.info("GPX   : %d waypoints -> %s", len(wpts_data), output_path)

# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "gccode", "name", "lat", "lon",
    "found_date",        # date of YOUR log (YYYY-MM-DD), blank if none
    "wpt_date",          # wpt <time> field (placed/listed date)
    "difficulty", "terrain",
    "cache_type", "container",
    "country", "state", "county",
    "placed_by",
    "archived", "available",
    "short_description",
    "source",
]


def _compact_to_iso(compact: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD, or return as-is if not 8 digits."""
    if re.fullmatch(r"\d{8}", compact):
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    return compact


def _clean_text(text: str) -> str:
    """Strip newlines and normalise whitespace for CSV cells."""
    return " ".join(text.split())


def write_csv(wpts_data: list[dict],
              output_path: Path,
              delimiter: str) -> None:
    """Write pipe-delimited (or custom) CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(CSV_COLUMNS)
        for d in wpts_data:
            wpt_compact = _gpx_time_to_compact(d["wpt_time"])
            row = [
                d["gccode"],
                _clean_text(d["name"]),
                d["lat"],
                d["lon"],
                _compact_to_iso(d.get("found_date", "")),
                _compact_to_iso(wpt_compact),
                d["difficulty"],
                d["terrain"],
                d["cache_type"],
                d["container"],
                d["country"],
                d["state"],
                d.get("county", ""),
                _clean_text(d["placed_by"]),
                d["archived"],
                d["available"],
                _clean_text(d["short_description"]),
                d.get("source", ""),
            ]
            writer.writerow(row)
    log.info("CSV   : %d rows -> %s", len(wpts_data), output_path)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Extract and filter geocaches from a Groundspeak or GSAK GPX file. "
            "Outputs a clean GPX (minus long descriptions, hints, and "
            "other-finder logs by default) plus a pipe-delimited CSV."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join([
            "gc.key (same dir as script or ~/.gc.key):",
            "  finder=jim_carson",
            "  finder_id=1730833",
            "",
            "Rating filter examples:",
            "  --difficulty 3,3.5,4      comma-separated list",
            "  --terrain 3-5             range (0.5 steps)",
            "  --difficulty 1,2-3,4.5    mixed",
            "",
            "Cache type aliases (--cache-type):",
            "  traditional  multi  mystery  earth  letterbox  wherigo",
            "  event        cito   virtual  webcam",
            "  Use --list-types to see raw types in a file.",
            "",
            "Future filters (not yet implemented):",
            "  --attribute   Filter by attribute text (e.g. 'Medium hike')",
        ])
    )

    p.add_argument("--gpx", required=True, metavar="FILE",
        help="Source GPX file (Groundspeak Pocket Query or GSAK export)")

    # Finder identity
    p.add_argument("--finder", default="", metavar="NAME_OR_ID",
        help="Your Geocaching username or numeric finder ID (overrides gc.key)")

    # Found date filtering  (--after/--before/--date are synonyms for --found-*)
    found_grp = p.add_mutually_exclusive_group()
    found_grp.add_argument("--found-date", "--date", dest="found_date",
        default="", metavar="DATE",
        help="Found on this date (shorthand for --found-after DATE --found-before DATE)")
    found_grp.add_argument("--found-after", "--after", dest="found_after",
        default="", metavar="DATE",
        help="Found on or after this date (inclusive)")
    p.add_argument("--found-before", "--before", dest="found_before",
        default="", metavar="DATE",
        help="Found on or before this date (inclusive)")

    # Placed date filtering  (wpt <time> field = cache listing date)
    placed_grp = p.add_mutually_exclusive_group()
    placed_grp.add_argument("--placed-date", default="", metavar="DATE",
        help="Placed on this date (shorthand for --placed-after DATE --placed-before DATE)")
    placed_grp.add_argument("--placed-after", default="", metavar="DATE",
        help="Placed on or after this date (inclusive)")
    p.add_argument("--placed-before", default="", metavar="DATE",
        help="Placed on or before this date (inclusive)")

    # Cache type filter
    p.add_argument("--cache-type", default="", metavar="ALIAS[,ALIAS...]",
        help=("Cache type alias(es), comma-separated: "
              "traditional, multi, mystery, earth, letterbox, wherigo, "
              "event, cito, virtual, webcam"))

    # Rating filters
    p.add_argument("--difficulty", default="", metavar="SPEC",
        help="Difficulty filter, e.g. '1,1.5,2' or '3-5'")
    p.add_argument("--terrain", default="", metavar="SPEC",
        help="Terrain filter, e.g. '1,1.5,2' or '3-5'")

    # Geographic filters
    p.add_argument("--country", default="", metavar="NAME[,NAME...]",
        help="Country filter (case-insensitive substring, comma-separated)")
    p.add_argument("--state", default="", metavar="NAME[,NAME...]",
        help="State filter (case-insensitive substring, comma-separated)")

    # GC code filter
    p.add_argument("--gccode", default="", metavar="GC1,GC2,...",
        help="Comma-separated list of GC codes to extract")

    # Attribute filter
    p.add_argument("--attribute", default="", metavar="TERM[,TERM...]",
        help=("Attribute filter, comma-separated. "
              "Text substring or numeric ID; prefix with ! to negate. "
              "e.g. 'medium hike,!dogs' or '56,!1'. All terms must match (AND)."))

    # Diagnostics
    p.add_argument("--list-types", action="store_true",
        help="Print all distinct cache types found in the GPX file and exit")
    p.add_argument("--list-attributes", action="store_true",
        help="Print all attributes found in the GPX file with counts and IDs, then exit")

    # GPX content flags
    p.add_argument("--include-longdesc", action="store_true",
        help="Include <groundspeak:long_description> in GPX output")
    p.add_argument("--include-hints", action="store_true",
        help="Include <groundspeak:encoded_hints> in GPX output")
    p.add_argument("--include-all-logs", action="store_true",
        help="Include logs from all finders, not just yours")

    # Outputs
    p.add_argument("--output-gpx", default="gpx_extract.gpx", metavar="FILE",
        help="Output GPX file (default: gpx_extract.gpx)")
    p.add_argument("--output-csv", default="gpx_extract.csv", metavar="FILE",
        help="Output CSV file (default: gpx_extract.csv)")
    p.add_argument("--no-gpx", action="store_true",
        help="Suppress GPX output")
    p.add_argument("--no-csv", action="store_true",
        help="Suppress CSV output")
    p.add_argument("--delimiter", default="|", metavar="CHAR",
        help="CSV field delimiter (default: |)")

    return p


def main() -> None:
    args = build_parser().parse_args()

    # -- Validate input
    gpx_path = Path(args.gpx)
    if not gpx_path.exists():
        log.error("File not found: %s", gpx_path)
        sys.exit(1)

    # -- Load finder identity
    script_dir = Path(__file__).parent.resolve()
    gc_cfg = load_gc_key(script_dir)
    finder_name, finder_id = resolve_finder(args.finder, gc_cfg)

    if not finder_name and not finder_id:
        log.warning(
            "No finder identity configured. "
            "All logs will be retained. "
            "Add finder= / finder_id= to gc.key or use --finder."
        )

    # -- Parse GPX (before date resolution so --list-types works early)
    log.info("=== GPX Extract ===")
    log.info("Source  : %s", gpx_path)

    tree   = parse_gpx(gpx_path)
    source = detect_source(tree)
    log.info("Format  : %s", source)

    root = tree.getroot()
    all_wpts = root.findall(_gpx("wpt"))
    log.info("Waypoints in file: %d", len(all_wpts))

    wpts_data = []
    for wpt in all_wpts:
        d = extract_wpt_data(wpt)
        if d is not None:
            d["source"] = source
            wpts_data.append(d)

    log.info("Geocache waypoints: %d", len(wpts_data))

    # -- --list-types: diagnostic mode, print and exit
    if args.list_types:
        raw_types: dict[str, int] = {}
        for d in wpts_data:
            t = d["cache_type"]
            raw_types[t] = raw_types.get(t, 0) + 1
        print(f"\nCache types in {gpx_path.name}:")
        for t, count in sorted(raw_types.items(), key=lambda x: -x[1]):
            normalised = _normalise_cache_type(t)
            matched_aliases = [
                alias for alias, substrings in CACHE_TYPE_GROUPS.items()
                if any(s in normalised for s in substrings)
            ]
            alias_str = f"  -> alias: {', '.join(matched_aliases)}" if matched_aliases else "  -> (no alias match)"
            print(f"  {count:4d}  {t}{alias_str}")
        print()
        sys.exit(0)

    # -- --list-attributes: diagnostic mode, print and exit
    if args.list_attributes:
        # Tally yes (inc=1) and no (inc=0) counts per attribute ID
        yes_count: dict[str, int] = {}
        no_count:  dict[str, int] = {}
        id_to_name: dict[str, str] = {}
        for d in wpts_data:
            for a in d["attributes"]:
                attr_id = a["id"]
                if attr_id not in id_to_name:
                    id_to_name[attr_id] = (
                        ATTRIBUTE_NAMES.get(attr_id)
                        or _decode_entities(a["text"])
                    )
                if a["inc"] == "1":
                    yes_count[attr_id] = yes_count.get(attr_id, 0) + 1
                else:
                    no_count[attr_id]  = no_count.get(attr_id,  0) + 1

        # Union of all seen IDs, sorted by yes count desc, then ID numeric
        all_ids = sorted(
            yes_count.keys() | no_count.keys(),
            key=lambda i: (-yes_count.get(i, 0), int(i))
        )

        # Column widths: fit the widest yes/no count
        max_yes = max((yes_count.get(i, 0) for i in all_ids), default=0)
        max_no  = max((no_count.get(i,  0) for i in all_ids), default=0)
        yw = max(3, len(str(max_yes)))   # "yes" header is 3 chars minimum
        nw = max(2, len(str(max_no)))    # "no"  header is 2 chars minimum

        print(f"\nAttributes in {gpx_path.name}:")
        print(f"  {'ID':>4}  {'Name':<40}  {'Yes':>{yw}}  {'No':>{nw}}")
        print(f"  {'--':>4}  {'----':<40}  {'---':>{yw}}  {'--':>{nw}}")
        for attr_id in all_ids:
            name = id_to_name.get(attr_id, f"(unknown ID {attr_id})")
            yes = yes_count.get(attr_id, 0)
            no  = no_count.get(attr_id,  0)
            yes_str = str(yes) if yes else "-"
            no_str  = str(no)  if no  else "-"
            print(f"  {attr_id:>4}  {name:<40}  {yes_str:>{yw}}  {no_str:>{nw}}")
        print()
        sys.exit(0)

    # -- Resolve found date bounds
    def _resolve_date_pair(date_val, after_val, before_val, prefix):
        if date_val:
            if before_val:
                log.error("--%s-before cannot be used with --%s-date", prefix, prefix)
                sys.exit(1)
            n = _normalise_date_arg(date_val, f"{prefix}-date")
            return n, n
        a = _normalise_date_arg(after_val,  f"{prefix}-after")  if after_val  else ""
        b = _normalise_date_arg(before_val, f"{prefix}-before") if before_val else ""
        if a and b and a > b:
            log.error("--%s-after (%s) is later than --%s-before (%s)", prefix, a, prefix, b)
            sys.exit(1)
        return a, b

    found_after,  found_before  = _resolve_date_pair(
        args.found_date, args.found_after, args.found_before, "found")
    placed_after, placed_before = _resolve_date_pair(
        args.placed_date, args.placed_after, args.placed_before, "placed")

    # -- Rating filter sets
    difficulty_set = _parse_rating_filter(args.difficulty, "difficulty") if args.difficulty else set()
    terrain_set    = _parse_rating_filter(args.terrain,    "terrain")    if args.terrain    else set()

    # -- Cache type aliases
    cache_type_aliases = parse_cache_type_filter(args.cache_type) if args.cache_type else []

    # -- Geographic filters
    countries = [c.strip() for c in args.country.split(",") if c.strip()] if args.country else []
    states    = [s.strip() for s in args.state.split(",")   if s.strip()] if args.state   else []

    # -- GC code filter
    gccodes = {g.strip().upper() for g in args.gccode.split(",") if g.strip()} if args.gccode else set()

    # -- Attribute filter terms
    attr_terms = parse_attribute_filter(args.attribute) if args.attribute else []

    # -- Log active filters
    def _fmt_date(compact):
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}" if compact else ""

    if args.found_date:
        log.info("Found date     : %s", _fmt_date(found_after))
    else:
        if found_after:  log.info("Found after    : %s", _fmt_date(found_after))
        if found_before: log.info("Found before   : %s", _fmt_date(found_before))
    if args.placed_date:
        log.info("Placed date    : %s", _fmt_date(placed_after))
    else:
        if placed_after:  log.info("Placed after   : %s", _fmt_date(placed_after))
        if placed_before: log.info("Placed before  : %s", _fmt_date(placed_before))
    if difficulty_set:     log.info("Difficulty     : %s", sorted(difficulty_set))
    if terrain_set:        log.info("Terrain        : %s", sorted(terrain_set))
    if cache_type_aliases: log.info("Cache type     : %s", cache_type_aliases)
    if attr_terms:         log.info("Attributes     : %s", _describe_attr_terms(attr_terms))
    if countries:          log.info("Country        : %s", countries)
    if states:             log.info("State          : %s", states)
    if gccodes:            log.info("GC codes       : %s", sorted(gccodes))
    if finder_name:        log.info("Finder name    : %s", finder_name)
    if finder_id:          log.info("Finder ID      : %s", finder_id)

    # -- Apply filters
    matched = apply_filters(
        wpts_data,
        found_after=found_after,
        found_before=found_before,
        placed_after=placed_after,
        placed_before=placed_before,
        difficulty_set=difficulty_set,
        terrain_set=terrain_set,
        countries=countries,
        states=states,
        gccodes=gccodes,
        cache_type_aliases=cache_type_aliases,
        attr_terms=attr_terms,
        finder_name=finder_name,
        finder_id=finder_id,
    )

    if not matched:
        log.warning("No geocaches matched the specified criteria.")
        sys.exit(0)

    log.info("Matched : %d geocaches", len(matched))

    # -- Write outputs
    if not args.no_gpx:
        write_gpx(
            matched,
            Path(args.output_gpx),
            source=source,
            include_longdesc=args.include_longdesc,
            include_hints=args.include_hints,
            include_all_logs=args.include_all_logs,
        )

    if not args.no_csv:
        write_csv(matched, Path(args.output_csv), delimiter=args.delimiter)

    if args.no_gpx and args.no_csv:
        log.warning("Both --no-gpx and --no-csv specified; no output produced.")

    log.info("=== Done. ===")


if __name__ == "__main__":
    main()
