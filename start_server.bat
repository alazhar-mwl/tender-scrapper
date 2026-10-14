@echo off
REM Launches TenderIQ without popping a browser window — used by the
REM scheduled task so the server starts unattended after a reboot.
cd /d "%~dp0"
set TENDERIQ_OPEN_BROWSER=0

REM Scheduled Task Scheduler's launched environment has silently differed
REM from an interactive shell's PATH before (see run_scraper.bat) — resolve
REM Python by absolute path instead of trusting `python` to be on PATH.
set "PY=python"
if exist "C:\Users\appadmin\AppData\Local\Programs\Python\Python314\python.exe" set "PY=C:\Users\appadmin\AppData\Local\Programs\Python\Python314\python.exe"
if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"

"%PY%" server.py
