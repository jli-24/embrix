"""Portable compatibility data captured by the hash-verified pre-upgrade writer."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from embedded_copilot.api.project_catalog_models import NativeMoveCandidateView
from embedded_copilot.execution_worker.contracts import (
    KiCadNativeFootprintMoveOperation,
    OperationRequest,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogRuntime,
)

GOLDENS = Path(__file__).resolve().parents[1] / "fixtures/kicad_ruleset_r1"


@pytest.fixture(autouse=True)
def prohibit_tool_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("r2 compatibility fixtures cannot run tools or network")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, 8, tzinfo=UTC)


@pytest.mark.parametrize("kind", ["standalone", "composite"])
def test_frozen_r1_catalog_rows_remain_historical_and_require_r2_repreview(tmp_path: Path, kind: str) -> None:
    name = "r1-catalog-" + kind + ".json"
    provenance = json.loads((GOLDENS / "catalog-provenance.json").read_text())
    expected = next(item["sha256"] for item in provenance["fixtures"] if item["filename"] == name)
    raw = (GOLDENS / name).read_bytes()
    assert sha256(raw).hexdigest() == expected
    fixture = json.loads(raw)
    root = tmp_path / "projects"
    root.mkdir()
    database = tmp_path / "catalog.sqlite3"
    catalog = ProjectCatalogRuntime(project_root=root, state_database=database, clock=Clock(), initialize_async=False)
    for relative, source in fixture["source_files"].items():
        path = root / "board" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    with sqlite3.connect(database) as connection:
        for table in ("projects", "project_owners", "project_eda_import_revisions", "project_eda_import_previews"):
            for row in fixture["rows"][table]:
                # Table and columns come from this fixed fixture allowlist, not API input.
                columns = tuple(row)
                connection.execute(
                    "INSERT INTO " + table + "(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
                    tuple(row[column] for column in columns),
                )
    old = fixture["revision"]
    detail = catalog.eda_import_revision(
        fixture["project_id"], old["import_revision_id"], old["import_revision_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert detail["source_currentness"] == "CURRENT" and detail["observation_currentness"] == "STALE"
    pending = fixture["pending_preview"]
    with pytest.raises(ProjectCatalogConflict, match="^PARSER_RULESET_CHANGED$"):
        catalog.commit_eda_import_preview(
            fixture["project_id"], pending["preview_id"], pending["preview_fingerprint"],
            tenant_id="tenant-1", requester="owner-1",
        )
    with sqlite3.connect(database) as connection:
        body = connection.execute("SELECT body FROM project_eda_import_revisions WHERE import_revision_id=?", (old["import_revision_id"],)).fetchone()[0]
    assert body == fixture["rows"]["project_eda_import_revisions"][0]["body"]
    arguments = {
        "tenant_id": "tenant-1", "requester": "owner-1", "backend": "KICAD",
        "native_entry_relative_path": fixture["entry"], "selected_native_entries": (),
        "expected_head_revision_id": old["import_revision_id"],
        "expected_head_revision_fingerprint": old["import_revision_fingerprint"],
    }
    first = catalog.create_eda_import_preview(fixture["project_id"], **arguments)
    competing = catalog.create_eda_import_preview(fixture["project_id"], **arguments)
    new = catalog.commit_eda_import_preview(
        fixture["project_id"], first["preview_id"], first["preview_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert new["commit_outcome"] == "CREATED"
    assert new["parser_ruleset_id"] == (
        "kicad-pcb-footprint-20240108-r2" if kind == "standalone" else "kicad-project-source-20240108-r2"
    )
    assert new["import_revision_id"] != old["import_revision_id"]
    assert new["import_revision_fingerprint"] != old["import_revision_fingerprint"]
    assert new["source_set_fingerprint"] == old["source_set_fingerprint"]
    with pytest.raises(ProjectCatalogConflict, match="^IMPORT_HEAD_CHANGED$"):
        catalog.commit_eda_import_preview(
            fixture["project_id"], competing["preview_id"], competing["preview_fingerprint"],
            tenant_id="tenant-1", requester="owner-1",
        )
    restarted = ProjectCatalogRuntime(project_root=root, state_database=database, clock=Clock(), initialize_async=False)
    restored = restarted.eda_import_revision(
        fixture["project_id"], new["import_revision_id"], new["import_revision_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert restored["active"] is True and restored["observation_currentness"] == "CURRENT"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT body FROM project_eda_import_revisions WHERE import_revision_id=?", (old["import_revision_id"],)).fetchone()[0] == body


@pytest.mark.parametrize("filename,model", [
    ("r1-api.json", NativeMoveCandidateView),
    ("r1-operation.json", KiCadNativeFootprintMoveOperation),
    ("r1-request.json", OperationRequest),
])
def test_frozen_r1_serialized_vector_roundtrips(filename: str, model: Any) -> None:
    provenance = json.loads((GOLDENS / "provenance.json").read_text())
    expected = next(item["sha256"] for item in provenance["vectors"] if item["filename"] == filename)
    raw = (GOLDENS / filename).read_bytes()
    assert sha256(raw).hexdigest() == expected
    assert model.model_validate_json(raw).model_dump_json().encode() == raw


def test_r2_api_and_operation_rebind_without_reusing_r1_fingerprint() -> None:
    view = json.loads((GOLDENS / "r1-api.json").read_bytes())
    view["parser_ruleset_id"] = "kicad-pcb-footprint-20240108-r2"
    checked = NativeMoveCandidateView.model_validate(view)
    assert checked.parser_ruleset_id.endswith("-r2")
    assert checked.can_request_operation_approval is False
    operation = json.loads((GOLDENS / "r1-operation.json").read_bytes())
    old_fingerprint = operation["fingerprint"]
    operation["parser_ruleset_id"] = "kicad-pcb-footprint-20240108-r2"
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        KiCadNativeFootprintMoveOperation.model_validate_json(json.dumps(operation))
    del operation["fingerprint"]
    rebound = KiCadNativeFootprintMoveOperation.model_validate_json(json.dumps(operation))
    assert rebound.fingerprint != old_fingerprint
