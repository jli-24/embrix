"""Offline stdio bridge: real React fetch -> FastAPI TestClient -> canonical SQLite.

No listening socket, provider transport, native application, or EDA executable.
The initial sources and application are explicitly qualified test fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUTPUT = sys.stdout
sys.stdout = sys.stderr
from tests.s02_offline_guard import pytest_configure, pytest_unconfigure

pytest_configure()
from embedded_copilot.engineering_copilot import SchematicPlanningService
from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime
from embedded_copilot.project_catalog.source_publication import (
    PublishedComponentSourcePort,
)
from tests.api.test_selection_revision_routes import client_for
from tests.project_catalog.test_predesign_publication import _command, _service


def emit(value):
    print(json.dumps(value, ensure_ascii=False), file=OUTPUT, flush=True)


def start(root, mode):
    identity = root / "identity.json"
    if mode == "init":
        from tests.engineering_copilot.test_schematic_production_g3 import (
            _runtime_with_approved_plan,
        )

        catalog, project, session, _ = _runtime_with_approved_plan(root)
        from embedded_copilot.component_recommendation.contracts import (
            ComponentSourceRecord,
            SupportingComponentRequirement,
            SupportingConnectionExpectation,
        )
        from embedded_copilot.project_catalog.source_publication import (
            ComponentSourcePublishCommandV1,
            TrustedComponentSourcePublisher,
        )
        from tests.engineering_copilot.test_s02_supporting_fulfillment import (
            _support_part,
        )

        source = catalog._hardware_component_source
        sensor = source.current_publication(project.project_id, "catalog-sensor").record
        support = SupportingComponentRequirement.create(
            role="DECOUPLING",
            description="Offline local bypass",
            quantity=1,
            evidence_refs=("offline-desktop-tree",),
            connection_expectations=(
                SupportingConnectionExpectation(
                    target_pin_number="1",
                    target_net_name="3V3",
                    signal_pin_number="1",
                    signal_net_name="3V3",
                    return_pin_number="2",
                    return_net_name="GND",
                ),
            ),
        )
        parent = ComponentSourceRecord.create(
            **{
                **sensor.model_dump(mode="python", exclude={"fingerprint"}),
                "source_id": "catalog-sensor-desktop-alt",
                "description": "Offline alternate parent",
                "supporting_requirements": (support,),
            }
        )
        publisher = TrustedComponentSourcePublisher(
            catalog._repository,
            tenant_id="tenant-1",
            project_id=project.project_id,
            publisher_id=source.publisher_id,
        )
        for index, record in enumerate(
            (parent, _support_part("DECOUPLING", "C-DESKTOP-100N"))
        ):
            publisher.publish(
                ComponentSourcePublishCommandV1(
                    schema_version=1,
                    request_id=f"desktop-alt-source-{index}",
                    source_id=record.source_id,
                    expected_head_publication_fingerprint=None,
                    record=record,
                )
            )
        detail = catalog.conversation(
            project.project_id,
            session.conversation.conversation_id,
            tenant_id="tenant-1",
            requester="engineer-1",
        )
        plan = detail.schematic_planning.approved_plan
        identity.write_text(
            json.dumps(
                {
                    "project": detail.project_id,
                    "conversation": detail.conversation_id,
                    "old_plan_id": plan.plan_id,
                    "old_plan_fp": plan.fingerprint,
                }
            ),
            encoding="utf-8",
        )
    else:
        ids = json.loads(identity.read_text(encoding="utf-8"))
        catalog = ProjectCatalogRuntime(
            project_root=root / "projects",
            state_database=root / "desktop.sqlite3",
            initialize_async=False,
        )
        catalog.set_hardware_component_source(
            PublishedComponentSourcePort(
                catalog._repository,
                tenant_id="tenant-1",
                publisher_id="g3-fixture-publisher",
            )
        )
        detail = catalog.conversation(
            ids["project"],
            ids["conversation"],
            tenant_id="tenant-1",
            requester="engineer-1",
        )
    client = client_for(catalog, detail)
    client.app.state.circuit_publication_service = _service(catalog)
    from embedded_copilot.api.desktop_workspace_projection_routes import (
        router as workspace_router,
    )
    from embedded_copilot.desktop_workspace_projection import (
        DesktopWorkspaceProjectionService,
    )

    client.app.include_router(workspace_router)
    client.app.state.desktop_workspace_projection = DesktopWorkspaceProjectionService(
        catalog=catalog
    )
    ids = json.loads(identity.read_text(encoding="utf-8"))
    prefix = (
        f"/api/v1/copilot/projects/{ids['project']}/conversations/{ids['conversation']}"
    )
    return catalog, client, ids, prefix


def checked(client, path, body):
    response = client.post(path, json=body)
    if response.status_code != 200:
        raise AssertionError(f"{path}: {response.status_code} {response.text}")
    return response.json()


def inputs(catalog, client, ids, prefix):
    """Only read and derive form values; never propose or approve a replacement."""
    scope = {"tenant_id": "tenant-1", "requester": "engineer-1"}
    detail = catalog.conversation(ids["project"], ids["conversation"], **scope)
    command = _command(detail).model_dump(mode="json")
    current = tuple(
        item
        for item in catalog.current_circuit_intents(ids["project"], **scope)
        if catalog.circuit_source_current(
            ids["project"], item.object_id, item.revision, **scope
        )
    )
    if len(current) != 1:
        return {"circuit_command": command}
    circuit = current[0]
    detail = catalog.conversation(ids["project"], ids["conversation"], **scope)
    selection = detail.hardware_selection
    truth = detail.design_truth
    plan = SchematicPlanningService().create_proposal(
        detail.architecture_design,
        detail.requirement_context,
        selection.hardware_truth,
        selection.bom,
        design_truth=truth,
        conversation_id=detail.conversation_id,
        circuit_intent=circuit,
        selection=selection,
    )
    domain = next(x for x in plan.power_domains if x.name != "GND")
    refs = [
        next(
            x.binding_id
            for x in truth.evidence_bindings
            if x.entity_id == component and x.source.value == "DATASHEET"
        )
        for component in domain.source_component_ids + domain.consumer_component_ids
    ]
    return {
        "circuit_command": command,
        "supply": {
            "power_domain_id": domain.domain_id,
            "sources": list(domain.source_component_ids),
            "consumers": list(domain.consumer_component_ids),
            "evidence": refs,
        },
    }


def verify(catalog, client, ids, prefix):
    history = client.get(prefix + "/schematic-plan/currentness?schema_version=2").json()
    assert history["head"]["planning"]["status"] == "APPROVED", history
    assert history["head"]["currentness"] == "CURRENT", history
    assert history["legacy"]["plan_id"] == ids["old_plan_id"], history
    assert history["legacy"]["currentness"] == "STALE", history
    return {"plan": history["head"], "old_plan": history["legacy"]}


def finish(catalog, client, ids, prefix):
    scope = {"tenant_id": "tenant-1", "requester": "engineer-1"}
    detail = catalog.conversation(ids["project"], ids["conversation"], **scope)
    current = catalog.current_circuit_intents(ids["project"], **scope)[0]
    command = _command(detail).model_copy(
        update={
            "idempotency_key": "desktop-new-circuit",
            "object_id": current.object_id,
            "base_revision": current.revision,
            "base_fingerprint": current.fingerprint,
        }
    )
    project = f"/api/v1/copilot/projects/{ids['project']}"
    preview = checked(
        client, project + "/circuit-intents/preview", command.model_dump(mode="json")
    )
    checked(
        client,
        project + "/circuit-intents/decision",
        {
            key: preview[key]
            for key in (
                "purpose",
                "proposal_id",
                "proposal_fingerprint",
                "source_fingerprint",
                "base_revision",
                "base_fingerprint",
            )
        }
        | {"decision": "APPROVE"},
    )
    circuit = next(
        item
        for item in catalog.current_circuit_intents(ids["project"], **scope)
        if catalog.circuit_source_current(
            ids["project"], item.object_id, item.revision, **scope
        )
    )
    detail = catalog.conversation(ids["project"], ids["conversation"], **scope)
    selection = detail.hardware_selection
    truth = detail.design_truth
    plan = SchematicPlanningService().create_proposal(
        detail.architecture_design,
        detail.requirement_context,
        selection.hardware_truth,
        selection.bom,
        design_truth=truth,
        conversation_id=detail.conversation_id,
        circuit_intent=circuit,
        selection=selection,
    )
    domain = next(x for x in plan.power_domains if x.name != "GND")
    supply = next(x for x in plan.nets if x.name == domain.name)
    ground = next(x for x in plan.nets if x.name == "GND")
    refs = [
        next(
            x.binding_id
            for x in truth.evidence_bindings
            if x.entity_id == component and x.source.value == "DATASHEET"
        )
        for component in domain.source_component_ids + domain.consumer_component_ids
    ]
    candidate = checked(
        client,
        prefix + "/project-supply",
        {
            "design_truth_id": truth.truth_id,
            "design_truth_fingerprint": truth.fingerprint,
            "selection_id": selection.proposal.proposal_id,
            "selection_fingerprint": selection.proposal.fingerprint,
            "circuit_id": circuit.object_id,
            "circuit_fingerprint": circuit.fingerprint,
            "plan_id": detail.engineering_plan.plan_id,
            "plan_fingerprint": detail.engineering_plan.fingerprint,
            "relations": [
                {
                    "power_domain_id": domain.domain_id,
                    "source_component_refs": list(domain.source_component_ids),
                    "consumer_component_refs": list(domain.consumer_component_ids),
                    "nominal_voltage_v": "3.3",
                    "supply_net_ref": supply.net_id,
                    "ground_net_ref": ground.net_id,
                    "evidence_refs": refs,
                }
            ],
        },
    )["candidate"]
    checked(
        client,
        prefix + f"/project-supply/{candidate['candidate_id']}/review",
        {"candidate_fingerprint": candidate["fingerprint"], "decision": "APPROVED"},
    )
    history = client.get(prefix + "/schematic-plan/currentness?schema_version=2").json()
    assert (
        history["legacy"]["plan_id"] == ids["old_plan_id"]
        and history["legacy"]["currentness"] == "STALE"
    ), history
    replacement = checked(
        client,
        prefix + "/schematic-plan/proposal",
        {
            "schema_version": 2,
            "request_id": "desktop-replan",
            "expected_head_revision_id": None,
            "expected_head_plan_id": ids["old_plan_id"],
            "expected_head_plan_fingerprint": ids["old_plan_fp"],
        },
    )
    approved = checked(
        client,
        prefix + "/schematic-plan/decision",
        {
            "schema_version": 2,
            "revision_id": replacement["revision_id"],
            "plan_id": replacement["plan_id"],
            "plan_fingerprint": replacement["plan_fingerprint"],
            "decision": "APPROVED",
        },
    )
    history = catalog.schematic_plan_legacy_snapshot(
        ids["project"], ids["conversation"], **scope
    )
    assert approved["currentness"] == "CURRENT" and history["currentness"] == "STALE"
    return {
        "plan": approved,
        "old_plan": history,
        "conversation": client.get(prefix).json(),
    }


if __name__ == "__main__":
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    catalog, client, ids, prefix = start(root, sys.argv[2])
    emit({"ready": True, "prefix": prefix, "conversation": client.get(prefix).json()})
    try:
        for line in sys.stdin:
            command = json.loads(line)
            try:
                if command.get("operation") == "STOP":
                    emit({"id": command["id"], "status": 200, "body": {}})
                    break
                if command.get("operation") in {"UI_INPUTS", "VERIFY"}:
                    operation = (
                        inputs if command["operation"] == "UI_INPUTS" else verify
                    )
                    before = catalog._repository._database.read_bytes()
                    body = operation(catalog, client, ids, prefix)
                    assert catalog._repository._database.read_bytes() == before
                    emit({"id": command["id"], "status": 200, "body": body})
                    continue
                if command.get("operation") == "FINISH":
                    emit(
                        {
                            "id": command["id"],
                            "status": 200,
                            "body": finish(catalog, client, ids, prefix),
                        }
                    )
                    continue
                response = client.request(
                    command["method"],
                    command["path"],
                    **({"json": command["body"]} if "body" in command else {}),
                )
                emit(
                    {
                        "id": command["id"],
                        "status": response.status_code,
                        "body": response.json(),
                    }
                )
            except Exception as error:  # noqa: BLE001 - report a failure frame rather than strand the test client
                emit(
                    {
                        "id": command["id"],
                        "status": 500,
                        "body": {"error": type(error).__name__, "detail": str(error)},
                    }
                )
    finally:
        client.close()
        pytest_unconfigure()
