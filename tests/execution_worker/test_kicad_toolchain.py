from __future__ import annotations

from pathlib import Path

from embedded_copilot.execution_worker import discover_kicad_cli


def test_discovery_uses_a_trusted_known_install_root_when_path_is_not_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "kicad" / "bin" / "kicad-cli.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"trusted-test-executable")
    monkeypatch.delenv("EMBRIX_KICAD_CLI", raising=False)
    monkeypatch.setattr("embedded_copilot.execution_worker.kicad_toolchain.shutil.which", lambda _: None)

    discovered = discover_kicad_cli(known_install_roots=(executable.parent.parent,))

    assert discovered == executable.resolve()

