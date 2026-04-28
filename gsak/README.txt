GSAK County / Regional Polygon Data
=====================================

About this data
---------------
This directory contains county and regional boundary polygon files
originally distributed with GSAK (Geocaching Swiss Army Knife).

Each file defines the boundary of a single county, parish, borough,
or regional district as a series of latitude/longitude coordinate pairs.
US files use whitespace-separated coordinates; Canadian files use
comma-separated coordinates. Both formats are handled automatically
by gsak_counties.py.

Directory structure
-------------------
  gsak/
    US/
      WA/          <- 2-letter state postal code
        King.txt
        Snohomish.txt
        ...
      CA/
        Los_Angeles.txt
        ...
    CA/            <- Canada
      AB/          <- 2-letter province postal code
        Calgary.txt
        ...
      QC/
        Montreal.txt
        ...
    AR, AU, CL, CZ, DE, ES, FO, FR, GR, HU, ID, IE, IS, IT, NL, NO, NZ, UK -
        regions within Argentina, Australia, Chile, Czechia, Germany, Spain, 
        Faroe Islands, France, Greece, Hungary, Indonesia, Ireland, Iceland,
        Italy, Netherlands, Norway, New Zealand and the United Kingdom
    Countries - polygons from GSAK

Filename conventions (US)
-------------------------
Spaces in county names are represented by underscores:
  San_Augustine.txt  ->  San Augustine County, TX
  St._Clair.txt      ->  St. Clair County, AL

Louisiana files omit the "Parish" suffix:
  Acadia.txt         ->  Acadia Parish, LA

Virginia independent cities are distinguished from counties by a
whitelist in gsak_counties.py — the filenames look the same but
the display names differ:
  Alexandria_City.txt  ->  Alexandria (Ind. City), VA
  Charles_City.txt     ->  Charles City County, VA  (a real county)

Filename conventions (Canada)
------------------------------
Canadian files were originally space-separated and have been normalized
to underscore convention using gsak_rename.py:
  City_of_Winnipeg.txt  ->  City of Winnipeg, MB
  Saguenay-Lac_St_Jean.txt  ->  Saguenay-Lac St Jean, QC

Hyphens are preserved as they are part of the official region name.

Attribution and license
-----------------------
These polygon files were distributed with GSAK (Geocaching Swiss Army
Knife), written by Clyde Findlay. GSAK was released as freeware after
Clyde was no longer able to maintain it following a stroke. The polygon
data itself is a community effort — contributed and refined by GSAK users
over many years.

We are grateful to Clyde and to the GSAK community for making this
boundary data freely available to the geocaching and broader mapping
community.

  GSAK website : https://gsak.net
  License      : Freeware

Coverage
--------
  United States : all 50 states + DC (~3,382 counties/parishes/boroughs)
  Canada        : 11 provinces/territories (~239 regional divisions)
                  (Yukon and Northwest Territories not present in GSAK data)
  Quebec        : 17 administrative regions

Known limitations
-----------------
- Saskatchewan uses a coarse 7-region division rather than municipalities
- Nunavut has only 3 regions (Baffin, Keewatin, Kitikmeot)
- Yukon and Northwest Territories are absent from the GSAK polygon set
- Quebec directory was empty in early GSAK releases; 17 regions were
  added by the community

Rebuilding the database
-----------------------
If you add or update polygon files, rebuild the SQLite database with:

  python gsak_counties.py build --gsak-dir gsak --country US --verbose
  python gsak_counties.py build --gsak-dir gsak --country CA --verbose
  python gsak_counties.py stats

Then regenerate the GeoJSON for map rendering:

  python gsak_build_geojson.py --verbose
