"""Offline tests for the explicitly configured KiCad XML exporter."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.schematic_xml import KiCadCliSchematicXmlExporter

INPUT = b"(kicad_sch (version 20250114))"
XML = b"<export><components/><libparts/><nets/></export>"


def _exporter(tmp_path: Path, runner, *, timeout_seconds: float = 17.0):
    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted executable")
    return KiCadCliSchematicXmlExporter(
        executable=executable,
        tool_version="10.0.0",
        runner=runner,
        timeout_seconds=timeout_seconds,
    ), executable


def test_export_uses_fixed_command_isolated_workspace_and_input_bytes(tmp_path: Path) -> None:
    observed = {}

    def runner(argv, *, cwd, timeout_seconds, environment):
        root = Path(cwd)
        observed["argv"] = argv
        observed["timeout"] = timeout_seconds
        observed["environment"] = environment
        assert (root / "hardware/main.kicad_sch").read_bytes() == INPUT
        assert Path(environment["KICAD_CONFIG_HOME"]).is_relative_to(root)
        assert Path(environment["KICAD_CACHE_HOME"]).is_relative_to(root)
        for key in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "USERPROFILE"):
            assert Path(environment[key]).is_relative_to(root)
            assert Path(environment[key]).is_dir()
        assert environment["HOMEDRIVE"] == root.drive or environment["HOMEDRIVE"] == "/"
        assert environment["HOMEPATH"] == str(root / "profile")[len(root.drive):]
        output = Path(argv[argv.index("--output") + 1])
        output.write_bytes(XML)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    exporter, executable = _exporter(tmp_path, runner)
    assert exporter.executable_sha256 == "sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest()
    assert exporter.tool_version == "10.0.0"
    assert exporter.export_xml(_operation(), INPUT) == (0, XML)
    assert observed["argv"][:4] == (str(executable), "sch", "export", "netlist")
    assert observed["argv"][4:6] == ("--format", "kicadxml")
    assert observed["timeout"] == 17.0


def test_export_rejects_tool_drift_before_running(tmp_path: Path) -> None:
    exporter, executable = _exporter(tmp_path, lambda *args, **kwargs: pytest.fail("runner called"))
    executable.write_bytes(b"changed")
    with pytest.raises(ValueError, match="executable drift"):
        exporter.export_xml(_operation(), INPUT)


def test_export_rejects_tool_drift_during_running(tmp_path: Path) -> None:
    executable = tmp_path / "kicad-cli.exe"

    def runner(argv, *, cwd, timeout_seconds, environment):
        Path(argv[argv.index("--output") + 1]).write_bytes(XML)
        executable.write_bytes(b"changed")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    exporter, _ = _exporter(tmp_path, runner)
    with pytest.raises(ValueError, match="executable drift"):
        exporter.export_xml(_operation(), INPUT)


def test_export_rejects_input_fingerprint_mismatch_before_running(tmp_path: Path) -> None:
    exporter, _ = _exporter(tmp_path, lambda *args, **kwargs: pytest.fail("runner called"))
    with pytest.raises(ValueError, match="input artifact mismatch"):
        exporter.export_xml(_operation(), b"changed")


@pytest.mark.parametrize("failure", ["missing", "oversize", "side_effect", "input_mutation"])
def test_export_rejects_invalid_output_or_side_effects(tmp_path: Path, failure: str) -> None:
    def runner(argv, *, cwd, timeout_seconds, environment):
        root = Path(cwd)
        output = Path(argv[argv.index("--output") + 1])
        if failure == "oversize":
            output.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        elif failure != "missing":
            output.write_bytes(XML)
        if failure == "side_effect":
            (root / "unexpected.kicad_prl").write_bytes(b"unexpected")
        if failure == "input_mutation":
            (root / "hardware/main.kicad_sch").write_bytes(b"changed")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    exporter, _ = _exporter(tmp_path, runner)
    with pytest.raises(ValueError):
        exporter.export_xml(_operation(), INPUT)


def test_export_propagates_timeout_without_retry(tmp_path: Path) -> None:
    calls = 0

    def runner(argv, *, cwd, timeout_seconds, environment):
        nonlocal calls
        calls += 1
        raise TimeoutError("KiCad CLI timed out")

    exporter, _ = _exporter(tmp_path, runner)
    with pytest.raises(TimeoutError):
        exporter.export_xml(_operation(), INPUT)
    assert calls == 1


class _Operation:
    artifact_fingerprint = "sha256:" + hashlib.sha256(INPUT).hexdigest()


def _operation():
    return _Operation()


def test_default_runner_overrides_host_user_and_temp_paths_without_shell(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted executable")
    for key in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "USERPROFILE"):
        monkeypatch.setenv(key, str(tmp_path / "host"))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        root = Path(kwargs["cwd"])
        for key in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "USERPROFILE"):
            assert Path(kwargs["env"][key]).is_relative_to(root)
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 120.0
        Path(argv[argv.index("--output") + 1]).write_bytes(XML)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    exporter = KiCadCliSchematicXmlExporter(executable=executable, tool_version="10.0.5")
    assert exporter.export_xml(_operation(), INPUT) == (0, XML)
    assert len(calls) == 1
