# Usage Reference — Mapping and QRZ Logbook Tools

Detailed option reference, workflows, CSV formats, and field documentation for all scripts. For installation and setup, see [README.md](README.md).

---

## `resolve_qrz_discrepancies.py`

Reads the discrepancy report exported from QRZ's Awards pages and applies the other party's values to your QRZ records via the API. Works on both unconfirmed records and confirmed/award-locked records. Can also be used to bulk update your own station fields.

> **Dry-run mode is the default.** No changes are written to QRZ unless you pass `--update` explicitly. Always review the output CSV before running with `--update`.

### Quick Start

**1. Export from QRZ**

- **ADIF export:** Logbook → Settings → Export. Wait, then click Settings again to refresh. Save the `.adi` file.

**2. Export discrepancy reports from QRZ Awards**

QRZ does not provide a bulk export for all discrepancy types at once — visit each Awards page separately:

    Logbook → Awards → [Your Callsign] → Grid Squares Award → (copy table or use Export)
    Logbook → Awards → [Your Callsign] → United States Counties Award → (copy table or use Export)
    Logbook → Awards → [Your Callsign] → United States States Award → (copy table or use Export)

Paste each table into a separate sheet (`Grids`, `County`, `State`) in an Excel workbook and save as `.xlsx`.

**3. Preview first (dry-run is the default)**

```bash
python resolve_qrz_discrepancies.py \
    --xlsx  qrz_errors.xlsx \
    --adif  wt8p.adi \
    --call  WT8P
```

**4. Apply corrections**

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
--my-station                Correct your own station fields instead of the other party's
--update                    Apply changes to QRZ (default is dry-run — preview only)
--derive-coords             Derive related fields automatically (see Coordinate Derivation below)
--grid-precision {4,6,8}    Maidenhead precision when deriving grid from coordinates (default: 6)
--output-csv <file>         Output CSV log (default: resolved_log.csv)
```

### Input: Excel

The Excel file can contain named sheets from the QRZ discrepancy export as well as an optional `MISC` sheet for anything else.

**Named sheets** (always correct the other party's fields):

| Sheet | ADIF field corrected |
|---|---|
| `Grids` | `GRIDSQUARE` |
| `State` | `STATE` |
| `County` | `CNTY` |

Column headers are matched by prefix, so `You Entered county`, `You Entered grid`, etc. all work. Rows where `Note` = `Bad Data` are skipped automatically.

**MISC sheet** (optional, supports all field keywords):

Add a sheet named `MISC` to your workbook with the same column format as the flat CSV: `field`, `qso_date`, `qso_with`, `new_value`, and optionally `note`. This sheet accepts every field keyword listed in the CSV Field Reference below — including `MY_GRIDSQUARE`, `MY_LOC`, `MY_LAT`/`MY_LON`, `MY_STATE`, `MY_CNTY`, `MY_CITY`, `MY_COUNTRY`, `MY_CQ_ZONE`, `MY_ITU_ZONE`, `MY_NAME`, and `COMMENT` — making it the easiest way to correct your own station fields from within the same Excel workbook. The `--derive-coords` and `--grid-precision` options apply to MISC sheet rows exactly as they do to CSV rows. Blank rows and rows whose first cell starts with `#` are skipped.

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
# Correct the other party's grid square.
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

# ── Your own station's fields ────────────────────────────────────────────────
MY_GRIDSQUARE,2025-08-11 02:22:00,W1AW,CN87xn
MY_STATE,2025-08-11 02:22:00,W1AW,WA
MY_CNTY,2025-08-11 02:22:00,W1AW,"King County, WA"

# ── Coordinates: separate rows ────────────────────────────────────────────────
MY_LAT,2025-08-11 02:22:00,W1AW,47.5625
MY_LON,2025-08-11 02:22:00,W1AW,-122.058

# ── Coordinates: combined row (MY_LOC) ────────────────────────────────────────
# MY_LOC sets both MY_LAT and MY_LON from a single row.
# The "lat,lon" value must be quoted so the comma is not treated as a column
# separator. With --derive-coords it also derives and updates MY_GRIDSQUARE.
MY_LOC,2025-08-11 02:22:00,W1AW,"47.5625,-122.058"

# ── Other station informational fields ────────────────────────────────────────
MY_CITY,2025-08-11 02:22:00,W1AW,Bellevue
MY_COUNTRY,2025-08-11 02:22:00,W1AW,United States
MY_CQ_ZONE,2025-08-11 02:22:00,W1AW,3
MY_ITU_ZONE,2025-08-11 02:22:00,W1AW,6
MY_NAME,2025-08-11 02:22:00,W1AW,Jim Carson

# ── Comment field ──────────────────────────────────────────────────────────────
COMMENT,2026-03-28 16:35:00,AB0LV,US-3263 Scenic Beach State Park WA
```

**Summary of `field` keywords:**

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
| `MY_CITY` | `MY_CITY` | Your station city / QTH |
| `MY_COUNTRY` | `MY_COUNTRY` | Your station country |
| `MY_CQ_ZONE` | `MY_CQ_ZONE` | Your CQ zone number |
| `MY_ITU_ZONE` | `MY_ITU_ZONE` | Your ITU zone number |
| `MY_NAME` | `MY_NAME` | Your name as logged |
| `COMMENT` | `COMMENT` | QRZ logbook comment (free text) |

### Coordinate Derivation (`--derive-coords`)

When `--derive-coords` is active:

- **`MY_LOC` row** — expands to `MY_LAT` + `MY_LON` updates, and also derives and updates `MY_GRIDSQUARE` from those coordinates.
- **`MY_GRIDSQUARE` row** — updates the grid square, and also derives and updates `MY_LAT` + `MY_LON` from the centre point of the specified grid square.

Use `--grid-precision` to set the number of Maidenhead characters when deriving a grid from coordinates:

| Precision | Characters | Approximate resolution |
|---|---|---|
| 4 | e.g. `CN87` | ~55 km |
| 6 | e.g. `CN87xn` | ~460 m (default) |
| 8 | e.g. `CN87xn35` | ~4 m |

> **Note:** When deriving coordinates *from* a grid square, the grid must be at least 6 characters — a 4-character grid spans ~55 km and is rejected as too coarse.

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

**County (`CNTY` / `MY_CNTY`):** Supply the value in QRZ display format, quoted because it contains a comma: `"Hartford County, CT"`. The script converts it to ADIF format (`ST,County Name`) before writing to QRZ. `County` is stripped automatically; for Alaska `Borough` is also stripped, and for Louisiana `Parish` is stripped.

**State (`STATE`):** Non-standard abbreviations (e.g. `IND` → `IN`) are automatically normalised to 2-letter ADIF values.

### How It Works

The script uses `ACTION=INSERT` with `OPTION=REPLACE` on the QRZ API. Including `APP_QRZLOG_LOGID` in the ADIF payload causes QRZ to replace the existing record in place, returning `RESULT=REPLACE`. This works on both unconfirmed and confirmed/award-locked records — unlike `ACTION=DELETE`, which fails silently on locked records.

Records are matched using: **Callsign + Date + Time (HHMM)**.

---

## `adif_extract.py`

Extracts QSOs from a QRZ ADIF export to an inspection CSV, with optional date-range or single-date filtering. Designed for the common portable-operations workflow: export a range of contacts, spot-check `MY_` fields and `COMMENT` in Excel, fill in corrections, then feed the result back to `resolve_qrz_discrepancies.py`.

Does not call the QRZ API and requires no API key.

### Quick Start

**1. Extract a single activation date**

```bash
python adif_extract.py --adif wt8p.adi --date 2026-03-28
```

**2. Extract a date range**

```bash
python adif_extract.py --adif wt8p.adi --after 2026-03-20 --before 2026-03-31 --output-csv march_pota.csv
```

**3. Open the CSV in Excel, review and fill in corrections**

The `field` and `new_value` columns are blank — add the field to correct and its new value on each row. Duplicate a row if the same QSO needs multiple corrections. Delete rows you don't need to change.

**4. Feed the edited CSV to resolve_qrz_discrepancies.py**

```bash
python resolve_qrz_discrepancies.py \
    --input-csv march_pota.csv \
    --adif wt8p.adi \
    --call WT8P \
    --derive-coords
```

### All Options

```
--adif <file>           QRZ ADIF export file (required)
--date <DATE>           Extract a single date — shorthand for --after DATE --before DATE
                        Mutually exclusive with --after.
--after <DATE>          Include QSOs on or after this date (inclusive)
--before <DATE>         Include QSOs on or before this date (inclusive)
--output-csv <file>     Output CSV filename (default: adif_extract.csv)
```

Date formats accepted: `YYYY-MM-DD` or `YYYYMMDD`.

### Output CSV Columns

| Column | Description |
|---|---|
| `field` | **Blank — fill in** the ADIF field name to correct (e.g. `MY_GRIDSQUARE`, `COMMENT`) |
| `qso_date` | QSO date in `YYYY-MM-DD` format |
| `time_on` | QSO time in `HH:MM` format |
| `call` | Contacted station callsign |
| `MY_GRIDSQUARE` | Your grid square as logged |
| `MY_LAT` | Your latitude (ADIF format) |
| `MY_LON` | Your longitude (ADIF format) |
| `MY_STATE` | Your state |
| `MY_CNTY` | Your county (ADIF format, e.g. `WA,Kitsap`) |
| `MY_CITY` | Your city |
| `MY_COUNTRY` | Your country |
| `MY_CQ_ZONE` | Your CQ zone |
| `MY_ITU_ZONE` | Your ITU zone |
| `MY_DXCC` | Your DXCC entity code |
| `MY_NAME` | Your name as logged |
| `COMMENT` | QRZ logbook comment |
| `new_value` | **Blank — fill in** the corrected value |

### Typical POTA / Portable Workflow

1. After an activation, export your full QRZ ADIF (or use a previous export if it's current).
2. Run `adif_extract.py --date <activation-date>` to pull just that day's QSOs.
3. In Excel: verify `MY_GRIDSQUARE`, `MY_LAT`, `MY_LON`, and `COMMENT`. For each field that needs correcting, set `field` = the ADIF field name and `new_value` = the correct value.
4. Save as CSV and run `resolve_qrz_discrepancies.py --input-csv ... --derive-coords`. With `--derive-coords`, a single `MY_GRIDSQUARE` correction (6+ characters) will also update `MY_LAT` and `MY_LON` from the grid centre automatically.

---

## `reconcile_adif.py`

Compares a LoTW ADIF export against a QRZ ADIF export for the same callsign, identifies field-level discrepancies, and optionally pushes corrections to QRZ.

> **Important:** Export only *confirmed* QSOs from LoTW. In LoTW, use **Search QSOs → QSL Rcvd = Yes** before downloading.

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

Per-field rules can be customised in a config file named `<CALLSIGN>.cfg`. See `sample.cfg` included in the repository.

| Rule | Behaviour |
|---|---|
| `lotw_wins` | Apply LoTW value to QRZ record regardless of existing QRZ value |
| `fill_blank` | Only apply LoTW value if QRZ field is empty |
| `flag_only` | Report the difference in the CSV but do not correct |
| `skip` | Ignore this field entirely |

### Output: CSV Report (`reconciliation_report.csv`)

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

## `adif_map.py`

Plots an ADIF file on an interactive map in your browser. Your activating location(s) are shown — including multiple sites for portable operations like POTA. Contacts are clustered by zoom level and colored by band.

> **Performance note:** The map works on a full log (tested at 35k+ contacts) but opening the HTML file is noticeably slow above ~5,000 QSOs. Use `adif_extract.py` to pull a date range before mapping if you only need a subset.

### Quick Start

```bash
# Basic map — opens map_output.html in your browser
python adif_map.py mylog.adi

# With all overlays and the filter panel
python adif_map.py mylog.adi --overlay states,counties,grids --show-filters

# With great-circle arcs (decimated automatically for large logs)
python adif_map.py mylog.adi --show-arcs --overlay states,counties,grids --show-filters
```

### All Options

```
--band <BAND>            Filter by band (e.g. 40m, 20m) — single band
--mode <MODE>            Filter by single mode (e.g. CW) — kept for compatibility
--modes <LIST>           Filter by multiple modes, comma-separated (e.g. --modes CW,FT8)
--date-from <DATE>       Filter QSOs on or after date (YYYYMMDD or YYYY-MM-DD)
--date-to <DATE>         Filter QSOs on or before date (YYYYMMDD or YYYY-MM-DD)
--confirmed              Only show confirmed QSOs (LoTW or QSL received)
--include-null-grid      Include JJ00 contacts (excluded by default — JJ00 is a placeholder
                         grid used when the other station has no location data, and produces
                         a dense cluster in the Atlantic Ocean near the prime meridian)
--overlays-only          Hide contact dots and arcs; show only the overlay choropleth.
                         Unworked cells render as ghost polygons (transparent fill, visible
                         border, hoverable tooltip) so you can identify needed entities
                         directly. For grids, requires land_grids.txt (run
                         build_land_grids.py once); if absent, falls back to showing all
                         grids in the bounding box of worked grids.
--show-arcs              Draw great-circle arc lines (default: off)
--arc-max <N>            Maximum total arcs to draw (default: 1000)
--arc-cell-max <N>       Maximum arcs per 5°×5° geographic cell (default: 2)
--overlay <LIST>         Comma-separated overlays: grids, states, counties
--show-filters           Show collapsible in-browser band/mode filter panel (top-left)
--theme <FILE>           Color theme YAML file (default: theme_default.yaml)
--verbose                Detailed console output: all operating locations, band breakdown
--output <FILE>          Output HTML filename (default: map_output.html beside input)
```

### Great-Circle Arcs (`--show-arcs`)

Arc lines connect your station to each contact along the great-circle path. On large logs, arcs are automatically decimated to keep the map usable:

- **Callsign deduplication** — only one arc per unique callsign, regardless of how many bands or modes were worked. A contact worked on 9 bands produces one arc, not nine.
- **Geographic cell cap** (`--arc-cell-max`, default 2) — the world is divided into 5°×5° cells. Cells with fewer contacts are filled first, so rare DX contacts always get an arc while dense clusters (e.g. hundreds of contacts to Western Europe) are limited to a small representative sample.
- **Global cap** (`--arc-max`, default 1000) — total arc count is capped after cell selection.
- **Antimeridian handling** — arcs that cross the ±180° meridian are automatically split into two segments so they render correctly in Leaflet regardless of map pan position.

The `Arcs` layer appears as a separate toggleable entry in the layer control (top-right), so arcs can be turned off after the map loads without regenerating the file.

Console output after a `--show-arcs` run:
```
Arcs: 847 drawn from 4,231 unique contacts (12,450 QSOs total)
```

### Overlays

The `--overlay` flag adds choropleth layers showing worked/confirmed status. Each overlay uses a distinct color palette:

| Overlay | Confirmed | Worked (unconfirmed) | Not worked |
|---|---|---|---|
| States / Provinces | Green `#2ecc71` | Amber `#f39c12` | Outline only |
| Counties | Dark green `#1a8a4a` | Burnt amber `#c0720a` | Outline only |
| Grid squares | Teal `#27ae9e` | Amber `#e8a020` | Not drawn (ghost cells with `--overlays-only`) |

All colors are configurable in `theme_default.yaml`. All three overlay layers are independently toggleable in the layer control.

**Grid squares** — generated from the `GRIDSQUARE` field (4-character precision). No external file required.

**States and provinces** (US + Canada) — read from `ne_states.geojson`. Uses the `STATE` field and `DXCC` code.

**Counties** — uses three data sources depending on geography:

- **US counties** — read from `us_counties.geojson` (generated from GSAK community polygon data via `gsak_build_geojson.py`). Uses the `CNTY` field (e.g. `WA,King`). County/Parish/Borough suffixes are stripped automatically before matching. LoTW exports county names in ALL CAPS — these are normalised to title case automatically. Known spelling differences between LoTW and GSAK (e.g. "De Kalb" vs "DeKalb") are also resolved transparently.
- **Canadian regional districts** — read from `gsak_counties.db`. Pass `--db gsak_counties.db` to enable. Build the DB with `python gsak_counties.py build --country CA`.
- **International regions** (Norway, Iceland, Czechia, Faroe Islands, etc.) — also read from `gsak_counties.db`. Build with `python gsak_counties.py build --country <code>`. All three sources are merged into a single choropleth layer.

### Overlays-Only Mode (`--overlays-only`)

Hides all contact dots and arcs, leaving only the overlay choropleth(s). Your home/activation station markers are preserved. Unworked cells render as ghost polygons — transparent fill, visible border, hoverable tooltip — so you can identify entities you still need directly on the map without inferring from surrounding worked cells.

```bash
# See which grid squares you still need in your worked area
python adif_map.py mylog.adi --overlay grids --overlays-only

# See which states and counties are still unworked
python adif_map.py mylog.adi --overlay states,counties --overlays-only
```

**Grid ghost cells and `land_grids.txt`:** Ghost cells are enumerated within the bounding box of your worked grids (± 1-cell padding). Without filtering, large logs spanning wide geographic areas can generate hundreds of open-ocean ghost cells, causing slow browser rendering. `land_grids.txt` (generated by `build_land_grids.py`) restricts ghost cells to land-adjacent grids only, eliminating the ocean clutter. If `land_grids.txt` is absent, the full bounding-box set is used as a fallback — the map still works, just with more cells rendered.

### Interactive Filter Panel (`--show-filters`)

Injects a collapsible **Filters** panel in the top-left corner of the map. Click the panel title to expand.

**Modes** — toggles contact dot visibility by mode group:

| Group | Includes |
|---|---|
| CW | CW |
| SSB | SSB, USB, LSB, AM, FM |
| Digital | FT8, FT4, DATA, RTTY, JT65, JT9, PSK31, and others |
| Other | Any mode not matched above |

**Bands** — does not affect contact dot visibility, but dynamically recomputes overlay choropleth colors. Unchecking 40m instantly updates the state/county/grid fill colors to reflect only your remaining active bands — useful for questions like "which counties have I confirmed on CW and 20m?"

When `--show-filters` is active with overlays, every mode and band toggle immediately recomputes confirmed/worked/needed status for every entity on screen.

> **Known limitation:** Individual sub-mode checkboxes (e.g. FM within the SSB group) are displayed but currently filter at the group level only. Per-mode filtering within groups is a planned improvement.

### Color Themes

Colors, stroke widths, and map centering are controlled by `theme_default.yaml`. Copy and edit it, then pass `--theme mytheme.yaml`:

```yaml
# Shift initial map center east (+) or west (-) of your station longitude.
# West Coast US station with contacts in both Europe and Asia/Pacific:
map_center_lon_offset: 122   # centers on prime meridian, splitting view evenly

overlay:
  states:
    confirmed_weight: 3.0      # thicker state borders
    border_confirmed: "#000000"  # black borders
    confirmed: "#27ae60"         # slightly different green
  counties:
    confirmed: "#1a5c33"         # darker county fill
```

**`map_center_lon_offset`** shifts the initial map view east or west of your station longitude. The default is `0` (centered on your station). A West Coast US station at lon −122° with contacts spanning both Europe and Asia can set this to `122` to center the map on the prime meridian, putting Europe and Asia symmetrically on either side.

The `contact_dot` section controls marker appearance (radius, fill opacity, border color and weight).

### Layer Control

The map's native layer control (top-right) lists all toggleable layers:

- **Tile layers** — CartoDB Light, CartoDB Dark, Esri Topo, Esri NatGeo, Esri Satellite
- **Mode groups** — one entry per mode group present in your log (CW, SSB, Digital, Other)
- **Arcs** — toggleable independently (only present when `--show-arcs` was used)
- **Overlays** — States & Provinces, Counties, Grid squares (only those requested via `--overlay`)
- **State/Province borders** — thin black boundary lines added automatically whenever any overlay is active, for geographic orientation. Non-interactive.

---

## `geocache_map.py`

Plots a GSAK GPX export on an interactive browser map. Caches are grouped by type into independently toggleable layers, clustered by zoom level, and colored by cache type. Shares all overlay and legend infrastructure with `adif_map.py` via `map_core.py`.

> County lookup requires `gsak_counties.db` — build it once with `gsak_counties.py build`. Without the DB, caches without a county field in the GPX will simply have no county assigned.

### Quick Start

```bash
# Basic map — opens map_output.html beside the GPX file
python geocache_map.py caches.gpx

# With type/D-T filter panel and county overlay
python geocache_map.py caches.gpx --show-filters --overlay counties

# Filter to Earthcaches and Traditional caches, difficulty ≤ 3
python geocache_map.py caches.gpx --type earth,traditional --difficulty 1-3
```

### All Options

```
--type <LIST>           Cache type filter, comma-separated (case-insensitive prefix match)
                        e.g. --type traditional,earth,mystery
                        Types: Traditional, Mystery, Multi, Earth, Virtual,
                               Letterbox, Wherigo, CITO, Mega, Giga, Event
--difficulty MIN-MAX    Difficulty range in 0.5 steps, e.g. --difficulty 1-3.5
--terrain MIN-MAX       Terrain range in 0.5 steps,    e.g. --terrain 2-5
--found                 Show only found caches
--not-found             Show only unfound caches
--overlay <LIST>        Comma-separated overlays: states, counties
                        (US: requires ne_states.geojson / us_counties.geojson;
                         Canadian and international counties: requires gsak_counties.db)
--show-filters          Show collapsible type/D/T filter panel (top-left)
--db <FILE>             Path to gsak_counties.db for coordinate→county lookup and
                        international county overlays
                        (default: gsak_counties.db beside the script or in CWD)
--theme <FILE>          Color theme YAML (default: theme_default.yaml)
--output <FILE>         Output HTML path (default: map_output.html beside GPX)
--verbose               Show cache type breakdown
```

### Cache Type Colors

| Type | Color |
|---|---|
| Traditional | Green |
| Mystery / Unknown | Blue |
| Multi | Orange |
| Earthcache | Brown |
| Virtual | Purple |
| Letterbox Hybrid | Teal |
| Wherigo | Teal (darker) |
| CITO / Event / Mega / Giga | Red/amber variants |

All colors are configured in the `cache_types` section of `theme_default.yaml`. Copy and edit it, then pass `--theme mytheme.yaml` to use a custom palette.

### County Assignment

County data is sourced from two places:

**GPX field** — GSAK can populate a `<gsak:County>` field in the export. When present, this is used directly. PEI and Ontario, for example, often have county data embedded.

**Coordinate lookup** — When no county field is present (common for Quebec and most Canadian exports), `geocache_map.py` automatically resolves the county by point-in-polygon lookup against `gsak_counties.db`. This requires the DB to be present. Results are memoized per coordinate, so a dense cluster of caches in the same county only costs one DB hit.

Both paths produce keys in `STATE,Name` format (e.g. `BC,Greater Vancouver`, `QC,Communauté-Métropolitaine-de-Montréal`) that are matched against the DB for choropleth rendering.

**Supported geographies** for the `--overlay counties` choropleth:

| Geography | Data source | Build command |
|---|---|---|
| US counties | `us_counties.geojson` | `gsak_build_geojson.py` |
| Canadian regional districts | `gsak_counties.db` | `gsak_counties.py build --country CA` |
| International regions (NO, IS, CZ, FO, FR, …) | `gsak_counties.db` | `gsak_counties.py build --country <code>` |

Build the DB once per country:
```bash
python gsak_counties.py build --gsak-dir gsak --country US --verbose
python gsak_counties.py build --gsak-dir gsak --country CA --verbose
python gsak_counties.py build --gsak-dir gsak --country IS --verbose
```

### Filter Panel (`--show-filters`)

Injects a collapsible **Filters** panel (top-left). Sections:

- **Cache Types** — checkboxes toggle each type's FeatureGroup layer on/off
- **Difficulty** — min/max range sliders (1–5 in 0.5 steps)
- **Terrain** — min/max range sliders (1–5 in 0.5 steps)

> The D/T sliders adjust the display of caches already loaded — they do not re-filter at the Python level. For a tighter pre-filtered set, use the CLI `--difficulty` and `--terrain` arguments.

---

## `gsak_counties.py`

Builds a SQLite database of county and regional boundary polygons from GSAK `.txt` polygon files, and provides fast point-in-polygon lookup for coordinate → county assignment.

County polygon data is sourced from GSAK (Clyde Findlay and GSAK community contributors). See `CREDITS.txt` and `gsak/README.txt` for full attribution.

### Quick Start

```bash
# Build the database (run once, or after adding new polygon files)
python gsak_counties.py build --gsak-dir gsak --country US --verbose
python gsak_counties.py build --gsak-dir gsak --country CA --verbose

# Verify coverage
python gsak_counties.py stats

# Look up a county by coordinates
python gsak_counties.py lookup 47.56 -122.03
python gsak_counties.py lookup 51.05 -114.07
```

### All Options

```
build
  --gsak-dir <DIR>    Root GSAK Counties directory (contains US/ and/or CA/ subdirs)
  --db <FILE>         Output DB path (default: gsak_counties.db)
  --country <CODE>    Country subdirectory to load: US or CA (default: usa)
  --verbose           Show per-state/province region counts and skip warnings

lookup <LAT> <LON>
  --db <FILE>         DB path (default: gsak_counties.db)

stats
  --db <FILE>         DB path (default: gsak_counties.db)
```

### Directory Structure

The script expects polygon files organised as:

```
gsak/
  US/
    WA/          ← 2-letter state postal code
      King.txt
      Snohomish.txt
      ...
  CA/            ← Canada
    AB/          ← 2-letter province postal code
      Calgary.txt
```

Full documentation of the directory structure, filename conventions, and coverage is in `gsak/README.txt`.

### Python API

```python
from gsak_counties import lookup_county, lookup_county_adif_key

# Returns ('WA', 'King') or (None, None) if not found / DB absent
state, county = lookup_county(47.56, -122.03)

# Returns 'WA,King' or None
key = lookup_county_adif_key(47.56, -122.03)

# With state hint for speed (bbox filter narrows candidates)
state, county = lookup_county(47.56, -122.03, state_hint='WA')
```

---

## `gsak_build_geojson.py`

Generates `us_counties.geojson` from `gsak_counties.db`, replacing the Census-derived file with higher-fidelity GSAK polygon boundaries.

### Quick Start

```bash
# Default — light simplification, ~12MB (recommended)
python gsak_build_geojson.py

# Full fidelity — ~26MB
python gsak_build_geojson.py --full

# Moderate simplification — ~8MB (if 12MB is still slow to load)
python gsak_build_geojson.py --simplify 0.001

# Custom paths
python gsak_build_geojson.py --db path/to/gsak_counties.db --out path/to/us_counties.geojson
```

### All Options

```
--db <FILE>         Path to gsak_counties.db (default: gsak_counties.db)
--out <FILE>        Output GeoJSON path (default: us_counties.geojson)
--simplify <ε>      RDP simplification tolerance in degrees
                    Default: 0.0005 (~12MB). Use 0 or --full for no simplification.
--full              Shorthand for --simplify 0 (full fidelity, ~26MB)
--verbose           Show simplification stats and namelsad spot-check
```

### Simplification Guide

| Flag | File size | Use case |
|---|---|---|
| `--full` or `--simplify 0` | ~26MB | Archival, highest detail |
| *(default)* `--simplify 0.0005` | ~12MB | Normal use — recommended |
| `--simplify 0.001` | ~8MB | Slower machines or large overlays |

Simplification uses the Ramer-Douglas-Peucker algorithm. At county zoom levels, differences between the default and full-fidelity versions are imperceptible.

The generated file uses the same property schema as the previous Census-derived file (`adif_key`, `namelsad`, `state`, `name`) — it is a drop-in replacement with no changes required to `map_core.py` or `adif_map.py`.


---

## Debugging and Diagnostics

### Console output

Both `adif_map.py` and `geocache_map.py` print a progress summary to the console. When an overlay produces fewer results than expected, this output is the first place to look:

```
  Theme loaded: theme_default.yaml
  Parsing contacts.adi ...
    2847 QSO records found.
  Station location: 47.6162, -122.1580
  2847 QSOs after filtering.
  223 JJ00 (null-grid) contact(s) excluded — use --include-null-grid to show them.
  Modes: FT8 (1203), CW (844), SSB (620), ...
  Building map ...
  Building county overlay ...
  County overlay: 312 confirmed, 48 worked-only (7 international region(s) from DB).
  Map saved → map_output.html
  Plotted 2847 contacts.
```

With `--overlays-only --overlay grids`:
```
  --overlays-only: contact dots suppressed; unworked overlay cells shown as ghost polygons.
  Land grid whitelist: 6,214 grids loaded from land_grids.txt
  Grid overlay: 187 confirmed, 43 worked-only squares, 892 unworked ghost cells.
  Map saved → map_output.html
  187 contacts included in overlay choropleth (dots hidden).
```

Key lines to check:

- **`Theme loaded`** — confirms the theme file was found. If absent, built-in defaults are used silently.
- **`N QSOs after filtering`** — if this is much lower than the total, check your `--band`, `--mode`, or `--date-from`/`--date-to` arguments.
- **`N JJ00 (null-grid) contact(s) excluded`** — contacts with a `GRIDSQUARE` of JJ00 (or coordinates in that grid) are excluded by default. Pass `--include-null-grid` to include them.
- **`Land grid whitelist: N grids loaded`** — confirms `land_grids.txt` was found for `--overlays-only` ghost cell filtering. If absent, a fallback note appears instead and the full bounding-box set is used.
- **`County overlay: N confirmed, M worked-only (K international region(s) from DB)`** — if `K` is 0 when you expect international counties, the DB wasn't found or the country hasn't been built into it yet.
- **`County overlay: N international region(s) skipped (no --db path provided)`** — you need to pass `--db gsak_counties.db`.

### Verifying the county database

```bash
# Show all countries and region counts in the DB
python gsak_counties.py stats --db gsak_counties.db

# Confirm a specific coordinate resolves correctly
python gsak_counties.py lookup 49.25 -123.10 --db gsak_counties.db
# Expected: BC, Greater Vancouver

python gsak_counties.py lookup 45.50 -73.59 --db gsak_counties.db
# Expected: QC, <regional county name>
```

If `lookup` returns nothing for a coordinate that should be in a built country, the polygon file for that region may be missing from the `gsak/` directory or wasn't included in the `build` run. Re-run with `--verbose` to see skip warnings.

### QSOs missing from the map (`adif_map.py`)

If plotted contacts are fewer than QSOs after filtering, the console will report:

```
  Note: 143 QSO(s) skipped — no usable coordinates found.
```

`adif_map.py` resolves contact coordinates in this order: `LAT`/`LON` fields → `GRIDSQUARE` field. If neither is present the QSO is silently skipped. Common causes:

- **JJ00 null-grid contacts** — contacts with `GRIDSQUARE=JJ00` (a placeholder for stations with no real location data) are excluded by default and do not count toward the plotted total. The console reports how many were excluded. Pass `--include-null-grid` to include them.
- **QRZ exports** — coordinates are usually present. If missing, re-export from QRZ with coordinate fields enabled.
- **LoTW exports** — LoTW does not export `LAT`/`LON`. `GRIDSQUARE` is usually present if the other party entered a grid. If it's missing, there's no remedy from this side.
- **FT8/digital contacts** — grid squares are normally exchanged as part of the protocol and will be present. Missing grids here usually indicate a logging error.

Add `--verbose` to see your operating location(s) resolved from `MY_LAT`/`MY_LON` or `MY_GRIDSQUARE`. If your own station location can't be resolved, the map will abort with an error — check that your ADIF header or records contain `MY_GRIDSQUARE`.

### Counties not coloring on the map

Work through this checklist:

1. **Is the DB found?** — `geocache_map.py` searches for `gsak_counties.db` in this order: the `--db` argument, beside the script, then the current working directory. If none is found it prints a warning and continues without DB features. Pass `--db gsak_counties.db` explicitly if auto-detection is failing — particularly if you're running the script from a different directory than where the DB lives.
2. **Is the country built into the DB?** — Run `gsak_counties.py stats` and confirm the expected country code appears.
3. **Does `lookup` work for a cache coordinate?** — If `lookup` returns nothing, the polygon for that region is missing. Re-run `build --verbose` for that country.
4. **Does the GPX have a country field?** — `geocache_map.py` uses the `<groundspeak:country>` element to identify international caches. If this field is blank or mismatched, the coordinate lookup will still attempt to resolve it, but the country border overlay won't include it.
5. **Is the adif_key format correct?** — County keys must match exactly what's in the DB. Run `sqlite3 gsak_counties.db "SELECT adif_key FROM counties WHERE state_code='BC' LIMIT 5"` to see the exact format. A key mismatch (e.g. accented vs. unaccented name) means the polygon exists in the DB but won't match. `geocache_map.py` strips accents from international county names before lookup — if you're building a custom tool, do the same.

### ADIF county fields not matching

If the `--overlay counties` layer shows fewer US counties than expected:

- Run `adif_map.py` without `--confirmed` to see worked-but-unconfirmed counties too.
- Check the raw `CNTY` field in your ADIF: QRZ and LoTW sometimes export as `WA,King County` (with suffix) — these are stripped automatically, but other unexpected formats may not be. Add `--verbose` and inspect the console.
- LoTW exports county names in ALL CAPS (`WA,KING`). These are title-cased automatically. If you see zero county matches from a LoTW export, verify the `CNTY` field is present at all — some LoTW exports omit it.

---

## `qrz_common.py` — Shared Library

Not run directly. Imported by all scripts. Provides:

- **ADIF parser** — `parse_adif_file()` for QSO records; `parse_adif_with_header()` also returns header-level fields (used by `adif_map.py` for `MY_LAT`/`MY_LON`/`MY_GRIDSQUARE`); handles HTML-escaped brackets from QRZ API responses
- **QRZ API client** — `ACTION=INSERT OPTION=REPLACE` for in-place updates
- **Key file loader** — reads `<CALLSIGN>.key`, maps `/` to `_` in filenames
- **Config file loader** — reads `<CALLSIGN>.cfg` for per-field rules
- **Field converters** — `CNTY` display-to-ADIF format, `STATE` normalisation, coordinate validation
- **Maidenhead grid utilities** — `latlon_to_grid()` and `grid_to_latlon()` for 4-, 6-, or 8-character locators; `adif_latlon_to_decimal()` converts ADIF `N/S/E/W DDD MM.MMM` strings to decimal degrees
- **Date/time normalisation** — `parse_qso_datetime()` accepts both ADIF compact format (`YYYYMMDD`/`HHMM`) and ISO format (`YYYY-MM-DD`/`HH:MM`); `format_qso_datetime()` converts ADIF compact to human-readable for CSV output
- **Field comparison utilities** — integer normalisation, gridsquare prefix matching, country name mapping
