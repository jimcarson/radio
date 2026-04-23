# Changelog

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
- Algorithm: unwrap longitude into a continuous chain (may temporarily exceed ±180°), renormalise each point into `[-180, 180]`, split into new segments wherever renormalisation causes a jump > 170°.

**Map centering**

- New theme key `map_center_lon_offset` (default `0`) shifts the initial map view east or west of the station longitude. West Coast US stations with contacts in both Europe and Asia/Pacific can set this to their longitude (e.g. `122`) to center the initial view on the prime meridian.

**Bug fixes and cleanup (from code review)**

- Fixed stray `parser.add_argument(...)` line in the module docstring (was not valid documentation).
- Removed dead first `computeStatus()` JavaScript function inside `inject_toggle_panel()` — the second (correct) two-parameter version is the one used.
- Removed redundant second set of `OVERLAY_COLORS` / `STATES_COLORS` / `COUNTIES_COLORS` / `GRIDS_COLORS` stub definitions that appeared after the grid overlay functions.
- `_classify_contacts()` and `_build_overlay_qso_data()` now call `is_confirmed()` instead of inlining the same field-check logic.
- `resolve_my_coords()` now uses a `set` for O(1) origin deduplication instead of O(n²) list membership testing. Measurable speedup on large logs.
- `--show-mode-filters` renamed to `--show-filters` for brevity (docstring updated to match).

### `theme_default.yaml`

- Added `map_center_lon_offset` key with documentation.
- State border colors changed to black (`#000000`) for better visibility against satellite and topo tile layers.
