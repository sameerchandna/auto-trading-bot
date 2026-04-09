@echo off
REM Remove all Auto Trading Bot scheduled jobs from Windows Task Scheduler.
REM Run this file as Administrator.

echo Removing ATB_DataFetch...
schtasks /Delete /TN "ATB_DataFetch" /F 2>nul

echo Removing ATB_CodeReview...
schtasks /Delete /TN "ATB_CodeReview" /F 2>nul

echo Removing ATB_Research...
schtasks /Delete /TN "ATB_Research" /F 2>nul

echo Removing ATB_DeepFetch...
schtasks /Delete /TN "ATB_DeepFetch" /F 2>nul

echo.
echo All ATB tasks removed.
