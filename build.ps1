# Builds dist\ThermalGuard\ (the app) and, if Inno Setup 6 is installed,
# dist\ThermalGuard-Setup-<version>.exe (the installer).
#   winget install JRSoftware.InnoSetup     # one-time, for the installer
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check --quiet -r requirements-dev.txt

.\.venv\Scripts\python.exe -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { Write-Host "Tests failed, not building." -ForegroundColor Red; exit 1 }

.\.venv\Scripts\pyinstaller.exe --noconfirm --clean ThermalGuard.spec
if ($LASTEXITCODE -ne 0) { Write-Host "PyInstaller failed." -ForegroundColor Red; exit 1 }

$version = (Select-String -Path version.py -Pattern '"(.+)"').Matches[0].Groups[1].Value
$iscc = @(
    (Get-Command iscc -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if ($iscc) {
    & $iscc /Qp "/DAppVersion=$version" installer\ThermalGuard.iss
    if ($LASTEXITCODE -ne 0) { Write-Host "Inno Setup failed." -ForegroundColor Red; exit 1 }
    Write-Host "Installer: dist\ThermalGuard-Setup-$version.exe" -ForegroundColor Green
} else {
    Write-Host "Built dist\ThermalGuard\. Inno Setup 6 not found, so no installer (winget install JRSoftware.InnoSetup)." -ForegroundColor Yellow
}
