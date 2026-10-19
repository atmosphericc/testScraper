# Keep Windows Update from rebooting the bot host during the overnight drop window.
# 2026-10-14: a WU reboot at 02:30-02:33 killed the 10-14 window (CURRENT_STATE, POST-RUN
# 2026-10-14); a reboot while a won-cart place-order is on the wire leaves no AC-1 latch
# (FX-1014-RTO pre-arming verifier, risk 1). Needs admin (HKLM). Run elevated:
#   powershell -ExecutionPolicy Bypass -File tools\ops\wu_drop_guard.ps1 [-Days 7]
# Effect: pauses updates for -Days (max 35) and sets active hours 19:00-13:00 (the 18 h max),
# so automatic restarts can only land 13:00-19:00. Undo: Settings > Windows Update > Resume,
# and set active hours back (was 11-2). Writes logs\wu_drop_guard.log.
# 2026-10-18 (readiness audit, C-1018-03): the ACTIVE-HOURS half is NOT durable. Windows Update
# Orchestrator's automatic active-hours adjustment (MoUsoCoreWorker, auto-adjust on by default)
# rewrote 19-13 to 9-3 on 2026-10-16 01:22:59 with no policy or user action, and nothing on this
# host disables it. Only the PAUSE holds; re-run this script elevated (-Days N) before every drop
# week and treat the active-hours write as best-effort.
param([int]$Days = 7)
$ErrorActionPreference = 'Stop'
$log = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'logs\wu_drop_guard.log'
function Log($m) { $l = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m; Write-Output $l; Add-Content -Path $log -Value $l }
try {
    $k = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
    if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
    $before = Get-ItemProperty -Path $k -ErrorAction SilentlyContinue
    Log ("before: ActiveHours {0}-{1}, PauseUpdatesExpiryTime={2}" -f $before.ActiveHoursStart, $before.ActiveHoursEnd, $before.PauseUpdatesExpiryTime)
    $now = (Get-Date).ToUniversalTime()
    $until = $now.AddDays([Math]::Min([Math]::Max($Days, 1), 35))
    $fmt = 'yyyy-MM-ddTHH:mm:ssZ'
    Set-ItemProperty -Path $k -Name PauseUpdatesStartTime -Value $now.ToString($fmt)
    Set-ItemProperty -Path $k -Name PauseUpdatesExpiryTime -Value $until.ToString($fmt)
    Set-ItemProperty -Path $k -Name PauseFeatureUpdatesStartTime -Value $now.ToString($fmt)
    Set-ItemProperty -Path $k -Name PauseFeatureUpdatesEndTime -Value $until.ToString($fmt)
    Set-ItemProperty -Path $k -Name PauseQualityUpdatesStartTime -Value $now.ToString($fmt)
    Set-ItemProperty -Path $k -Name PauseQualityUpdatesEndTime -Value $until.ToString($fmt)
    Set-ItemProperty -Path $k -Name ActiveHoursStart -Value 19 -Type DWord
    Set-ItemProperty -Path $k -Name ActiveHoursEnd -Value 13 -Type DWord
    $after = Get-ItemProperty -Path $k
    Log ("after: ActiveHours {0}-{1}, updates paused until {2} UTC" -f $after.ActiveHoursStart, $after.ActiveHoursEnd, $after.PauseUpdatesExpiryTime)
} catch {
    Log ("FAILED: {0}" -f $_.Exception.Message)
    exit 1
}
