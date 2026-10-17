"""Offline restart check for a published Circuit bound to current schematic bytes."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from embedded_copilot.engineering_copilot import (
    SchematicErcGateStatus,
    SchematicErcReceipt,
    SchematicErcResult,
    SchematicErcStatus,
    SchematicGenerationReceipt,
    SchematicGenerationStatus,
    SchematicParseValidationEvidence,
    SchematicProductionState,
    SchematicProductionStatus,
    SchematicProposalDecisionStatus,
    decide_schematic_proposal,
)
from embedded_copilot.engineering_runtime.evidence import EvidenceReceiptReference
from embedded_copilot.project_catalog.models import (
    ConversationDetail,
    ConversationTurn,
    ConversationTurnStatus,
)
from tests.engineering_copilot.test_schematic_production_g3 import (
    _runtime_with_approved_plan,
)
from tests.project_catalog.test_circuit_catalog_persistence import (
    NOW,
    _approval,
    _candidate,
    _publication,
)


def test_published_erc_bytes_are_current_then_stale_in_new_process(tmp_path: Path) -> None:
    catalog, project, _session, detail = _runtime_with_approved_plan(tmp_path)
    project_id = project.project_id
    base_production = detail.schematic_production
    assert base_production is not None
    schematic = base_production.proposal
    request = base_production.request
    decision = decide_schematic_proposal(
        schematic, decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer="engineer-1", decided_at=NOW,
    )
    artifact_path = "hardware/main.kicad_sch"
    artifact = catalog.knowledge_snapshot_root(project_id, tenant_id="tenant-1") / artifact_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"offline schematic fixture")
    artifact_fp = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    source_receipt_fp = "sha256:" + "a" * 64
    parse = SchematicParseValidationEvidence.create(
        validation_id="s01-parse", project_id=project_id,
        conversation_id=detail.conversation_id, operation_id="s01-source-write",
        artifact_path=artifact_path, artifact_fingerprint=artifact_fp,
        kicad_cli_path="offline-fixture", kicad_version="fixture-version",
        command_identity="offline-parse-fixture", exit_code=0,
        validation_status="PARSE_VERIFIED", artifact_unchanged=True,
        stdout_summary="fixture", stderr_summary="", validated_at=NOW,
    )
    generation = SchematicGenerationReceipt.create(
        receipt_id="s01-generation", project_id=project_id,
        conversation_id=detail.conversation_id, operation_id="s01-source-write",
        operation_fingerprint="sha256:" + "8" * 64,
        operation_receipt_fingerprint=source_receipt_fp,
        workspace_apply_receipt_fingerprint="sha256:" + "9" * 64,
        artifact_path=artifact_path,
        schematic_plan_id=request.schematic_plan_id,
        schematic_plan_fingerprint=request.schematic_plan_fingerprint,
        design_truth_fingerprint=schematic.design_truth_fingerprint,
        artifact_fingerprint=artifact_fp, generator="offline-fixture",
        generator_version="1", validation_status="PARSE_VERIFIED",
        parse_validation=parse, status=SchematicGenerationStatus.SUCCESS,
        source_refs=("s01-source-write",), created_at=NOW,
    )
    result = SchematicErcResult.create(
        operation_id="s01-erc-operation", schematic_artifact_ref=artifact_path,
        schematic_content_fingerprint=artifact_fp, kicad_version="fixture-version",
        exit_status=0, started_at=NOW, finished_at=NOW,
        receipt_ref=source_receipt_fp, status=SchematicErcStatus.ERC_PASS,
    )
    erc_receipt_fp = "sha256:" + "b" * 64
    erc = SchematicErcReceipt.create(
        receipt_id="s01-erc-receipt", project_id=project_id,
        conversation_id=detail.conversation_id, operation_id=result.operation_id,
        operation_receipt_fingerprint=erc_receipt_fp,
        artifact_path=artifact_path, artifact_fingerprint=artifact_fp,
        schematic_plan_fingerprint=request.schematic_plan_fingerprint,
        design_truth_fingerprint=schematic.design_truth_fingerprint,
        kicad_version=result.kicad_version, command_identity=result.command_identity,
        tool_execution_status="SUCCEEDED", engineering_status=result.engineering_status,
        result=result, created_at=NOW,
    )
    production = SchematicProductionState.create(
        production_id=base_production.production_id, project_id=project_id,
        conversation_id=detail.conversation_id, request=request,
        proposal=schematic, decision=decision, operation_id="s01-source-write",
        operation_fingerprint=generation.operation_fingerprint,
        status=SchematicProductionStatus.GENERATED,
        generation_receipt=generation, erc_receipt=erc,
        erc_gate_status=SchematicErcGateStatus.PASS,
    )
    turn = ConversationTurn(
        turn_id="turn-design", request_id="request-design", route="GREENFIELD_DESIGN",
        status=ConversationTurnStatus.COMPLETED, question="Circuit intent",
        answer_text="Design analysis", created_at=NOW, completed_at=NOW,
    )
    detail = ConversationDetail.model_validate(detail.model_copy(update={
        "schematic_production": production,
        "turns": (*detail.turns, turn), "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog._repository.save_conversation(detail)
    candidate = _candidate(detail)
    reference = EvidenceReceiptReference(
        reference_id="s01-erc-evidence", receipt_id=result.operation_id,
        tenant_id="tenant-1", project_id=project_id,
        source="KICAD_EXECUTION_WORKER", tool=result.command_identity,
        scope="SCHEMATIC_ERC", result_summary="ERC passed", occurred_at=NOW,
        source_fingerprint=artifact_fp, receipt_fingerprint=erc_receipt_fp,
        artifact_ref=artifact_path, artifact_fingerprint=artifact_fp,
        schematic_revision="r1", tool_version=result.kicad_version,
        execution_result="ERC_PASS",
    )
    candidate = candidate.model_copy(update={"evidence_reference": reference})
    catalog.save_circuit_proposal(candidate, tenant_id="tenant-1", requester="engineer-1")
    approval = _approval(candidate)
    catalog.decide_circuit_proposal(
        project_id, candidate.proposal.proposal_id,
        tenant_id="tenant-1", requester="engineer-1",
        proposal_fingerprint=candidate.proposal.fingerprint,
        decision=approval, publication=_publication(candidate, approval),
    )
    reader = """
import sys
from pathlib import Path
from embedded_copilot.project_catalog import ProjectCatalogRuntime
root = Path(sys.argv[1])
catalog = ProjectCatalogRuntime(project_root=root / 'projects', state_database=root / 'desktop.sqlite3', initialize_async=False)
print('CURRENT=' + str(catalog.circuit_source_current(sys.argv[2], 'circuit-1', 'r1', tenant_id='tenant-1', requester='engineer-1')))
"""

    def recovered_current() -> bool:
        process = subprocess.run(
            [sys.executable, "-B", "-c", reader, str(tmp_path), project_id],
            capture_output=True, text=True, check=True,
        )
        return "CURRENT=True" in process.stdout

    assert recovered_current()
    artifact.write_bytes(b"changed after publication")
    assert not recovered_current()
    artifact.unlink()
    assert not recovered_current()
