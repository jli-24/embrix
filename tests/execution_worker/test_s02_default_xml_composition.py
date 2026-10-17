"""Default XML composition with a fake binary and runner; never real EDA."""
import subprocess
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from embedded_copilot.api import main
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceApplyPort,
)
from embedded_copilot.services.config import Settings
from tests.execution_worker.test_s02_offline_wiring import (
    HEADERS,
    XML,
    prepare,
    wait_for,
)


def test_default_xml_adapter_is_bound_and_runs_only_after_separate_approval(tmp_path, monkeypatch):
    catalog, project, session, workspace, execution, write_id, now = prepare(tmp_path, monkeypatch)
    binary = tmp_path / "tool" / "kicad-cli.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"OFFLINE_COMPOSITION_FAKE_BINARY")
    monkeypatch.setattr(main, "discover_kicad_cli", lambda **kw: binary)
    monkeypatch.setattr(main, "probe_kicad_cli_version", lambda *a, **kw: "10.0.5")
    for name in ("discover_kicad_footprint_root", "discover_kicad_symbol_root", "discover_kicad_pcb_drc_command", "discover_kicad_pcb_parse_command"):
        monkeypatch.setattr(main, name, lambda *a, **kw: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("real discovery/process must not run")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        Path(argv[argv.index("--output") + 1]).write_bytes(XML)
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    from embedded_copilot.execution_worker import schematic_xml
    monkeypatch.setattr(schematic_xml, "run_kicad_cli", runner)
    app = main.create_app(settings=Settings(environment="test", project_root=tmp_path / "projects", desktop_state_database=tmp_path / "app.sqlite3", _env_file=None),
        hardware_component_source=catalog._hardware_component_source, project_catalog_runtime=catalog,
        workspace_apply_port=ProjectCatalogWorkspaceApplyPort(service=workspace, proposals=None), execution_operation_port=execution)
    base = f"/api/v1/copilot/projects/{project.project_id}/conversations/{session.conversation.conversation_id}"
    with TestClient(app) as client:
        wait_for(lambda: execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=write_id))
        parse = wait_for(lambda: next((op for op in execution.store.list_operations("tenant-1", project.project_id, limit=100) if op.operation_type.value == "KICAD_SCHEMATIC_PARSE"), None))
        assert calls == []
        assert execution.get_status(tenant_id="tenant-1", project_id=project.project_id, operation_id=parse.operation_id).status.value == "WAIT_APPROVAL"
        response = client.post(f"/api/v1/copilot/operations/{parse.operation_id}/approval", params={"project_id": project.project_id}, headers=HEADERS,
            json={"reviewer_id": "engineer-1", "nonce": "default-parse-only", "expires_at": (now + timedelta(minutes=5)).isoformat()})
        assert response.status_code == 200, response.text
        receipt = wait_for(lambda: execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=parse.operation_id))
        assert receipt.status.value == "SUCCEEDED"
        assert receipt.schematic_parse_result.parse_status == "PASS"
        assert len(calls) == 1
        assert receipt.schematic_parse_result.tool_version == "10.0.5"
        readback = client.post(base + "/schematic-evidence/operations", headers=HEADERS, json={"kind": "READBACK"})
        assert readback.status_code == 200, readback.text
        assert len(calls) == 1
        response = client.post(f"/api/v1/copilot/operations/{readback.json()['operation_id']}/approval", params={"project_id": project.project_id}, headers=HEADERS,
            json={"reviewer_id": "engineer-1", "nonce": "default-readback-only", "expires_at": (now + timedelta(minutes=5)).isoformat()})
        assert response.status_code == 200, response.text
        result = wait_for(lambda: execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=readback.json()['operation_id']))
        assert result.status.value == "FAILED"
        assert result.schematic_readback_result.parse_status == "UNKNOWN"
        assert len(calls) == 2
