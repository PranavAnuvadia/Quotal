@echo off
REM Quotal download-page preview — double-click, test, Ctrl+C to stop.
cd /d "%~dp0"
echo Serving download-page at http://localhost:8080/  (Ctrl+C to stop)
start "" http://localhost:8080/
python -m http.server 8080
