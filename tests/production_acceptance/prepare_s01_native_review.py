"""Prepare an isolated, owner-bound real ERC for manual S01 Native review.

Run only with EMBRIX_RUN_S01_REAL_KICAD=1. The script stops before Circuit preview
or decision and never launches KiCad capabilities other than schematic parse/ERC.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import (
    DESKTOP_SESSION_COOKIE,
    ProductionLocalSessionIdentityAdapter,
)
from embedded_copilot.api.main import create_app
from embedded_copilot.engineering_copilot import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureInterface,
    ArchitectureInterfaceType,
    AuthorizationDecisionStatus,
    DesignTruthBuilder,
    HardwareSelectionDecision,
    SchematicPlanDecision,
    SchematicProductionRequest,
    SchematicProductionState,
    SchematicProductionStatus,
    SchematicProposal,
    SchematicProposalDecisionStatus,
    approve_architecture_design,
    approve_engineering_plan,
    create_schematic_production_state,
    decide_schematic_proposal,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
    merge_requirement_context,
)
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ArtifactStore,
    ExecutionOperationService,
    ExecutionWorker,
    KiCadCliSchematicErcAdapter,
    KiCadCliSchematicParseAdapter,
    OperationStatus,
    OperationType,
    SchematicSymbolLibraryValidator,
    SchematicWriteAdapter,
    SQLiteExecutionStore,
    probe_kicad_cli_version,
)
from embedded_copilot.production_release.configuration import load_production_settings
from embedded_copilot.production_release.paths import ProductionStateLayout
from embedded_copilot.production_release.session import LocalSessionManager
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from embedded_copilot.project_catalog.models import ConversationDetail
from embedded_copilot.schematic_execution import SchematicExecutionBridge
from tests.engineering_copilot.test_schematic_planning_g2 import _catalog
from tests.production_acceptance.test_real_schematic_production_golden import (
    _proposal as device_resistor_proposal,
)

REPOSITORY = Path(__file__).resolve().parents[2]
SANDBOX = REPOSITORY / "dist" / "_staging" / "s01-native-interactive-20260930"
CLI = Path(r"D:\ruanjian\32\kicad\bin\kicad-cli.exe")
TENANT = "desktop-tenant"
OWNER = "desktop-user"
PROJECT_NAME = "S01 Native ERC Review"
ZERO = "sha256:" + "0" * 64
API = "/api/v1/copilot/projects"


class _Answer:
    def model_dump(self, mode: str = "json") -> dict[str, object]:
        del mode
        return {
            "answer_text": "隔离验收预置的领域输入",
            "evidence_details": (),
            "recommendations": (),
        }


def _check_scope() -> tuple[ProductionStateLayout, Path]:
    if os.environ.get("EMBRIX_RUN_S01_REAL_KICAD") != "1":
        raise RuntimeError("S01 real KiCad preparation requires explicit opt-in")
    if not CLI.is_file():
        raise RuntimeError("authorized KiCad CLI is unavailable")
    if not SANDBOX.is_dir() or not (SANDBOX / "local").is_dir():
        raise RuntimeError("existing isolated Native state is unavailable")
    if not SANDBOX.resolve().is_relative_to(REPOSITORY.resolve()):
        raise RuntimeError("isolated Native state escaped the repository")
    os.environ["LOCALAPPDATA"] = str(SANDBOX / "local")
    os.environ["TEMP"] = str(SANDBOX / "temp")
    os.environ["TMP"] = str(SANDBOX / "temp")
    os.environ.pop("EMBRIX_KICAD_CLI", None)
    if shutil.which("kicad-cli.exe") or shutil.which("kicad-cli"):
        raise RuntimeError("production app would discover extra KiCad capabilities")
    layout = ProductionStateLayout.for_current_user()
    if layout.root != (SANDBOX / "local" / "Embedded Copilot").resolve():
        raise RuntimeError("Native state identity mismatch")
    project_root = layout.current / "projects"
    config_file = layout.production_config
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("isolated production config is invalid")
    existing = raw.get("project_root")
    if existing not in (None, str(project_root)):
        raise RuntimeError("isolated production project root changed")
    project_root.mkdir(parents=True, exist_ok=True)
    raw["project_root"] = str(project_root)
    config_file.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return layout, project_root


def _production_app(layout: ProductionStateLayout):
    settings = load_production_settings(
        assets=REPOSITORY / "frontend" / "dist", layout=layout
    )
    if settings.kicad_cli_path is not None or settings.kicad_install_roots:
        raise RuntimeError(
            "production KiCad discovery is outside this acceptance scope"
        )
    sessions = LocalSessionManager()
    app = create_app(
        settings=settings,
        desktop_identity_provider=ProductionLocalSessionIdentityAdapter(sessions),
    )
    return app, sessions


def _create_project_and_conversation(
    layout: ProductionStateLayout,
) -> tuple[str, str, str]:
    app, sessions = _production_app(layout)
    with TestClient(app) as client:
        token = sessions.exchange(
            sessions.issue_bootstrap(requester=OWNER, tenant_id=TENANT)
        )
        client.cookies.set(DESKTOP_SESSION_COOKIE, token, path="/api")
        locations = client.get(f"{API}/locations")
        if locations.status_code != 200:
            raise RuntimeError(f"project locations failed: {locations.status_code}")
        choices = locations.json()
        if not choices:
            raise RuntimeError("isolated project location is unavailable")
        location = choices[0]
        existing = client.get(API)
        if existing.status_code != 200:
            raise RuntimeError(f"project list failed: {existing.status_code}")
        matches = [item for item in existing.json() if item["name"] == PROJECT_NAME]
        if len(matches) > 1:
            raise RuntimeError(
                "multiple review projects exist; refusing ambiguous preparation"
            )
        if matches:
            project_id = matches[0]["project_id"]
            listed = client.get(f"{API}/{project_id}/conversations")
            if listed.status_code != 200 or len(listed.json().get("items", ())) != 1:
                raise RuntimeError(
                    "review conversation cannot be resumed unambiguously"
                )
            conversation_id = listed.json()["items"][0]["conversation_id"]
            resumed = client.post(
                f"{API}/{project_id}/conversations/{conversation_id}/resume", json={}
            )
            if resumed.status_code != 200:
                raise RuntimeError(
                    f"review conversation resume failed: {resumed.status_code}"
                )
            return project_id, resumed.json()["session_id"], conversation_id
        preview = client.post(
            f"{API}/previews",
            json={
                "name": PROJECT_NAME,
                "project_type": "STM32",
                "creation_mode": "CREATE_NEW",
                "location": {
                    "location_id": location["location_id"],
                    "fingerprint": location["fingerprint"],
                },
            },
        )
        if preview.status_code != 200:
            raise RuntimeError(
                f"project preview failed: {preview.status_code} {preview.json()}"
            )
        proposed = preview.json()
        created = client.post(
            API,
            json={
                "preview_id": proposed["preview_id"],
                "preview_fingerprint": proposed["fingerprint"],
                "confirmed_at": datetime.now(UTC).isoformat(),
            },
        )
        if created.status_code != 202:
            raise RuntimeError(
                f"project creation failed: {created.status_code} {created.json()}"
            )
        project_id = created.json()["project_id"]
        conversation = client.post(f"{API}/{project_id}/conversations", json={})
        if conversation.status_code != 201:
            raise RuntimeError(
                f"conversation creation failed: {conversation.status_code} {conversation.json()}"
            )
        session = conversation.json()
        detail = session["conversation"]
        return project_id, session["session_id"], detail["conversation_id"]


def _seed_domain_input(
    catalog: ProjectCatalogRuntime,
    project_id: str,
    session_id: str,
    conversation_id: str,
    now: datetime,
) -> tuple[ConversationDetail, str]:
    existing = catalog.conversation(
        project_id, conversation_id, tenant_id=TENANT, requester=OWNER
    )
    completed_design = next(
        (
            item
            for item in existing.turns
            if item.request_id == "s01-design" and item.route == "GREENFIELD_DESIGN"
        ),
        None,
    )
    if completed_design is not None and existing.schematic_production is not None:
        return existing, completed_design.turn_id
    if any(item.request_id == "s01-context" for item in existing.turns):
        current = existing
    else:
        partial = merge_requirement_context(
            None,
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id="s01-requirements-1",
            utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
        )
        context = merge_requirement_context(
            partial,
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id="s01-requirements-2",
            utterance="开始设计",
            confirmation_requested=True,
        )
        original_plan, _ = approve_engineering_plan(
            generate_engineering_plan(context),
            reviewer=OWNER,
            decided_at=now,
        )
        original_architecture, _ = approve_architecture_design(
            generate_architecture_design(context, original_plan),
            reviewer=OWNER,
            decided_at=now,
        )
        protocol_by_source = {
            original_architecture.interfaces[0].source_component_id: "I2C",
            original_architecture.interfaces[1].source_component_id: "POWER",
            original_architecture.interfaces[2].source_component_id: "GPIO",
        }
        interfaces = []
        for item in original_architecture.interfaces:
            values = item.model_dump(mode="python")
            if item.interface_type is ArchitectureInterfaceType.HARDWARE:
                values["protocol"] = protocol_by_source[item.source_component_id]
            values["fingerprint"] = ZERO
            interfaces.append(ArchitectureInterface.create(**values))
        design_values = original_architecture.model_dump(mode="python")
        design_values.update(
            {
                "interfaces": tuple(interfaces),
                "status": ArchitectureDesignStatus.PROPOSED,
                "fingerprint": ZERO,
            }
        )
        architecture, architecture_review = approve_architecture_design(
            ArchitectureDesign.create(**design_values),
            reviewer=OWNER,
            decided_at=now,
        )
        plan, plan_review = approve_engineering_plan(
            generate_engineering_plan(context),
            reviewer=OWNER,
            decided_at=now,
        )
        conversation = catalog.conversation(
            project_id, conversation_id, tenant_id=TENANT, requester=OWNER
        )
        seeded = conversation.model_copy(
            update={
                "requirement_context": context,
                "engineering_plan": plan,
                "engineering_plan_review": plan_review,
                "architecture_design": architecture,
                "architecture_review": architecture_review,
                "engineering_workflow": generate_engineering_workflow(
                    architecture, plan
                ),
                "design_truth": DesignTruthBuilder().build_from_approved_architecture(
                    architecture,
                    tenant_id=TENANT,
                    conversation_id=conversation_id,
                    architecture_review=architecture_review,
                ),
                "fingerprint": ZERO,
            }
        )
        catalog.persist_conversation_detail(seeded)
        catalog.create_hardware_selection_proposal(
            project_id, conversation_id, tenant_id=TENANT, requester=OWNER
        )
        catalog.decide_hardware_selection(
            project_id,
            conversation_id,
            HardwareSelectionDecision.APPROVED,
            tenant_id=TENANT,
            requester=OWNER,
            decided_at=now,
        )
        catalog.create_schematic_plan(
            project_id, conversation_id, tenant_id=TENANT, requester=OWNER
        )
        catalog.decide_schematic_plan(
            project_id,
            conversation_id,
            SchematicPlanDecision.APPROVED,
            tenant_id=TENANT,
            requester=OWNER,
            decided_at=now,
        )
        catalog.begin_turn(
            session_id,
            request_id="s01-context",
            question="准备隔离工程执行",
            created_at=now,
        )
        catalog.complete_turn(session_id, "s01-context", _Answer(), now)
        current = catalog.conversation(
            project_id, conversation_id, tenant_id=TENANT, requester=OWNER
        )
    if current.execution_authorization is None or current.implementation_spec is None:
        raise RuntimeError("schematic authorization inputs were not produced")
    task = next(
        item
        for item in current.implementation_spec.tasks
        if "SCHEMATIC" in item.expected_artifacts
        or any(
            artifact.artifact_id in item.expected_artifacts
            and artifact.artifact_type.value == "SCHEMATIC"
            for artifact in current.implementation_spec.expected_artifacts
        )
    )
    intent = next(
        item
        for item in current.execution_intents
        if item.source_task_refs == (task.task_id,)
    )
    request = next(
        item
        for item in current.execution_authorization.requests
        if item.intent_id == intent.intent_id
        and item.capability_id == "ARTIFACT_CREATION"
    )
    if not any(
        item.request_id == request.request_id and item.decision.value == "APPROVED"
        for item in current.execution_authorization.decisions
    ):
        catalog.decide_execution_authorization(
            project_id,
            conversation_id,
            request.request_id,
            AuthorizationDecisionStatus.APPROVED,
            tenant_id=TENANT,
            requester=OWNER,
            decided_at=now,
        )
    if completed_design is None:
        catalog.begin_turn(
            session_id,
            request_id="s01-design",
            question="设计隔离 STM32 电路",
            created_at=now,
        )
        catalog.complete_turn(
            session_id, "s01-design", _Answer(), now, route="GREENFIELD_DESIGN"
        )
    if not any(item.request_id == "s01-schematic" for item in existing.turns):
        catalog.begin_turn(
            session_id,
            request_id="s01-schematic",
            question="生成原理图",
            created_at=now,
        )
        catalog.complete_turn(session_id, "s01-schematic", _Answer(), now)
    proposed = catalog.conversation(
        project_id, conversation_id, tenant_id=TENANT, requester=OWNER
    )
    if (
        proposed.schematic_production is None
        or proposed.schematic_production.status
        is not SchematicProductionStatus.AWAITING_APPROVAL
    ):
        raise RuntimeError("schematic production proposal was not produced")
    turn_id = next(
        item.turn_id
        for item in proposed.turns
        if item.request_id == "s01-design" and item.route == "GREENFIELD_DESIGN"
    )
    return proposed, turn_id


def _produce_real_erc(
    layout: ProductionStateLayout,
    catalog: ProjectCatalogRuntime,
    project_id: str,
    conversation_id: str,
    proposed: ConversationDetail,
    now: datetime,
) -> tuple[str, str, str, str, str]:
    project_root = catalog.knowledge_snapshot_root(project_id, tenant_id=TENANT)
    library = CLI.parent.parent / "share" / "kicad" / "symbols" / "Device.kicad_sym"
    if not library.is_file():
        raise RuntimeError("authorized Device symbol library is unavailable")
    (project_root / "sym-lib-table").write_text(
        '(sym_lib_table (version 7) (lib (name "Device") (type "KiCad") '
        f'(uri "{library.as_posix()}") (options "") (descr "")))',
        encoding="utf-8",
    )
    version = probe_kicad_cli_version(str(CLI), cwd=project_root)
    if version != "10.0.5":
        raise RuntimeError(f"KiCad {version} is outside the S01 verified scope")
    artifact_store = ArtifactStore(layout.database / "v66-artifacts")
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(layout.database / "desktop-v66.sqlite3"),
        artifact_store=artifact_store,
        clock=lambda: now,
    )
    workspace = WorkspaceApplyService(
        resolver=ProjectCatalogWorkspaceTargetResolver(catalog),
        store=SQLiteWorkspaceApplyStore(layout.database / "desktop-v65.sqlite3"),
        state_root=layout.database / "v65-workspace",
        clock=type("Clock", (), {"now": lambda self: now})(),
    )
    original = proposed.schematic_production
    assert original is not None
    device = device_resistor_proposal()
    proposal = SchematicProposal.create(
        **{
            **original.proposal.model_dump(mode="python", exclude={"fingerprint"}),
            "components": device.components,
            "pins": device.pins,
            "nets": device.nets,
            "power_symbols": device.power_symbols,
        }
    )
    production_request = SchematicProductionRequest.create(
        **{
            **original.request.model_dump(mode="python", exclude={"fingerprint"}),
            "source_request_id": None,
            "trace_id": None,
        }
    )
    decision = decide_schematic_proposal(
        proposal,
        decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer=OWNER,
        decided_at=now,
    )
    bridge = SchematicExecutionBridge(
        workspace=workspace,
        execution=execution,
        clock=lambda: now,
        symbol_binding_validator=SchematicSymbolLibraryValidator(
            lambda **kwargs: project_root
        ),
    )
    write_request = bridge.approve_and_enqueue(
        production_request=production_request,
        proposal=proposal,
        decision=decision,
        tenant_id=TENANT,
    )
    initial = create_schematic_production_state(production_request, proposal)
    queued = SchematicProductionState.create(
        production_id=initial.production_id,
        project_id=project_id,
        conversation_id=conversation_id,
        request=production_request,
        proposal=proposal,
        decision=decision,
        operation_id=write_request.operation.operation_id,
        operation_fingerprint=write_request.operation.fingerprint,
        status=SchematicProductionStatus.QUEUED,
    )
    catalog.persist_conversation_detail(
        ConversationDetail.model_validate(
            proposed.model_copy(
                update={"schematic_production": queued, "fingerprint": ZERO}
            ).model_dump(mode="python")
        )
    )
    write_worker = ExecutionWorker(
        worker_id="s01-native-write",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_write=SchematicWriteAdapter(workspace),
        artifact_store=artifact_store,
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_WRITE,),
        clock=lambda: now,
    )
    write_status = write_worker.run_once()
    if write_status is None or write_status.status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("controlled schematic write did not succeed")
    write_id = write_request.operation.operation_id
    write_operation = execution.get_operation(
        tenant_id=TENANT, project_id=project_id, operation_id=write_id
    )
    write_receipt = execution.get_receipt(
        tenant_id=TENANT, project_id=project_id, operation_id=write_id
    )
    if write_operation is None or write_receipt is None:
        raise RuntimeError("canonical schematic write evidence is unavailable")
    generated = catalog.record_schematic_generation(
        operation=write_operation,
        receipt=write_receipt,
        external_validation_status="UNAVAILABLE",
    )
    generation = generated.schematic_production.generation_receipt
    if generation is None:
        raise RuntimeError("schematic generation receipt is unavailable")
    schematic = project_root / generation.artifact_path
    source_hash = "sha256:" + hashlib.sha256(schematic.read_bytes()).hexdigest()
    parse = KiCadCliSchematicParseAdapter(
        executable=str(CLI),
        kicad_version=version,
        clock=lambda: now,
    ).execute(
        project_id=project_id,
        conversation_id=conversation_id,
        operation_id=write_id,
        artifact=schematic,
        artifact_path=generation.artifact_path,
        expected_artifact_fingerprint=source_hash,
    )
    if parse.validation_status != "PARSE_VERIFIED" or not parse.artifact_unchanged:
        raise RuntimeError("real schematic parse failed")
    catalog.record_schematic_parse_validation(evidence=parse)
    erc_request = bridge.enqueue_erc(
        write_operation=write_operation,
        receipt_fingerprint=write_receipt.fingerprint,
        reviewer_id=OWNER,
    )
    erc_worker = ExecutionWorker(
        worker_id="s01-native-erc",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_erc=KiCadCliSchematicErcAdapter(
            executable=str(CLI),
            kicad_version=version,
            workspace_resolver=lambda operation: project_root,
        ),
        artifact_store=artifact_store,
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_ERC,),
        clock=lambda: now,
    )
    erc_status = erc_worker.run_once()
    if erc_status is None or erc_status.status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("real ERC did not succeed")
    erc_id = erc_request.operation.operation_id
    erc_operation = execution.get_operation(
        tenant_id=TENANT, project_id=project_id, operation_id=erc_id
    )
    erc_receipt = execution.get_receipt(
        tenant_id=TENANT, project_id=project_id, operation_id=erc_id
    )
    if (
        erc_operation is None
        or erc_receipt is None
        or erc_receipt.schematic_erc_status != "ERC_PASS"
    ):
        raise RuntimeError("canonical ERC PASS receipt is unavailable")
    catalog.record_schematic_erc_validation(
        operation=erc_operation, receipt=erc_receipt
    )
    return (
        write_id,
        erc_id,
        erc_receipt.fingerprint,
        generation.artifact_path,
        source_hash,
    )


def main() -> None:
    layout, project_root = _check_scope()
    evidence_dir = SANDBOX / "acceptance"
    manifest_path = evidence_dir / "native-review-preparation.json"
    if manifest_path.exists():
        raise RuntimeError(
            "Native review is already prepared; refusing to overwrite it"
        )
    project_id, session_id, conversation_id = _create_project_and_conversation(layout)
    now = datetime.now(UTC)
    catalog = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=layout.database / "desktop.sqlite3",
        metadata_directory=layout.artifacts / "projects",
        production_mode=True,
        hardware_component_source=_catalog(),
        initialize_async=False,
    )
    catalog.get_project_for_requester(project_id, tenant_id=TENANT, requester=OWNER)
    proposed, turn_id = _seed_domain_input(
        catalog, project_id, session_id, conversation_id, now
    )
    write_id, erc_id, receipt_fingerprint, artifact_ref, source_hash = (
        _produce_real_erc(
            layout,
            catalog,
            project_id,
            conversation_id,
            proposed,
            now,
        )
    )
    detail = catalog.conversation(
        project_id, conversation_id, tenant_id=TENANT, requester=OWNER
    )
    if (
        detail.schematic_production is None
        or detail.schematic_production.erc_gate_status.value != "PASS"
    ):
        raise RuntimeError("Catalog did not publish ERC validation evidence")
    if catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER):
        raise RuntimeError("Circuit was unexpectedly published during preparation")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "screenshots").mkdir(exist_ok=True)
    manifest = {
        "package": str(
            REPOSITORY / "dist/_staging/s01-native-review-20260930/Embrix/Embrix.exe"
        ),
        "state_root": str(layout.root),
        "project_root": str(project_root),
        "project_name": PROJECT_NAME,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "design_turn_id": turn_id,
        "write_operation_id": write_id,
        "erc_operation_id": erc_id,
        "erc_receipt_fingerprint": receipt_fingerprint,
        "schematic_artifact": artifact_ref,
        "schematic_before_fingerprint": source_hash,
        "kiCad_version": "10.0.5",
        "domain_input_preseed": "requirement/architecture/plan/hardware selection and isolated answer text; no Circuit preview/decision",
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("S01_NATIVE_PREP=" + json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
