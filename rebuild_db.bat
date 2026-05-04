@echo off
REM rebuild_db.bat -- Clean rebuild of gsak_counties.db
REM =======================================================
REM Architecture:
REM   US + Canada : GSAK polygon files (exact match for LoTW CNTY field)
REM   All others  : GADM 4.1 (coordinate-based lookup, ISO 2-letter codes)
REM
REM Prerequisites:
REM   gsak_counties.py  (updated -- list/delete subcommands)
REM   import_gadm.py    (updated -- ISO codes, --level support)
REM   Internet access   (for GADM downloads)
REM
REM Edit GSAK_DIR below to match your path, then run:
REM   rebuild_db.bat
REM
REM Estimated time: 5-15 min depending on internet speed (GADM downloads)
REM =======================================================

REM ---- CONFIGURE THIS ----
set GSAK_DIR=D:\dev\radio\gsak
set DB=gsak_counties.db
REM -------------------------

echo.
echo ============================================================
echo  Rebuilding %DB% from scratch
echo  GSAK source: %GSAK_DIR%
echo ============================================================

REM Step 0: Delete old DB
if exist %DB% (
    echo.
    echo Deleting old %DB% ...
    del %DB%
)

REM ------------------------------------------------------------------
REM GSAK section: US + Canada
REM (Must come from GSAK so county names match LoTW CNTY field values)
REM ------------------------------------------------------------------

echo.
echo [1/2 GSAK] US counties ...
python gsak_counties.py build --gsak-dir "%GSAK_DIR%" --db %DB% --country US
if errorlevel 1 goto :error

echo.
echo [2/2 GSAK] Canada provinces ...
python gsak_counties.py build --gsak-dir "%GSAK_DIR%" --db %DB% --country CA
if errorlevel 1 goto :error

REM ------------------------------------------------------------------
REM GADM section: all international countries, ISO 2-letter state_codes
REM Level 1 unless noted. Downloads ~10-50 MB per country.
REM ------------------------------------------------------------------

echo.
echo ---- GADM imports (requires internet) ----

REM -- Nordic / North Atlantic --
echo.
echo [GADM] Norway (NO, level 1 - 15 fylker) ...
python import_gadm.py --db %DB% NO
if errorlevel 1 goto :error

echo.
echo [GADM] Finland (FI, level 2 - 19 maakunta) ...
python import_gadm.py --db %DB% FI --level 2
if errorlevel 1 goto :error

echo.
echo [GADM] Iceland (IS, level 1 - 8 regions) ...
python import_gadm.py --db %DB% IS
if errorlevel 1 goto :error

echo.
echo [GADM] Sweden (SE, level 1 - 21 lan) ...
python import_gadm.py --db %DB% SE
if errorlevel 1 goto :error

echo.
echo [GADM] Denmark (DK, level 1 - 5 regions) ...
python import_gadm.py --db %DB% DK
if errorlevel 1 goto :error

REM -- Western Europe --
echo.
echo [GADM] Germany (DE, level 1 - 16 Bundeslander) ...
python import_gadm.py --db %DB% DE
if errorlevel 1 goto :error

echo.
echo [GADM] France (FR, level 1 - 18 regions) ...
python import_gadm.py --db %DB% FR
if errorlevel 1 goto :error

echo.
echo [GADM] United Kingdom (GB, level 1 - 4 countries) ...
python import_gadm.py --db %DB% GB
if errorlevel 1 goto :error

echo.
echo [GADM] Netherlands (NL, level 1 - 12 provinces) ...
python import_gadm.py --db %DB% NL
if errorlevel 1 goto :error

echo.
echo [GADM] Belgium (BE, level 1 - 3 regions) ...
python import_gadm.py --db %DB% BE
if errorlevel 1 goto :error

echo.
echo [GADM] Switzerland (CH, level 1 - 26 cantons) ...
python import_gadm.py --db %DB% CH
if errorlevel 1 goto :error

echo.
echo [GADM] Austria (AT, level 1 - 9 Bundeslander) ...
python import_gadm.py --db %DB% AT
if errorlevel 1 goto :error

REM -- Southern Europe --
echo.
echo [GADM] Italy (IT, level 1 - 20 regions) ...
python import_gadm.py --db %DB% IT
if errorlevel 1 goto :error

echo.
echo [GADM] Spain (ES, level 1 - 17 communities) ...
python import_gadm.py --db %DB% ES
if errorlevel 1 goto :error

REM -- Central / Eastern Europe --
echo.
echo [GADM] Czechia (CZ, level 1 - 14 kraje) ...
python import_gadm.py --db %DB% CZ
if errorlevel 1 goto :error

echo.
echo [GADM] Poland (PL, level 1 - 16 voivodeships) ...
python import_gadm.py --db %DB% PL
if errorlevel 1 goto :error

echo.
echo [GADM] Ukraine (UA, level 1 - 27 oblasts) ...
python import_gadm.py --db %DB% UA
if errorlevel 1 goto :error

REM -- Asia-Pacific --
echo.
echo [GADM] India (VU, level 1 - 36 states/UTs) ...
python import_gadm.py --db %DB% IN
if errorlevel 1 goto :error

REM -- Asia-Pacific --
echo.
echo [GADM] China (VU, level 1 - 36 states/UTs) ...
python import_gadm.py --db %DB% CN
if errorlevel 1 goto :error

echo.
echo [GADM] Indonesia (YB, level 1 - 34 provinces) ...
python import_gadm.py --db %DB% ID
if errorlevel 1 goto :error
echo.
echo [GADM] Japan (JP, level 1 - 47 prefectures) ...
python import_gadm.py --db %DB% JP
if errorlevel 1 goto :error

echo.
echo [GADM] South Korea (KR, level 1 - 17 provinces) ...
python import_gadm.py --db %DB% KR
if errorlevel 1 goto :error

echo.
echo [GADM] Australia (AU, level 1 - 8 states/territories) ...
python import_gadm.py --db %DB% AU
if errorlevel 1 goto :error

echo.
echo [GADM] New Zealand (NZ, level 1 - 16 regions) ...
python import_gadm.py --db %DB% NZ
if errorlevel 1 goto :error

REM -- Americas --
echo.
echo [GADM] Argentina (AR, level 1 - 24 provinces) ...
python import_gadm.py --db %DB% AR
if errorlevel 1 goto :error

REM Russia is large (~100 MB, 83 subjects) -- comment out if not needed
echo.
echo [GADM] Russia (RU, level 1 - 83 subjects, large download) ...
python import_gadm.py --db %DB% RU
if errorlevel 1 goto :error

REM ------------------------------------------------------------------
echo.
echo ============================================================
echo  Rebuild complete. Final DB contents:
echo ============================================================
python gsak_counties.py stats --db %DB%

echo.
echo Spot checks:
python gsak_counties.py list --db %DB% --state-code NO
echo.
python gsak_counties.py list --db %DB% --state-code FI

goto :eof

:error
echo.
echo ERROR: Step failed. Re-run rebuild_db.bat to start fresh.
exit /b 1
