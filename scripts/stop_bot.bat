@echo off
REM ================================================================
REM  stop_bot.bat — terminate the bot window launched by start_bot
REM ----------------------------------------------------------------
REM  - Matches on the unique window title "AutoTradingBot"
REM  - Uses taskkill /T to kill the cmd window AND the python.exe
REM    child process in one go
REM  - Logs the action to scripts\actions.txt
REM ================================================================

setlocal
set LOG=%~dp0actions.txt
set TITLE=AutoTradingBot

tasklist /V /FI "WINDOWTITLE eq %TITLE%" 2>nul | find /I "%TITLE%" >nul
if errorlevel 1 (
    echo [%date% %time%] stop_bot: no window "%TITLE%" found >> "%LOG%"
    echo No bot window found (title "%TITLE%").
    pause
    exit /b 0
)

echo [%date% %time%] stop_bot: killing window "%TITLE%" + child python >> "%LOG%"
taskkill /FI "WINDOWTITLE eq %TITLE%" /T /F
if errorlevel 1 (
    echo [%date% %time%] stop_bot: taskkill failed >> "%LOG%"
    echo taskkill failed — you may need to close the window manually.
    pause
    exit /b 1
)

echo [%date% %time%] stop_bot: stopped cleanly >> "%LOG%"
echo Bot stopped.
endlocal
