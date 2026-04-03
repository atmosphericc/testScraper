@echo off
echo Adding Windows Firewall rules for Patchright Chromium...
echo.

set CHROME=C:\Users\elric\AppData\Local\ms-playwright\chromium-1200\chrome-win64\chrome.exe

if not exist "%CHROME%" (
    echo ERROR: Chromium not found at %CHROME%
    echo Run: python -m patchright install chromium
    pause
    exit /b 1
)

echo Removing any existing block rules...
netsh advfirewall firewall delete rule name="Patchright Chromium" program="%CHROME%" 2>nul
netsh advfirewall firewall delete rule name="Patchright Chromium In" program="%CHROME%" 2>nul
netsh advfirewall firewall delete rule name="Patchright Chromium Out" program="%CHROME%" 2>nul

echo Adding allow rules...
netsh advfirewall firewall add rule name="Patchright Chromium In" dir=in action=allow program="%CHROME%" enable=yes profile=any
netsh advfirewall firewall add rule name="Patchright Chromium Out" dir=out action=allow program="%CHROME%" enable=yes profile=any

echo.
echo Done. Verifying...
netsh advfirewall firewall show rule name="Patchright Chromium In"
echo.
pause
