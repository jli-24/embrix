from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_pcb import (
    EDAMappingCandidate,
    EDAMappingCandidateComponent,
    EDAMappingCandidatePin,
    build_mapping_input_from_existing,
)
from embedded_copilot.pcb_production import PCBComponentTruth
from embedded_copilot.project_catalog import (
    ConversationDetail,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
)
from tests.engineering_pcb.test_g7b5_mapping import _input
from tests.pcb_production.test_service import _request

from .owner_seed import seed_project_owner

NOW = datetime(2026, 9, 19, tzinfo=UTC)
ZERO = "sha256:" + "0" * 64


def _runtime(tmp_path: Path) -> ProjectCatalogRuntime:
    root = tmp_path / "projects"
    root.mkdir()
    runtime = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    runtime._repository.save_project(
        ProjectView(
            project_id="g7b5-project",
            tenant_id="g7b5-tenant",
            name="G7-B5",
            location_id="g7b5-location",
            directory_name="g7b5",
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path="g7b5",
            project_type=ProjectType.GENERIC_FIRMWARE,
            initialization_status=ProjectInitializationStatus.READY,
            created_at=NOW,
            updated_at=NOW,
            last_activity_at=NOW,
        )
    )
    seed_project_owner(tmp_path / "catalog.sqlite3", "g7b5-project", "g7b5-engineer")
    return runtime


def _detail() -> ConversationDetail:
    state = _input().state
    request = _request(
        project_id="g7b5-project",
        conversation_id="g7b5-conversation",
        requester="g7b5-engineer",
        components=(
            PCBComponentTruth.create(
                reference="U1",
                symbol_binding="Device:D",
                footprint_binding="Package_QFN:QFN-2",
                footprint_source_refs=("g5-footprint-u1",),
                pin_numbers=("1", "2"),
                pad_numbers=("1", "2"),
            ),
        ),
    )
    return ConversationDetail(
        conversation_id="g7b5-conversation",
        project_id="g7b5-project",
        tenant_id="g7b5-tenant",
        requester="g7b5-engineer",
        title="G7-B5 mapping",
        turns=(),
        created_at=NOW,
        updated_at=NOW,
        pcb_production_request=request,
        pcb_engineering_state=state,
    )


def test_mapping_runtime_persists_relationships_and_survives_restart(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())

    verified = runtime.verify_pcb_mapping(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )

    assert verified.pcb_mapping_verification is not None
    assert verified.pcb_mapping_verification.status.value == "UNKNOWN"
    assert verified.pcb_component_mapping_relationships
    assert verified.pcb_pin_mapping_relationships
    assert verified.pcb_pad_mapping_relationships

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored = restarted.conversation(
        verified.project_id,
        verified.conversation_id,
        tenant_id=verified.tenant_id,
        requester=verified.requester,
    )
    assert restored.pcb_mapping_verification is not None
    assert restored.pcb_mapping_verification.fingerprint == verified.pcb_mapping_verification.fingerprint
    assert restored.pcb_component_mapping_relationships
    assert restored.pcb_pin_mapping_relationships
    assert restored.pcb_pad_mapping_relationships


def test_mapping_verification_is_marked_stale_when_g7a_input_changes(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())
    verified = runtime.verify_pcb_mapping(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )

    incoming = verified.model_copy(
        update={
            "pcb_engineering_state": None,
            "pcb_production_request": None,
            "pcb_mapping_verification": None,
            "fingerprint": ZERO,
        }
    )
    stale = runtime.persist_conversation_detail(incoming)

    assert stale.pcb_mapping_verification is not None
    assert stale.pcb_mapping_verification.stale is True
    assert stale.pcb_mapping_verification.stale_reason
    assert stale.pcb_mapping_verification.required_reverification == (
        "reverify_footprint_symbol_pin_mapping",
    )


def test_mapping_prevent_runtime_is_read_only_and_fails_closed_on_missing_evidence(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())
    input_value = build_mapping_input_from_existing(
        project_id=stored.project_id,
        state=stored.pcb_engineering_state,
        production_request=stored.pcb_production_request,
        design_truth=stored.design_truth,
        hardware_truth=None,
    )
    component = input_value.authority_components[0]
    candidate = EDAMappingCandidate.create(
        project_id=stored.project_id,
        candidate_id="g7b5-runtime-candidate",
        base_mapping_fingerprint=input_value.authority_fingerprint,
        components=(
            EDAMappingCandidateComponent.create(
                component_id=component.component_id,
                symbol_identity=component.symbol_identity,
                footprint_identity=component.footprint_identity,
                package_identity=component.package_identity,
            ),
        ),
        pins=tuple(
            EDAMappingCandidatePin.create(
                component_id=item.component_id,
                datasheet_pin_number=item.datasheet_pin_number,
                symbol_pin_number=item.datasheet_pin_number,
                footprint_pad_numbers=item.pad_numbers,
                electrical_role=item.electrical_role,
            )
            for item in input_value.approved_pin_pad_bindings
        ),
    )
    before = runtime.conversation(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )

    evaluation = runtime.evaluate_pcb_mapping_prevent(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
        candidate=candidate,
    )
    after = runtime.conversation(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )

    assert evaluation.status.value == "UNKNOWN"
    assert after.fingerprint == before.fingerprint
    assert after.pcb_mapping_verification is None
