"""Offline C1 index diagnostics; no JLC client or native semantic oracle."""

import json
import sqlite3
from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.project_catalog_routes import router
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from tests.api.test_eda_import_routes import OWNER, Clock, _app


@pytest.mark.parametrize(
    ("raw", "diagnostic"),
    [
        (b"", "INDEX_EMPTY"),
        (b'{"private-source-marker":', "INDEX_JSON_INVALID"),
        (b"\xff\xfe", "INDEX_ENCODING_UNCONFIRMED"),
        (b'{"profile":{}}', None),
        (b'{"profile":null,"boards":{}}', None),
        (b'[]', None),
    ],
)
def test_index_diagnostics_survive_api_commit_restart_and_source_change(
    tmp_path, raw, diagnostic
):
    app, project_id = _app(tmp_path)
    source = tmp_path / "projects" / "board" / "board.eprj3"
    source.write_bytes(raw)
    path = f"/api/v1/copilot/projects/{project_id}"
    body = {
        "backend": "JLCEDA_PRO",
        "native_entry_relative_path": "board.eprj3",
        "selected_native_entries": [],
        "expected_head_revision_id": None,
        "expected_head_revision_fingerprint": None,
    }
    with TestClient(app) as client:
        response = client.post(path + "/eda-import-previews", headers=OWNER, json=body)
        assert response.status_code == 200, response.text
        candidate = response.json()
        codes = {issue["code"] for issue in candidate["issues"]}
        assert "NATIVE_FORMAT_UNCONFIRMED" in codes
        if diagnostic is not None:
            assert diagnostic in codes
        else:
            assert not any(code.startswith("INDEX_") for code in codes)
        assert candidate["parser_ruleset_id"] == "jlceda-index-inventory-r2"
        assert candidate["semantic_completeness"] == "UNKNOWN"
        assert candidate["native_format_versions"] == {"board.eprj3": "UNKNOWN"}
        assert candidate["objects"] == []
        assert candidate["verified"] is candidate["can_modify"] is False
        assert all(issue["locator"] == "board.eprj3" for issue in candidate["issues"])
        assert all(issue["blocks_mutation"] for issue in candidate["issues"])
        assert candidate["manifest"][0]["sha256"] == sha256(raw).hexdigest()
        assert "private-source-marker" not in response.text
        response = client.post(
            path + "/eda-imports",
            headers=OWNER,
            json={
                "preview_id": candidate["preview_id"],
                "preview_fingerprint": candidate["preview_fingerprint"],
            },
        )
        assert response.status_code == 200, response.text
        revision = response.json()
        assert revision["issues"] == candidate["issues"]
        assert source.read_bytes() == raw

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        clock=Clock(),
        initialize_async=False,
    )
    recovered = FastAPI()
    recovered.state.project_catalog_runtime = restarted
    recovered.include_router(router)
    url = path + "/eda-imports/" + revision["import_revision_id"]
    params = {"fingerprint": revision["import_revision_fingerprint"]}
    with TestClient(recovered) as client:
        response = client.get(url, headers=OWNER, params=params)
        assert response.status_code == 200, response.text
        current = response.json()
        assert current["issues"] == candidate["issues"]
        assert current["source_currentness"] == current["observation_currentness"] == "CURRENT"
        assert current["verified"] is current["can_modify"] is False
        source.write_bytes(raw + b"\n")
        changed = client.get(url, headers=OWNER, params=params).json()
        assert changed["source_currentness"] == changed["observation_currentness"] == "STALE"
        assert changed["issues"] == candidate["issues"]
        assert changed["verified"] is changed["can_modify"] is False


def test_old_inventory_revision_remains_immutable_and_requires_explicit_reimport(
    tmp_path, monkeypatch
):
    app, project_id = _app(tmp_path)
    service = app.state.project_catalog_runtime
    source = tmp_path / "projects" / "board" / "board.eprj3"
    source.write_bytes(b"{")
    current_material = service._eda_import_material

    def legacy_material(*args, **kwargs):
        material = current_material(*args, **kwargs)
        material["parser_ruleset_id"] = "inventory-only-r1"
        material["parser_ruleset_fingerprint"] = service._eda_import_fingerprint(
            {"id": "inventory-only-r1", "semantic_rules": "native-version-gated-observation-v1"}
        )
        material["issues"] = [
            issue for issue in material["issues"] if not issue["code"].startswith("INDEX_")
        ]
        material.pop("import_revision_fingerprint")
        material["import_revision_fingerprint"] = service._eda_import_fingerprint(material)
        return material

    with monkeypatch.context() as old:
        old.setattr(service, "_eda_import_material", legacy_material)
        candidate = service.create_eda_import_preview(
            project_id,
            tenant_id="tenant-1",
            requester="owner-1",
            backend="JLCEDA_PRO",
            native_entry_relative_path="board.eprj3",
            selected_native_entries=(),
            expected_head_revision_id=None,
            expected_head_revision_fingerprint=None,
        )
        legacy = service.commit_eda_import_preview(
            project_id,
            candidate["preview_id"],
            candidate["preview_fingerprint"],
            tenant_id="tenant-1",
            requester="owner-1",
        )
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        before = connection.execute(
            "SELECT body FROM project_eda_import_revisions WHERE import_revision_id=?",
            (legacy["import_revision_id"],),
        ).fetchone()[0]
    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        clock=Clock(),
        initialize_async=False,
    )
    stale = restarted.eda_import_revision(
        project_id,
        legacy["import_revision_id"],
        legacy["import_revision_fingerprint"],
        tenant_id="tenant-1",
        requester="owner-1",
    )
    assert stale["source_currentness"] == "CURRENT"
    assert stale["observation_currentness"] == "STALE"
    assert stale["verified"] is stale["can_modify"] is False
    assert stale["issues"] == json.loads(before)["issues"]
    candidate = restarted.create_eda_import_preview(
        project_id,
        tenant_id="tenant-1",
        requester="owner-1",
        backend="JLCEDA_PRO",
        native_entry_relative_path="board.eprj3",
        selected_native_entries=(),
        expected_head_revision_id=legacy["import_revision_id"],
        expected_head_revision_fingerprint=legacy["import_revision_fingerprint"],
    )
    upgraded = restarted.commit_eda_import_preview(
        project_id,
        candidate["preview_id"],
        candidate["preview_fingerprint"],
        tenant_id="tenant-1",
        requester="owner-1",
    )
    assert upgraded["import_revision_id"] != legacy["import_revision_id"]
    assert upgraded["source_set_fingerprint"] == legacy["source_set_fingerprint"]
    assert upgraded["observation_currentness"] == "CURRENT"
    assert upgraded["verified"] is upgraded["can_modify"] is False
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        after = connection.execute(
            "SELECT body FROM project_eda_import_revisions WHERE import_revision_id=?",
            (legacy["import_revision_id"],),
        ).fetchone()[0]
    assert after == before
    assert source.read_bytes() == b"{"
