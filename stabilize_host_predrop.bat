@echo off
REM ===========================================================================
REM  stabilize_host_predrop.bat  --  RIGHT-CLICK > "Run as administrator"
REM ---------------------------------------------------------------------------
REM  One-shot host stabilizer for the daily silent hard-freezes (Kernel-Power
REM  41, evenings, 6x since 08-12). Root cause evidence (2026-08-20 audit):
REM  13900K on PRE-FIX microcode 0x11F (BIOS 1.90, Oct-2023 -- never got
REM  Intel's 0x129/0x12B degradation fix) + 4-DIMM XMP 5600 (above 4-stick
REM  spec). This bat is the SOFTWARE stopgap; the durable fix is a BIOS
REM  update + XMP down + MemTest86 + Intel RMA (see docs / session memory).
REM
REM  What it does (all reversible):
REM   1) Disables CPU turbo boost + caps processor at 99%% (AC) -- drops
REM      Vcore/heat and PSU load spikes; the standard stopgap for degraded
REM      Raptor Lake. Bot workload does not need turbo.
REM   2) Starts Windows Time + resyncs the clock (stuck on CMOS after the
REM      crash-reboots).
REM  Revert #1 any time with: restore_host_turbo.bat
REM ===========================================================================
net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Not elevated. Right-click this file and pick "Run as administrator".
  pause
  exit /b 1
)
echo [1/3] Disabling turbo boost + capping CPU at 99%% ...
powercfg /setacvalueindex scheme_current sub_processor PERFBOOSTMODE 0
powercfg /setacvalueindex scheme_current sub_processor PROCTHROTTLEMAX 99
powercfg /setactive scheme_current
echo [2/3] Verifying (both lines below should end in 0x00000000 and 0x00000063):
powercfg /query scheme_current sub_processor PERFBOOSTMODE   | findstr /C:"Current AC Power Setting"
powercfg /query scheme_current sub_processor PROCTHROTTLEMAX | findstr /C:"Current AC Power Setting"
echo [3/3] Clock resync ...
sc config w32time start= demand >nul
net start w32time >nul 2>&1
w32tm /resync
w32tm /query /status | findstr /C:"Source" /C:"Last Successful Sync"
echo.
echo DONE. CPU cap reverts via restore_host_turbo.bat (run after the drop era).
pause
