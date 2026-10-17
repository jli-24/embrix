from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_v71_installer_uses_fixed_embrix_layout_and_launcher() -> None:
    installer = (
        ROOT / "packaging" / "windows" / "EmbeddedCopilot-v7.1.iss"
    ).read_text(encoding="utf-8")

    assert r'DefaultDirName={localappdata}\Programs\Embedded Copilot' in installer
    assert (
        r'Source: "..\..\dist\Embrix-current\Embrix\*"; '
        r'DestDir: "{app}\Embrix-current\Embrix"; '
        "Flags: recursesubdirs ignoreversion"
    ) in installer
    assert (
        r'Source: "..\..\dist\Embrix-current\verification.json"; '
        r'DestDir: "{app}\Embrix-current"; Flags: ignoreversion'
    ) in installer
    assert (
        r'Source: "..\..\dist\EmbrixLauncher.exe"; '
        r'DestDir: "{app}"; Flags: ignoreversion'
    ) in installer
    assert r'Name: "{autoprograms}\{#ProductName}"; Filename: "{app}\{#LauncherExe}"' in installer
    assert r'Filename: "{app}\{#LauncherExe}"; Description: "Launch {#ProductName}"' in installer
    assert '#define LauncherExe "EmbrixLauncher.exe"' in installer
