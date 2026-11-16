#Requires -RunAsAdministrator
<#
Sets up LibreHardwareMonitor (free, open source) as ThermalGuard's temperature
source. Without a temperature reading ThermalGuard never goes above Balanced.

  1. Installs LibreHardwareMonitor to C:\Program Files\LibreHardwareMonitor
     with winget (Windows Package Manager).
  2. Pre-sets it to start minimised to the tray with its web server on, which
     ThermalGuard reads at http://127.0.0.1:8085/data.json.
  3. Starts it at every logon, elevated (it needs admin rights to read CPU
     sensors), via a Task Scheduler task for the current user.
#>

$installDir = "C:\Program Files\LibreHardwareMonitor"
$exe = Join-Path $installDir "LibreHardwareMonitor.exe"
$taskName = "LibreHardwareMonitor"
$user = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }

# 1. Install
if (-not (Test-Path $exe)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget (App Installer) is missing. Install LibreHardwareMonitor yourself from" -ForegroundColor Red
        Write-Host "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases into $installDir and run this again." -ForegroundColor Red
        return
    }
    winget install --id LibreHardwareMonitor.LibreHardwareMonitor --exact --source winget --location $installDir
    if (-not (Test-Path $exe)) {
        Write-Host "LibreHardwareMonitor did not install to $installDir - see the winget output above." -ForegroundColor Red
        return
    }
}

# 2. Settings: start minimised to tray, keep running when the window is closed,
#    web server on. Only written if LHM has no settings yet; otherwise set these
#    in its Options menu.
$settings = Join-Path $installDir "LibreHardwareMonitor.config"
if (-not (Test-Path $settings)) {
    @"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <appSettings>
    <add key="startMinMenuItem" value="true" />
    <add key="minTrayMenuItem" value="true" />
    <add key="minCloseMenuItem" value="true" />
    <add key="runWebServerMenuItem" value="true" />
    <add key="listenerPort" value="8085" />
  </appSettings>
</configuration>
"@ | Set-Content $settings -Encoding UTF8
}

# 3. Autostart task (same recipe as ThermalGuard's own: DOMAIN\user, elevated, no UAC prompt)
$esc = { param($s) [System.Security.SecurityElement]::Escape($s) }
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hardware sensors for ThermalGuard (web server on port 8085)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT10S</Delay>
      <UserId>$(& $esc $user)</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$(& $esc $user)</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$(& $esc $exe)</Command>
      <WorkingDirectory>$(& $esc $installDir)</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
$xmlPath = Join-Path $env:TEMP "LhmTask.xml"
[System.IO.File]::WriteAllText($xmlPath, $xml, [System.Text.Encoding]::Unicode)
schtasks /Create /TN $taskName /XML $xmlPath /F
Remove-Item $xmlPath -ErrorAction SilentlyContinue

schtasks /Run /TN $taskName | Out-Null
Write-Host "Waiting for LibreHardwareMonitor's web server..."
$client = New-Object System.Net.WebClient
$client.Proxy = $null  # localhost, never via a corporate proxy
$ok = $false
foreach ($i in 1..15) {
    Start-Sleep -Seconds 2
    try { $client.DownloadString("http://127.0.0.1:8085/data.json") | Out-Null; $ok = $true; break } catch {}
}
if ($ok) {
    Write-Host "LibreHardwareMonitor is running and serving sensors - ThermalGuard will pick it up within 30 s." -ForegroundColor Green
} else {
    Write-Host @"
LibreHardwareMonitor started, but its web server isn't answering yet. In its window (tray icon):
  Options > Remote Web Server > Run   (port 8085)
  Options > Start Minimized, Minimize To Tray, Minimize On Close
"@ -ForegroundColor Yellow
}
