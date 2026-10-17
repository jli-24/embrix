"""C2-I additive Catalog records; no native files or tools are used here."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.project_catalog import (
    ProjectCatalogForbidden,
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)
from embedded_copilot.project_catalog.exceptions import ProjectCatalogConflict


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, 8, tzinfo=UTC)


def _fingerprint(value: dict[str, object]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _project(tmp_path: Path) -> tuple[ProjectCatalogRuntime, str]:
    root = tmp_path / "projects"
    root.mkdir()
    catalog = ProjectCatalogRuntime(
        project_root=root, state_database=tmp_path / "catalog.sqlite3", clock=Clock(), initialize_async=False
    )
    created_at = datetime(2026, 9, 30, 8, tzinfo=UTC)
    preview = catalog.preview(
        ProjectCreationRequest(name="Board", relative_path="board", project_type=ProjectType.STM32),
        tenant_id="tenant-1", requester="owner-1",
    )
    project = catalog.create(
        preview.preview_id, preview.fingerprint, confirmed_at=created_at,
        tenant_id="tenant-1", requester="owner-1",
    )
    return catalog, project.project_id


def _candidate(project_id: str) -> dict[str, object]:
    binding: dict[str, object] = {
        "schema": "c2-kicad-footprint-move-v1",
        "tenant_id": "tenant-1",
        "project_id": project_id,
        "requester": "owner-1",
        "import_revision_id": "eda-import-selected",
        "import_revision_fingerprint": "sha256:" + "1" * 64,
        "native_uuid": "01234567-89ab-4cde-8f01-23456789abcd",
        "after": {"x_mm": 11.0, "y_mm": 20.0},
    }
    return {
        "candidate_id": "native-move-1",
        "candidate_fingerprint": _fingerprint(binding),
        "tenant_id": "tenant-1",
        "project_id": project_id,
        "requester": "owner-1",
        "import_revision_id": "eda-import-selected",
        "import_revision_fingerprint": "sha256:" + "1" * 64,
        "created_at": datetime(2026, 9, 30, 8, tzinfo=UTC).isoformat(),
        "binding": binding,
    }


def _review(candidate: dict[str, object]) -> dict[str, object]:
    binding: dict[str, object] = {
        "schema": "c2-kicad-mapping-review-v1",
        "tenant_id": "tenant-1",
        "project_id": candidate["project_id"],
        "candidate_id": candidate["candidate_id"],
        "candidate_fingerprint": candidate["candidate_fingerprint"],
        "reviewer_id": "owner-1",
        "decision": "ACCEPT_MAPPING",
        "design_intent": "Move selected footprint x by 1 mm.",
    }
    return {
        "mapping_review_id": "mapping-review-1",
        "mapping_review_fingerprint": _fingerprint(binding),
        "tenant_id": "tenant-1",
        "project_id": candidate["project_id"],
        "candidate_id": candidate["candidate_id"],
        "candidate_fingerprint": candidate["candidate_fingerprint"],
        "reviewer_id": "owner-1",
        "decision": "ACCEPT_MAPPING",
        "created_at": datetime(2026, 9, 30, 8, tzinfo=UTC).isoformat(),
        "binding": binding,
    }


def test_c2i_store_candidate_review_restart_and_idempotent_migration(tmp_path: Path) -> None:
    catalog, project_id = _project(tmp_path)
    candidate = _candidate(project_id)
    review = _review(candidate)
    store = catalog._repository
    assert store.save_eda_native_move_candidate(candidate, tenant_id="tenant-1", requester="owner-1") == candidate
    assert store.save_eda_native_mapping_review(review, tenant_id="tenant-1", requester="owner-1") == review
    reopened = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "catalog.sqlite3", clock=Clock(), initialize_async=False
    )
    assert reopened._repository.eda_native_move_candidate(
        project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    ) == candidate
    assert reopened._repository.eda_native_mapping_review(
        project_id, review["mapping_review_id"], review["mapping_review_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    ) == review


def test_c2i_store_owner_before_write_and_review_single_decision(tmp_path: Path) -> None:
    catalog, project_id = _project(tmp_path)
    candidate = _candidate(project_id)
    store = catalog._repository
    with pytest.raises(ProjectCatalogForbidden):
        store.save_eda_native_move_candidate(candidate, tenant_id="tenant-1", requester="other-1")
    assert store.save_eda_native_move_candidate(candidate, tenant_id="tenant-1", requester="owner-1") == candidate
    review = _review(candidate)
    assert store.save_eda_native_mapping_review(review, tenant_id="tenant-1", requester="owner-1") == review
    assert store.save_eda_native_mapping_review(review, tenant_id="tenant-1", requester="owner-1") == review
    changed_binding = {**review["binding"], "decision": "REJECT_MAPPING"}
    changed = {
        **review,
        "mapping_review_id": "mapping-review-2",
        "mapping_review_fingerprint": _fingerprint(changed_binding),
        "decision": "REJECT_MAPPING",
        "binding": changed_binding,
    }
    with pytest.raises(ProjectCatalogConflict):
        store.save_eda_native_mapping_review(changed, tenant_id="tenant-1", requester="owner-1")
