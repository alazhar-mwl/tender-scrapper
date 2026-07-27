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

echo %TODAY%  [START] Running tender scraper >> scraper.log
python tender_scraper.py >> scraper.log 2>&1
echo %TODAY%  [DONE] Exit code: %ERRORLEVEL% >> scraper.log
