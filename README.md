# QRZ Logbook Tools

Logging QSLs accurately is [surprisingly complicated](https://wt8p.com/logging-amateur-radio-contacts-accurately-is-complicated/).  This group of programs attempts to reconcile data discrepancies between your [QRZ Logbook](https://logbook.qrz.com) and between QRZ and [LoTW (Logbook of the World)](https://lotw.arrl.org).  It will only update the QRZ side.

## Use cases ##

1) QRZ identifies cases where you and the other party logged different values for Grid Square, State, and County — but provides no bulk-correction mechanism.  Correcting can be done from the browser, but requires 8-13 clicks *for each record*.  As someone who accumulates 40 of these a month, but cannot let it go, I've been hoping for a better way.  Here it is.

2) If you do portable operations, the veracity of QRZ data can be affected by how you upload logs.   For example, when I do a POTA, I will define a specific station location for each park, using its proper grid, county, state.  If I upload to LoTW first, then use QRZ's import from LoTW, it's *mostly correct*.  QRZ still infers the location from your QRZ, causing errors in distance.

Because LoTW can often incur processing delays, I've often forgotten and uploaded to QRZ, too.  When data is subsequently imported from to QRZ from LoTW, it does not update these fields.  Editing my records is very tedious.

These scripts provides a mechanism of bulk-correcting your own data in QRZ via the QRZ API.  They will require an API key (which you can get with a premium QRZ membership).

USE AT YOUR OWN RISK.  These are presented AS IS and without any warranty.  

---

## Files

| File | Purpose |
|---|---|
| `qrz_common.py` | Shared library — ADIF parsing, QRZ API client, field converters, Maidenhead grid utilities, config loading |
| `resolve_qrz_discrepancies.py` | Corrects Grid, State, and County discrepancies reported by QRZ's Awards pages as well as allowing bulk correction of your own records. |
| `adif_extract.py` | Lightweight ADIF spreadsheet editor. Extracts QSOs from any ADIF file (QRZ, LoTW, N3FJP, WSJT-X) to a formatted Excel workbook or CSV; supports date filtering, preset field views, and round-trip editing (Excel → ADIF). |
| `reconcile_adif.py` | Compares LoTW and QRZ ADIF exports and optionally pushes corrections to QRZ |
| `sample_corrections.csv` | Annotated sample CSV covering all supported `field` keywords — copy and edit for your own use |

All files must be in the same directory. `qrz_common.py` is not run directly.

---

## Requirements

```
pip install pandas openpyxl requests
```

Python 3.10 or later is recommended.  A requirements.txt file with instructions on creating a custom environment is provided.

There are only three non-standard libraries used and versions very conservative, e.g., Currently pandas 3.x is shipping, but we only require at least 1.5.

---

## Callsign File Naming

Both tools use files named after your callsign (API key file, config file). Because portable callsigns can contain a `/` which is not valid in filenames, replace `/` with `_`.  For example:

| Callsign | Key file | Config file |
|---|---|---|
| `WT8P` | `WT8P.key` | `WT8P.cfg` |
| `TF/WT8P` | `TF_WT8P.key` | `TF_WT8P.cfg` |
| `WT8P/M` | `WT8P_M.key` | `WT8P_M.cfg` |

---

## API Key Setup

Create a file named `<CALLSIGN>.key` in the working directory containing your QRZ API key on a single line.  If your call sign has a slant, e.g., TF/WT8P, replace that with an underscore, e.g., TF_WT8P.key.  The key will be of the format:

```
abcd-1234-efcd-5678
```

Your API key is found in your QRZ Logbook under **Settings → API Access Key**. When the key file exists, the `--key` argument becomes optional for both scripts.

> QRZ requires an active XML-level subscription to use the Logbook API.

---

## `resolve_qrz_discrepancies.py`

Reads the discrepancy report exported from QRZ's Awards pages and applies the other party's values to your QRZ records via the API. Works on both unconfirmed records and confirmed/award-locked records.

It can also be used to bulk update your own records.

> **Dry-run mode is the default.** No changes are written to QRZ unless you pass `--update` explicitly. Always review the output CSV before running with `--update`.

### Quick Start

**1. Export from QRZ**

- **ADIF export:** Logbook → Settings → Export.   Wait.  Click Settings again to refresh.  Save the `.adi` file.
- **Discrepancy report:** Logbook → Awards → United States Counties Award → Details → Export. Save as Excel (`.xlsx`).

**2. Find discrepancies in QRZ**

There is no bulk export option for these.  Rather, rather we visit the Awards page for each.

    Awards → Click on your call sign → Click on United States Counties Award → 

Select and copy the table displayed.  Repeat this for these pages:

    Awards → Click on your call sign → Click on Grid Squared Award → 
    Awards → Click on your call sign → Click on United States Counties → 

**2. Preview first (dry-run is the default)**

```bash
python resolve_qrz_discrepancies.py \
    --xlsx  qrz_errors.xlsx \
    --adif  wt8p.adi \
    --call  WT8P
```

**3. Apply corrections**

```bash
python resolve_qrz_discrepancies.py \
    --xlsx  qrz_errors.xlsx \
    --adif  wt8p.adi \
    --call  WT8P \
    --update
```

### All Options

```
--xlsx <file>               QRZ discrepancy Excel file (mutually exclusive with --input-csv)
--input-csv <file>          Flat CSV instead of Excel (see CSV Format below)
--adif <file>               Your QRZ ADIF export (must contain APP_QRZLOG_LOGID)
--call <callsign>           Your callsign (e.g. WT8P or TF/WT8P)
--key <api-key>             QRZ API key — optional if <CALLSIGN>.key file exists
--my-station                Correct your own station's fields instead of the other party's
--update                    Apply changes to QRZ (default is dry-run — preview only)
--derive-coords             Derive related fields automatically (see Coordinate Derivation below)
--grid-precision {4,6,8}    Maidenhead precision when deriving grid from coordinates (default: 6)
--output-csv <file>         Output CSV log (default: resolved_log.csv)
```

### Input: Excel

The Excel file exported from QRZ's discrepancy view contains three worksheets:

| Sheet | ADIF field corrected |
|---|---|
| `Grids` | `GRIDSQUARE` |
| `State` | `STATE` |
| `County` | `CNTY` |

Column headers are matched by prefix, so `You Entered county`, `You Entered grid`, etc. all work. Rows where `Note` = `Bad Data` are skipped automatically.

### Input: Flat CSV (`--input-csv`)

All discrepancy types in a single file. The `field` column indicates which ADIF field to correct.

**Required columns:** `field`, `qso_date`, `qso_with`, `new_value`

**Optional columns:** `you_entered`, `de`, `note` (`Bad Data` to skip a row)

Column names are case-insensitive. Common aliases accepted: `call` for `qso_with`, `adif_field` for `field`, `other_party_entered` or `other_value` for `new_value`.

#### CSV Field Reference and Examples

The following example covers all supported `field` keywords. W1AW (the ARRL club station in Newington, CT) is used as the example contact.

> Comment lines (beginning with `#`) and blank lines are silently skipped,
> so you can annotate your CSV freely. The `sample_corrections.csv` file
> included in this repository is a ready-to-edit starting point.

```csv
field,qso_date,qso_with,new_value,note

# ── Other party's fields ──────────────────────────────────────────────────────
# Correct the other party's grid square (bare name, no --my-station needed)
GRIDSQUARE,2024-07-06 20:28:00,W1AW,FN31

# Correct the other party's state. Non-standard abbreviations (e.g. TEN → TN)
# are normalised automatically.
STATE,2017-10-28 15:14:00,W1AW,CT

# Correct the other party's county. Supply the QRZ display format, quoted
# because it contains a comma. "County", "Borough" (AK), and "Parish" (LA)
# are stripped automatically and the value converted to ADIF format.
CNTY,2025-08-11 02:22:00,W1AW,"Hartford County, CT"

# Mark a row as bad data to skip it without removing it from the file.
GRIDSQUARE,2024-03-01 14:00:00,W1AW,LNA,Bad Data

# ── Your own station's fields (requires --my-station) ─────────────────────────
# Bare names (GRIDSQUARE, STATE, CNTY) are promoted to MY_ equivalents
# when --my-station is active. You can also use the MY_ names directly.

# Your grid square
MY_GRIDSQUARE,2025-08-11 02:22:00,W1AW,CN87xn

# Your state and county
MY_STATE,2025-08-11 02:22:00,W1AW,WA
MY_CNTY,2025-08-11 02:22:00,W1AW,"King County, WA"

# ── Coordinates: separate rows ────────────────────────────────────────────────
# MY_LAT and MY_LON accept decimal degrees or ADIF native format.
# Positive lat = North, negative = South.
# Positive lon = East,  negative = West.
MY_LAT,2025-08-11 02:22:00,W1AW,47.5625
MY_LON,2025-08-11 02:22:00,W1AW,-122.058

# ── Coordinates: combined row (MY_LOC) ────────────────────────────────────────
# MY_LOC sets both MY_LAT and MY_LON from a single row.
# The "lat,lon" value must be quoted so the comma is not treated as a column
# separator. This expands into two separate MY_LAT and MY_LON updates.
# With --derive-coords it also derives and updates MY_GRIDSQUARE.
MY_LOC,2025-08-11 02:22:00,W1AW,"47.5625,-122.058"

# ── Grid square with coordinate derivation (--derive-coords) ──────────────────
# When --derive-coords is active, a MY_GRIDSQUARE row also emits MY_LAT
# and MY_LON updates derived from the centre of the specified grid square.
# Useful when your logging app reports a precise grid and you want all
# three fields updated consistently.
MY_GRIDSQUARE,2025-08-11 02:22:00,W1AW,CN87xn

# ── Comment field ──────────────────────────────────────────────────────────────
# COMMENT sets the QRZ logbook comment. Valid with or without --my-station.
# Useful for adding or correcting POTA/SOTA park references logged in the field.
COMMENT,2026-03-28 16:35:00,AB0LV,US-3263 Scenic Beach State Park WA
```

**Summary of `field` keywords and what they update:**

| `field` value | Updates | Notes |
|---|---|---|
| `GRIDSQUARE` | `GRIDSQUARE` | Other party's grid |
| `STATE` | `STATE` | Other party's state; non-standard abbrevs normalised |
| `CNTY` | `CNTY` | Other party's county; quoted QRZ display format: `"Hartford County, CT"` |
| `MY_GRIDSQUARE` | `MY_GRIDSQUARE` | Your grid; also `MY_LAT` + `MY_LON` with `--derive-coords` |
| `MY_STATE` | `MY_STATE` | Your state |
| `MY_CNTY` | `MY_CNTY` | Your county; quoted QRZ display format: `"King County, WA"` |
| `MY_LAT` | `MY_LAT` | Your latitude (decimal or ADIF native format) |
| `MY_LON` | `MY_LON` | Your longitude (decimal or ADIF native format) |
| `MY_LOC` | `MY_LAT` + `MY_LON` | Combined lat/lon — value must be quoted `"lat,lon"`; also updates `MY_GRIDSQUARE` with `--derive-coords` |
| `COMMENT` | `COMMENT` | QRZ logbook comment (free text); valid with or without `--my-station` |

### Correcting Your Own Station's Fields (`--my-station`)

Use `--my-station` to correct your own station's fields (`MY_GRIDSQUARE`, `MY_STATE`, `MY_CNTY`, `MY_LAT`, `MY_LON`) instead of the other party's fields. The input format is identical — bare field names are automatically promoted to their `MY_` equivalents.

```bash
python resolve_qrz_discrepancies.py \
    --input-csv my_corrections.csv \
    --adif wt8p.adi \
    --call WT8P \
    --my-station
```

**`MY_LAT` and `MY_LON`** accept either decimal degrees or ADIF native format:

| Format | Example | Meaning |
|---|---|---|
| Decimal, positive lat | `47.5625` | North |
| Decimal, negative lat | `-47.5625` | South |
| Decimal, positive lon | `122.058` | East |
| Decimal, negative lon | `-122.058` | West |
| ADIF native | `N047 33.750` | North 47° 33.750' |
| ADIF native | `W122 03.480` | West 122° 03.480' |

### Coordinate Derivation (`--derive-coords`)

When `--derive-coords` is active, the script automatically derives related fields so that your grid square and coordinates stay in sync:

- **`MY_LOC` row** — expands to `MY_LAT` + `MY_LON` updates (always), and also derives and updates `MY_GRIDSQUARE` from those coordinates.
- **`MY_GRIDSQUARE` row** — updates the grid square (always), and also derives and updates `MY_LAT` + `MY_LON` from the centre point of the specified grid square.

Use `--grid-precision` to set the number of Maidenhead characters when deriving a grid from coordinates:

| Precision | Characters | Approximate resolution |
|---|---|---|
| 4 | e.g. `CN87` | ~55 km |
| 6 | e.g. `CN87xn` | ~460 m (default) |
| 8 | e.g. `CN87xn35` | ~4 m |

> **Note:** When deriving coordinates *from* a grid square, the lat/lon written to QRZ is the centre point of that square. A 6-character grid can place you up to ~230 m from your actual position. If precision matters, use `MY_LOC` with your actual decimal coordinates and let `--derive-coords` derive the grid from those.

```bash
# Field operation: app gives you a precise grid — update all three fields
python resolve_qrz_discrepancies.py \
    --input-csv my_corrections.csv \
    --adif wt8p.adi \
    --call WT8P \
    --my-station \
    --derive-coords \
    --grid-precision 6
```

### Output CSV (`resolved_log.csv`)

| Column | Description |
|---|---|
| `sheet` | Source sheet (`Grids`, `State`, `County`) |
| `adif_field` | ADIF field corrected |
| `qso_with` | Other party's callsign |
| `qso_date` | YYYYMMDD |
| `time_on` | HHMM |
| `logid` | QRZ internal record ID |
| `old_value` | Value in QRZ before correction |
| `new_value` | Value applied (in ADIF format) |
| `status` | `updated` \| `dry_run` \| `no_change` \| `skipped_bad_data` \| `no_match` \| `error` |
| `error_msg` | Failure detail, blank on success |

### Field Format Conversions

**County (`CNTY` / `MY_CNTY`):** Supply the value in QRZ display format, quoted because it contains a comma: `"Hartford County, CT"`. The script converts it to ADIF format (`ST,County Name`) before writing to QRZ. The word `County` is stripped automatically; for Alaska `Borough` is also stripped (e.g. `"Anchorage Borough, AK"` → `AK,Anchorage`), and for Louisiana `Parish` is stripped.

**State (`STATE`):** Non-standard abbreviations (e.g. `IND` → `IN`) are automatically normalised to 2-letter ADIF values.

### How It Works

The script uses `ACTION=INSERT` with `OPTION=REPLACE` on the QRZ API. Including `APP_QRZLOG_LOGID` in the ADIF payload causes QRZ to replace the existing record in place, returning `RESULT=REPLACE`. This works on both unconfirmed and confirmed/award-locked records — unlike `ACTION=DELETE`, which fails silently on locked records.

Records are matched using: **Callsign + Date + Time (HHMM)**.

**API call consolidation:** When multiple fields on the same QSO need correction (e.g. `MY_GRIDSQUARE`, `MY_LAT`, `MY_LON`, and `COMMENT` all on the same record), all changes are applied in a single `INSERT OPTION=REPLACE` call rather than one call per field. This is both faster and safer — it eliminates the risk of a partial update if the script is interrupted mid-run. The output CSV still records one row per field corrected, with the logid repeating across rows for the same QSO. The summary line at the end of a run shows how many API calls were made versus how many field updates were applied.

---

## `adif_extract.py`

A lightweight ADIF spreadsheet editor. Extracts QSOs from any ADIF file to a formatted Excel workbook or CSV, lets you make corrections directly in Excel, then converts the edited workbook back to ADIF. Works with QRZ, LoTW, N3FJP, WSJT-X, and any other ADIF-producing program.

Does not call the QRZ API and requires no API key.

### Two Modes

| Mode | Trigger | Purpose |
|---|---|---|
| Extract | `--adif FILE` | ADIF → Excel / CSV for inspection and editing |
| Round-trip | `--from-xlsx FILE` | Edited Excel → ADIF for import or further processing |

### Typical POTA / Portable Workflow

```bash
# 1. Extract a single activation to a full editable Excel workbook
python adif_extract.py --adif wt8p.adi --date 2026-03-28 --output-xlsx pota_scenic.xlsx --no-csv

# 2. Open pota_scenic.xlsx in Excel.
#    Correct values in any cell. Delete columns you don't need.
#    Delete rows you don't want to reimport. Save.

# 3. Convert the edited workbook back to ADIF
python adif_extract.py --from-xlsx pota_scenic.xlsx --output-adif pota_fixed.adi

# 4a. Import pota_fixed.adi into your logging program directly, OR
# 4b. Use it as the --adif source for resolve_qrz_discrepancies.py
python resolve_qrz_discrepancies.py \
    --input-csv corrections.csv \
    --adif pota_fixed.adi \
    --call WT8P \
    --my-station \
    --derive-coords
```

### Narrow CSV Workflow (resolve-only)

If you only need to feed corrections into `resolve_qrz_discrepancies.py` and don't need to edit the full record, the narrow CSV is simpler:

```bash
# Extract inspection CSV (qrz preset by default)
python adif_extract.py --adif wt8p.adi --date 2026-03-28

# Open adif_extract.csv, fill in 'field' and 'new_value' columns, save.

python resolve_qrz_discrepancies.py \
    --input-csv adif_extract.csv \
    --adif wt8p.adi \
    --call WT8P \
    --my-station \
    --derive-coords
```

### All Options

```
Input mode (required, mutually exclusive):
  --adif <file>             Source ADIF file (QRZ, LoTW, N3FJP, WSJT-X, ...)
  --from-xlsx <file>        Convert an edited Full Excel workbook back to ADIF

Date filtering (extract mode only):
  --date <DATE>             Single date — shorthand for --after DATE --before DATE
                            Mutually exclusive with --after.
  --after <DATE>            Include QSOs on or after this date (inclusive)
  --before <DATE>           Include QSOs on or before this date (inclusive)

Field selection (extract mode only):
  --preset {qrz,lotw,n3fjp,wsjtx}
                            Named inspection column set (default: qrz)
  --fields FIELD1,FIELD2,...
                            Explicit comma-separated list of inspection columns.
                            Mutually exclusive with --preset.

Outputs:
  --output-csv <file>       Narrow inspection CSV (default: adif_extract.csv)
  --no-csv                  Suppress CSV — useful when only --output-xlsx is wanted
  --output-xlsx <file>      Full Excel workbook with all ADIF fields from the source
  --output-adif <file>      ADIF output for --from-xlsx round-trip
                            (default: adif_extract.adi)
```

Date formats accepted for all date arguments: `YYYY-MM-DD` or `YYYYMMDD`.

### Presets (`--preset`)

Presets control which fields appear as the highlighted inspection columns in the CSV and Excel — placed immediately after the key identity columns (`QSO_DATE`, `TIME_ON`, `CALL`, `BAND`, `MODE`, `FREQ`). In Full Excel mode, all other ADIF fields from the source file are also included, alphabetically after the preset columns.

| Preset | Fields included |
|---|---|
| `qrz` *(default)* | `MY_GRIDSQUARE` `MY_LAT` `MY_LON` `MY_STATE` `MY_CNTY` `MY_CITY` `MY_COUNTRY` `MY_CQ_ZONE` `MY_ITU_ZONE` `MY_DXCC` `MY_NAME` `COMMENT` |
| `lotw` | `GRIDSQUARE` `STATE` `CNTY` `DXCC` `CQZ` `ITUZ` `CONT` `QSL_RCVD` `LOTW_QSL_RCVD` `APP_LOTW_2XQSL` `APP_LOTW_RXQSL` |
| `n3fjp` | `RST_SENT` `RST_RCVD` `FREQ` `BAND` `MODE` `PROGRAMID` `LOG_PGM` |
| `wsjtx` | `RST_SENT` `RST_RCVD` `FREQ` `BAND` `MODE` `GRIDSQUARE` `COMMENT` |

Use `--fields` to specify any arbitrary list of fields not covered by a preset.

### Output: Full Excel (`--output-xlsx`)

The Full Excel workbook contains every ADIF field present anywhere in the source file, formatted for direct editing:

- **Column order:** key identity fields → preset/`--fields` inspection columns (highlighted in medium blue) → all remaining fields alphabetically (dark navy header)
- **Frozen panes:** header row and first four columns stay visible when scrolling
- **Auto-filter** on the header row for sorting and filtering
- **Column widths** sized by field type (narrow for codes and dates, wider for free text)
- `QSO_DATE` and `TIME_ON` are displayed as `YYYY-MM-DD` and `HH:MM` for readability

**Editing tips:**
- Delete entire columns to exclude those fields from the output ADIF
- Delete rows to exclude QSOs from reimport
- Edit any cell value directly — the round-trip will write whatever is in the sheet
- The `field` and `new_value` columns from the narrow CSV are not present in the Full Excel; add them manually after round-tripping if you then want to feed the result into `resolve_qrz_discrepancies.py` as an `--input-csv`

### Output: Narrow CSV (default)

The narrow CSV contains only the key identity columns, the preset/`--fields` inspection columns, and two blank placeholder columns:

| Column | Description |
|---|---|
| `field` | **Blank — fill in** the ADIF field name to correct (e.g. `MY_GRIDSQUARE`, `COMMENT`) |
| `qso_date` | `YYYY-MM-DD` |
| `time_on` | `HH:MM` |
| `call` | Contacted station callsign |
| *(preset columns)* | Current values from the ADIF for spot-checking |
| `new_value` | **Blank — fill in** the corrected value |

Fill in `field` and `new_value`, then pass the CSV to `resolve_qrz_discrepancies.py --input-csv`.

### Round-Trip: Excel → ADIF (`--from-xlsx`)

Converts an edited Full Excel workbook back to ADIF:

- Every non-blank cell becomes an ADIF field; the column header is the field name
- Columns named `field` or `new_value` are silently skipped
- `QSO_DATE` / `QSO_DATE_OFF` (`YYYY-MM-DD`) are converted back to ADIF compact `YYYYMMDD`
- `TIME_ON` / `TIME_OFF` (`HH:MM`) are converted back to `HHMM`
- `APP_QRZLOG_LOGID` is preserved if present, enabling later use with `resolve_qrz_discrepancies.py`
- The output ADIF contains only the columns still present in the sheet — deleted columns produce no ADIF fields

### Examples

```bash
# Full Excel for a date range, LoTW field view, no CSV
python adif_extract.py \
    --adif lotw_export.adi \
    --after 2026-01-01 --before 2026-03-31 \
    --preset lotw \
    --output-xlsx lotw_q1.xlsx \
    --no-csv

# Custom fields — just the fields you care about
python adif_extract.py \
    --adif wt8p.adi \
    --fields GRIDSQUARE,MY_GRIDSQUARE,FREQ,MODE,COMMENT \
    --output-xlsx custom_view.xlsx \
    --no-csv

# Extract N3FJP log, edit in Excel, convert back for LoTW upload
python adif_extract.py --adif n3fjp_log.adi --preset n3fjp --output-xlsx n3fjp_review.xlsx
# ... edit n3fjp_review.xlsx ...
python adif_extract.py --from-xlsx n3fjp_review.xlsx --output-adif n3fjp_cleaned.adi

# Round-trip with explicit output name
python adif_extract.py --from-xlsx pota_scenic.xlsx --output-adif scenic_beach_fixed.adi
```

---

## `reconcile_adif.py`

Compares a LoTW ADIF export against a QRZ ADIF export for the same callsign, identifies field-level discrepancies, and optionally pushes corrections to QRZ.

> **Important:** Export only *confirmed* QSOs from LoTW. In LoTW, use **Search QSOs → QSL Rcvd = Yes** before downloading. The script also checks `APP_LOTW_2XQSL=Y` as a safety net, but the export filter is the primary mechanism.

### Quick Start

**1. Export your logs**

- **LoTW:** Download confirmed QSOs as ADIF (all callsigns can be in one file).
- **QRZ:** Logbook → Settings → Export. One file per callsign/logbook.

**2. Compare only (no API writes)**

```bash
python reconcile_adif.py \
    --lotw  lotw_confirmed.adi \
    --qrz   wt8p.adi \
    --call  WT8P
```

This produces `corrected_qrz.adi` (for manual import) and `reconciliation_report.csv`.

**3. Compare and push corrections to QRZ**

```bash
python reconcile_adif.py \
    --lotw  lotw_confirmed.adi \
    --qrz   wt8p.adi \
    --call  WT8P \
    --update-qrz \
    --dry-run
```

Remove `--dry-run` to apply live.

### All Options

```
--lotw <file>           LoTW ADIF export (confirmed QSOs only)
--qrz <file>            QRZ ADIF export (must contain APP_QRZLOG_LOGID)
--call <callsign>       Callsign to process — filters LoTW by STATION_CALLSIGN
--config <file>         Field rules config file (default: <CALLSIGN>.cfg)
--update-qrz            Push corrections to QRZ via API
--key <api-key>         QRZ API key — optional if <CALLSIGN>.key file exists
--dry-run               Preview corrections without writing to QRZ
--output-adif <file>    Corrected ADIF output (default: corrected_qrz.adi)
--output-csv <file>     Report CSV (default: reconciliation_report.csv)
```

### Multiple Callsigns

LoTW can export all your callsigns in a single file. The `--call` argument filters the export to only the records for that callsign's `STATION_CALLSIGN`. Run the script once per callsign, pointing `--qrz` at the corresponding QRZ export each time.

### Fields Compared

| Field | Default Rule | Notes |
|---|---|---|
| `GRIDSQUARE` | `lotw_wins` | First 4 chars compared (QRZ may have more precision) |
| `COUNTRY` | `fill_blank` | Only fills if QRZ field is empty |
| `DXCC` | `lotw_wins` | Compared as integer |
| `CQZ` | `lotw_wins` | Compared as integer, leading zeros ignored |
| `ITUZ` | `lotw_wins` | Compared as integer, leading zeros ignored |
| `MODE` | `flag_only` | Reported but not auto-corrected |
| `STATE` | `lotw_wins` | US contacts only (`DXCC=291`); skipped if value is numeric |
| `CNTY` | `lotw_wins` | US contacts only; skipped if value is numeric |
| `MY_COUNTRY` | `lotw_wins` | Normalises verbose names (e.g. `UNITED STATES OF AMERICA` → `United States`) |
| `MY_CQ_ZONE` | `lotw_wins` | Integer comparison |
| `MY_ITU_ZONE` | `lotw_wins` | Integer comparison |
| `MY_DXCC` | `lotw_wins` | Integer comparison |
| `MY_STATE` | `lotw_wins` | |
| `MY_CNTY` | `lotw_wins` | |
| `APP_LOTW_RXQSL` | `fill_blank` | Maps to `LOTW_QSL_RCVD` in QRZ |

### Configuration File

Per-field rules can be customised in a config file named `<CALLSIGN>.cfg` (e.g. `WT8P.cfg`). See the sample config file (`sample.cfg`) included in this repository.

**Valid rules:**

| Rule | Behaviour |
|---|---|
| `lotw_wins` | Apply LoTW value to QRZ record regardless of existing QRZ value |
| `fill_blank` | Only apply LoTW value if QRZ field is empty |
| `flag_only` | Report the difference in the CSV but do not correct |
| `skip` | Ignore this field entirely |

### Output: CSV Report (`reconciliation_report.csv`)

One row per field-level discrepancy found. Clean records with no discrepancies are omitted.

| Column | Description |
|---|---|
| `call` | Other party's callsign |
| `qso_date` | YYYYMMDD |
| `time_on` | HHMM |
| `band` | Band |
| `mode` | Mode |
| `logid` | QRZ record ID |
| `field` | ADIF field name |
| `lotw_value` | Value from LoTW |
| `qrz_value` | Value currently in QRZ |
| `rule` | Rule applied (`lotw_wins`, `fill_blank`, etc.) |
| `action` | `corrected` \| `flagged` \| `skipped` |
| `record_status` | `ok` \| `updated` \| `dry_run` \| `no_match` \| `error` |
| `error_msg` | Failure detail if applicable |

### Output: Corrected ADIF (`corrected_qrz.adi`)

Contains only records with at least one `corrected` field change. Can be imported into QRZ manually via **Logbook → Settings → ADIF Import** as an alternative to `--update-qrz`.

---

## `qrz_common.py` — Shared Library

This file is used by all scripts and is not run directly. It provides:

- **ADIF parser** — handles HTML-escaped brackets from QRZ API responses
- **QRZ API client** — `ACTION=INSERT OPTION=REPLACE` for in-place updates
- **Key file loader** — reads `<CALLSIGN>.key`, maps `/` to `_` in filenames
- **Config file loader** — reads `<CALLSIGN>.cfg` for per-field rules
- **Field converters** — `CNTY` display-to-ADIF format, `STATE` normalisation, coordinate validation
- **Maidenhead grid utilities** — `latlon_to_grid()` converts decimal coordinates to a 4-, 6-, or 8-character grid locator; `grid_to_latlon()` converts a grid locator back to the decimal lat/lon of its centre point
- **Date/time normalisation** — `parse_qso_datetime()` accepts both ADIF compact format (`YYYYMMDD` / `HHMM`, as found in QRZ exports) and human-readable format (`YYYY-MM-DD` / `HH:MM`), used by both `resolve_qrz_discrepancies.py` and `adif_extract.py`; `format_qso_datetime()` converts ADIF compact to human-readable for CSV output
- **Field comparison utilities** — integer normalisation, gridsquare prefix matching, country name mapping

---

## Notes

- Always run `resolve_qrz_discrepancies.py` without `--update` first to verify matches and proposed values before writing.
- Export a fresh ADIF from QRZ before each run — `APP_QRZLOG_LOGID` values can change if records were previously updated.
- The scripts pause 1 second between API calls to avoid rate limiting. When multiple fields on the same QSO are being corrected, they are consolidated into a single API call automatically.
- For `reconcile_adif.py`, unmatched LoTW records (no corresponding QRZ entry) are logged in the CSV as `no_match` — this is normal for contacts logged in LoTW before you joined QRZ, or contacts the other party hasn't logged in QRZ.
- QRZ's user interface will report counties with "County" or "Borough" (Alaska only), which differs from what is contained in the ADIF file.  We will strip that off for you, so no worries.
- In some cases, bad data is reported by the other person.  For example, if a user specifies their grid as "LNA."  You can mark these as bad data, or the API will simply fail silently.  There's really no remedy from our side.
- When round-tripping through Excel (`adif_extract.py --output-xlsx` → edit → `--from-xlsx`), columns deleted from the workbook will be absent from the output ADIF. This is intentional — the sheet is the source of truth. Start from a fresh QRZ export if you need to restore dropped fields.
