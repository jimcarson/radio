# Changelog

## 2026-05-04

### Architecture change: GADM replaces GSAK for international regions

The county polygon database now uses a two-source architecture:

- **US + Canada** — GSAK polygon files (unchanged). These must match LoTW `CNTY` field values exactly, so GSAK remains the source of truth.
- **All other countries** — GADM 4.1 (UC Davis), imported via `import_gadm.py`. GADM provides clean UTF-8 GeoJSON with consistent structure across all countries, and is actively maintained.

All international countries are now stored under **ISO 2-letter state_codes** (NO, FI, JP, DE, FR, …) rather than ham radio prefixes (LA, OH, JA, DL, F, …). Ham prefixes are accepted as command-line aliases but the DB always stores the ISO code. Three countries have ISO codes that collide with US state abbreviations and use their ham prefix as the DB state_code instead:

| Country | ISO | Ham prefix (DB code) | Collision avoided |
|---|---|---|---|
| Finland | FI | — | no collision; `FI` used directly |
| Norway | NO | — | no collision; `NO` used directly |
| Indonesia | ID | YB | Idaho (`ID`) |
| India | IN | VU | Indiana (`IN`) |

### `import_gadm.py` (v1.1.3)

Complete rewrite of the country table and CLI:

- **ISO-primary `COUNTRY_TABLE`** — each entry is now `(GADM ISO3, DB state_code, display name)`. Primary keys are ISO 2-letter codes; ham prefix aliases resolve to the same ISO3/state_code. `--list` output now shows DB code, ISO3, country name, and all accepted aliases in a single table.
- **`--level` argument** (1 or 2, default 1) — selects GADM admin level. Level 1 is the top administrative division (states, fylker, Bundesländer). Level 2 gives finer subdivisions — needed for Finland, where level 1 is only 5 macro-regions but level 2 gives the proper 19 maakunta.
- **`--state-code` override** — forces a specific state_code into the DB, rarely needed.
- **GeometryCollection support** in `_flatten_geometry()` — handles features that use this type instead of Polygon/MultiPolygon. Previously these were silently skipped.
- **Geometry skip warnings** always printed (not just with `--verbose`) — missing regions are never silent.
- **Encoding fix** — `_decode_json_bytes()` tries UTF-8 then falls back to Latin-1, covering older GADM vintages.
- **New countries added:** Poland (`PL`/`SP`), Indonesia (`YB`/`ID`), India (`VU`/`IN`), Belgium (`BE`), and a full European + Asia-Pacific + Americas set.
- **`SP` alias corrected** — `SP` is the Poland ham prefix, not Spain. Spain's ham prefix is `EA`.

### `gsak_counties.py` (v1.6.1)

Two new CLI subcommands for DB inspection and maintenance:

**`list`** — shows all region names and adif_keys stored for a given state_code, with polygon part counts for MultiPolygon regions:
```
python gsak_counties.py list --db gsak_counties.db --state-code NO
python gsak_counties.py list --db gsak_counties.db --state-code FI
```

**`delete`** — removes all county rows for a given state_code (prompts for confirmation unless `--yes`/`-y` is passed):
```
python gsak_counties.py delete --db gsak_counties.db --state-code NO
python gsak_counties.py delete --db gsak_counties.db --state-code NO --yes
```

### `map_core.py` (v1.4.7)

**`_INTL_ISO_CODES` routing fix** — `build_counties_overlay()` previously routed any key whose state_code was in the US postal abbreviation set to the GeoJSON path. This caused Finland (`FI` fine, but old `OH` imports) and any future country whose ISO code or DB code coincides with a US state to silently fail. A new `_INTL_ISO_CODES` frozenset lists all ISO/ham codes used by `import_gadm.py`; keys in this set are always routed to the DB regardless of whether the code also appears in `_US_CODES`. Currently includes: `AR AT AU BE BR BY CH CL CN CZ DE DK ES FI FR GB HK IS IT JP KR MX NL NO NZ PE PL RU SE UA VE VU YB`.

### New file: `rebuild_db.bat`

One-shot Windows batch script that deletes the existing `gsak_counties.db` and rebuilds it cleanly from scratch. Runs GSAK builds for US and Canada, then GADM imports for the full international set (Norway, Finland, Iceland, Sweden, Denmark, Germany, France, UK, Netherlands, Belgium, Switzerland, Austria, Italy, Spain, Czechia, Poland, Ukraine, Japan, South Korea, Australia, New Zealand, Argentina, India, Indonesia, Russia). Edit `GSAK_DIR` at the top to match your installation path before running.

## 2026-05-03

### New files
**`import_gadm.py`** (v1.0.0) - one-time import for downloading from UC Davis GADM, provides us with Japan, Mexico, South Korea among others.  Run this with the "--list" option.

**`gsak/??`** - added numerous country region polygons from GSAK.  I may need to cull these as the GADM maps end up working better for many (not all, e.g., CZ) cases.

## 2026-05-02

### New files

**`build_land_grids.py`** (v1.0.0) — one-time setup script that generates `land_grids.txt`, a whitelist of Maidenhead grid4 squares that are within 2 grid-widths (~4°) of a land mass. Fetches the Natural Earth 110m land polygon GeoJSON (public domain), buffers it with `shapely`, and tests all 32,400 valid grid4 squares. Output is a plain-text file (one grid per line) read by `map_core.py` at runtime — `shapely` is not a runtime dependency. Accepts `--buffer N` (default 4.0°) and `--output FILE` arguments.

**`land_grids.txt`** — generated output of `build_land_grids.py`. Static whitelist used to restrict ghost cell rendering in `--overlays-only` mode to land-adjacent grids only, eliminating open-ocean cells from the bounding-box enumeration and substantially reducing browser render time.

### `adif_map.py` (v1.2.3)

**JJ00 null-grid exclusion**

- Contacts resolving to the JJ00 grid are now excluded by default. JJ00 (0°–2° N, 0°–2° W, mid-Atlantic near the prime meridian/equator) is a common placeholder for stations with no real location data and previously produced a dense, meaningless cluster on the map.
- Detection covers two cases: `GRIDSQUARE` starting with `JJ00` (any sub-square suffix, case-insensitive), and explicit `LAT`/`LON` coordinates falling within the JJ00 bounding box.
- New `is_null_grid(record)` helper encapsulates both checks.
- New CLI flag `--include-null-grid` re-enables JJ00 contacts when needed.
- Console output reports the count of excluded JJ00 contacts when any are present.

**`--overlays-only` mode**

- New CLI flag `--overlays-only` hides all contact dots (MarkerCluster FeatureGroups) and arcs, leaving only the choropleth overlay(s) visible. Home station markers are preserved.
- When active, unworked cells in all overlay types render as ghost polygons: transparent fill, visible border, fully hoverable — so you can identify needed grids/states/counties directly without inferring from neighbors.
- Ghost grid cells are filtered through `land_grids.txt` (see `map_core.py`) so only land-adjacent cells appear. If `land_grids.txt` is missing, ghost cells are skipped with a warning directing you to run `build_land_grids.py`.
- `band_groups` is still populated in overlays-only mode so the legend and `layer_meta` remain consistent.
- Console output reflects the suppression and reports ghost cell count.

### `map_core.py` (v1.4.4)

**Ghost cell rendering for `--overlays-only`**

- `build_grid_overlay()` — new `overlays_only` parameter. When `True`, enumerates all valid Maidenhead grid4 squares within the bounding box of worked grids ± 1-cell padding (2° lon × 1° lat per cell), filters candidates through `land_grids.txt`, and adds unworked land-adjacent ones as ghost features styled with a visible border, zero fill opacity, and a tooltip showing the grid designator (e.g. `DN82`). Ghost count reported in console output.
- New `_load_land_grids()` — reads `land_grids.txt` into a `frozenset` on first call and caches it. If the file is absent, prints a warning once and returns `None`; ghost cells are then skipped entirely.
- New `_all_grid4_in_bbox(lat_min, lat_max, lon_min, lon_max)` helper enumerates all valid grid4 squares whose SW corner falls within the given bounding box. Clamps to Maidenhead limits and snaps to grid-cell boundaries.
- `build_states_overlay()` — new `overlays_only` parameter. When `True`, unworked states render with a faint fill (`fillOpacity: 0.10`) and their configured border, making them hoverable for identification.
- `build_counties_overlay()` — new `overlays_only` parameter. When `True`, unworked counties render with zero fill but their configured `unworked_border` weight, keeping county lines visible.
- `THEME_DEFAULTS` grids section gains `unworked_fill`, `unworked_border`, and `unworked_weight` keys (matching the existing states/counties schema) so grid ghost styling is overrideable via `theme_default.yaml`.

### `adif_map.py` (v1.2.5)

- `verbose` parameter added to `build_map()` and threaded through to `build_base_map()` so the tile layer summary prints correctly under `--verbose`.

### `geocache_map.py` (v1.3.3)

- `verbose` parameter added to `build_map()` and threaded through to both `build_base_map()` call sites.

### `map_core.py` (v1.4.5)

**Optional tile providers via `map.cfg`**

- New `_load_map_cfg()` — reads `map.cfg` (beside `map_core.py`) into a `{provider: apikey}` dict on first call, cached at module level. Format: `provider : apikey`, one per line, `#` comments supported. Returns empty dict silently if file is absent.
- `build_base_map()` gains `verbose: bool = False` parameter.
- **Thunderforest** — if `thunderforest` key is present in `map.cfg`, adds three layers: Outdoors, Landscape, Cycle.
- **Stadia Maps** — if `stadia` key is present in `map.cfg`, adds four layers: Alidade Smooth, Alidade Smooth Dark, Stamen Toner, Stamen Terrain.
- **Esri NatGeo** moved to last position so it is the default visible layer on map open (Leaflet activates the last tile layer added).
- **OpenStreetMap removed** — OSM's tile servers enforce a referer policy that blocks requests from locally-opened HTML files. Stadia Toner is a suitable high-contrast alternative.
- When `verbose=True`, prints a one-line tile layer summary including any optional layers loaded from `map.cfg`.

---

## 2026-04-30

### New files

**`country_mapping.py`** — ISO 3166-1 alpha-2 ↔ GSAK country name mapping. Contains `GSAK_NAME_TO_ISO` (250 entries, keyed on canonical `#GsakName=` values) and the auto-derived inverse `ISO_TO_GSAK_NAME`. Corrects three errors present in the GSAK `countries.txt` source: Belgium/Belarus/Barbados were cyclically swapped; Belize was missing (its `BZ` code had been misassigned to Benin). Adds alias entries for old GSAK names (`Swaziland`, `East Timor`) and the de-facto `XK` code for Kosovo. Planned to be merged into `geo_mapping.py` in a future session alongside `_STATE_POSTAL`.

### `gsak_counties.py` (v1.6.0)

**New `country_polygons` table** added to `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS country_polygons (
    id INTEGER PRIMARY KEY, country_name TEXT, iso_code TEXT,
    part_num INTEGER, min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL,
    polygon TEXT
);
```

Indexes on `country_name` and `iso_code`. Schema is applied in `_open_db()` (moved from `build_db()`) so both tables are always present on any DB open.

**New CLI subcommand: `build-countries`**

```
python gsak_counties.py build-countries \
    --gsak-dir "D:/dev/radio/gsak" --db gsak_counties.db [--verbose]
```

Walks `gsak_dir/Countries/*.txt`, parses each file via the existing `_parse_polygon()`, assigns `part_num` per country (multi-part countries like Belgium store 23 separate rows), looks up ISO code from `GSAK_NAME_TO_ISO`. Full rebuild: deletes all rows for found country names before inserting. Reports country count and total part count.

**New CLI subcommand: `list-countries`**

```
python gsak_counties.py list-countries --db gsak_counties.db [--country Iceland]
```

Without `--country`: tabular summary of all countries (name, ISO, part count). With `--country NAME`: lists each part with its bounding box.

**New public function: `lookup_country(lat, lon, db_path)`**

Returns the `country_name` string for the given coordinates, or `None`. Uses bbox pre-filter then point-in-polygon. Gracefully handles missing DB or missing table.

**New helper: `_gsak_name_from_stem(stem)`** — strips trailing digits and separators from a filename stem to derive a country name when no `#GsakName=` header is present (`Belgium23` → `Belgium`).

**Import:** `GSAK_NAME_TO_ISO`, `ISO_TO_GSAK_NAME` now imported from `country_mapping`.

### `map_core.py` (v1.3.0)

**New function: `build_country_borders_overlay(m, db_path, country_names=None)`**

Adds a country border line layer from `country_polygons` table. Renders as thin dark lines (`#444444`, weight 1.5, no fill) with country name tooltip. Accepts optional `country_names` filter list to render only countries present in the cache data. Safe to call multiple times (deduplicates via `_country_borders_added` set, same pattern as `_add_state_borders`).

**`build_counties_overlay()` extended** with `db_path=None` parameter. When provided:

- Keys are classified as US/CA (prefix in `_POSTAL_STATE`) or international
- US/CA keys go to the existing GeoJSON path (unchanged — no regression)
- International keys are queried from the `counties` table by `adif_key`, polygons reconstructed from stored JSON (lat/lon flipped to GeoJSON order), tooltip properties normalised to the same schema as GeoJSON features
- All features merged into one `FeatureCollection`, rendered as a single layer
- If `db_path=None`: existing US/CA-only behavior unchanged

### `geocache_map.py` (v1.1.0)

**New `--db FILE` argument** — path to `gsak_counties.db`. Auto-detected beside the script or in CWD if not specified; warns but continues if not found.

**International county/district shading** — `build_counties_overlay` now receives `db_path` and shades international regions from the GSAK polygon DB.

**Country borders overlay** — when any overlay is active and a DB is available, `build_country_borders_overlay` is called automatically with the set of countries present in the filtered cache data. Borders render beneath county shading and cache dots.

**Layer ordering fix** — the base map is now created first, overlays added in order (country borders → county shading), then `build_map()` adds cache dot FeatureGroups on top. Previously all overlays were added after dots, causing the country border layer to render above markers and toggle to the top when clicked.

**`build_map()` now accepts optional `m=None`** — if an existing map is passed, dots are added to it; otherwise a new base map is created. Allows callers to pre-populate overlays before adding markers.

**`_strip_accents()` helper** — two-pass transliterator: substitutes non-NFD-decomposable characters (`ø→o`, `ð→d`, `þ→th`, `æ→ae`, `å→a`) then strips combining accent marks via NFD. Covers Icelandic, Norse, Faroese, and Czech names.

**`_cnty_key()` rewritten** to handle three distinct GPX field patterns:

| Pattern | Example | Result |
|---|---|---|
| US/CA: 2-letter state + county field | `state='WA', county='King'` | `WA,King` |
| US/CA: 2-letter state, no county | `state='MI', county=''` | coordinate lookup → `MI,Mason` |
| Flat country: region name in state | `state='Höfudborgarsvaedi'` | `IS,Hofudborgarsvaedi` |
| CZ-style: region in state, district in county | `state='Ústecký kraj', county='Decin'` | `CZ,Decin` |
| CZ-style: region in state, no county | `state='Ústecký kraj', county=''` | coordinate lookup → `CZ,Decin` |

US/CA caches with no GPX county field (the common case for GSAK exports) now use `lookup_county(lat, lon)` with `state_hint` for fast point-in-polygon resolution. International caches with a region-level state field (CZ, and potentially others) also fall back to coordinate lookup. Results memoized in `_coord_county_cache` keyed by rounded coordinates.

**`countries_to_gsak_names()` helper** — maps free-text GPX country strings to canonical GSAK names via `GSAK_NAME_TO_ISO` / `ISO_TO_GSAK_NAME`, with case-insensitive fallback.

**`resolve_db_path()` helper** — resolves `--db` argument with fallback search (script dir → CWD), warns if not found.



### New files

**`map_core.py`** — shared mapping engine extracted from `adif_map.py`. Both `adif_map.py` and `geocache_map.py` now import from this module. Contains: theme loading, base map construction, great-circle geometry, all three choropleth overlay builders (states, counties, grids), overlay legend, and the JS theme color serializer.

**`geocache_map.py`** — new geocache map viewer. Parses GSAK GPX exports and plots caches on an interactive map, clustered by type. Supports `--type`, `--difficulty`, `--terrain`, `--found`/`--not-found` filters. Optional `--show-filters` panel with type checkboxes and D/T range sliders. Shares all overlay and legend infrastructure with `adif_map.py` via `map_core.py`.

**`gsak_counties.py`** — GSAK polygon database builder and point-in-polygon lookup engine. Reads county/regional boundary `.txt` files distributed with GSAK and builds a SQLite database with bounding-box index for fast lookup. Provides `lookup_county(lat, lon)` for coordinate → county assignment. Supports US (all 50 states + DC, ~3,382 counties) and Canada (11 provinces/territories, ~239 regional divisions). CLI subcommands: `build`, `lookup`, `stats`.

**`gsak_build_geojson.py`** — generates `us_counties.geojson` from `gsak_counties.db`. Replaces the Census/Natural Earth-derived county file with higher-fidelity GSAK polygon boundaries. Includes Ramer-Douglas-Peucker simplification (`--simplify`, default `0.0005` / ~12MB; `--full` for the 26MB unsimplified version). Reconstructs proper `namelsad` display names (e.g. "King County", "Acadia Parish", "Juneau City and Borough") by state.

**`gsak_rename.py`** — one-time utility (lives in `gsak/`) to normalize Canadian GSAK polygon filenames from space-separated to underscore convention, matching the US file format. Handles accented characters, apostrophes, dots, and hyphens. Idempotent — safe to re-run.

**`gsak/README.txt`** — attribution and documentation for the GSAK polygon data directory.

**`CREDITS.txt`** — attribution for all third-party data sources (GSAK polygons, Folium, tile providers, Natural Earth, LoTW/QRZ, Geocaching.com).

### `map_core.py` (v1.2.0)

- `classify_records()` now returns a 4-tuple `(status, counts, confirmed_counts, worked_counts)` — confirmed and worked counts tracked separately for tooltip display.
- County, state, and grid tooltips now show `Confirmed: N | Worked: M` counts. Counts are embedded into GeoJSON feature properties at build time so `GeoJsonTooltip` can display them without JS.
- `_add_state_borders()` — new thin black boundary line layer (weight 1.2, no fill, non-interactive) added automatically whenever any choropleth overlay is active. Uses a per-map-id set to prevent duplicate layers when multiple overlays are built.
- Fixed closure/rebinding bug: `style_fn` closures in all three overlay builders now reference module globals (`COUNTIES_COLORS`, `STATES_COLORS`, `GRIDS_COLORS`) directly rather than capturing a local alias. Previously, `load_theme()`'s reassignment of these globals left closures pointing at the original empty dicts, causing all counties to render with `fillOpacity: 0`.
- Overlay legend repositioned to `bottom-right` (was `bottom-left`) to avoid overlapping the layer control.

### `adif_map.py` (v1.2.0)

- Refactored to import all shared map infrastructure from `map_core.py`. ADIF-specific logic (parsing, filtering, arc rendering, toggle panel) remains in this file.
- `_cnty_key_fn` now normalises county names to title case before matching, fixing zero-match results when using LoTW exports (which store county names in ALL CAPS).
- Added `_DEFIX` normalisation table in `_cnty_key_fn` for known LoTW/GSAK spelling differences: `De Kalb → DeKalb` (6 states), `De Soto → DeSoto` (FL, MS), `De Witt → DeWitt` (TX). Louisiana "De Soto" (Parish) correctly excluded from normalisation.
- Band legend hidden when `--show-filters` is active (`display:none`) — the filter panel already shows band swatches so the separate legend is redundant.
- Overlay calls updated to pass `_confirmed`-tagged records and per-entity key functions to `map_core` overlay builders.

### `theme_default.yaml`

- County `fill_opacity` raised from `0.55` → `0.75` — fills are now clearly visible.
- County borders changed to thin black (`#000000`, weight `0.8` for worked/confirmed, `0.3` for unworked) — consistent with state borders, visually separates county grid from fill.
- State `fill_opacity` raised from `0.35` → `0.55`.
- Unworked counties show a thin black border (`weight: 0.3`) with no fill — county grid always visible regardless of worked status.

### `us_counties.geojson`

- Replaced Census/Natural Earth-derived file with GSAK community polygon data via `gsak_build_geojson.py`.
- Higher-fidelity boundaries (King County: 1,359 points vs. ~50 in the Census version).
- Property schema preserved: `adif_key`, `namelsad`, `state`, `name` — drop-in replacement, no changes to `map_core.py` required.
- Default output is lightly simplified (RDP ε=0.0005, ~12MB). Run `gsak_build_geojson.py --full` for the 26MB unsimplified version.

---

## 2026-04-22

### `adif_map.py`

**Arc rendering — major overhaul**

- Arcs are now decimated before rendering, making `--show-arcs` practical on large logs. Previously every QSO produced an arc; a 12,500-QSO log now draws ~850 arcs instead of thousands.
- Deduplication by callsign: only one arc per unique contact regardless of how many bands or modes were worked. A station worked on 9 bands produces one arc, not nine.
- Geographic cell cap (`--arc-cell-max`, default 2): the globe is divided into 5°×5° cells; cells with fewer contacts are filled first, ensuring rare DX always gets an arc while dense corridors (e.g. Europe, Japan) are sampled rather than flooded.
- Global arc cap (`--arc-max`, default 1000).
- Two new CLI flags: `--arc-max N` and `--arc-cell-max N`.
- Arcs are now rendered in a dedicated `Arcs` FeatureGroup, making them independently toggleable in the layer control without regenerating the file.
- `_gc_points()` default interpolation points reduced from 60 to 32 — visually indistinguishable at map scale, cuts per-arc point count nearly in half.

**Antimeridian fix**

- Arcs crossing the ±180° meridian (e.g. West Coast US → Japan/Pacific) are now split into two Leaflet-renderable segments, each within `[-180, 180]`. Previously these arcs were rendered off-canvas or wrapped incorrectly.

**Map centering**

- New theme key `map_center_lon_offset` (default `0`) shifts the initial map view east or west of the station longitude.

**Bug fixes and cleanup**

- Fixed stray `parser.add_argument(...)` line in the module docstring.
- Removed dead first `computeStatus()` JavaScript function inside `inject_toggle_panel()`.
- Removed redundant second set of overlay color stub definitions.
- `_classify_contacts()` and `_build_overlay_qso_data()` now call `is_confirmed()` instead of inlining the same field-check logic.
- `resolve_my_coords()` now uses a `set` for O(1) origin deduplication.
- `--show-mode-filters` renamed to `--show-filters`.

### `theme_default.yaml`

- Added `map_center_lon_offset` key with documentation.
- State border colors changed to black (`#000000`).
