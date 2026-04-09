@echo off
REM Register Auto Trading Bot scheduled jobs in Windows Task Scheduler.
REM Times are BST (UTC+1) — London local time as of April 2026.
REM Run this file as Administrator.
REM
REM   Job             UTC    BST(local)
REM   DataFetch       06:00  07:00
REM   CodeReview      07:00  08:00
REM   Research+Email  08:00  09:00
REM   DeepFetch (Sun) 05:00  06:00

set XMLDIR=%~dp0task_xml

echo Registering tasks from XML...
echo.

schtasks /Create /F /TN "ATB_DataFetch"  /XML "%XMLDIR%\ATB_DataFetch.xml"
schtasks /Create /F /TN "ATB_CodeReview" /XML "%XMLDIR%\ATB_CodeReview.xml"
schtasks /Create /F /TN "ATB_Research"   /XML "%XMLDIR%\ATB_Research.xml"
schtasks /Create /F /TN "ATB_DeepFetch"  /XML "%XMLDIR%\ATB_DeepFetch.xml"

echo.
echo === Registered tasks ===
schtasks /Query /TN "ATB_DataFetch"  /FO LIST | findstr "TaskName Status Next"
schtasks /Query /TN "ATB_CodeReview" /FO LIST | findstr "TaskName Status Next"
schtasks /Query /TN "ATB_Research"   /FO LIST | findstr "TaskName Status Next"
schtasks /Query /TN "ATB_DeepFetch"  /FO LIST | findstr "TaskName Status Next"

echo.
echo Done. To remove all, run: scripts\unregister_scheduled_jobs.bat
