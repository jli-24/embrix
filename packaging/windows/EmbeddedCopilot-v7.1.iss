; v7.1.0 per-user installer. v7.0 installer metadata remains in EmbeddedCopilot.iss.
#define ProductName "Embrix"
#define ProductVersion "7.1.0"
#define LauncherExe "EmbrixLauncher.exe"

[Setup]
AppId={{A7A0C1D1-8FA8-4C93-9138-000000007100}
AppName={#ProductName}
AppVersion={#ProductVersion}
DefaultDirName={localappdata}\Programs\Embedded Copilot
DefaultGroupName={#ProductName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UninstallDisplayName={#ProductName}
OutputBaseFilename=Embrix-{#ProductVersion}-Setup
SetupIconFile="..\..\frontend\public\brand\embrix-app-icon-white.ico"

[Files]
Source: "..\..\dist\Embrix-current\Embrix\*"; DestDir: "{app}\Embrix-current\Embrix"; Flags: recursesubdirs ignoreversion
Source: "..\..\dist\Embrix-current\verification.json"; DestDir: "{app}\Embrix-current"; Flags: ignoreversion
Source: "..\..\dist\EmbrixLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#ProductName}"; Filename: "{app}\{#LauncherExe}"

[Run]
Filename: "{app}\{#LauncherExe}"; Description: "Launch {#ProductName}"; Flags: nowait postinstall skipifsilent

; Mutable desktop state, user projects and credentials are intentionally retained.
