@echo off
REM FX-1118 px-recovery-watch launcher -- daily hands-off PX-flag decay check.
REM Runs the money-safe guest probe (6 PDP reads, no login/cart/checkout) and lifts the
REM dark-hold once the home line reads clean. See tools/ops/px_recovery_watch.py header.
cd /d C:\Users\elric\Desktop\testScraper
venv\Scripts\python.exe tools\ops\px_recovery_watch.py
