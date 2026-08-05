@echo off
setlocal

set "PROJDIR=C:\Users\alazhar\OneDrive - Seven Seas Petroleum LLC\Work\Digital Transformation\Tender Scrapper Project"
set "LOG=%PROJDIR%\scraper.log"
:: Absolute path - Task Scheduler's launched environment has silently
:: differed from an interactive shell's PATH before (see 2026-08-05
:: incident: the script died with zero log output, root-caused to this
:: file having LF-only line endings, which breaks cmd.exe's parsing of
:: multi-line parenthesized if-blocks like the skip-check below).
set "PY=C:\Python314\python.exe"

cd /d "%PROJDIR%"
if errorlevel 1 (
    echo %DATE% %TIME%  [FATAL] Could not cd to project dir >> "%LOG%"
    exit /b 1
)

:: Log from the very first line - a run that dies before reaching its own
:: [START] entries should still leave a trace of how far it got.
echo. >> "%LOG%"
echo %DATE% %TIME%  [BOOT] run_scraper.bat started (cwd=%CD%) >> "%LOG%"

for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
echo %DATE% %TIME%  [BOOT] TODAY=%TODAY% >> "%LOG%"

:: Skip if already ran today
if exist last_run.txt (
    set /p LAST_RUN=<last_run.txt
    if "%LAST_RUN%"=="%TODAY%" (
        echo %TODAY%  [SKIP] Already ran today >> "%LOG%"
        exit /b 0
    )
)

:: Record run date before starting (prevents double-run if script crashes mid-way)
echo %TODAY%> last_run.txt
echo %TODAY%  [BOOT] last_run.txt written >> "%LOG%"

:: Belt-and-suspenders: force the fast, correctly-scoped POWL query even if
:: tender_scraper.py's own default ever changes back (see 2026-07-27 incident)
set SAP_POWL_QUERY=Published

echo %TODAY%  [START] PDO listings >> "%LOG%"
"%PY%" tender_scraper.py >> "%LOG%" 2>&1
echo %TODAY%  [DONE] PDO listings - exit code: %ERRORLEVEL% >> "%LOG%"

echo %TODAY%  [START] OQ Tawreed listings >> "%LOG%"
"%PY%" tawreed_scraper.py >> "%LOG%" 2>&1
echo %TODAY%  [DONE] OQ Tawreed listings - exit code: %ERRORLEVEL% >> "%LOG%"

echo %TODAY%  [START] PDO documents >> "%LOG%"
"%PY%" fetch_documents.py >> "%LOG%" 2>&1
echo %TODAY%  [DONE] PDO documents - exit code: %ERRORLEVEL% >> "%LOG%"

echo %TODAY%  [START] OQ Tawreed documents >> "%LOG%"
"%PY%" tawreed_fetch_documents.py >> "%LOG%" 2>&1
echo %TODAY%  [DONE] OQ Tawreed documents - exit code: %ERRORLEVEL% >> "%LOG%"

echo %TODAY%  [START] SOW extraction (offline) >> "%LOG%"
"%PY%" extract_sow.py >> "%LOG%" 2>&1
echo %TODAY%  [DONE] SOW extraction - exit code: %ERRORLEVEL% >> "%LOG%"

echo %TODAY%  [START] Market-intel classification >> "%LOG%"
"%PY%" classify_tenders.py >> "%LOG%" 2>&1
echo %TODAY%  [DONE] Market-intel classification - exit code: %ERRORLEVEL% >> "%LOG%"

echo %TODAY%  [BOOT] run_scraper.bat finished >> "%LOG%"
