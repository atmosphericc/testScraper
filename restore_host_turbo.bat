@echo off
REM  Reverts stabilize_host_predrop.bat: turbo boost back to Aggressive
REM  (Windows default) and processor max back to 100%%.
REM  RIGHT-CLICK > "Run as administrator".
net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Not elevated. Right-click and "Run as administrator".
  pause
  exit /b 1
)
powercfg /setacvalueindex scheme_current sub_processor PERFBOOSTMODE 2
powercfg /setacvalueindex scheme_current sub_processor PROCTHROTTLEMAX 100
powercfg /setactive scheme_current
echo Turbo restored (Aggressive) + processor max 100%%.
pause
