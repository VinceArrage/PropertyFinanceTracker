@echo off
rem Double-click to start Property Tracker and open it in your browser.
cd /d "%~dp0"
".venv\Scripts\proptrack.exe" serve --open
pause
