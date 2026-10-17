from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_pcb import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBContextRequest,
    PCBProposal,
    PCBProposalRisk,
    PCBProposalType,
    PCBSourceFormat,
)

from .conftest import BOARD_CONTENT, NOW, PROJECT, make_artifact, make_evidence


def test_contract_is_frozen(artifacts):
    with pytest.raises(ValidationError):
        artifacts[0].artifact_id = "changed"


def test_extra_fields_are_forbidden(artifacts):
    material = artifacts[0].model_dump(mode="python")
    material["payload"] = "forbidden"
    with pytest.raises(ValidationError):
        PCBArtifactReference.model_validate(material)


def test_tuple_only_is_enforced(artifacts):
    with pytest.raises((TypeError, ValidationError)):
        PCBContextRequest.create(
            request_id="request",
            project_id=PROJECT,
            artifacts=list(artifacts),
            datasheet_scope=(),
            engineering_task="Analyze PCB",
            requested_at=NOW,
        )


def test_nfc_and_trim_are_canonicalized():
    artifact = make_artifact(PCBArtifactType.BOARD, BOARD_CONTENT)
    material = artifact.model_dump(mode="python")
    material.pop("fingerprint")
    material["artifact_id"] = "  bo\u0061rd  "
    rebuilt = PCBArtifactReference.create(**material)
    assert rebuilt.artifact_id == "board"


def test_non_utc_time_is_rejected():
    with pytest.raises(ValidationError):
        PCBArtifactReference.create(
            project_id=PROJECT,
            artifact_id="board",
            artifact_type=PCBArtifactType.BOARD,
            source_format=PCBSourceFormat.KICAD_PCB,
            source_reference="artifact://pcb-project/board",
            filename="main.kicad_pcb",
            byte_size=1,
            content_fingerprint="sha256:" + "1" * 64,
            captured_at=NOW.replace(tzinfo=None),
        )


def test_artifact_format_binding_is_strict():
    with pytest.raises(ValidationError):
        PCBArtifactReference.create(
            project_id=PROJECT,
            artifact_id="board",
            artifact_type=PCBArtifactType.BOARD,
            source_format=PCBSourceFormat.CSV_BOM,
            source_reference="artifact://pcb-project/board",
            filename="main.csv",
            byte_size=1,
            content_fingerprint="sha256:" + "1" * 64,
            captured_at=NOW,
        )


def test_machine_path_is_rejected(artifacts):
    material = artifacts[0].model_dump(mode="python")
    material.pop("fingerprint")
    material["source_reference"] = "C:\\private\\board.kicad_pcb"
    with pytest.raises(ValidationError):
        PCBArtifactReference.create(**material)


def test_fingerprint_is_deterministic(artifacts):
    material = artifacts[0].model_dump(mode="python")
    material.pop("fingerprint")
    assert (
        PCBArtifactReference.create(**material).fingerprint == artifacts[0].fingerprint
    )


def test_fingerprint_tampering_is_rejected(artifacts):
    material = artifacts[0].model_dump(mode="python")
    material["artifact_id"] = "other"
    with pytest.raises(ValidationError):
        PCBArtifactReference.model_validate(material)


def test_nested_input_is_deep_copy_isolated(artifacts):
    raw = tuple(item.model_dump(mode="python") for item in artifacts)
    request = PCBContextRequest.create(
        request_id="request",
        project_id=PROJECT,
        artifacts=raw,
        datasheet_scope=(),
        engineering_task="Analyze PCB",
        requested_at=NOW,
    )
    raw[0]["artifact_id"] = "mutated"
    assert request.artifacts[0].artifact_id != "mutated"


@pytest.mark.parametrize(
    "text",
    ("password=abc", "raw model output", "hidden reasoning", "https://private.example"),
)
def test_unsafe_text_is_rejected(artifacts, text):
    evidence = make_evidence(artifacts[0])
    with pytest.raises(ValidationError):
        PCBProposal.create(
            proposal_id="proposal",
            project_id=PROJECT,
            finding_fingerprints=("sha256:" + "1" * 64,),
            target_artifact_fingerprints=(artifacts[0].fingerprint,),
            proposal_type=PCBProposalType.LAYOUT_REVIEW,
            recommended_changes=(text,),
            rationale="Review evidence",
            risk=PCBProposalRisk.LOW,
            evidence_refs=(evidence,),
        )


def test_proposal_rejects_executable_diff(artifacts):
    evidence = make_evidence(artifacts[0])
    with pytest.raises(ValidationError):
        PCBProposal.create(
            proposal_id="proposal",
            project_id=PROJECT,
            finding_fingerprints=("sha256:" + "1" * 64,),
            target_artifact_fingerprints=(artifacts[0].fingerprint,),
            proposal_type=PCBProposalType.LAYOUT_REVIEW,
            recommended_changes=("diff --git a b",),
            rationale="Review evidence",
            risk=PCBProposalRisk.LOW,
            evidence_refs=(evidence,),
        )
