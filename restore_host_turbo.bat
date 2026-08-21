@echo off
REM  Reverts stabilize_host_predrop.bat: turbo boost back to Aggressive
REM  (Windows default, boost mode = 2) and max processor state back to 100%%
REM  on all three core classes.  RIGHT-CLICK > "Run as administrator".
setlocal
set "SUBP=54533251-82be-4824-96c1-47b60b740d00"
set "BOOST=be337238-0d82-4146-a960-4f3749d470c7"
set "MAX0=bc5038f7-23e0-4960-96da-33abaf5935ec"
set "MAX1=bc5038f7-23e0-4960-96da-33abaf5935ed"
set "MAX2=bc5038f7-23e0-4960-96da-33abaf5935ee"
net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Not elevated. Right-click and "Run as administrator".
  pause
  exit /b 1
)
powercfg /setacvalueindex scheme_current %SUBP% %BOOST% 2
powercfg /setacvalueindex scheme_current %SUBP% %MAX0% 100
powercfg /setacvalueindex scheme_current %SUBP% %MAX1% 100 >nul 2>&1
powercfg /setacvalueindex scheme_current %SUBP% %MAX2% 100 >nul 2>&1
powercfg /setactive scheme_current
echo Turbo restored (Aggressive) + max processor state 100%% on all classes.
pause
