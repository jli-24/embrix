from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from embedded_copilot.project_catalog.exceptions import (
    ProjectCatalogConflict,
    ProjectCatalogExpired,
    ProjectCatalogForbidden,
    ProjectCatalogRejected,
    ProjectCatalogUnavailable,
)
from embedded_copilot.project_catalog.models import (
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
)
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository

NOW = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
PROJECT = "project-1"
TENANT = "tenant-1"
OWNER = "owner-1"
ENTRY = "hardware/board.kicad_pro"
BACKEND = "KICAD"
PROJECT_FINGERPRINT = "sha256:" + hashlib.sha256(json.dumps(
    {
        "schema": "eda-import-project-binding-v1",
        "project_id": PROJECT,
        "tenant_id": TENANT,
        "location_id": "location-1",
        "relative_path": "board",
        "requester": OWNER,
    },
    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
).encode("utf-8")).hexdigest()


def _repository(tmp_path):
    database = tmp_path / "catalog.sqlite3"
    repository = SQLiteProjectCatalogRepository(database)
    if repository.project(PROJECT) is None:
        repository.save_project(ProjectView(
            project_id=PROJECT,
            tenant_id=TENANT,
            name="Board",
            location_id="location-1",
            directory_name="board",
            creation_mode=ProjectCreationMode.ATTACH_EXISTING,
            relative_path="board",
            project_type=ProjectType.STM32,
            initialization_status=ProjectInitializationStatus.READY,
            created_at=NOW,
            updated_at=NOW,
            last_activity_at=NOW,
        ))
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO project_owners(project_id,requester) VALUES(?,?)",
                (PROJECT, OWNER),
            )
    return repository, database


def _candidate(
    preview_id: str,
    content: str,
    *,
    expected: dict | None = None,
    entry: str = ENTRY,
    expires_at: datetime | None = None,
) -> dict:
    return {
        "preview_id": preview_id,
        "preview_fingerprint": "sha256:" + preview_id[-1] * 64,
        "project_id": PROJECT,
        "tenant_id": TENANT,
        "requester": OWNER,
        "backend": BACKEND,
        "native_entry_relative_path": entry,
        "project_binding_fingerprint": PROJECT_FINGERPRINT,
        "expected_head_revision_id": None if expected is None else expected["import_revision_id"],
        "expected_head_revision_fingerprint": None if expected is None else expected["import_revision_fingerprint"],
        "created_at": NOW.isoformat(),
        "expires_at": (expires_at or NOW + timedelta(minutes=10)).isoformat(),
        "revision": {
            "import_revision_fingerprint": "sha256:" + content * 64,
            "source_set_fingerprint": "sha256:" + content * 64,
            "parser_ruleset_id": "kicad-r1",
            "parser_ruleset_fingerprint": "sha256:" + "e" * 64,
            "supported_subset_id": "inventory-r1",
            "supported_subset_fingerprint": "sha256:" + "f" * 64,
            "semantic_completeness": "UNKNOWN",
            "verified": False,
            "can_modify": False,
            "objects": [],
            "issues": [{"code": "UNKNOWN_OBJECT", "kind": "UNKNOWN", "blocks_mutation": True}],
        },
    }


def _commit(repository, candidate: dict, *, now: datetime = NOW):
    return repository.commit_eda_import_preview(
        PROJECT,
        candidate["preview_id"],
        candidate["preview_fingerprint"],
        tenant_id=TENANT,
        requester=OWNER,
        now=now,
        current_project_fingerprint=PROJECT_FINGERPRINT,
    )


def test_non_owner_is_rejected_before_candidate_read_or_write(tmp_path):
    repository, database = _repository(tmp_path)
    candidate = _candidate("preview-1", "1")
    candidate["requester"] = "other-user"
    with pytest.raises(ProjectCatalogForbidden):
        repository.save_eda_import_preview(candidate)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM project_eda_import_previews").fetchone()[0] == 0
    with pytest.raises(ProjectCatalogForbidden):
        repository.eda_import_preview(PROJECT, "preview-1", tenant_id=TENANT, requester="other-user")


def test_candidate_cannot_persist_verified_or_mutable_observation(tmp_path):
    repository, database = _repository(tmp_path)
    candidate = _candidate("preview-1", "1")
    candidate["revision"]["verified"] = True
    with pytest.raises(ProjectCatalogRejected, match="IMPORT_PREVIEW_INVALID"):
        repository.save_eda_import_preview(candidate)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM project_eda_import_previews").fetchone()[0] == 0


def test_two_previews_same_active_content_reuse_id_and_do_not_advance_head(tmp_path):
    repository, _ = _repository(tmp_path)
    first = _candidate("preview-1", "1")
    repository.save_eda_import_preview(first)
    a, outcome = _commit(repository, first)
    assert outcome == "CREATED"
    second = _candidate("preview-2", "1", expected=a)
    third = _candidate("preview-3", "1", expected=a)
    repository.save_eda_import_preview(second)
    repository.save_eda_import_preview(third)
    b, second_outcome = _commit(repository, second)
    c, third_outcome = _commit(repository, third)
    assert (b["import_revision_id"], second_outcome) == (a["import_revision_id"], "REUSED_ACTIVE")
    assert (c["import_revision_id"], third_outcome) == (a["import_revision_id"], "REUSED_ACTIVE")
    assert len(repository.eda_import_revisions(PROJECT, BACKEND, ENTRY, tenant_id=TENANT, requester=OWNER)) == 1
    assert repository.eda_import_head(PROJECT, BACKEND, ENTRY, tenant_id=TENANT, requester=OWNER)["import_revision_id"] == a["import_revision_id"]


def test_a_b_a_gets_three_revision_ids_and_historical_fingerprint_can_repeat(tmp_path):
    repository, _ = _repository(tmp_path)
    revisions = []
    for index, content in enumerate(("1", "2", "1"), start=1):
        candidate = _candidate(f"preview-{index}", content, expected=revisions[-1] if revisions else None)
        repository.save_eda_import_preview(candidate)
        revision, outcome = _commit(repository, candidate)
        assert outcome == "CREATED"
        revisions.append(revision)
    assert len({item["import_revision_id"] for item in revisions}) == 3
    assert revisions[0]["import_revision_fingerprint"] == revisions[2]["import_revision_fingerprint"]
    history = repository.eda_import_revisions(PROJECT, BACKEND, ENTRY, tenant_id=TENANT, requester=OWNER)
    assert len(history) == 3
    assert [item["active"] for item in history] == [False, False, True]


def test_committed_preview_retry_returns_original_after_head_changes(tmp_path):
    repository, _ = _repository(tmp_path)
    first = _candidate("preview-1", "1")
    repository.save_eda_import_preview(first)
    a, _ = _commit(repository, first)
    second = _candidate("preview-2", "2", expected=a)
    repository.save_eda_import_preview(second)
    _commit(repository, second)
    retry, outcome = _commit(repository, first, now=NOW + timedelta(hours=1))
    assert outcome == "ALREADY_COMMITTED"
    assert retry["import_revision_id"] == a["import_revision_id"]
    assert retry["active"] is False
    with pytest.raises(ProjectCatalogRejected, match="IMPORT_PREVIEW_INVALID"):
        repository.commit_eda_import_preview(
            PROJECT, first["preview_id"], "sha256:" + "0" * 64,
            tenant_id=TENANT, requester=OWNER, now=NOW,
            current_project_fingerprint=PROJECT_FINGERPRINT,
        )


def test_cas_conflicts_only_when_actual_head_differs_even_for_same_content(tmp_path):
    repository, _ = _repository(tmp_path)
    first = _candidate("preview-1", "1")
    competing = _candidate("preview-2", "1")
    repository.save_eda_import_preview(first)
    repository.save_eda_import_preview(competing)
    _commit(repository, first)
    with pytest.raises(ProjectCatalogConflict, match="IMPORT_HEAD_CHANGED"):
        _commit(repository, competing)
    assert repository.eda_import_preview(PROJECT, "preview-2", tenant_id=TENANT, requester=OWNER)["status"] == "OPEN"


def test_concurrent_previews_with_same_old_head_have_one_winner(tmp_path):
    repository, database = _repository(tmp_path)
    first = _candidate("preview-1", "1")
    second = _candidate("preview-2", "1")
    repository.save_eda_import_preview(first)
    repository.save_eda_import_preview(second)
    start = Event()

    def run(candidate):
        start.wait()
        try:
            return SQLiteProjectCatalogRepository(database).commit_eda_import_preview(
                PROJECT, candidate["preview_id"], candidate["preview_fingerprint"],
                tenant_id=TENANT, requester=OWNER, now=NOW,
                current_project_fingerprint=PROJECT_FINGERPRINT,
            )[1]
        except ProjectCatalogConflict as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(run, candidate) for candidate in (first, second)]
        start.set()
        outcomes = [future.result() for future in results]
    assert sorted(outcomes) == ["CREATED", "IMPORT_HEAD_CHANGED"]
    assert len(repository.eda_import_revisions(PROJECT, BACKEND, ENTRY, tenant_id=TENANT, requester=OWNER)) == 1


def test_expired_or_rebound_preview_remains_open(tmp_path):
    repository, database = _repository(tmp_path)
    expired = _candidate("preview-1", "1")
    repository.save_eda_import_preview(expired)
    with pytest.raises(ProjectCatalogExpired, match="IMPORT_PREVIEW_EXPIRED"):
        _commit(repository, expired, now=NOW + timedelta(minutes=10))
    rebound = _candidate("preview-2", "2")
    repository.save_eda_import_preview(rebound)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE projects SET relative_path='renamed' WHERE project_id=?", (PROJECT,))
    with pytest.raises(ProjectCatalogConflict, match="IMPORT_PROJECT_CHANGED"):
        _commit(repository, rebound)
    assert repository.eda_import_preview(PROJECT, "preview-2", tenant_id=TENANT, requester=OWNER)["status"] == "OPEN"


def test_failed_commit_rolls_back_active_and_preview_consumption(tmp_path):
    repository, database = _repository(tmp_path)
    first = _candidate("preview-1", "1")
    repository.save_eda_import_preview(first)
    a, _ = _commit(repository, first)
    second = _candidate("preview-2", "2", expected=a)
    repository.save_eda_import_preview(second)
    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TRIGGER interrupt_eda_import BEFORE UPDATE OF status ON project_eda_import_previews
            WHEN NEW.preview_id = 'preview-2' AND NEW.status = 'COMMITTED'
            BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="simulated interruption"):
        _commit(repository, second)
    restarted = SQLiteProjectCatalogRepository(database)
    assert restarted.eda_import_preview(PROJECT, "preview-2", tenant_id=TENANT, requester=OWNER)["status"] == "OPEN"
    assert restarted.eda_import_head(PROJECT, BACKEND, ENTRY, tenant_id=TENANT, requester=OWNER)["import_revision_id"] == a["import_revision_id"]
    assert len(restarted.eda_import_revisions(PROJECT, BACKEND, ENTRY, tenant_id=TENANT, requester=OWNER)) == 1


def test_restart_recovers_issue_and_owner_scoped_history(tmp_path):
    repository, database = _repository(tmp_path)
    candidate = _candidate("preview-1", "1")
    repository.save_eda_import_preview(candidate)
    saved, _ = _commit(repository, candidate)
    reopened = SQLiteProjectCatalogRepository(database)
    read = reopened.eda_import_revision(PROJECT, saved["import_revision_id"], saved["import_revision_fingerprint"], tenant_id=TENANT, requester=OWNER)
    assert read["issues"] == candidate["revision"]["issues"]
    assert reopened.eda_import_revision(PROJECT, saved["import_revision_id"], "sha256:" + "0" * 64, tenant_id=TENANT, requester=OWNER) is None
    with pytest.raises(ProjectCatalogForbidden):
        reopened.eda_import_revisions(PROJECT, BACKEND, ENTRY, tenant_id=TENANT, requester="other-user")


def test_migration_is_idempotent_and_rejects_incompatible_existing_table(tmp_path):
    repository, database = _repository(tmp_path)
    SQLiteProjectCatalogRepository(database)
    candidate = _candidate("preview-1", "1")
    repository.save_eda_import_preview(candidate)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE project_eda_import_previews")
        connection.execute("DROP TABLE project_eda_import_revisions")
    migrated = SQLiteProjectCatalogRepository(database)
    assert migrated.project(PROJECT) is not None
    assert migrated.eda_import_revisions(PROJECT, BACKEND, ENTRY, tenant_id=TENANT, requester=OWNER) == ()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE project_eda_import_previews")
        connection.execute("CREATE TABLE project_eda_import_previews(preview_id TEXT PRIMARY KEY)")
    with pytest.raises(ProjectCatalogUnavailable, match="EDA import schema"):
        SQLiteProjectCatalogRepository(database)
