from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.project_catalog import (
    ProjectCatalogForbidden,
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, 8, 0, tzinfo=UTC)


def _project(tmp_path: Path) -> tuple[ProjectCatalogRuntime, str, Path]:
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    service = ProjectCatalogRuntime(
        project_root=root, state_database=tmp_path / "catalog.sqlite3", clock=Clock(), initialize_async=False
    )
    preview = service.preview(
        ProjectCreationRequest(name="Board", relative_path="board", project_type=ProjectType.STM32),
        tenant_id="tenant-1", requester="owner-1",
    )
    project = service.create(
        preview.preview_id, preview.fingerprint, confirmed_at=Clock().now(), tenant_id="tenant-1", requester="owner-1"
    )
    source = root / "board" / "native"
    source.mkdir()
    (source / "board.eprj3").write_bytes(b"opaque v1")
    return service, project.project_id, source


def test_preview_commit_restart_and_source_currentness(tmp_path: Path) -> None:
    service, project_id, source = _project(tmp_path)
    preview = service.create_eda_import_preview(
        project_id, tenant_id="tenant-1", requester="owner-1", backend="JLCEDA_PRO",
        native_entry_relative_path="native/board.eprj3", selected_native_entries=(),
        expected_head_revision_id=None, expected_head_revision_fingerprint=None,
    )
    assert preview["semantic_completeness"] == "UNKNOWN"
    assert preview["verified"] is False and preview["can_modify"] is False
    committed = service.commit_eda_import_preview(
        project_id, preview["preview_id"], preview["preview_fingerprint"], tenant_id="tenant-1", requester="owner-1"
    )
    assert committed["source_currentness"] == "CURRENT"
    assert committed["commit_outcome"] == "CREATED"

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "catalog.sqlite3", clock=Clock(), initialize_async=False
    )
    listed = restarted.eda_import_revisions(
        project_id, tenant_id="tenant-1", requester="owner-1", backend="JLCEDA_PRO",
        native_entry_relative_path="native/board.eprj3",
    )
    assert listed["items"][0]["import_revision_id"] == committed["import_revision_id"]
    detail = restarted.eda_import_revision(
        project_id, committed["import_revision_id"], committed["import_revision_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert detail["source_currentness"] == "CURRENT"
    (source / "unrecognized.extension").write_bytes(b"new")
    changed = restarted.eda_import_revision(
        project_id, committed["import_revision_id"], committed["import_revision_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert changed["source_currentness"] == "STALE"
    (source / "board.eprj3").unlink()
    deleted = restarted.eda_import_revision(
        project_id, committed["import_revision_id"], committed["import_revision_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert deleted["source_currentness"] == "STALE"


def test_non_owner_is_rejected_before_source_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id, _ = _project(tmp_path)
    reads: list[str] = []

    def forbidden_scan(*args, **kwargs):
        reads.append("called")
        raise AssertionError("source scan occurred")

    monkeypatch.setattr(service._artifact_intelligence, "scan_eda_import_directory", forbidden_scan)
    with pytest.raises(ProjectCatalogForbidden):
        service.create_eda_import_preview(
            project_id, tenant_id="tenant-1", requester="intruder", backend="JLCEDA_PRO",
            native_entry_relative_path="native/board.eprj3", selected_native_entries=(),
            expected_head_revision_id=None, expected_head_revision_fingerprint=None,
        )
    assert reads == []


def test_active_reuse_does_not_advance_head_and_history_a_b_a_gets_new_id(tmp_path: Path) -> None:
    service, project_id, source = _project(tmp_path)

    def preview(head: dict | None) -> dict:
        return service.create_eda_import_preview(
            project_id, tenant_id="tenant-1", requester="owner-1", backend="JLCEDA_PRO",
            native_entry_relative_path="native/board.eprj3", selected_native_entries=(),
            expected_head_revision_id=None if head is None else head["import_revision_id"],
            expected_head_revision_fingerprint=None if head is None else head["import_revision_fingerprint"],
        )

    def commit(candidate: dict) -> dict:
        return service.commit_eda_import_preview(
            project_id, candidate["preview_id"], candidate["preview_fingerprint"],
            tenant_id="tenant-1", requester="owner-1",
        )

    a1 = commit(preview(None))
    same_1 = preview(a1)
    same_2 = preview(a1)
    reused_1 = commit(same_1)
    reused_2 = commit(same_2)
    assert reused_1["commit_outcome"] == reused_2["commit_outcome"] == "REUSED_ACTIVE"
    assert reused_1["import_revision_id"] == reused_2["import_revision_id"] == a1["import_revision_id"]
    (source / "board.eprj3").write_bytes(b"opaque B")
    b = commit(preview(a1))
    (source / "board.eprj3").write_bytes(b"opaque v1")
    a2 = commit(preview(b))
    assert a2["import_revision_fingerprint"] == a1["import_revision_fingerprint"]
    assert len({a1["import_revision_id"], b["import_revision_id"], a2["import_revision_id"]}) == 3
    retry = commit(same_1)
    assert retry["import_revision_id"] == a1["import_revision_id"]
    assert retry["active"] is False and retry["commit_outcome"] == "ALREADY_COMMITTED"
    assert len(service.eda_import_revisions(
        project_id, tenant_id="tenant-1", requester="owner-1", backend="JLCEDA_PRO",
        native_entry_relative_path="native/board.eprj3",
    )["items"]) == 3


def test_preview_after_source_delete_cannot_commit(tmp_path: Path) -> None:
    service, project_id, source = _project(tmp_path)
    candidate = service.create_eda_import_preview(
        project_id, tenant_id="tenant-1", requester="owner-1", backend="JLCEDA_PRO",
        native_entry_relative_path="native/board.eprj3", selected_native_entries=(),
        expected_head_revision_id=None, expected_head_revision_fingerprint=None,
    )
    (source / "board.eprj3").unlink()
    with pytest.raises(Exception) as error:
        service.commit_eda_import_preview(
            project_id, candidate["preview_id"], candidate["preview_fingerprint"],
            tenant_id="tenant-1", requester="owner-1",
        )
    assert str(error.value) == "SOURCE_SET_CHANGED"


def test_preview_source_mutation_rejects_without_consuming_candidate(tmp_path: Path) -> None:
    service, project_id, source = _project(tmp_path)
    candidate = service.create_eda_import_preview(
        project_id, tenant_id="tenant-1", requester="owner-1", backend="JLCEDA_PRO",
        native_entry_relative_path="native/board.eprj3", selected_native_entries=(),
        expected_head_revision_id=None, expected_head_revision_fingerprint=None,
    )
    (source / "board.eprj3").write_bytes(b"changed")
    with pytest.raises(Exception) as error:
        service.commit_eda_import_preview(
            project_id, candidate["preview_id"], candidate["preview_fingerprint"],
            tenant_id="tenant-1", requester="owner-1",
        )
    assert str(error.value) == "SOURCE_SET_CHANGED"
    assert service._repository.eda_import_preview(
        project_id, candidate["preview_id"], tenant_id="tenant-1", requester="owner-1"
    )["status"] == "OPEN"


def test_expired_open_candidate_does_not_read_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id, _ = _project(tmp_path)
    candidate = service.create_eda_import_preview(
        project_id, tenant_id="tenant-1", requester="owner-1", backend="JLCEDA_PRO",
        native_entry_relative_path="native/board.eprj3", selected_native_entries=(),
        expected_head_revision_id=None, expected_head_revision_fingerprint=None,
    )

    class LateClock:
        def now(self) -> datetime:
            return Clock().now() + timedelta(minutes=11)

    service._clock = LateClock()
    monkeypatch.setattr(service._artifact_intelligence, "scan_eda_import_directory", lambda **_: pytest.fail("expired source read"))
    with pytest.raises(Exception) as error:
        service.commit_eda_import_preview(
            project_id, candidate["preview_id"], candidate["preview_fingerprint"],
            tenant_id="tenant-1", requester="owner-1",
        )
    assert str(error.value) == "IMPORT_PREVIEW_EXPIRED"


def test_kicad_board_native_subset_and_ruleset_currentness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id, source = _project(tmp_path)
    board = source / "layout.kicad_pcb"
    board.write_bytes(
        b'(kicad_pcb (version 20240108) (generator pcbnew) '
        b'(footprint "Library:Part" (uuid 01234567-89ab-4cde-8f01-23456789abcd) '
        b'(at 10 20 90) (property "Reference" "U1") (pad "1" smd rect (net 1 "GND"))))'
    )
    candidate = service.create_eda_import_preview(
        project_id, tenant_id="tenant-1", requester="owner-1", backend="KICAD",
        native_entry_relative_path="native/layout.kicad_pcb", selected_native_entries=(),
        expected_head_revision_id=None, expected_head_revision_fingerprint=None,
    )
    assert candidate["objects"][0]["native_id"] == "01234567-89ab-4cde-8f01-23456789abcd"
    assert "pins" not in candidate["objects"][0] and "nets" not in candidate["objects"][0]
    revision = service.commit_eda_import_preview(
        project_id, candidate["preview_id"], candidate["preview_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert revision["source_currentness"] == revision["observation_currentness"] == "CURRENT"
    original_fingerprint = service._eda_import_fingerprint
    monkeypatch.setattr(service, "_eda_import_fingerprint", lambda _: "sha256:" + "f" * 64)
    stale = service.eda_import_revision(
        project_id, revision["import_revision_id"], revision["import_revision_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert stale["source_currentness"] == "CURRENT"
    assert stale["observation_currentness"] == "STALE"
    monkeypatch.setattr(service, "_eda_import_fingerprint", original_fingerprint)


def test_explicit_external_sheet_dependency_keeps_observation_unavailable(tmp_path: Path) -> None:
    service, project_id, source = _project(tmp_path)
    (source / "demo.kicad_pro").write_bytes(b"{}")
    (source / "demo.kicad_pcb").write_bytes(b"(kicad_pcb (version 20990101))")
    (source / "demo.kicad_sch").write_bytes(
        b'(kicad_sch (version 20231120) (sheet (property "Sheetfile" "../outside.kicad_sch")))'
    )
    candidate = service.create_eda_import_preview(
        project_id, tenant_id="tenant-1", requester="owner-1", backend="KICAD",
        native_entry_relative_path="native/demo.kicad_pro", selected_native_entries=(),
        expected_head_revision_id=None, expected_head_revision_fingerprint=None,
    )
    assert any(item["kind"] == "EXTERNAL_DEPENDENCY" for item in candidate["manifest"])
    assert all("outside.kicad_sch" not in str(item) for item in candidate["manifest"])
    revision = service.commit_eda_import_preview(
        project_id, candidate["preview_id"], candidate["preview_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert revision["source_currentness"] == "UNAVAILABLE"
    assert revision["can_modify"] is False
