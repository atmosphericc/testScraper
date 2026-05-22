@echo off
REM ===========================================================================
REM  install_autostart.bat  --  one-time setup
REM ---------------------------------------------------------------------------
REM  Adds the Target bot wrapper to this user's Startup folder so it relaunches
REM  automatically after a reboot / logon.
REM
REM  Why: on 2026-05-22 the machine was rebooted ~8 AM (a DisplayPort issue).
REM  The reboot killed the bot, the crash-restart wrapper, and every Chrome.
REM  The wrapper only survives app.py crashes — it cannot survive an OS reboot
REM  because nothing relaunches the wrapper itself. This shortcut closes that
REM  gap: after any reboot, logging in relaunches the wrapper.
REM
REM  Run this once (double-click). Undo: press Win+R, type  shell:startup  ,
REM  and delete TargetBot.lnk.
REM ===========================================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $l=$w.CreateShortcut([Environment]::GetFolderPath('Startup')+'\TargetBot.lnk'); $l.TargetPath='%~dp0run_bot_with_nightly_restart.bat'; $l.WorkingDirectory='%~dp0'; $l.Description='Target bot - overnight crash-resilient wrapper'; $l.Save(); Write-Host ('Startup shortcut created: '+[Environment]::GetFolderPath('Startup')+'\TargetBot.lnk')"

echo.
echo Done -- the bot wrapper will now auto-launch when you log in.
echo To undo: press Win+R, type  shell:startup  , delete TargetBot.lnk
pause
