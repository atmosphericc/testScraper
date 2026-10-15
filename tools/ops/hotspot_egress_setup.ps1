# FX-1014-EGRESS one-time host setup (run from an ADMIN PowerShell):
#   powershell -ExecutionPolicy Bypass -File tools\ops\hotspot_egress_setup.ps1 [-Alias "Wi-Fi"] [-Metric 100]
#   powershell -ExecutionPolicy Bypass -File tools\ops\hotspot_egress_setup.ps1 -Undo
#
# Why: Windows gives the Wi-Fi adapter the same automatic metric as the home Ethernet
# (25 on this host, 2026-10-14). Joining a phone hotspot on Wi-Fi could then move the
# DEFAULT route -- every account and the stock monitor -- onto the phone. A fixed,
# higher Wi-Fi metric keeps the home line as the default; only the account named in
# TARGET_ACCOUNT_EGRESS_BIND (whose sockets bind to the Wi-Fi address) uses the phone.
# The bot refuses the egress bind unless this holds (logs "[EGRESS] [WARN] ... stays on HOME").
#
# This script only sets the interface metric (persistent until -Undo) and prints the
# routes. It never edits the bat: arming the bind is a separate, verified step.
param(
    [string]$Alias = "Wi-Fi",
    [int]$Metric = 100,
    [switch]$Undo
)

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Run this from an ADMIN PowerShell (right-click PowerShell -> Run as administrator)." -ForegroundColor Red
    exit 1
}

if (-not (Get-NetIPInterface -InterfaceAlias $Alias -AddressFamily IPv4 -ErrorAction SilentlyContinue)) {
    Write-Host "No IPv4 interface named '$Alias'. Adapters:" -ForegroundColor Red
    Get-NetIPInterface -AddressFamily IPv4 | Select-Object InterfaceAlias, InterfaceMetric, ConnectionState | Format-Table -AutoSize
    exit 1
}

if ($Undo) {
    Set-NetIPInterface -InterfaceAlias $Alias -AddressFamily IPv4 -AutomaticMetric Enabled
    Write-Host "'$Alias' is back on the automatic metric." -ForegroundColor Yellow
} else {
    Set-NetIPInterface -InterfaceAlias $Alias -AddressFamily IPv4 -InterfaceMetric $Metric
    Write-Host "'$Alias' IPv4 interface metric set to $Metric (persistent; -Undo restores automatic)." -ForegroundColor Green
}

Write-Host ""
Write-Host "Default routes now (lower effective metric wins):"
$rows = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object {
    $i = Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4
    [pscustomobject]@{ Interface = $_.InterfaceAlias; NextHop = $_.NextHop; Effective = $_.RouteMetric + $i.InterfaceMetric }
}
$rows | Sort-Object Effective | Format-Table -AutoSize

if (-not $Undo) {
    $mine = $rows | Where-Object { $_.Interface -eq $Alias }
    $others = $rows | Where-Object { $_.Interface -ne $Alias }
    if (-not $mine) {
        Write-Host "'$Alias' has no default route yet -- connect it to the phone hotspot and re-run to check." -ForegroundColor Yellow
    } elseif (-not $others) {
        Write-Host "'$Alias' is the ONLY default route: the home line is down or unplugged." -ForegroundColor Red
    } elseif (($mine | Measure-Object Effective -Minimum).Minimum -le ($others | Measure-Object Effective -Minimum).Minimum) {
        Write-Host "'$Alias' still ranks with or ahead of the home route -- raise -Metric." -ForegroundColor Red
    } else {
        Write-Host "OK: the home line stays the default; only the bound account will use '$Alias'." -ForegroundColor Green
        Write-Host "The bot's boot log proves it: [EGRESS] W3/alt-1: public IP via ... = X; home = Y"
    }
}
