@echo off
REM ================================================================
REM  start_bot.bat — launch the trading bot in a new window
REM ----------------------------------------------------------------
REM  - Opens a new cmd window titled "AutoTradingBot" so stop_bot.bat
REM    can find it reliably via /FI "WINDOWTITLE eq ..."
REM  - Window stays open after the bot exits (cmd /k + pause)
REM  - Appends a timestamped line to scripts\actions.txt
REM ================================================================

setlocal
set REPO=%~dp0..
set LOG=%~dp0actions.txt
set TITLE=AutoTradingBot

REM --- Abort if a window with this title is already open
tasklist /V /FI "WINDOWTITLE eq %TITLE%" 2>nul | find /I "%TITLE%" >nul
if not errorlevel 1 (
    echo [%date% %time%] start_bot: ABORT window "%TITLE%" already running >> "%LOG%"
    echo Bot already running. Use stop_bot.bat first.
    pause
    exit /b 1
)

echo [%date% %time%] start_bot: launching python main.py run (cwd=%REPO%) >> "%LOG%"

REM --- Launch in a new window, keep it open after exit
start "%TITLE%" cmd /k "cd /d %REPO% && echo Trading Bot started %date% %time% && echo Window title: %TITLE% && echo. && python main.py run & echo. & echo --- Bot exited. Window kept open, close manually. --- & pause"

echo [%date% %time%] start_bot: launched window "%TITLE%" >> "%LOG%"
echo Bot launched in new window titled "%TITLE%".
echo Actions log: %LOG%
endlocal
