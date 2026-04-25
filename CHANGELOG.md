# Changelog

## 2026-04-25

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
