from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    Artifact,
    ArtifactEvidence,
    ArtifactIntelligenceService,
    ArtifactType,
    DesignSource,
    DesignTruthBuilder,
    EngineeringEvidence,
    EngineeringEvidenceBundle,
    EngineeringEvidenceStatus,
    EvidenceType,
    PlanProvenance,
    TruthLevel,
    parse_kicad_schematic,
    transition_engineering_evidence,
)
from embedded_copilot.engineering_review import (
    HumanDecision,
    ReviewDecision,
    ReviewRequest,
    ReviewService,
    ReviewTargetReference,
    ReviewTargetType,
)
from embedded_copilot.engineering_review.adapters.fake import (
    FakeDecisionAuditSinkAdapter,
    FakeReviewStoreAdapter,
)

FP = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _artifact() -> Artifact:
    return Artifact.create(
        artifact_id="artifact-1",
        tenant_id="tenant-1",
        project_id="project-1",
        type=ArtifactType.SCHEMATIC,
        name="robot.kicad_sch",
        path_reference="hardware/robot.kicad_sch",
        hash=FP,
        metadata=(("conversation_id", "conversation-1"),),
    )


def _evidence(
    *,
    evidence_id: str = "evidence-1",
    evidence_type: EvidenceType = EvidenceType.SCHEMATIC_COMPONENT,
    claim: str = "U1 STM32F103",
    attributes: tuple[tuple[str, str], ...] = (
        ("reference", "U1"),
        ("name", "STM32F103"),
        ("category", "MCU"),
    ),
) -> EngineeringEvidence:
    return EngineeringEvidence.create(
        evidence_id=evidence_id,
        tenant_id="tenant-1",
        project_id="project-1",
        conversation_id="conversation-1",
        artifact_id="artifact-1",
        source=DesignSource.SCHEMATIC,
        evidence_type=evidence_type,
        claim_type="COMPONENT_EXISTS" if evidence_type is EvidenceType.SCHEMATIC_COMPONENT else "PIN_CONNECTED",
        claim=claim,
        locator="symbol:U1" if evidence_type is EvidenceType.SCHEMATIC_COMPONENT else "pin:PA8",
        truth_level=TruthLevel.EXPLICIT,
        confidence=1.0,
        artifact_version="v1",
        content_fingerprint=FP,
        attributes=attributes,
    )


def _bundle(evidence: tuple[EngineeringEvidence, ...] = ()) -> EngineeringEvidenceBundle:
    values = evidence or (_evidence(),)
    return EngineeringEvidenceBundle.create(
        bundle_id="bundle-1",
        tenant_id="tenant-1",
        project_id="project-1",
        conversation_id="conversation-1",
        artifact_fingerprints=(FP,),
        evidence=values,
    )


def _xlsx_bom() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>
            <row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>References</t></is></c><c r=\"B1\" t=\"inlineStr\"><is><t>Value</t></is></c></row>
            <row r=\"2\"><c r=\"A2\" t=\"inlineStr\"><is><t>U1</t></is></c><c r=\"B2\" t=\"inlineStr\"><is><t>STM32F103C8T6</t></is></c></row>
            </sheetData></worksheet>""",
        )
    return output.getvalue()


def test_evidence_bundle_lifecycle_keeps_content_binding_stable() -> None:
    bundle = _bundle()
    reviewing = transition_engineering_evidence(bundle, EngineeringEvidenceStatus.REVIEWING)
    approved = transition_engineering_evidence(reviewing, EngineeringEvidenceStatus.APPROVED)

    assert reviewing.bundle_fingerprint == bundle.bundle_fingerprint
    assert approved.bundle_fingerprint == bundle.bundle_fingerprint
    assert all(item.verification_status is EngineeringEvidenceStatus.APPROVED for item in approved.evidence)
    superseded = transition_engineering_evidence(
        approved, EngineeringEvidenceStatus.SUPERSEDED
    )
    assert all(
        item.verification_status is EngineeringEvidenceStatus.SUPERSEDED
        for item in superseded.evidence
    )
    assert superseded.bundle_fingerprint == bundle.bundle_fingerprint

    tampered = approved.model_dump(mode="python")
    tampered["evidence"][0]["claim"] = "U1 OTHER"
    with pytest.raises(ValidationError):
        EngineeringEvidenceBundle.model_validate(tampered)


def test_artifact_evidence_cannot_enter_truth_builder_directly() -> None:
    artifact_evidence = ArtifactEvidence.create(
        evidence_id="artifact-evidence-1",
        tenant_id="tenant-1",
        project_id="project-1",
        conversation_id="conversation-1",
        artifact_id="artifact-1",
        evidence_type=EvidenceType.SCHEMATIC_COMPONENT,
        source=DesignSource.SCHEMATIC,
        claim_type="COMPONENT_EXISTS",
        claim="U1 STM32F103",
        locator="symbol:U1",
        truth_level=TruthLevel.EXPLICIT,
        confidence=1.0,
        artifact_version="v1",
        content_fingerprint=FP,
        attributes=(("reference", "U1"), ("name", "STM32F103"), ("category", "MCU")),
    )
    with pytest.raises(TypeError):
        DesignTruthBuilder().build_from_approved_evidence(artifact_evidence)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="approved engineering evidence"):
        DesignTruthBuilder().build_from_approved_evidence(_bundle())


def test_reverse_builder_promotes_only_confirmed_hardware_bindings() -> None:
    pin = _evidence(
        evidence_id="evidence-pin",
        evidence_type=EvidenceType.SCHEMATIC_PIN,
        claim="PA8 connected MOTOR_PWM",
        attributes=(
            ("component_ref", "U1"),
            ("pin_name", "PA8"),
            ("pin_number", "PA8"),
            ("function", "TIM1_CH1 PWM"),
            ("signal", "MOTOR_PWM"),
        ),
    )
    bundle = _bundle((_evidence(), pin))
    approved = transition_engineering_evidence(
        transition_engineering_evidence(bundle, EngineeringEvidenceStatus.REVIEWING),
        EngineeringEvidenceStatus.APPROVED,
    )
    truth = DesignTruthBuilder().build_from_approved_evidence(approved)

    assert truth.origin.value == "REVERSE"
    assert truth.architecture_id is None
    assert truth.conversation_id == "conversation-1"
    assert any(item.pin_name == "PA8" for item in truth.pins)
    assert any(item.peripheral == "TIM1_CH1" for item in truth.peripherals)
    assert all(item.evidence_id for item in truth.evidence_bindings)


def test_artifact_intelligence_is_deterministic_and_read_only() -> None:
    schematic = b'''(kicad_sch (version 20231120) (generator eeschema)
      (symbol (lib_id MCU:STM32F103) (property "Reference" "U1") (property "Value" "STM32F103"))
      pin PA8 connected MOTOR_PWM)'''
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("robot.kicad_sch", schematic)
        archive.writestr("BOM.xlsx", _xlsx_bom())
        archive.writestr("firmware/main.c", b"void main(void) {}")

    service = ArtifactIntelligenceService()
    first = service.scan_zip(
        content.getvalue(),
        tenant_id="tenant-1",
        project_id="project-1",
        conversation_id="conversation-1",
    )
    second = service.scan_zip(
        content.getvalue(),
        tenant_id="tenant-1",
        project_id="project-1",
        conversation_id="conversation-1",
    )

    assert first.fingerprint == second.fingerprint
    assert first.coverage.schematic_status.value == "PARSED"
    assert first.coverage.bom_status.value == "PARSED"
    assert first.coverage.firmware_status.value == "AVAILABLE"
    assert first.engineering_evidence_bundle is not None
    assert first.engineering_evidence_bundle.status is EngineeringEvidenceStatus.CANDIDATE
    assert all(item.source is not DesignSource.FIRMWARE for item in first.engineering_evidence_bundle.evidence)


def test_schematic_artifact_can_carry_canonical_source_binding() -> None:
    schematic = b'''(kicad_sch (version 20231120) (generator eeschema)
      (symbol (lib_id MCU:STM32F103) (property "Reference" "U1") (property "Value" "STM32F103")))'''
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("hardware/robot.kicad_sch", schematic)
    snapshot = ArtifactIntelligenceService().scan_zip(
        content.getvalue(),
        tenant_id="tenant-1",
        project_id="project-1",
        conversation_id="conversation-1",
        source_operation_id="kicad-schematic-write-1",
        source_proposal_fingerprint=FP,
    )
    artifact = snapshot.inventory.artifacts[0]
    assert dict(artifact.metadata)["source_operation_id"] == "kicad-schematic-write-1"
    assert dict(artifact.metadata)["source_proposal_fingerprint"] == FP


def test_explicit_schematic_pin_and_net_bindings_promote_without_inference() -> None:
    schematic = b'''(kicad_sch (version 20231120) (generator eeschema)
      (symbol (lib_id MCU:STM32F103) (property "Reference" "U1") (property "Value" "STM32F103"))
      (symbol (lib_id Driver:TB6612) (property "Reference" "U2") (property "Value" "TB6612"))
      pin U1.PA8 connected MOTOR_PWM
      net MOTOR_PWM source U1 targets U2)'''
    artifact = _artifact()
    extracted = parse_kicad_schematic(artifact, schematic)

    assert any(item.evidence_type is EvidenceType.SCHEMATIC_PIN for item in extracted)
    assert any(item.evidence_type is EvidenceType.SCHEMATIC_NET for item in extracted)
    pin = next(item for item in extracted if item.evidence_type is EvidenceType.SCHEMATIC_PIN)
    assert dict(pin.attributes)["component_ref"] == "U1"

    converted_values = []
    for item in extracted:
        values = item.model_dump(mode="python")
        values.update(
            evidence_id=f"engineering-{item.evidence_id}",
            provenance=(
                PlanProvenance.create(
                    project_id=item.project_id,
                    source_type=item.source.value,
                    source_id=item.artifact_id,
                    source_fingerprint=item.content_fingerprint,
                ),
            ),
        )
        values.pop("fingerprint", None)
        converted_values.append(EngineeringEvidence.create(**values))
    converted = tuple(converted_values)
    bundle = EngineeringEvidenceBundle.create(
        bundle_id="bundle-schematic-explicit",
        tenant_id="tenant-1",
        project_id="project-1",
        conversation_id="conversation-1",
        artifact_fingerprints=(FP,),
        evidence=converted,
    )
    approved = transition_engineering_evidence(
        transition_engineering_evidence(bundle, EngineeringEvidenceStatus.REVIEWING),
        EngineeringEvidenceStatus.APPROVED,
    )
    truth = DesignTruthBuilder().build_from_approved_evidence(approved)

    assert any(item.pin_name == "PA8" for item in truth.pins)
    assert any(item.name == "MOTOR_PWM" for item in truth.signals)
    assert any(item.net_name == "MOTOR_PWM" for item in truth.nets)


def test_existing_review_service_accepts_engineering_evidence_bundle() -> None:
    bundle = _bundle()

    class Source:
        def get_target(self, project_id, target_type, target_id):
            assert project_id == "project-1"
            assert target_type is ReviewTargetType.ENGINEERING_EVIDENCE
            return bundle if target_id == bundle.bundle_id else None

    review = ReviewService(
        source=Source(),
        store=FakeReviewStoreAdapter(),
        audit_sink=FakeDecisionAuditSinkAdapter(),
    )
    request = ReviewRequest.create(
        review_id="review-bundle-1",
        project_id="project-1",
        target_reference=ReviewTargetReference.create(
            target_type=ReviewTargetType.ENGINEERING_EVIDENCE,
            target_id=bundle.bundle_id,
            target_fingerprint=bundle.bundle_fingerprint,
        ),
        requester="engineer-1",
        created_at=NOW,
    )
    stored = review.create_review(request)
    record = review.decide(
        stored.review_id,
        HumanDecision.create(
            decision_id="decision-bundle-1",
            review_id=stored.review_id,
            project_id="project-1",
            decision=ReviewDecision.APPROVE,
            reviewer="reviewer-1",
            comment="Evidence reviewed.",
            decided_at=NOW,
        ),
    )

    assert record.decision.decision is ReviewDecision.APPROVE
