@echo off
REM Launches TenderIQ without popping a browser window — used by the
REM scheduled task so the server starts unattended after a reboot.
cd /d "%~dp0"
set TENDERIQ_OPEN_BROWSER=0
python server.py
