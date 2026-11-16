#Requires -RunAsAdministrator
<#
Developer install, without the Setup installer: copies dist\ThermalGuard\ (from
build.ps1) to C:\Program Files\ThermalGuard and registers the logon task.
Most people should use ThermalGuard-Setup-<version>.exe from the Releases page.
#>
param([string]$Source = (Join-Path $PSScriptRoot "dist\ThermalGuard"))

$installDir = "C:\Program Files\ThermalGuard"
$exe = Join-Path $installDir "ThermalGuard.exe"

if (-not (Test-Path (Join-Path $Source "ThermalGuard.exe"))) {
    Write-Host "Build not found in $Source - run build.ps1 first." -ForegroundColor Red
    return
}

schtasks /End /TN ThermalGuard 2>$null | Out-Null
Get-Process ThermalGuard -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process ThermalGuard -ErrorAction SilentlyContinue | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue

# Something (often antivirus) can hold the old exe open for a moment after it
# exits. Windows can't overwrite a locked exe but can rename it, so move it aside.
New-Item -ItemType Directory -Force $installDir | Out-Null
Get-ChildItem $installDir -Filter "*.old" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
if (Test-Path $exe) {
    Rename-Item $exe "ThermalGuard.$(Get-Date -Format yyyyMMddHHmmss).old" -ErrorAction Stop
}
Remove-Item (Join-Path $installDir "_internal") -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Source "*") $installDir -Recurse -Force -ErrorAction Stop
if ((Get-FileHash $exe).Hash -ne (Get-FileHash (Join-Path $Source "ThermalGuard.exe")).Hash) {
    Write-Host "Copy failed - the installed exe does not match the new build." -ForegroundColor Red
    return
}
Get-ChildItem $installDir -Filter "*.old" | Remove-Item -Force -ErrorAction SilentlyContinue

$p = Start-Process $exe -ArgumentList "--register-task" -Wait -PassThru
if ($p.ExitCode -ne 0) {
    Write-Host "Could not create the logon task - see $env:LOCALAPPDATA\ThermalGuard\thermalguard.log" -ForegroundColor Red
    return
}
schtasks /Run /TN ThermalGuard | Out-Null
Start-Sleep -Seconds 4
if (Get-Process ThermalGuard -ErrorAction SilentlyContinue) {
    Write-Host "ThermalGuard is running - look for the coloured circle in the system tray." -ForegroundColor Green
} else {
    Write-Host "Task created but ThermalGuard is not running yet. Check $env:LOCALAPPDATA\ThermalGuard\thermalguard.log" -ForegroundColor Yellow
}
Write-Host "Settings: $env:LOCALAPPDATA\ThermalGuard\config.json"
