from __future__ import annotations

from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot.artifact_intelligence import (
    ArtifactIntelligenceService,
)


def test_unknown_files_are_part_of_the_source_identity(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "hardware"
    source.mkdir(parents=True)
    (source / "board.eprj3").write_bytes(b"opaque index")
    unknown = source / "future.native"
    unknown.write_bytes(b"one")
    service = ArtifactIntelligenceService()

    first = service.scan_eda_import_directory(root=root, native_entry_relative_path="hardware/board.eprj3", backend="JLCEDA_PRO")
    unknown.write_bytes(b"two")
    second = service.scan_eda_import_directory(root=root, native_entry_relative_path="hardware/board.eprj3", backend="JLCEDA_PRO")

    assert first["source_set_fingerprint"] != second["source_set_fingerprint"]
    assert {item["relative_path"] for item in first["manifest"] if item["kind"] == "FILE"} == {
        "hardware/board.eprj3", "hardware/future.native"
    }


def test_kicad_missing_named_root_is_recorded_without_guessing(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "board.kicad_pro").write_bytes(b"{}")

    result = ArtifactIntelligenceService().scan_eda_import_directory(
        root=root, native_entry_relative_path="board.kicad_pro", backend="KICAD"
    )

    assert {item["relative_path"] for item in result["manifest"] if item["kind"] == "MISSING_DEPENDENCY"} == {
        "board.kicad_sch", "board.kicad_pcb"
    }


def test_source_change_between_scan_passes_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "board.eprj3"
    source.write_bytes(b"A")
    service = ArtifactIntelligenceService()
    original = service._eda_import_scan_once
    calls = 0

    def mutate(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            source.write_bytes(b"B")
        return result

    monkeypatch.setattr(service, "_eda_import_scan_once", mutate)
    with pytest.raises(ValueError, match="SOURCE_SET_CHANGED"):
        service.scan_eda_import_directory(root=root, native_entry_relative_path="board.eprj3", backend="JLCEDA_PRO")


def test_explicit_sheet_reference_missing_and_external_are_visible(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "demo.kicad_pro").write_bytes(b"{}")
    (root / "demo.kicad_pcb").write_bytes(b"(kicad_pcb (version 20990101))")
    (root / "demo.kicad_sch").write_bytes(
        b'(kicad_sch (version 20231120) '
        b'(sheet (property "Sheetfile" "missing.kicad_sch")) '
        b'(sheet (property "Sheetfile" "../outside.kicad_sch")))'
    )
    result = ArtifactIntelligenceService().scan_eda_import_directory(
        root=root, native_entry_relative_path="demo.kicad_pro", backend="KICAD"
    )
    markers = {(item["kind"], item["relative_path"]) for item in result["manifest"] if item["kind"] != "FILE"}
    assert ("MISSING_DEPENDENCY", "missing.kicad_sch") in markers
    assert any(kind == "EXTERNAL_DEPENDENCY" for kind, _ in markers)


@pytest.mark.parametrize("native_entry", ("../board.eprj3", "C:/board.eprj3", "native/C:board.eprj3", "native\\board.eprj3"))
def test_unsafe_native_entry_is_rejected_before_read(tmp_path: Path, native_entry: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(ValueError, match="IMPORT_INPUT_REJECTED"):
        ArtifactIntelligenceService().scan_eda_import_directory(
            root=root, native_entry_relative_path=native_entry, backend="JLCEDA_PRO"
        )
