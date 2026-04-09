@echo off
REM Launch the dashboard in a new window titled "AutoTradingDashboard".
REM Window stays open after exit so errors are visible.
setlocal
set REPO=%~dp0..
set LOG=%~dp0actions.txt
set TITLE=AutoTradingDashboard

tasklist /V /FI "WINDOWTITLE eq %TITLE%" 2>nul | find /I "%TITLE%" >nul
if not errorlevel 1 (
    echo [%date% %time%] start_dashboard: ABORT already running >> "%LOG%"
    echo Dashboard already running.
    pause
    exit /b 1
)

echo [%date% %time%] start_dashboard: launching python main.py dashboard >> "%LOG%"
start "%TITLE%" cmd /k "cd /d %REPO% && echo Dashboard started %date% %time% && python main.py dashboard & echo. & echo --- Dashboard exited. --- & pause"
echo [%date% %time%] start_dashboard: launched >> "%LOG%"
echo Dashboard launched. Open http://127.0.0.1:8050 in your browser.
endlocal
