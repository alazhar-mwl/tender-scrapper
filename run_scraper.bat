@echo off
cd /d "C:\Users\alazhar\OneDrive - Seven Seas Petroleum LLC\Work\Digital Transformation\Tender Scrapper Project"

:: Get today's date in a locale-safe format via PowerShell
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d

:: Skip if already ran today
if exist last_run.txt (
    set /p LAST_RUN=<last_run.txt
    if "%LAST_RUN%"=="%TODAY%" (
        echo %TODAY%  [SKIP] Already ran today >> scraper.log
        exit /b 0
    )
)

:: Record run date before starting (prevents double-run if script crashes mid-way)
echo %TODAY%> last_run.txt

:: Belt-and-suspenders: force the fast, correctly-scoped POWL query even if
:: tender_scraper.py's own default ever changes back (see 2026-07-27 incident)
set SAP_POWL_QUERY=Published

echo %TODAY%  [START] PDO listings >> scraper.log
python tender_scraper.py >> scraper.log 2>&1
echo %TODAY%  [DONE] PDO listings — exit code: %ERRORLEVEL% >> scraper.log

echo %TODAY%  [START] OQ Tawreed listings >> scraper.log
python tawreed_scraper.py >> scraper.log 2>&1
echo %TODAY%  [DONE] OQ Tawreed listings — exit code: %ERRORLEVEL% >> scraper.log

echo %TODAY%  [START] PDO documents >> scraper.log
python fetch_documents.py >> scraper.log 2>&1
echo %TODAY%  [DONE] PDO documents — exit code: %ERRORLEVEL% >> scraper.log

echo %TODAY%  [START] OQ Tawreed documents >> scraper.log
python tawreed_fetch_documents.py >> scraper.log 2>&1
echo %TODAY%  [DONE] OQ Tawreed documents — exit code: %ERRORLEVEL% >> scraper.log

echo %TODAY%  [START] SOW extraction (offline) >> scraper.log
python extract_sow.py >> scraper.log 2>&1
echo %TODAY%  [DONE] SOW extraction — exit code: %ERRORLEVEL% >> scraper.log

echo %TODAY%  [START] Market-intel classification >> scraper.log
python classify_tenders.py >> scraper.log 2>&1
echo %TODAY%  [DONE] Market-intel classification — exit code: %ERRORLEVEL% >> scraper.log
