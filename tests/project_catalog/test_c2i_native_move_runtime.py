"""C2-I Catalog Owner, C1 source and review binding over synthetic board bytes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from datetime import timedelta
import hashlib
import json
import os
import subprocess
import sys

import pytest

from embedded_copilot.execution_worker.contracts import (
    KiCadNativeFootprintMoveOperation, native_move_operation_id, native_move_operation_nonce,
)

from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogRejected,
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)


UUID = "01234567-89ab-4cde-8f01-23456789abcd"
BOARD = (
    b'(kicad_pcb (version 20240108) (generator pcbnew) '
    b'(footprint "Library:Part" (uuid 01234567-89ab-4cde-8f01-23456789abcd) '
    b'(at 10 20 90) (property "Reference" "U1")))'
)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, 8, tzinfo=UTC)


class Workspace:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.project_fingerprint = "sha256:" + "a" * 64
        self.target_fingerprint = "sha256:" + "b" * 64

    def snapshot(self, *, tenant_id: str, project_id: str):
        assert (tenant_id, project_id) == ("tenant-1", self.project_id)
        return SimpleNamespace(
            tenant_id=tenant_id, project_id=project_id, target_id="project-root",
            project_fingerprint=self.project_fingerprint,
            target_fingerprint=self.target_fingerprint,
        )


def _catalog(tmp_path: Path) -> tuple[ProjectCatalogRuntime, str, Path, dict]:
    root = tmp_path / "projects"
    root.mkdir()
    catalog = ProjectCatalogRuntime(
        project_root=root, state_database=tmp_path / "catalog.sqlite3", clock=Clock(), initialize_async=False
    )
    preview = catalog.preview(
        ProjectCreationRequest(name="Board", relative_path="board", project_type=ProjectType.STM32),
        tenant_id="tenant-1", requester="owner-1",
    )
    project = catalog.create(
        preview.preview_id, preview.fingerprint, confirmed_at=Clock().now(),
        tenant_id="tenant-1", requester="owner-1",
    )
    source = root / "board" / "native"
    source.mkdir()
    board = source / "layout.kicad_pcb"
    board.write_bytes(BOARD)
    catalog.set_workspace_snapshot_source(Workspace(project.project_id))
    c1_preview = catalog.create_eda_import_preview(
        project.project_id, tenant_id="tenant-1", requester="owner-1", backend="KICAD",
        native_entry_relative_path="native/layout.kicad_pcb", selected_native_entries=(),
        expected_head_revision_id=None, expected_head_revision_fingerprint=None,
    )
    revision = catalog.commit_eda_import_preview(
        project.project_id, c1_preview["preview_id"], c1_preview["preview_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    return catalog, project.project_id, board, revision


def _candidate(catalog: ProjectCatalogRuntime, project_id: str, revision: dict) -> dict:
    return catalog.create_native_move_candidate(
        project_id, tenant_id="tenant-1", requester="owner-1",
        import_revision_id=revision["import_revision_id"],
        import_revision_fingerprint=revision["import_revision_fingerprint"],
        workspace_target_id="project-root", target_native_uuid=UUID,
        after_x_mm=11.0, after_y_mm=20.0,
    )


def test_c2i_candidate_review_restart_and_production_write_blocked(tmp_path: Path) -> None:
    catalog, project_id, _, revision = _catalog(tmp_path)
    candidate = _candidate(catalog, project_id, revision)
    assert candidate["candidate_fingerprint"].startswith("sha256:")
    assert candidate["binding"]["target"]["native_uuid"] == UUID
    assert candidate["binding"]["source"]["import_revision_id"] == revision["import_revision_id"]
    review = catalog.review_native_move_candidate(
        project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        tenant_id="tenant-1", requester="owner-1", decision="ACCEPT_MAPPING",
        design_intent="Move this selected footprint x by 1 mm.",
    )
    assert review["candidate_fingerprint"] == candidate["candidate_fingerprint"]
    assert catalog.review_native_move_candidate(
        project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        tenant_id="tenant-1", requester="owner-1", decision="ACCEPT_MAPPING",
        design_intent="Move this selected footprint x by 1 mm.",
    ) == review
    with pytest.raises(ProjectCatalogConflict, match="NATIVE_MOVE_REVIEW_CONFLICT"):
        catalog.review_native_move_candidate(
            project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
            tenant_id="tenant-1", requester="owner-1", decision="REJECT_MAPPING",
            design_intent="Move this selected footprint x by 1 mm.",
        )
    reopened = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "catalog.sqlite3",
        clock=Clock(), initialize_async=False,
    )
    reopened.set_workspace_snapshot_source(Workspace(project_id))
    assert reopened.native_move_candidate(
        project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )["candidate_id"] == candidate["candidate_id"]
    assert reopened.native_mapping_review(
        project_id, review["mapping_review_id"], review["mapping_review_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )["mapping_review_id"] == review["mapping_review_id"]
    with pytest.raises(ProjectCatalogRejected, match="PRESERVATION_UNPROVEN"):
        reopened.create_native_move_operation(
            project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
            review["mapping_review_id"], review["mapping_review_fingerprint"],
            tenant_id="tenant-1", requester="owner-1", execution=None,
        )


def test_c2i_owner_rejection_precedes_source_scan_and_stale_review_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, project_id, board, revision = _catalog(tmp_path)
    calls: list[str] = []

    def forbidden_scan(*args, **kwargs):
        calls.append("source")
        raise AssertionError("source scanned before Owner check")

    original = catalog._artifact_intelligence.scan_eda_import_directory
    monkeypatch.setattr(catalog._artifact_intelligence, "scan_eda_import_directory", forbidden_scan)
    with pytest.raises(ProjectCatalogForbidden):
        catalog.create_native_move_candidate(
            project_id, tenant_id="tenant-1", requester="other-1",
            import_revision_id=revision["import_revision_id"],
            import_revision_fingerprint=revision["import_revision_fingerprint"],
            workspace_target_id="project-root", target_native_uuid=UUID,
            after_x_mm=11.0, after_y_mm=20.0,
        )
    assert calls == []
    monkeypatch.setattr(catalog._artifact_intelligence, "scan_eda_import_directory", original)
    candidate = _candidate(catalog, project_id, revision)
    board.write_bytes(BOARD + b" ")
    with pytest.raises(ProjectCatalogConflict, match="SOURCE_STALE"):
        catalog.review_native_move_candidate(
            project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
            tenant_id="tenant-1", requester="owner-1", decision="ACCEPT_MAPPING",
            design_intent="Move this selected footprint x by 1 mm.",
        )


def test_c2i_workspace_target_drift_invalidates_candidate_and_review(tmp_path: Path) -> None:
    catalog, project_id, _, revision = _catalog(tmp_path)
    candidate = _candidate(catalog, project_id, revision)
    workspace = Workspace(project_id)
    workspace.target_fingerprint = "sha256:" + "c" * 64
    catalog.set_workspace_snapshot_source(workspace)
    with pytest.raises(ProjectCatalogConflict, match="WORKSPACE_STALE"):
        catalog.native_move_candidate(
            project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
            tenant_id="tenant-1", requester="owner-1",
        )
    with pytest.raises(ProjectCatalogConflict, match="WORKSPACE_STALE"):
        catalog.review_native_move_candidate(
            project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
            tenant_id="tenant-1", requester="owner-1", decision="ACCEPT_MAPPING",
            design_intent="Move selected footprint.",
        )


def test_c2i_revalidator_binds_exact_review_source_and_workspace(tmp_path: Path) -> None:
    from embedded_copilot.execution_worker.contracts import OperationRequest, OperationStatus
    from embedded_copilot.execution_worker.ports import AdapterResult
    from embedded_copilot.execution_worker.service import ExecutionOperationService
    from embedded_copilot.execution_worker.store import SQLiteExecutionStore
    from embedded_copilot.execution_worker.worker import ExecutionWorker

    catalog, project_id, board, revision = _catalog(tmp_path)
    candidate = _candidate(catalog, project_id, revision)
    review = catalog.review_native_move_candidate(
        project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        tenant_id="tenant-1", requester="owner-1", decision="ACCEPT_MAPPING",
        design_intent="Move selected footprint.",
    )
    source, target, workspace = (candidate["binding"][name] for name in ("source", "target", "workspace"))
    reviewed_at = datetime.fromisoformat(review["created_at"])
    operation = KiCadNativeFootprintMoveOperation(
        operation_id=native_move_operation_id("tenant-1", project_id, candidate["candidate_id"],
                                               candidate["candidate_fingerprint"], review["mapping_review_id"],
                                               review["mapping_review_fingerprint"]),
        tenant_id="tenant-1", project_id=project_id, requester_id="owner-1",
        target_id=workspace["target_id"], target_fingerprint=workspace["target_fingerprint"],
        workspace_snapshot_id="workspace-snapshot-" + hashlib.sha256(workspace["project_fingerprint"].encode()).hexdigest()[:24],
        workspace_fingerprint=workspace["project_fingerprint"], proposal_id=candidate["candidate_id"],
        proposal_fingerprint=candidate["candidate_fingerprint"],
        created_at=reviewed_at, expires_at=reviewed_at + timedelta(minutes=10),
        nonce=native_move_operation_nonce(review["mapping_review_id"], review["mapping_review_fingerprint"]),
        mapping_review_id=review["mapping_review_id"], mapping_review_fingerprint=review["mapping_review_fingerprint"],
        mapping_reviewed_at=reviewed_at, import_revision_id=revision["import_revision_id"],
        import_revision_fingerprint=revision["import_revision_fingerprint"],
        source_set_fingerprint=source["source_set_fingerprint"], manifest_fingerprint=source["manifest_fingerprint"],
        parser_ruleset_id=source["parser_ruleset_id"], parser_ruleset_fingerprint=source["parser_ruleset_fingerprint"],
        supported_subset_id=source["supported_subset_id"], supported_subset_fingerprint=source["supported_subset_fingerprint"],
        native_entry_relative_path=source["native_entry_relative_path"], pcb_relative_path=target["pcb_relative_path"],
        pcb_sha256=target["pcb_sha256"], native_uuid=target["native_uuid"], native_locator=target["native_locator"],
        before_x_mm=target["before_x_mm"], before_y_mm=target["before_y_mm"],
        after_x_mm=target["after_x_mm"], after_y_mm=target["after_y_mm"],
        rotation_degrees=target["rotation_degrees"], diff_fingerprint=target["diff_fingerprint"],
    )
    assert catalog.revalidate_native_move_operation(operation) is True
    forged = operation.model_copy(update={"diff_fingerprint": "sha256:" + "f" * 64})
    assert catalog.revalidate_native_move_operation(forged) is False
    store = SQLiteExecutionStore(tmp_path / "offline-worker.sqlite3")
    service = ExecutionOperationService(
        store=store, clock=lambda: reviewed_at + timedelta(minutes=1),
        native_move_authorizer=catalog.revalidate_native_move_operation,
    )
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    approval = service.issue_approval(
        operation=request, reviewer_id="owner-1", nonce="offline-test-approval",
        expires_at=reviewed_at + timedelta(minutes=5),
    )
    service.enqueue(operation=request, approval_id=approval.grant_id)

    class FakeOnly:
        def execute(self, _operation):
            return AdapterResult(status="SUCCEEDED", summary="offline fake only")

    status = ExecutionWorker(
        worker_id="offline-only", store=store, registry=None, workspace_resolver=None,
        targets=None, native_move_adapter=FakeOnly(),
        native_move_revalidator=catalog.revalidate_native_move_operation,
        clock=lambda: reviewed_at + timedelta(minutes=2),
    ).run_once()
    assert status is not None and status.status is OperationStatus.SUCCEEDED
    assert store.get_receipt("tenant-1", project_id, operation.operation_id).native_move_offline.circuit_verified is False
    repo_root = Path(__file__).resolve().parents[2]
    child_code = """
import json, sys
from pathlib import Path
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
catalog = SQLiteProjectCatalogRepository(Path(sys.argv[1]))
candidate = catalog.eda_native_move_candidate(sys.argv[3], sys.argv[4], sys.argv[5], tenant_id='tenant-1', requester='owner-1')
review = catalog.eda_native_mapping_review(sys.argv[3], sys.argv[6], sys.argv[7], tenant_id='tenant-1', requester='owner-1')
worker = SQLiteExecutionStore(Path(sys.argv[2]))
operation = worker.get_operation('tenant-1', sys.argv[3], sys.argv[8])
receipt = worker.get_receipt('tenant-1', sys.argv[3], sys.argv[8])
print(json.dumps({
    'candidate_id': candidate['candidate_id'], 'candidate_fingerprint': candidate['candidate_fingerprint'],
    'review_id': review['mapping_review_id'], 'review_fingerprint': review['mapping_review_fingerprint'],
    'operation_id': operation.operation_id, 'operation_fingerprint': operation.fingerprint,
    'status': receipt.status.value, 'native_read_back': receipt.native_move_offline.native_read_back_status,
    'drc': receipt.native_move_offline.drc_status, 'circuit_verified': receipt.native_move_offline.circuit_verified,
}, sort_keys=True))
"""
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + child_env.get("PYTHONPATH", "")
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    child = subprocess.run(
        [sys.executable, "-c", child_code, str(tmp_path / "catalog.sqlite3"),
         str(tmp_path / "offline-worker.sqlite3"), project_id, candidate["candidate_id"],
         candidate["candidate_fingerprint"], review["mapping_review_id"],
         review["mapping_review_fingerprint"], operation.operation_id],
        cwd=repo_root, env=child_env, capture_output=True, text=True, timeout=20, check=True,
    )
    assert json.loads(child.stdout.splitlines()[-1]) == {
        "candidate_id": candidate["candidate_id"], "candidate_fingerprint": candidate["candidate_fingerprint"],
        "review_id": review["mapping_review_id"], "review_fingerprint": review["mapping_review_fingerprint"],
        "operation_id": operation.operation_id, "operation_fingerprint": operation.fingerprint,
        "status": "SUCCEEDED", "native_read_back": "NOT_RUN", "drc": "NOT_RUN", "circuit_verified": False,
    }
    board.write_bytes(BOARD + b" ")
    assert catalog.revalidate_native_move_operation(operation) is False
