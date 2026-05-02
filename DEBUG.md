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
