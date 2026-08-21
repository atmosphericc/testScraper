@echo off
REM ===========================================================================
REM  stabilize_host_predrop.bat  --  RIGHT-CLICK > "Run as administrator"
REM ---------------------------------------------------------------------------
REM  One-shot host stabilizer for the daily silent hard-freezes (Kernel-Power
REM  41, six evenings since 08-12). Root cause evidence (2026-08-20 audit):
REM  13900K on PRE-FIX microcode 0x11F (BIOS 1.90, Oct-2023 -- never got
REM  Intel's 0x129/0x12B degradation fix) + 4-DIMM XMP 5600 (above 4-stick
REM  spec). This bat is the SOFTWARE stopgap; the durable fix is a BIOS
REM  update + XMP down + MemTest86 + Intel RMA.
REM
REM  What it does (volatile Windows power settings only -- NO BIOS/hardware
REM  writes, fully reversible via restore_host_turbo.bat or Windows defaults):
REM   1) Turbo boost OFF (global "boost mode" = Disabled) -- drops Vcore/heat
REM      and PSU load spikes; the standard degraded-Raptor-Lake band-aid.
REM   2) Max processor state 99%% on ALL THREE core classes (E-cores,
REM      P-cores, favored cores -- hybrid CPUs keep one setting per class;
REM      raw GUIDs used so it works regardless of alias availability).
REM   3) Starts Windows Time + resyncs the clock (stuck on CMOS after the
REM      crash-reboots).
REM  The bot does not need turbo: its load is network-bound; sweeps ran at
REM  full rate with huge headroom on far weaker settings.
REM ===========================================================================
setlocal
set "SUBP=54533251-82be-4824-96c1-47b60b740d00"
set "BOOST=be337238-0d82-4146-a960-4f3749d470c7"
set "MAX0=bc5038f7-23e0-4960-96da-33abaf5935ec"
set "MAX1=bc5038f7-23e0-4960-96da-33abaf5935ed"
set "MAX2=bc5038f7-23e0-4960-96da-33abaf5935ee"
net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Not elevated. Right-click this file and pick "Run as administrator".
  pause
  exit /b 1
)
echo [1/3] Turbo boost OFF + max state 99%% on all core classes ...
powercfg /setacvalueindex scheme_current %SUBP% %BOOST% 0
powercfg /setacvalueindex scheme_current %SUBP% %MAX0% 99
powercfg /setacvalueindex scheme_current %SUBP% %MAX1% 99 >nul 2>&1
powercfg /setacvalueindex scheme_current %SUBP% %MAX2% 99 >nul 2>&1
powercfg /setactive scheme_current
echo [2/3] Verifying (want boost AC=0x00000000, max-state AC=0x00000063):
powercfg /query scheme_current %SUBP% %BOOST% | findstr /C:"Current AC Power Setting"
powercfg /query scheme_current %SUBP% %MAX0%  | findstr /C:"Current AC Power Setting"
powercfg /query scheme_current %SUBP% %MAX1%  2>nul | findstr /C:"Current AC Power Setting"
powercfg /query scheme_current %SUBP% %MAX2%  2>nul | findstr /C:"Current AC Power Setting"
echo [3/3] Clock resync ...
sc config w32time start= demand >nul
net start w32time >nul 2>&1
w32tm /resync
w32tm /query /status | findstr /C:"Source" /C:"Last Successful Sync"
echo.
echo DONE. Revert the CPU settings any time with restore_host_turbo.bat
pause
