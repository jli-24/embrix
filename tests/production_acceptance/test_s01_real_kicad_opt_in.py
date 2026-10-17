"""The S01 real ERC golden must never discover a tool by default."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.production_acceptance import prepare_s01_native_review as native_prep
from tests.production_acceptance import test_real_schematic_production_golden as golden
from tests.production_acceptance import test_s01_real_circuit_owner as circuit


def test_real_erc_skips_before_tool_discovery_without_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBRIX_RUN_S01_REAL_KICAD", raising=False)

    def forbidden_discovery() -> None:
        raise AssertionError("real KiCad discovery occurred without opt-in")

    monkeypatch.setattr(golden, "_kicad_cli", forbidden_discovery)
    with pytest.raises(pytest.skip.Exception, match="opt-in"):
        golden.test_real_schematic_production_golden(tmp_path)


def test_real_circuit_chain_skips_before_setup_or_tool_probe_without_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBRIX_RUN_S01_REAL_KICAD", raising=False)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("real Circuit setup or KiCad probe occurred without opt-in")

    monkeypatch.setattr(circuit, "_runtime_with_approved_plan", forbidden)
    monkeypatch.setattr(circuit, "probe_kicad_cli_version", forbidden)
    with pytest.raises(pytest.skip.Exception, match="EMBRIX_RUN_S01_REAL_KICAD"):
        circuit.test_s01_real_erc_circuit_review_owner_and_restart(tmp_path)


def test_native_preparation_rejects_before_tool_discovery_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBRIX_RUN_S01_REAL_KICAD", raising=False)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Native preparation discovered or called KiCad without opt-in")

    monkeypatch.setattr(native_prep.shutil, "which", forbidden)
    monkeypatch.setattr(native_prep, "probe_kicad_cli_version", forbidden)
    with pytest.raises(RuntimeError, match="explicit opt-in"):
        native_prep.main()


def test_real_erc_reaches_discovery_only_with_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBRIX_RUN_S01_REAL_KICAD", "1")

    def observed_discovery() -> None:
        raise RuntimeError("discovery reached after opt-in")

    monkeypatch.setattr(golden, "_kicad_cli", observed_discovery)
    with pytest.raises(RuntimeError, match="discovery reached after opt-in"):
        golden.test_real_schematic_production_golden(tmp_path)


def test_real_erc_rejects_other_executable_before_version_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBRIX_RUN_S01_REAL_KICAD", "1")
    monkeypatch.setattr(golden, "_kicad_cli", lambda: Path("C:/other/kicad-cli.exe"))

    def forbidden_probe(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("version probe occurred for an unauthorized executable")

    monkeypatch.setattr(golden, "probe_kicad_cli_version", forbidden_probe)
    with pytest.raises(pytest.fail.Exception, match="outside the authorized path"):
        golden.test_real_schematic_production_golden(tmp_path)
