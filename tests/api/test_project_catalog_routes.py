from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity
from embedded_copilot.api.project_catalog_routes import router
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    SystemWorkspaceClock,
    WorkspaceApplyService,
)
from embedded_copilot.native_shell.handoff_telemetry import (
    LOGGER_NAME as HANDOFF_LOGGER_NAME,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectTargetNotWritable,
)

NOW = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


class PermissionDeniedCatalog:
    def create(self, *args, **kwargs):
        raise PermissionError("permission denied")


class TargetNotWritableCatalog:
    def create(self, *args, **kwargs):
        raise ProjectTargetNotWritable("project target root is not writable")


def app(tmp_path: Path, *, available: bool = True, location_allow_list: tuple[Path, ...] = ()) -> FastAPI:
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    application = FastAPI()
    application.state.project_catalog_runtime = (
        ProjectCatalogRuntime(
            project_root=root,
            state_database=tmp_path / "state" / "desktop.sqlite3",
            metadata_directory=tmp_path / "metadata",
            clock=Clock(),
            initialize_async=False,
            location_allow_list=location_allow_list,
        )
        if available
        else None
    )

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-1"
        return await call_next(request)

    application.include_router(router)
    return application


HEADERS = {
    "X-Copilot-Requester": "engineer-1",
    "X-Environment-Tenant": "tenant-1",
}


def test_brownfield_build_api_uses_attached_catalog_project_and_review(tmp_path: Path) -> None:
    source = tmp_path / "projects" / "firmware"
    source.mkdir(parents=True)
    for path in (
        "CMakeLists.txt", "cmake/arm-none-eabi.cmake", "src/main.c", "startup.s", "system.c",
    ):
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("source\n", encoding="utf-8")
    fixture_time_ns = int(NOW.timestamp()) * 1_000_000_000
    os.utime(source, ns=(fixture_time_ns, fixture_time_ns))
    application = app(tmp_path)
    catalog = application.state.project_catalog_runtime
    workspace = WorkspaceApplyService(
        resolver=ProjectCatalogWorkspaceTargetResolver(catalog),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=SystemWorkspaceClock(),
    )
    catalog.set_workspace_snapshot_source(workspace)
    with TestClient(application) as client:
        location = client.get("/api/v1/copilot/projects/locations", headers=HEADERS).json()[0]
        directory = client.get(
            f"/api/v1/copilot/projects/locations/{location['location_id']}/directories", headers=HEADERS,
        ).json()["items"][0]
        preview = client.post("/api/v1/copilot/projects/previews", headers=HEADERS, json={
            "name": "Existing Firmware", "project_type": "STM32", "creation_mode": "ATTACH_EXISTING",
            "parent_location": {"location_id": location["location_id"], "fingerprint": location["fingerprint"]},
            "existing_directory": {"directory_id": directory["directory_id"], "fingerprint": directory["fingerprint"]},
        })
        assert preview.status_code == 200
        project = client.post("/api/v1/copilot/projects", headers=HEADERS, json={
            "preview_id": preview.json()["preview_id"],
            "preview_fingerprint": preview.json()["fingerprint"],
            "confirmed_at": NOW.isoformat(),
        })
        assert project.status_code == 202
        project_id = project.json()["project_id"]
        before = client.get(f"/api/v1/copilot/projects/{project_id}/brownfield-build", headers=HEADERS)
        assert before.status_code == 404
        scanned = client.get(f"/api/v1/copilot/projects/{project_id}/brownfield-build/preview", headers=HEADERS)
        assert scanned.status_code == 200
        assert "src/main.c" in scanned.json()["source_files"]
        proposed = client.post(f"/api/v1/copilot/projects/{project_id}/brownfield-build/proposal", headers=HEADERS, json={
            "workspace_fingerprint": scanned.json()["workspace_fingerprint"],
            "target_platform": "STM32", "mcu_family": "STM32F1", "mcu_exact_part": "STM32F103C8T6",
            "source_layout": ["src/main.c"],
            "configuration": {
                "target_kind": "STM32F103C8T6", "build_system": "CMAKE",
                "toolchain_id": "arm-none-eabi-gcc", "toolchain_version": "1.0", "profile": "DEBUG",
                "build_directory": "build", "cmake_project": "CMakeLists.txt",
                "cmake_toolchain_file": "cmake/arm-none-eabi.cmake",
                "startup_source": "startup.s", "system_source": "system.c",
            },
        })
        assert proposed.status_code == 201
        assert proposed.json()["plan"]["status"] == "PROPOSED"
        assert proposed.json()["review"] is None
        approved = client.post(f"/api/v1/copilot/projects/{project_id}/brownfield-build/decision", headers=HEADERS, json={
            "proposal_fingerprint": proposed.json()["proposal_fingerprint"], "decision": "APPROVED",
        })
        assert approved.status_code == 200
        assert approved.json()["review"]["status"] == "APPROVED"
        current = client.get(f"/api/v1/copilot/projects/{project_id}/brownfield-build", headers=HEADERS)
        assert current.status_code == 200
        assert current.json()["fingerprint"] == approved.json()["fingerprint"]


def test_project_api_preview_create_list_and_context(tmp_path: Path) -> None:
    with TestClient(app(tmp_path)) as client:
        preview = client.post(
            "/api/v1/copilot/projects/previews",
            headers=HEADERS,
            json={
                "name": "Sensor Gateway",
                "relative_path": "sensor-gateway",
                "project_type": "ESP_IDF",
            },
        )
        created = client.post(
            "/api/v1/copilot/projects",
            headers=HEADERS,
            json={
                "preview_id": preview.json()["preview_id"],
                "preview_fingerprint": preview.json()["fingerprint"],
                "confirmed_at": "2026-08-11T08:00:00Z",
            },
        )
        listed = client.get("/api/v1/copilot/projects", headers=HEADERS)
        context = client.get(
            f"/api/v1/copilot/projects/{created.json()['project_id']}/context",
            headers=HEADERS,
        )

    assert preview.status_code == 200
    assert created.status_code == 202
    assert listed.json()[0]["name"] == "Sensor Gateway"
    assert context.status_code == 200
    assert "workspace" not in created.text.lower()
    assert str(tmp_path) not in created.text


def test_project_catalog_reads_are_private_to_project_owner(tmp_path: Path) -> None:
    owner_headers = HEADERS
    other_headers = {**HEADERS, "X-Copilot-Requester": "engineer-2"}
    with TestClient(app(tmp_path)) as client:
        projects = {}
        for headers, name, relative_path in (
            (owner_headers, "Owner Project", "owner-project"),
            (other_headers, "Other Project", "other-project"),
        ):
            preview = client.post(
                "/api/v1/copilot/projects/previews",
                headers=headers,
                json={"name": name, "relative_path": relative_path, "project_type": "ESP_IDF"},
            )
            assert preview.status_code == 200
            created = client.post(
                "/api/v1/copilot/projects",
                headers=headers,
                json={
                    "preview_id": preview.json()["preview_id"],
                    "preview_fingerprint": preview.json()["fingerprint"],
                    "confirmed_at": NOW.isoformat(),
                },
            )
            assert created.status_code == 202
            projects[name] = created.json()["project_id"]

        owner_id = projects["Owner Project"]
        other_id = projects["Other Project"]
        owner_list = client.get("/api/v1/copilot/projects", headers=owner_headers)
        other_list = client.get("/api/v1/copilot/projects", headers=other_headers)
        owner_get = client.get(f"/api/v1/copilot/projects/{owner_id}", headers=owner_headers)
        owner_context = client.get(f"/api/v1/copilot/projects/{owner_id}/context", headers=owner_headers)
        other_get = client.get(f"/api/v1/copilot/projects/{owner_id}", headers=other_headers)
        other_context = client.get(f"/api/v1/copilot/projects/{owner_id}/context", headers=other_headers)

    assert owner_list.status_code == other_list.status_code == 200
    assert {item["project_id"] for item in owner_list.json()} == {owner_id}
    assert {item["project_id"] for item in other_list.json()} == {other_id}
    assert owner_get.status_code == owner_context.status_code == 200
    assert other_get.status_code == other_context.status_code == 403
    assert other_get.json()["error"] == other_context.json()["error"] == "PROJECT_CATALOG_FORBIDDEN"


def test_project_api_exposes_safe_locations_and_accepts_new_preview_contract(tmp_path: Path) -> None:
    with TestClient(app(tmp_path)) as client:
        locations = client.get("/api/v1/copilot/projects/locations", headers=HEADERS)
        location = locations.json()[0]
        preview = client.post(
            "/api/v1/copilot/projects/previews",
            headers=HEADERS,
            json={
                "name": "循迹智能小车",
                "location": {"location_id": location["location_id"], "fingerprint": location["fingerprint"]},
                "project_type": "STM32",
                "creation_mode": "CREATE_NEW",
                "existing_directory": None,
            },
        )

    assert locations.status_code == 200
    assert str(tmp_path) not in locations.text
    assert preview.status_code == 200
    assert preview.json()["directory_name"] == "循迹智能小车"
    assert preview.json()["creation_mode"] == "CREATE_NEW"
    assert "relative_path" in preview.json()  # one-version legacy response compatibility


def test_project_api_accepts_parent_location_contract_without_path_leakage(tmp_path: Path) -> None:
    with TestClient(app(tmp_path)) as client:
        location = client.get("/api/v1/copilot/projects/locations", headers=HEADERS).json()[0]
        preview = client.post(
            "/api/v1/copilot/projects/previews",
            headers=HEADERS,
            json={
                "name": "循迹智能小车",
                "parent_location": {"location_id": location["location_id"], "fingerprint": location["fingerprint"]},
                "project_type": "STM32",
                "creation_mode": "CREATE_NEW",
                "existing_directory": None,
            },
        )

    assert preview.status_code == 200
    assert preview.json()["name"] == "循迹智能小车"
    assert preview.json()["location"]["display_label"] == "Embedded Projects"
    assert str(tmp_path) not in preview.text
    assert "parent_location" not in preview.text


def test_project_api_registers_allowed_location_without_path_leakage(tmp_path: Path, monkeypatch) -> None:
    simulated_home = tmp_path / "home"
    simulated_home.mkdir()
    allowed = tmp_path / "stm32-projects"
    allowed.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))

    with TestClient(app(tmp_path, location_allow_list=(allowed,))) as client:
        response = client.post(
            "/api/v1/copilot/projects/locations",
            headers=HEADERS,
            json={"path": str(allowed), "display_label": "STM32 Projects"},
        )

    assert response.status_code == 201
    assert response.json()["display_label"] == "STM32 Projects"
    assert str(allowed) not in response.text


def test_project_api_maps_unavailable_validation_and_identity_errors(tmp_path: Path) -> None:
    with TestClient(app(tmp_path, available=False)) as client:
        unavailable = client.get("/api/v1/copilot/projects", headers=HEADERS)
    with TestClient(app(tmp_path)) as client:
        invalid = client.post(
            "/api/v1/copilot/projects/previews",
            headers=HEADERS,
            json={"name": "Bad", "relative_path": "../outside", "project_type": "ESP_IDF"},
        )

    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "PROJECT_CATALOG_UNAVAILABLE"
    assert invalid.status_code == 422


def test_execution_and_source_decision_routes_use_trusted_identity(tmp_path: Path) -> None:
    application = app(tmp_path)
    runtime = application.state.project_catalog_runtime
    runtime.decide_execution_authorization = Mock(return_value={"status": "PENDING"})
    runtime.decide_firmware_source_generation = Mock(return_value={"status": "QUEUED"})
    bridge = object()
    application.state.firmware_source_execution_bridge = bridge

    with TestClient(application) as client:
        authorization = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/execution-authorization/request-1/decision",
            headers=HEADERS,
            json={"decision": "APPROVED"},
        )
        generation = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/firmware-source-generation/decision",
            headers=HEADERS,
            json={"decision": "APPROVED"},
        )
        rejected_extra = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/firmware-source-generation/decision",
            headers=HEADERS,
            json={"decision": "APPROVED", "reviewer": "attacker"},
        )

    assert authorization.status_code == 200
    assert generation.status_code == 200
    assert rejected_extra.status_code == 422
    auth_kwargs = runtime.decide_execution_authorization.call_args.kwargs
    generation_kwargs = runtime.decide_firmware_source_generation.call_args.kwargs
    assert auth_kwargs["requester"] == "engineer-1"
    assert auth_kwargs["tenant_id"] == "tenant-1"
    assert auth_kwargs["decided_at"].tzinfo is not None
    assert generation_kwargs["requester"] == "engineer-1"
    assert generation_kwargs["bridge"] is bridge


def test_pcb_production_routes_use_trusted_identity_and_canonical_bridge(tmp_path: Path) -> None:
    application = app(tmp_path)
    runtime = application.state.project_catalog_runtime
    runtime.create_pcb_production_proposal = Mock(return_value={"status": "AWAITING_APPROVAL"})
    runtime.decide_pcb_production = Mock(return_value={"status": "QUEUED"})
    bridge = object()
    application.state.pcb_execution_bridge = bridge

    with TestClient(application) as client:
        proposal = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/pcb-production/proposal",
            headers=HEADERS,
            json={},
        )
        decision = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/pcb-production/decision",
            headers=HEADERS,
            json={"decision": "APPROVED"},
        )
        rejected_extra = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/pcb-production/decision",
            headers=HEADERS,
            json={"decision": "APPROVED", "reviewer": "attacker"},
        )

    assert proposal.status_code == 200
    assert decision.status_code == 200
    assert rejected_extra.status_code == 422
    proposal_kwargs = runtime.create_pcb_production_proposal.call_args.kwargs
    decision_kwargs = runtime.decide_pcb_production.call_args.kwargs
    assert proposal_kwargs["requester"] == "engineer-1"
    assert proposal_kwargs["tenant_id"] == "tenant-1"
    assert decision_kwargs["requester"] == "engineer-1"
    assert decision_kwargs["tenant_id"] == "tenant-1"
    assert decision_kwargs["bridge"] is bridge


def test_pcb_repair_decision_route_binds_identity_and_requires_typed_fingerprints(tmp_path: Path) -> None:
    application = app(tmp_path)
    runtime = application.state.project_catalog_runtime
    runtime.decide_pcb_repair = Mock(return_value={"status": "DRC_FAIL"})
    fingerprint = "sha256:" + "1" * 64

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/pcb-production/repair-decision",
            headers=HEADERS,
            json={
                "repair_id": "pcb-repair-1",
                "proposal_fingerprint": fingerprint,
                "artifact_fingerprint": fingerprint,
                "action": "DEFER",
                "comment": "defer for user review",
            },
        )

    assert response.status_code == 200
    kwargs = runtime.decide_pcb_repair.call_args.kwargs
    assert kwargs["requester"] == "engineer-1"
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["decision"].action.value == "DEFER"


def test_hardware_selection_routes_use_trusted_identity(tmp_path: Path) -> None:
    application = app(tmp_path)
    runtime = application.state.project_catalog_runtime
    runtime.create_hardware_selection_proposal = Mock(return_value={"status": "REVIEW"})
    runtime.decide_hardware_selection = Mock(return_value={"status": "APPROVED"})

    with TestClient(application) as client:
        proposal = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/proposal",
            headers=HEADERS,
            json={},
        )
        decision = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/decision",
            headers=HEADERS,
            json={"decision": "APPROVED"},
        )
        rejected_extra = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/decision",
            headers=HEADERS,
            json={"decision": "APPROVED", "reviewer": "attacker"},
        )

    assert proposal.status_code == 200
    assert decision.status_code == 200
    assert rejected_extra.status_code == 422
    proposal_kwargs = runtime.create_hardware_selection_proposal.call_args.kwargs
    decision_kwargs = runtime.decide_hardware_selection.call_args.kwargs
    assert proposal_kwargs["requester"] == "engineer-1"
    assert proposal_kwargs["tenant_id"] == "tenant-1"
    assert decision_kwargs["requester"] == "engineer-1"
    assert decision_kwargs["tenant_id"] == "tenant-1"
    assert decision_kwargs["decided_at"].tzinfo is not None


def test_schematic_planning_routes_use_trusted_identity(tmp_path: Path) -> None:
    application = app(tmp_path)
    runtime = application.state.project_catalog_runtime
    runtime.create_schematic_plan = Mock(return_value={"status": "REVIEW"})
    runtime.decide_schematic_plan = Mock(return_value={"status": "APPROVED"})

    with TestClient(application) as client:
        proposal = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/proposal",
            headers=HEADERS,
            json={},
        )
        decision = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/decision",
            headers=HEADERS,
            json={"decision": "APPROVED"},
        )
        rejected_extra = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/decision",
            headers=HEADERS,
            json={"decision": "APPROVED", "reviewer": "attacker"},
        )

    assert proposal.status_code == 200
    assert decision.status_code == 200
    assert rejected_extra.status_code == 422
    proposal_kwargs = runtime.create_schematic_plan.call_args.kwargs
    decision_kwargs = runtime.decide_schematic_plan.call_args.kwargs
    assert proposal_kwargs["requester"] == "engineer-1"
    assert proposal_kwargs["tenant_id"] == "tenant-1"
    assert decision_kwargs["requester"] == "engineer-1"
    assert decision_kwargs["tenant_id"] == "tenant-1"
    assert decision_kwargs["decided_at"].tzinfo is not None


def test_schematic_plan_route_forwards_only_explicit_circuit_selector(tmp_path: Path) -> None:
    application = app(tmp_path)
    runtime = application.state.project_catalog_runtime
    runtime.create_schematic_plan = Mock(return_value={"status": "REVIEW"})
    fingerprint = "sha256:" + "a" * 64
    url = "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/proposal"

    with TestClient(application) as client:
        selected = client.post(url, headers=HEADERS, json={
            "circuit_intent_id": "circuit-1",
            "circuit_intent_revision": "r1",
            "circuit_intent_fingerprint": fingerprint,
        })
        incomplete = client.post(url, headers=HEADERS, json={"circuit_intent_id": "circuit-1"})
        legacy = client.post(url, headers=HEADERS, json={})

    assert selected.status_code == 200
    assert incomplete.status_code == 422
    assert legacy.status_code == 200
    assert runtime.create_schematic_plan.call_args_list[0].kwargs["circuit_intent_selector"] == (
        "circuit-1", "r1", fingerprint,
    )
    assert runtime.create_schematic_plan.call_args_list[1].kwargs["circuit_intent_selector"] is None


def test_h10_readiness_is_not_reported_before_projects_complete(tmp_path: Path, monkeypatch, caplog) -> None:
    application = app(tmp_path)
    runtime = application.state.project_catalog_runtime

    def fail_projects(_tenant_id: str):
        raise PermissionError("blocked")

    monkeypatch.setattr(runtime, "projects", fail_projects)
    with caplog.at_level("INFO", logger=HANDOFF_LOGGER_NAME):
        with TestClient(application) as client:
            response = client.get(
                "/api/v1/copilot/projects",
                headers={**HEADERS, "X-Embedded-Copilot-Native-Handoff": "H10_CHAT_READY"},
            )

    assert response.status_code == 503
    assert not any(
        getattr(record, "native_stage", None) == "H10_CHAT_READY"
        and getattr(record, "telemetry_status", None) == "PASS"
        for record in caplog.records
    )


def test_project_list_success_does_not_claim_aggregate_h10_readiness(tmp_path: Path, caplog) -> None:
    with caplog.at_level("INFO", logger=HANDOFF_LOGGER_NAME):
        with TestClient(app(tmp_path)) as client:
            response = client.get(
                "/api/v1/copilot/projects",
                headers={**HEADERS, "X-Embedded-Copilot-Native-Handoff": "H10_CHAT_READY"},
            )

    assert response.status_code == 200
    assert not any(
        getattr(record, "native_stage", None) == "H10_CHAT_READY"
        and getattr(record, "telemetry_status", None) == "PASS"
        for record in caplog.records
    )


def test_project_api_maps_persistence_permission_error_to_unavailable(tmp_path: Path) -> None:
    application = FastAPI()
    application.state.project_catalog_runtime = PermissionDeniedCatalog()

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-1"
        return await call_next(request)

    application.include_router(router)
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/copilot/projects",
            headers=HEADERS,
            json={
                "preview_id": "preview-1",
                "preview_fingerprint": "sha256:" + "0" * 64,
                "confirmed_at": "2026-08-11T08:00:00Z",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"] == "PROJECT_CATALOG_UNAVAILABLE"


def test_project_api_maps_target_root_permission_to_stable_error() -> None:
    application = FastAPI()
    application.state.project_catalog_runtime = TargetNotWritableCatalog()

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-1"
        return await call_next(request)

    application.include_router(router)
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/copilot/projects",
            headers=HEADERS,
            json={
                "preview_id": "preview-1",
                "preview_fingerprint": "sha256:" + "0" * 64,
                "confirmed_at": "2026-08-11T08:00:00Z",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"] == "PROJECT_TARGET_NOT_WRITABLE"


def test_project_api_keeps_conversations_private_to_requester(tmp_path: Path) -> None:
    with TestClient(app(tmp_path)) as client:
        preview = client.post(
            "/api/v1/copilot/projects/previews",
            headers=HEADERS,
            json={"name": "Gateway", "relative_path": "gateway", "project_type": "ESP_IDF"},
        ).json()
        project = client.post(
            "/api/v1/copilot/projects",
            headers=HEADERS,
            json={"preview_id": preview["preview_id"], "preview_fingerprint": preview["fingerprint"], "confirmed_at": "2026-08-11T08:00:00Z"},
        ).json()
        created = client.post(
            f"/api/v1/copilot/projects/{project['project_id']}/conversations",
            headers=HEADERS,
            json={},
        )
        private = client.get(
            f"/api/v1/copilot/projects/{project['project_id']}/conversations/{created.json()['conversation']['conversation_id']}",
            headers={**HEADERS, "X-Copilot-Requester": "engineer-2"},
        )

    assert created.status_code == 201
    assert created.json()["conversation"]["title"] == "新对话"
    assert private.status_code == 403


def test_project_api_rejects_non_owner_conversation_creation(tmp_path: Path) -> None:
    with TestClient(app(tmp_path)) as client:
        preview = client.post(
            "/api/v1/copilot/projects/previews",
            headers=HEADERS,
            json={"name": "Owner Chat", "relative_path": "owner-chat", "project_type": "ESP_IDF"},
        ).json()
        project = client.post(
            "/api/v1/copilot/projects",
            headers=HEADERS,
            json={"preview_id": preview["preview_id"], "preview_fingerprint": preview["fingerprint"], "confirmed_at": NOW.isoformat()},
        ).json()
        project_id = project["project_id"]
        non_owner = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations",
            headers={**HEADERS, "X-Copilot-Requester": "engineer-2"},
            json={},
        )
        non_owner_conversations = client.get(
            f"/api/v1/copilot/projects/{project_id}/conversations",
            headers={**HEADERS, "X-Copilot-Requester": "engineer-2"},
        )
        owner = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations",
            headers=HEADERS,
            json={},
        )

    assert non_owner.status_code == 403
    assert non_owner.json()["error"] == "PROJECT_CATALOG_FORBIDDEN"
    assert non_owner_conversations.status_code == 403
    assert non_owner_conversations.json()["error"] == "PROJECT_CATALOG_FORBIDDEN"
    assert owner.status_code == 201


def test_project_api_deletes_conversation_without_deleting_project_directory(tmp_path: Path) -> None:
    with TestClient(app(tmp_path)) as client:
        preview = client.post(
            "/api/v1/copilot/projects/previews",
            headers=HEADERS,
            json={"name": "Conversation Delete", "relative_path": "conversation-delete", "project_type": "ESP_IDF"},
        ).json()
        project = client.post(
            "/api/v1/copilot/projects",
            headers=HEADERS,
            json={"preview_id": preview["preview_id"], "preview_fingerprint": preview["fingerprint"], "confirmed_at": "2026-08-11T08:00:00Z"},
        ).json()
        created = client.post(
            f"/api/v1/copilot/projects/{project['project_id']}/conversations",
            headers=HEADERS,
            json={},
        ).json()
        conversation_id = created["conversation"]["conversation_id"]
        deleted = client.delete(
            f"/api/v1/copilot/projects/{project['project_id']}/conversations/{conversation_id}",
            headers=HEADERS,
        )
        listed = client.get(
            f"/api/v1/copilot/projects/{project['project_id']}/conversations",
            headers=HEADERS,
        )

    assert deleted.status_code == 200
    assert deleted.json() == {"conversation_id": conversation_id, "deleted": True}
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert (tmp_path / "projects" / "conversation-delete").is_dir()


def test_project_api_delete_keeps_disk_directory_and_clears_catalog(tmp_path: Path) -> None:
    with TestClient(app(tmp_path)) as client:
        preview = client.post(
            "/api/v1/copilot/projects/previews",
            headers=HEADERS,
            json={"name": "Delete Me", "relative_path": "delete-me", "project_type": "ESP_IDF"},
        ).json()
        project = client.post(
            "/api/v1/copilot/projects",
            headers=HEADERS,
            json={"preview_id": preview["preview_id"], "preview_fingerprint": preview["fingerprint"], "confirmed_at": "2026-08-11T08:00:00Z"},
        ).json()
        created = client.post(
            f"/api/v1/copilot/projects/{project['project_id']}/conversations",
            headers=HEADERS,
            json={},
        )
        deleted = client.delete(
            f"/api/v1/copilot/projects/{project['project_id']}",
            headers={**HEADERS, "X-Project-Fingerprint": project["fingerprint"]},
        )
        listed = client.get("/api/v1/copilot/projects", headers=HEADERS)

    assert created.status_code == 201
    assert deleted.status_code == 200
    assert deleted.json()["operation"] == "PROJECT_DELETED"
    assert listed.json() == []
    assert (tmp_path / "projects" / "delete-me").is_dir()


def test_project_api_trusted_identity_provider_ignores_spoofed_headers(tmp_path: Path) -> None:
    application = app(tmp_path)
    application.state.desktop_identity_provider = type(
        "Provider",
        (),
        {"resolve": lambda self, request: TrustedDesktopIdentity(requester="trusted-user", tenant_id="trusted-tenant", authentication="TEST")},
    )()
    with TestClient(application) as client:
        response = client.get(
            "/api/v1/copilot/projects",
            headers={"X-Copilot-Requester": "spoofed-user", "X-Environment-Tenant": "spoofed-tenant"},
        )

    assert response.status_code == 200
    assert response.json() == []
