from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_webview_binding_is_confined_to_presentation_adapter() -> None:
    source_root = ROOT / "src" / "embedded_copilot"
    forbidden = []
    for path in source_root.rglob("*.py"):
        if "native_shell" in path.parts or path.name == "v70_desktop_launcher.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "import webview" in text or "import clr" in text or "pythonnet" in text:
            forbidden.append(str(path.relative_to(ROOT)))
    assert forbidden == []


def test_native_host_exposes_no_business_bridge() -> None:
    text = (ROOT / "src" / "embedded_copilot" / "native_shell" / "host.py").read_text(encoding="utf-8")
    assert "NativeProjectRootBridge" in text
    picker = (ROOT / "src" / "embedded_copilot" / "native_shell" / "picker.py").read_text(encoding="utf-8")
    assert "def select_project_root" in picker
    assert "read_file" not in picker
    assert "write_file" not in picker
    assert "run_command" not in picker
    assert "PowerShell" not in picker
    assert "Workspace" not in text
    assert "Build" not in text
    assert "Flash" not in text
    assert "HIL" not in text


def test_native_bridge_has_exactly_one_public_capability() -> None:
    from embedded_copilot.native_shell import NativeProjectRootBridge, NativeProjectRootSelection, NativeProjectRootSelectionStatus

    class Picker:
        def select_project_root(self):
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.CANCELLED)

    bridge = NativeProjectRootBridge(Picker())
    assert [name for name in dir(bridge) if not name.startswith("_")] == ["select_project_root"]
