"""D2 default composition and real process restart, using isolated SYNTHETIC originals."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from embedded_copilot.api import main
from embedded_copilot.engineering_copilot.schematic_evidence import (
    ParameterCandidate,
    ParameterConditions,
    ParameterLocator,
)
from embedded_copilot.execution_worker.schematic_erc import (
    UnavailableSchematicErcAdapter,
)
from embedded_copilot.services.config import Settings
from tests.engineering_copilot.test_schematic_production_g3 import (
    _runtime_with_approved_plan,
)

HEADERS = {"X-Environment-Tenant": "tenant-1", "X-Copilot-Requester": "engineer-1"}


def prepare_original(tmp_path):
    catalog, project, session, detail = _runtime_with_approved_plan(tmp_path)
    source = detail.hardware_selection.proposal.selected_components[0].source
    root = catalog.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1")
    path = root / "d2-synthetic-original.json"
    original = source.model_dump_json().encode("utf-8")
    path.write_bytes(original)
    mapping = root / ".embrix" / "parameter-documents.json"
    mapping.parent.mkdir(exist_ok=True)
    mapping.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tenant_id": "tenant-1",
                "project_id": project.project_id,
                "documents": [
                    {
                        "kind": "COMPONENT_SOURCE_JSON",
                        "document_uri": source.source_uri,
                        "document_revision": source.source_revision,
                        "relative_path": path.name,
                        "content_sha256": "sha256:"
                        + hashlib.sha256(original).hexdigest(),
                        "source_record_id": source.source_id,
                        "source_record_fingerprint": source.fingerprint,
                        "manufacturer": source.manufacturer,
                        "mpn": source.mpn,
                        "package": source.package,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    value = ParameterCandidate(
        evidence_id="synthetic-d2-supply-min",
        tenant_id="tenant-1",
        project_id=project.project_id,
        conversation_id=session.conversation.conversation_id,
        source_record_id=source.source_id,
        source_record_fingerprint=source.fingerprint,
        manufacturer=source.manufacturer,
        mpn=source.mpn,
        package=source.package,
        document_uri=source.source_uri,
        document_revision=source.source_revision,
        content_sha256="sha256:" + hashlib.sha256(original).hexdigest(),
        locator=ParameterLocator(
            page=1, table="electrical_facts", row="operating_voltage"
        ),
        parameter_key="SUPPLY_MIN",
        bound_kind="MIN",
        value_decimal="3",
        unit="V",
        conditions=ParameterConditions(package=source.package),
    )
    return catalog, project, session, source, path, mapping, value


def default_app(tmp_path, catalog):
    # Existing EDA adapter injection disables tool discovery; parameter port is NOT injected.
    return main.create_app(
        settings=Settings(
            environment="test",
            project_root=tmp_path / "projects",
            desktop_state_database=tmp_path / "app.sqlite3",
            _env_file=None,
        ),
        project_catalog_runtime=catalog,
        hardware_component_source=catalog._hardware_component_source,
        schematic_erc_port=UnavailableSchematicErcAdapter(),
    )


def urls(project, session, value):
    base = f"/api/v1/copilot/projects/{project.project_id}"
    return (
        base
        + f"/conversations/{session.conversation.conversation_id}/schematic-parameters",
        base + f"/schematic-parameters/{value.evidence_id}",
    )


def approve(client, proposal_url, read_url, value):
    response = client.post(
        proposal_url, json=value.model_dump(mode="json"), headers=HEADERS
    )
    assert response.status_code == 200, response.text
    response = client.post(
        read_url + "/review",
        json={"evidence_fingerprint": value.fingerprint, "decision": "APPROVED"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_default_reader_formal_api_review_and_unknown_conditions_do_not_upgrade_claim(
    tmp_path,
):
    catalog, project, session, source, _path, _mapping, value = prepare_original(
        tmp_path
    )
    proposal_url, read_url = urls(project, session, value)
    with TestClient(default_app(tmp_path, catalog)) as client:
        port = catalog._schematic_parameter_port
        assert type(port).__name__ == "ProjectMappedParameterPort"
        approve(client, proposal_url, read_url, value)
        response = client.get(read_url, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["currentness"] == "CURRENT"
        assert (
            response.json()["evidence"]["candidate"]["fingerprint"] == value.fingerprint
        )
        assert port.read_component_source(source, (value,)) == source
        required = ParameterConditions(
            voltage_min="3.2",
            voltage_max="3.4",
            temperature_min="20",
            temperature_max="30",
            mode="I2C_STANDARD",
            package=source.package,
        )
        assert not value.conditions.covers(required)
        assert "SCHEMATIC_VERIFIED" not in response.text
        again = client.post(
            read_url + "/review",
            json={"evidence_fingerprint": value.fingerprint, "decision": "APPROVED"},
            headers=HEADERS,
        )
        assert again.status_code == 200
        conflict = client.post(
            read_url + "/review",
            json={"evidence_fingerprint": value.fingerprint, "decision": "REJECTED"},
            headers=HEADERS,
        )
        assert conflict.status_code == 409
        assert (
            client.get(
                read_url, headers={**HEADERS, "X-Copilot-Requester": "other-owner"}
            ).status_code
            == 403
        )


@pytest.mark.parametrize(
    "change", ["digest", "locator", "mpn", "package", "project", "missing", "condition"]
)
def test_default_reader_rejects_unproven_parameter_without_saving(tmp_path, change):
    catalog, project, session, _source, path, _mapping, value = prepare_original(
        tmp_path
    )
    proposal_url, read_url = urls(project, session, value)
    body = value.model_dump(mode="json")
    body.pop("fingerprint")
    if change == "digest":
        body["content_sha256"] = "sha256:" + "e" * 64
    elif change == "locator":
        body["locator"]["row"] = "not_a_row"
    elif change == "mpn":
        body["mpn"] = "not_the_selected_part"
    elif change == "package":
        body["package"] = "OTHER"
        body["conditions"]["package"] = "OTHER"
    elif change == "project":
        body["project_id"] = "other-project"
    elif change == "missing":
        path.unlink()
    else:
        body["conditions"]["mode"] = "I2C_STANDARD"
    with TestClient(default_app(tmp_path, catalog)) as client:
        response = client.post(proposal_url, json=body, headers=HEADERS)
        assert response.status_code == 422, response.text
        assert client.get(read_url, headers=HEADERS).status_code == 503


@pytest.mark.parametrize("change", ["original", "mapping"])
def test_review_currentness_reopens_original_and_mapping_without_rewriting_history(
    tmp_path, change
):
    catalog, project, session, _source, path, mapping, value = prepare_original(
        tmp_path
    )
    proposal_url, read_url = urls(project, session, value)
    with TestClient(default_app(tmp_path, catalog)) as client:
        approve(client, proposal_url, read_url, value)
        before = client.get(read_url, headers=HEADERS).json()
        if change == "original":
            path.write_bytes(path.read_bytes() + b"\n")
        else:
            doc = json.loads(mapping.read_text(encoding="utf-8"))
            doc["documents"][0]["document_revision"] = "replaced-revision"
            mapping.write_text(json.dumps(doc), encoding="utf-8")
        after = client.get(read_url, headers=HEADERS).json()
        assert before["currentness"] == "CURRENT" and after["currentness"] == "STALE"
        assert after["evidence"] == before["evidence"]
        late = client.post(
            read_url + "/review",
            json={"evidence_fingerprint": value.fingerprint, "decision": "APPROVED"},
            headers=HEADERS,
        )
        assert late.status_code == 409


def test_review_and_mapping_are_revalidated_by_default_composition_in_new_process(
    tmp_path,
):
    catalog, project, session, _source, path, _mapping, value = prepare_original(
        tmp_path
    )
    proposal_url, read_url = urls(project, session, value)
    with TestClient(default_app(tmp_path, catalog)) as client:
        approve(client, proposal_url, read_url, value)
        before = client.get(read_url, headers=HEADERS).json()
    script = textwrap.dedent("""
        import json,os,sys
        from pathlib import Path
        from fastapi.testclient import TestClient
        from embedded_copilot.api.main import create_app
        from embedded_copilot.project_catalog import ProjectCatalogRuntime
        from embedded_copilot.execution_worker.schematic_erc import UnavailableSchematicErcAdapter
        from embedded_copilot.services.config import Settings
        from tests.engineering_copilot.test_schematic_planning_g2 import _catalog
        p=Path(sys.argv[1])
        c=ProjectCatalogRuntime(project_root=p/'projects',state_database=p/'desktop.sqlite3',initialize_async=False,hardware_component_source=_catalog())
        app=create_app(settings=Settings(environment='test',project_root=p/'projects',desktop_state_database=p/'child-app.sqlite3',_env_file=None),project_catalog_runtime=c,hardware_component_source=c._hardware_component_source,schematic_erc_port=UnavailableSchematicErcAdapter())
        with TestClient(app) as client:
            response=client.get(sys.argv[2],headers={'X-Environment-Tenant':'tenant-1','X-Copilot-Requester':'engineer-1'})
            assert response.status_code==200,response.text
            print('D2RECOVERY='+json.dumps({'pid':os.getpid(),'reader':type(c._schematic_parameter_port).__name__,'view':response.json()}))
    """)

    def new_process():
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(tmp_path), read_url],
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=40,
        )
        line = next(
            line
            for line in result.stdout.splitlines()
            if line.startswith("D2RECOVERY=")
        )
        parsed = json.loads(line[len("D2RECOVERY=") :])
        assert parsed["pid"] != os.getpid()
        assert parsed["reader"] == "ProjectMappedParameterPort"
        return parsed["view"]

    current = new_process()
    assert current == before
    path.write_bytes(path.read_bytes() + b"\n")
    stale = new_process()
    assert stale["currentness"] == "STALE"
    assert stale["evidence"] == before["evidence"]
