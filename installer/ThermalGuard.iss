; Inno Setup 6 script. Build with build.ps1 (runs the tests and PyInstaller first):
;   iscc /DAppVersion=1.0.0 installer\ThermalGuard.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define RepoUrl "https://github.com/siddharthsingh-chauhan/ThermalGuard"

[Setup]
; Never change AppId: upgrades and uninstall find the existing install by it.
AppId={{B7E3C1D2-5A4F-4E8B-9C21-7F3D2A6E9B14}
AppName=ThermalGuard
AppVersion={#AppVersion}
AppVerName=ThermalGuard {#AppVersion}
AppPublisher=ThermalGuard contributors
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
DefaultDirName={autopf}\ThermalGuard
DisableProgramGroupPage=yes
; The logon task runs elevated (power settings need it), so setup must be too.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=ThermalGuard-Setup-{#AppVersion}
SetupIconFile=..\assets\ThermalGuard.ico
UninstallDisplayIcon={app}\ThermalGuard.exe
UninstallDisplayName=ThermalGuard
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Setup stops ThermalGuard itself in PrepareToInstall (it runs elevated from a
; scheduled task, which Restart Manager can't always close).
CloseApplications=no

[Tasks]
Name: "lhm"; Description: "Install LibreHardwareMonitor for CPU temperature readings (downloaded with winget; without it ThermalGuard stays at Balanced at most)"; Flags: unchecked
Name: "lidpolicy"; Description: "Hibernate when the lid is closed on battery or the power button is pressed (all power plans)"; Flags: unchecked

[Files]
Source: "..\dist\ThermalGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\Set-LidPolicy.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\Setup-LibreHardwareMonitor.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Left behind by older one-file builds and the developer install script
Type: files; Name: "{app}\*.old"

[Icons]
; Start through the logon task so it runs elevated without a UAC prompt
Name: "{autoprograms}\ThermalGuard"; Filename: "{sys}\schtasks.exe"; Parameters: "/Run /TN ThermalGuard"; IconFilename: "{app}\ThermalGuard.exe"; Comment: "Start ThermalGuard"

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\Set-LidPolicy.ps1"" -OnBattery Hibernate -PowerButton Hibernate"; StatusMsg: "Setting lid and power button to hibernate..."; Tasks: lidpolicy; Flags: runhidden waituntilterminated
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\Setup-LibreHardwareMonitor.ps1"""; StatusMsg: "Installing LibreHardwareMonitor..."; Tasks: lhm; Flags: waituntilterminated
Filename: "{sys}\schtasks.exe"; Parameters: "/Run /TN ThermalGuard"; Description: "Start ThermalGuard now"; Flags: postinstall runhidden nowait

[UninstallRun]
Filename: "{app}\ThermalGuard.exe"; Parameters: "--unregister"; RunOnceId: "Unregister"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: files; Name: "{app}\*.old"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN ThermalGuard', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM ThermalGuard.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1500);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not Exec(ExpandConstant('{app}\ThermalGuard.exe'), '--register-task', ExpandConstant('{app}'),
                SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      MsgBox('ThermalGuard was installed, but the task that starts it at logon could not be created.' + #13#10 +
             'Details are in %LOCALAPPDATA%\ThermalGuard\thermalguard.log.', mbError, MB_OK);
  end;
end;
