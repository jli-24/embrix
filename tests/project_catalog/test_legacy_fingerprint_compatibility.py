from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    DesignPin,
    DesignTruth,
    SchematicProductionRequest,
    SchematicProductionState,
    SchematicProductionStatus,
    SchematicProposal,
    SchematicProposalDecisionStatus,
    TruthLevel,
    decide_schematic_proposal,
)
from embedded_copilot.execution_worker import (
    OperationReceipt,
    OperationStatus,
    OperationType,
)
from embedded_copilot.pcb_production import (
    PCBProductionService,
    PCBProductionState,
    PCBProductionStatus,
    PCBProposalDecisionStatus,
    decide_pcb_proposal,
    PCBDrcResult,
    PCBDrcStatus,
)
from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration
from embedded_copilot.project_catalog.models import _firmware_build_configuration_fingerprints
from tests.engineering_copilot.test_schematic_production import (
    _proposal as schematic_proposal,
    _request as schematic_request,
)
from tests.engineering_copilot.test_knowledge_model import _truth as design_truth
from tests.pcb_production.test_service import _Availability, _request as pcb_request


def _legacy_material(model: object, *removed_fields: str) -> dict[str, object]:
    material = model.model_dump(mode="json")
    for field in removed_fields:
        material.pop(field)
    unsigned = {key: value for key, value in material.items() if key != "fingerprint"}
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    material["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return material


def test_build_configuration_accepts_only_exact_pre_toolchain_layout_binding() -> None:
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_GNU",
        toolchain_version="13.2",
        profile="RELEASE",
    )
    legacy = configuration.model_dump(mode="json")
    for field in ("cmake_project", "cmake_toolchain_file", "startup_source", "system_source", "cmsis_include", "device_include"):
        legacy.pop(field)
    encoded = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    legacy_fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()

    assert legacy_fingerprint in _firmware_build_configuration_fingerprints(configuration)

    tampered = configuration.model_copy(update={"profile": "DEBUG"})
    assert legacy_fingerprint not in _firmware_build_configuration_fingerprints(tampered)


def test_legacy_pcb_drc_result_and_receipt_remain_typed_and_tamper_rejected() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    result = PCBDrcResult.create(
        operation_id="drc-legacy",
        pcb_artifact_ref="hardware/main.kicad_pcb",
        pcb_content_hash="sha256:" + "1" * 64,
        kicad_version="10.0.5",
        status=PCBDrcStatus.DRC_PASS,
        exit_status=0,
        violations=(),
        warnings=(),
        report_artifact_ref="artifact://drc-legacy",
        started_at=now,
        finished_at=now,
        receipt_ref="sha256:" + "2" * 64,
        tool_observed=True,
        project_id="project-legacy",
        conversation_id="conversation-legacy",
        kicad_cli_path="C:/KiCad/bin/kicad-cli.exe",
        command_identity="kicad-cli pcb drc",
        raw_report_fingerprint="sha256:" + "3" * 64,
        tool_execution_status="SUCCEEDED",
        engineering_status=None,
    )
    legacy_result = _legacy_material(
        result,
        "project_id",
        "conversation_id",
        "kicad_cli_path",
        "command_identity",
        "raw_report_fingerprint",
        "tool_execution_status",
        "engineering_status",
        "parse_validation",
        "issues",
    )
    receipt = OperationReceipt.model_validate(
        {
            "operation_id": "drc-legacy",
            "tenant_id": "tenant-legacy",
            "project_id": "project-legacy",
            "operation_type": OperationType.KICAD_PCB_DRC,
            "status": OperationStatus.SUCCEEDED,
            "tool_execution_status": "SUCCEEDED",
            "flash_verification_status": "NOT_APPLICABLE",
            "target_runtime_verification_status": "NOT_APPLICABLE",
            "pcb_drc_result": legacy_result,
            "created_at": now,
        },
        strict=False,
    )
    assert receipt.pcb_drc_result is not None
    tampered = dict(legacy_result, pcb_content_hash="sha256:" + "4" * 64)
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        PCBDrcResult.model_validate(tampered, strict=False)


def test_design_pin_accepts_only_exact_pre_unconnected_fields_fingerprint() -> None:
    pin = DesignPin.create(
        pin_id="pin-legacy",
        component_id="component-legacy",
        pin_name="PA0",
        pin_number="1",
        function="GPIO",
        signal_id="signal-legacy",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("evidence-legacy",),
    )
    legacy = _legacy_material(pin, "intentionally_unconnected", "unconnected_reason")

    restored = DesignPin.model_validate(legacy, strict=False)

    assert restored.intentionally_unconnected is False
    assert restored.unconnected_reason is None
    tampered = dict(legacy, pin_name="PA1")
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        DesignPin.model_validate(tampered, strict=False)


def test_design_truth_accepts_exact_nested_legacy_pin_material() -> None:
    truth = design_truth()
    legacy = truth.model_dump(mode="json")
    for pin in legacy["pins"]:
        pin.pop("intentionally_unconnected")
        pin.pop("unconnected_reason")
    unsigned = {key: value for key, value in legacy.items() if key != "fingerprint"}
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    legacy["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()

    restored = DesignTruth.model_validate(legacy, strict=False)

    assert restored.pins[0].intentionally_unconnected is False
    tampered = dict(legacy, design_revision="tampered")
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        DesignTruth.model_validate(tampered, strict=False)


def test_schematic_contracts_accept_only_exact_pre_plan_binding_fingerprints() -> None:
    proposal = schematic_proposal()
    request = schematic_request(proposal)
    legacy_request = _legacy_material(request, "schematic_plan_id", "schematic_plan_fingerprint")
    legacy_proposal = _legacy_material(proposal, "schematic_plan_id", "schematic_plan_fingerprint")

    assert SchematicProductionRequest.model_validate(legacy_request, strict=False).schematic_plan_id is None
    assert SchematicProposal.model_validate(legacy_proposal, strict=False).schematic_plan_id is None

    tampered = dict(legacy_request, requester="different-engineer")
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        SchematicProductionRequest.model_validate(tampered, strict=False)


def test_schematic_state_accepts_exact_nested_legacy_plan_material() -> None:
    proposal = schematic_proposal()
    request = schematic_request(proposal)
    decision = decide_schematic_proposal(
        proposal,
        decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    state = SchematicProductionState.create(
        production_id="schematic-production-legacy",
        project_id=request.project_id,
        conversation_id=request.conversation_id,
        request=request,
        proposal=proposal,
        decision=decision,
        operation_id="schematic-operation-legacy",
        operation_fingerprint="sha256:" + "8" * 64,
        status=SchematicProductionStatus.QUEUED,
    )
    legacy = state.model_dump(mode="json")
    for nested in (legacy["request"], legacy["proposal"]):
        nested.pop("schematic_plan_id")
        nested.pop("schematic_plan_fingerprint")
    for field in (
        "generation_receipt",
        "erc_receipt",
        "erc_assessment",
        "repair_proposals",
        "repair_decisions",
        "repair_application_receipts",
        "erc_gate_status",
    ):
        legacy.pop(field)
    unsigned = {key: value for key, value in legacy.items() if key != "fingerprint"}
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    legacy["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()

    restored = SchematicProductionState.model_validate(legacy, strict=False)

    assert restored.status is SchematicProductionStatus.QUEUED
    tampered = dict(legacy, production_id="schematic-production-tampered")
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        SchematicProductionState.model_validate(tampered, strict=False)


def test_pcb_state_accepts_only_exact_pre_evidence_fields_fingerprint() -> None:
    request = pcb_request()
    proposal = PCBProductionService(footprints=_Availability()).create_proposal(request)
    decision = decide_pcb_proposal(
        proposal,
        decision=PCBProposalDecisionStatus.APPROVED,
        reviewer=request.requester,
        decided_at=request.requested_at,
    )
    state = PCBProductionState.create(
        production_id="pcb-production-legacy",
        project_id=request.project_id,
        conversation_id=request.conversation_id,
        request=request,
        proposal=proposal,
        decision=decision,
        operation_id="pcb-operation-legacy",
        operation_fingerprint="sha256:" + "9" * 64,
        status=PCBProductionStatus.QUEUED,
    )
    legacy = _legacy_material(
        state,
        "stale_reason",
        "generation_receipt",
        "drc_result",
        "drc_receipt",
        "drc_receipt_fingerprint",
        "drc_gate_status",
        "repair_proposals",
        "repair_decisions",
        "repair_application_receipts",
    )

    restored = PCBProductionState.model_validate(legacy, strict=False)

    assert restored.status is PCBProductionStatus.QUEUED
    assert restored.generation_receipt is None
    tampered = dict(legacy, production_id="pcb-production-tampered")
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        PCBProductionState.model_validate(tampered, strict=False)
