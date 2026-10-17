"""Offline C1 acceptance against licensed KiCad 8.0.9 and 10.0.0 demos.

The source under acceptance/s06-third-party is third-party material. Every test
copies it into tmp_path; no test edits the preserved original or invokes EDA.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.core.config import Settings
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT / "acceptance/s06-third-party/kicad-pic-programmer-8.0.9/original"
PCB_SHA256 = "b9d90e6184a1e4873d50be65da1faefaf7d003638ed16cef8e0212f247e64da7"
UNSUPPORTED_ORIGINAL = (
    ROOT / "acceptance/s06-third-party/kicad-pic-programmer-10.0.0/original"
)
UNSUPPORTED_PCB_SHA256 = (
    "1ca70b620506818eb0227e2863bbd573d1fec1672072dc43d0dbe8475e69c8fb"
)
ENTRY = "pic_programmer.kicad_pro"
OWNER = {"X-Copilot-Requester": "owner-1", "X-Environment-Tenant": "tenant-1"}
OTHER = {"X-Copilot-Requester": "other-1", "X-Environment-Tenant": "tenant-1"}


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, 8, 0, tzinfo=UTC)


def _setup(
    tmp_path: Path, original: Path = ORIGINAL
) -> tuple[TestClient, str, Path, Path, Path]:
    expected_sha = PCB_SHA256 if original == ORIGINAL else UNSUPPORTED_PCB_SHA256
    assert (
        hashlib.sha256((original / "pic_programmer.kicad_pcb").read_bytes()).hexdigest()
        == expected_sha
    )
    provenance = json.loads(
        (original.parent / "provenance.json").read_text(encoding="utf-8")
    )
    for item in provenance["files"]:
        assert (
            hashlib.sha256((original / item["relative_path"]).read_bytes()).hexdigest()
            == item["sha256"]
        )
    project_root = tmp_path / "projects"
    project_root.mkdir()
    database = tmp_path / "catalog.sqlite3"
    catalog = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=database,
        clock=Clock(),
        initialize_async=False,
    )
    candidate = catalog.preview(
        ProjectCreationRequest(
            name="KiCad third-party demo",
            relative_path="board",
            project_type=ProjectType.STM32,
        ),
        tenant_id="tenant-1",
        requester="owner-1",
    )
    project = catalog.create(
        candidate.preview_id,
        candidate.fingerprint,
        confirmed_at=Clock().now(),
        tenant_id="tenant-1",
        requester="owner-1",
    )
    source = project_root / "board"
    shutil.copytree(original, source, dirs_exist_ok=True)
    app = create_app(
        settings=Settings(
            environment="test", project_root=project_root, _env_file=None
        ),
        project_catalog_runtime=catalog,
    )
    return TestClient(app), project.project_id, source, project_root, database


def _preview(client: TestClient, project_id: str, head: dict | None) -> dict:
    response = client.post(
        f"/api/v1/copilot/projects/{project_id}/eda-import-previews",
        headers=OWNER,
        json={
            "backend": "KICAD",
            "native_entry_relative_path": ENTRY,
            "selected_native_entries": [],
            "expected_head_revision_id": None
            if head is None
            else head["import_revision_id"],
            "expected_head_revision_fingerprint": None
            if head is None
            else head["import_revision_fingerprint"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _commit(client: TestClient, project_id: str, preview: dict) -> dict:
    response = client.post(
        f"/api/v1/copilot/projects/{project_id}/eda-imports",
        headers=OWNER,
        json={
            "preview_id": preview["preview_id"],
            "preview_fingerprint": preview["preview_fingerprint"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _detail(client: TestClient, project_id: str, revision: dict) -> dict:
    response = client.get(
        f"/api/v1/copilot/projects/{project_id}/eda-imports/{revision['import_revision_id']}",
        headers=OWNER,
        params={"fingerprint": revision["import_revision_fingerprint"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_official_kicad_demo_preview_commit_oracle_and_new_process(
    tmp_path: Path,
) -> None:
    client, project_id, source, project_root, database = _setup(tmp_path)
    with client:
        preview = _preview(client, project_id, None)
        manifest = {
            item["relative_path"]: item
            for item in preview["manifest"]
            if item["kind"] == "FILE"
        }
        original_files = {
            path.relative_to(ORIGINAL).as_posix()
            for path in ORIGINAL.rglob("*")
            if path.is_file()
        }
        assert set(manifest) == original_files
        assert manifest["pic_programmer.kicad_pcb"]["sha256"] == PCB_SHA256
        assert (
            preview["native_format_versions"]["pic_programmer.kicad_pcb"] == "20240108"
        )
        # Manual oracle: original board lines 232-238, not output generated by the parser.
        observed = [
            item
            for item in preview["objects"]
            if item["native_id"] == "00000000-0000-0000-0000-000054032b86"
        ]
        assert len(observed) == 1
        assert observed[0]["locator"] == "/kicad_pcb/footprint[0]"
        assert observed[0]["reference"] == "C1"
        assert (
            observed[0]["library_id"]
            == "Capacitor_THT:CP_Axial_L18.0mm_D6.5mm_P25.00mm_Horizontal"
        )
        assert (
            observed[0]["position_x_mm"],
            observed[0]["position_y_mm"],
            observed[0]["rotation_degrees"],
        ) == (110.49, 78.867, 180.0)
        assert "pads" not in observed[0] and "nets" not in observed[0]
        assert preview["semantic_completeness"] == "PARTIAL"
        assert preview["verified"] is False and preview["can_modify"] is False
        revision = _commit(client, project_id, preview)
        assert revision["commit_outcome"] == "CREATED"
        assert (
            revision["source_currentness"]
            == revision["observation_currentness"]
            == "CURRENT"
        )
        assert _detail(client, project_id, revision)["objects"] == preview["objects"]

    # Fresh Python interpreter, fresh Catalog runtime, formal production app/routes.
    probe = """import json,sys
from pathlib import Path
from fastapi.testclient import TestClient
from embedded_copilot.api.main import create_app
from embedded_copilot.core.config import Settings
from embedded_copilot.project_catalog import ProjectCatalogRuntime
root,db,pid,expected=sys.argv[1:]
runtime=ProjectCatalogRuntime(project_root=Path(root),state_database=Path(db),initialize_async=False)
app=create_app(settings=Settings(environment="test",project_root=Path(root),_env_file=None),project_catalog_runtime=runtime)
headers={"X-Copilot-Requester":"owner-1","X-Environment-Tenant":"tenant-1"}
with TestClient(app) as client:
  path=f"/api/v1/copilot/projects/{pid}/eda-imports"
  listing=client.get(path,headers=headers,params={"backend":"KICAD","native_entry_relative_path":"pic_programmer.kicad_pro"})
  assert listing.status_code==200,listing.text
  selected=next(item for item in listing.json()["items"] if item["import_revision_id"]==expected)
  detail=client.get(path+"/"+selected["import_revision_id"],headers=headers,params={"fingerprint":selected["import_revision_fingerprint"]})
  assert detail.status_code==200,detail.text
  print(json.dumps({"id":selected["import_revision_id"],"fingerprint":selected["import_revision_fingerprint"],"source":detail.json()["source_currentness"],"issues":len(detail.json()["issues"]),"verified":detail.json()["verified"],"can_modify":detail.json()["can_modify"]}))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            probe,
            str(project_root),
            str(database),
            project_id,
            revision["import_revision_id"],
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    restarted = json.loads(result.stdout.splitlines()[-1])
    assert restarted["id"] == revision["import_revision_id"]
    assert restarted["fingerprint"] == revision["import_revision_fingerprint"]
    assert restarted["source"] == "CURRENT" and restarted["issues"] > 0
    assert restarted["verified"] is False and restarted["can_modify"] is False
    assert source.is_dir()


def test_official_kicad_demo_stale_reuse_and_a_b_a(tmp_path: Path) -> None:
    client, project_id, source, _, _ = _setup(tmp_path)
    with client:
        a_preview = _preview(client, project_id, None)
        a = _commit(client, project_id, a_preview)
        extra = source / "unknown-native-extension.s06"
        extra.write_bytes(b"isolated change B")
        assert _detail(client, project_id, a)["source_currentness"] == "STALE"
        b = _commit(client, project_id, _preview(client, project_id, a))
        assert b["import_revision_id"] != a["import_revision_id"]
        extra.unlink()
        assert _detail(client, project_id, b)["source_currentness"] == "STALE"
        a2 = _commit(client, project_id, _preview(client, project_id, b))
        assert a2["import_revision_fingerprint"] == a["import_revision_fingerprint"]
        assert (
            len(
                {
                    a["import_revision_id"],
                    b["import_revision_id"],
                    a2["import_revision_id"],
                }
            )
            == 3
        )
        p1 = _preview(client, project_id, a2)
        p2 = _preview(client, project_id, a2)
        reused1 = _commit(client, project_id, p1)
        reused2 = _commit(client, project_id, p2)
        assert reused1["commit_outcome"] == reused2["commit_outcome"] == "REUSED_ACTIVE"
        assert (
            reused1["import_revision_id"]
            == reused2["import_revision_id"]
            == a2["import_revision_id"]
        )
        old_retry = _commit(client, project_id, a_preview)
        assert old_retry["commit_outcome"] == "ALREADY_COMMITTED"
        assert (
            old_retry["import_revision_id"] == a["import_revision_id"]
            and old_retry["active"] is False
        )
        listing = client.get(
            f"/api/v1/copilot/projects/{project_id}/eda-imports",
            headers=OWNER,
            params={"backend": "KICAD", "native_entry_relative_path": ENTRY},
        )
        assert listing.status_code == 200
        assert {item["import_revision_id"] for item in listing.json()["items"]} == {
            a["import_revision_id"],
            b["import_revision_id"],
            a2["import_revision_id"],
        }
        assert _detail(client, project_id, a2)["source_currentness"] == "CURRENT"


def test_official_kicad_demo_auth_and_changed_preview(tmp_path: Path) -> None:
    client, project_id, source, _, _ = _setup(tmp_path)
    path = f"/api/v1/copilot/projects/{project_id}/eda-import-previews"
    body = {
        "backend": "KICAD",
        "native_entry_relative_path": ENTRY,
        "selected_native_entries": [],
        "expected_head_revision_id": None,
        "expected_head_revision_fingerprint": None,
    }
    with client:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, headers=OTHER, json=body).status_code == 403
        assert (
            client.post(path, headers=OWNER, json={**body, "objects": []}).status_code
            == 422
        )
        preview = _preview(client, project_id, None)
        (source / "pic_sockets.kicad_sch").unlink()
        missing = _preview(client, project_id, None)
        assert any(item["kind"] == "MISSING_DEPENDENCY" for item in missing["manifest"])
        assert any(item["code"] == "MISSING_DEPENDENCY" for item in missing["issues"])
        assert (
            client.post(
                f"/api/v1/copilot/projects/{project_id}/eda-imports",
                headers=OWNER,
                json={
                    "preview_id": preview["preview_id"],
                    "preview_fingerprint": preview["preview_fingerprint"],
                },
            ).status_code
            == 409
        )
        shutil.copy2(
            ORIGINAL / "pic_sockets.kicad_sch", source / "pic_sockets.kicad_sch"
        )
        a = _commit(client, project_id, preview)
        assert a["source_currentness"] == "CURRENT"
        table = source / "fp-lib-table"
        table.write_bytes(table.read_bytes() + b"\n")
        assert _detail(client, project_id, a)["source_currentness"] == "STALE"
        shutil.copy2(ORIGINAL / "fp-lib-table", table)
        assert _detail(client, project_id, a)["source_currentness"] == "CURRENT"

        second = client.app.state.project_catalog_runtime.preview(
            ProjectCreationRequest(
                name="Other board",
                relative_path="other",
                project_type=ProjectType.STM32,
            ),
            tenant_id="tenant-1",
            requester="owner-1",
        )
        other = client.app.state.project_catalog_runtime.create(
            second.preview_id,
            second.fingerprint,
            confirmed_at=Clock().now(),
            tenant_id="tenant-1",
            requester="owner-1",
        )
        cross = client.post(
            f"/api/v1/copilot/projects/{other.project_id}/eda-imports",
            headers=OWNER,
            json={
                "preview_id": preview["preview_id"],
                "preview_fingerprint": preview["preview_fingerprint"],
            },
        )
        assert cross.status_code == 422


def test_official_kicad_demo_old_head_cas(tmp_path: Path) -> None:
    client, project_id, source, _, _ = _setup(tmp_path)
    with client:
        a = _commit(client, project_id, _preview(client, project_id, None))
        (source / "added.unknown").write_bytes(b"B")
        first = _preview(client, project_id, a)
        second = _preview(client, project_id, a)
        b = _commit(client, project_id, first)
        assert b["commit_outcome"] == "CREATED"
        response = client.post(
            f"/api/v1/copilot/projects/{project_id}/eda-imports",
            headers=OWNER,
            json={
                "preview_id": second["preview_id"],
                "preview_fingerprint": second["preview_fingerprint"],
            },
        )
        assert response.status_code == 409
        assert response.json()["error"] == "IMPORT_HEAD_CHANGED"


def test_official_kicad_10_demo_stays_inventory_for_unhandled_version(
    tmp_path: Path,
) -> None:
    client, project_id, _, _, _ = _setup(tmp_path, UNSUPPORTED_ORIGINAL)
    with client:
        preview = _preview(client, project_id, None)
        assert (
            preview["native_format_versions"]["pic_programmer.kicad_pcb"] == "UNKNOWN"
        )
        assert preview["objects"] == []
        assert any(
            item["code"] == "UNSUPPORTED_FORMAT_VERSION" for item in preview["issues"]
        )
        revision = _commit(client, project_id, preview)
        assert revision["source_currentness"] == "CURRENT"
        assert revision["semantic_completeness"] == "UNKNOWN"
        assert revision["verified"] is False and revision["can_modify"] is False
