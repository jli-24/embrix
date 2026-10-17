; v7.0.0 per-user installer. Application files and mutable user state are separate.
#define ProductName "Embedded Copilot"
#define ProductVersion "7.0.0"
#define ProductExe "EmbeddedCopilot.exe"

[Setup]
AppId={{A7A0C1D1-8FA8-4C93-9138-000000007000}
AppName={#ProductName}
AppVersion={#ProductVersion}
DefaultDirName={localappdata}\Programs\Embedded Copilot
DefaultGroupName={#ProductName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UninstallDisplayName={#ProductName}
OutputBaseFilename=EmbeddedCopilot-{#ProductVersion}-Setup

[Files]
Source: "..\..\dist\EmbeddedCopilot-7.0.0\EmbeddedCopilot\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#ProductName}"; Filename: "{app}\{#ProductExe}"

[Run]
Filename: "{app}\{#ProductExe}"; Description: "Launch {#ProductName}"; Flags: nowait postinstall skipifsilent

; Uninstall deliberately does not remove %LOCALAPPDATA%\Embedded Copilot,
; user projects, backups, or Credential Manager entries.
