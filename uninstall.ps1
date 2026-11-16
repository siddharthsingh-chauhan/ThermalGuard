#Requires -RunAsAdministrator
# For a copy installed with install.ps1. If you used the Setup installer, use
# Settings > Apps > ThermalGuard > Uninstall instead.
# Removes the logon task and the ThermalGuard power plan, switches back to
# Balanced. Keeps %LOCALAPPDATA%\ThermalGuard (settings + logs).
$installDir = "C:\Program Files\ThermalGuard"
$exe = Join-Path $installDir "ThermalGuard.exe"

if (Test-Path $exe) {
    Start-Process $exe -ArgumentList "--unregister" -Wait
}
Remove-Item $installDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "ThermalGuard removed; your previous power plan is active again." -ForegroundColor Green
