rem process GSAK polygons for countries
python gsak_counties.py build --gsak-dir gsak --country US --verbose
python gsak_counties.py build --gsak-dir gsak --country CA --verbose
python gsak_counties.py build --gsak-dir gsak --country UK --verbose
python gsak_counties.py build --gsak-dir gsak --country IS --verbose
python gsak_counties.py build --gsak-dir gsak --country CZ --verbose
python gsak_counties.py build --gsak-dir gsak --country FO --verbose

rem # Default — 12.8MB light, what you'll use day-to-day
python gsak_build_geojson.py

rem # Full fidelity — 26MB, for comparison or if you ever need the detail
python gsak_build_geojson.py --full

rem # If 12.8MB still loads slowly in practice
python gsak_build_geojson.py --simplify 0.001
