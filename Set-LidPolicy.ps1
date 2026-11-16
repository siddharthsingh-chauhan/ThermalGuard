#Requires -RunAsAdministrator
<#
Makes closing the lid and pressing the power button reliably put the laptop to
rest, in every power plan (so it still holds when the active plan changes).

Why: with lid close set to "Do nothing" (or Modern Standby that never reaches
deep sleep), a laptop in a bag stays on, heats up and drains its battery.

  -OnBattery        lid close on battery:   Sleep | Hibernate | Keep (default Hibernate)
  -PluggedIn        lid close plugged in:   Sleep | Hibernate | Nothing | Keep (default Keep)
                    "Nothing" keeps external monitors running with the lid shut.
  -PowerButton      power button, both:     Sleep | Hibernate | ShutDown | Keep (default Hibernate)
                    Holding the button for 4+ s still forces the laptop off.
  -HibernateAfterMin  on battery, sleep turns into hibernate after this many
                    minutes (only changed when given).

Example:
  .\Set-LidPolicy.ps1 -OnBattery Hibernate -PluggedIn Nothing -PowerButton Hibernate
#>
param(
    [ValidateSet("Sleep", "Hibernate", "Keep")][string]$OnBattery = "Hibernate",
    [ValidateSet("Sleep", "Hibernate", "Nothing", "Keep")][string]$PluggedIn = "Keep",
    [ValidateSet("Sleep", "Hibernate", "ShutDown", "Keep")][string]$PowerButton = "Hibernate",
    [ValidateRange(5, 240)][int]$HibernateAfterMin
)

$action = @{ Nothing = 0; Sleep = 1; Hibernate = 2; ShutDown = 3 }
$names = @{ 0 = "Do nothing"; 1 = "Sleep"; 2 = "Hibernate"; 3 = "Shut down"; 4 = "Turn off display" }
$plans = powercfg /list | ForEach-Object {
    if ($_ -match '([0-9a-f]{8}-[0-9a-f-]{27})\s+\((.*?)\)') { [pscustomobject]@{ Guid = $matches[1]; Name = $matches[2] } }
}

if ("Hibernate" -in @($OnBattery, $PluggedIn, $PowerButton)) { powercfg /hibernate on }
foreach ($p in $plans) {
    if ($OnBattery -ne "Keep") { powercfg /setdcvalueindex $p.Guid SUB_BUTTONS LIDACTION $action[$OnBattery] }
    if ($PluggedIn -ne "Keep") { powercfg /setacvalueindex $p.Guid SUB_BUTTONS LIDACTION $action[$PluggedIn] }
    if ($PowerButton -ne "Keep") {
        powercfg /setdcvalueindex $p.Guid SUB_BUTTONS PBUTTONACTION $action[$PowerButton]
        powercfg /setacvalueindex $p.Guid SUB_BUTTONS PBUTTONACTION $action[$PowerButton]
    }
    if ($PSBoundParameters.ContainsKey("HibernateAfterMin")) {
        powercfg /setdcvalueindex $p.Guid SUB_SLEEP HIBERNATEIDLE ($HibernateAfterMin * 60)
    }
}
powercfg /setactive SCHEME_CURRENT

foreach ($p in $plans) {
    foreach ($s in @(@("LIDACTION", "lid close"), @("PBUTTONACTION", "power button"))) {
        # /qh: the lid setting is hidden on some laptops, so plain /q omits it
        $q = powercfg /qh $p.Guid SUB_BUTTONS $s[0] | Select-String "Current (AC|DC)"
        if ($q.Count -lt 2) { continue }
        $ac = [Convert]::ToInt32(($q[0] -split ": ")[1], 16)
        $dc = [Convert]::ToInt32(($q[1] -split ": ")[1], 16)
        Write-Host ("{0,-16} {1,-12}: battery = {2}, plugged in = {3}" -f $p.Name, $s[1], $names[$dc], $names[$ac]) -ForegroundColor Green
    }
}
