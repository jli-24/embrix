from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_runtime import (
    ContextRequest,
    ContextResolutionError,
    EngineeringObjectRuntime,
    EvidenceObject,
    EvidenceQualificationStatus,
    LifecycleActor,
    LifecycleStatus,
    Provenance,
    RelationType,
    SchematicDesignObject,
)


NOW = datetime(2026, 9, 21, tzinfo=UTC)


def _provenance(ref: str) -> Provenance:
    return Provenance.create(
        source="TEST_FIXTURE",
        source_ref=ref,
        created_by="test",
        created_at=NOW,
    )


def _active(runtime: EngineeringObjectRuntime, value):
    runtime.register_object(value)
    for status, actor in (
        (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
        (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
        (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
    ):
        value = runtime.transition_object(value.object_id, value.revision, status, actor)
    return value


def _schematic(runtime: EngineeringObjectRuntime) -> SchematicDesignObject:
    return _active(
        runtime,
        SchematicDesignObject.create(
            object_id="schematic-qualification",
            project_id="project-qualification",
            symbols=("U1:MCU",),
            pins=("U1:PA0",),
            nets=("GPIO0",),
            artifact_ref="hardware/main.kicad_sch",
            artifact_fingerprint="sha256:" + "a" * 64,
            provenance=_provenance("project-qualification:schematic"),
        ),
    )


def _evidence(
    schematic: SchematicDesignObject,
    *,
    qualification: EvidenceQualificationStatus = EvidenceQualificationStatus.UNVERIFIED,
    result: str = "ERC_PASS",
) -> EvidenceObject:
    return EvidenceObject.create(
        object_id="evidence-qualification",
        project_id=schematic.project_id,
        source="KICAD_EXECUTION_WORKER",
        tool="kicad-cli sch erc",
        result=result,
        timestamp=NOW,
        scope="SCHEMATIC_ERC",
        canonical_evidence_ref="erc-receipt-qualification",
        qualification_status=qualification,
        subject_object_id=schematic.object_id,
        subject_revision=schematic.revision,
        subject_fingerprint=schematic.fingerprint,
        subject_artifact_ref=schematic.artifact_ref,
        subject_artifact_fingerprint=schematic.artifact_fingerprint,
        provenance=_provenance("project-qualification:receipt"),
    )


def test_unverified_evidence_cannot_satisfy_validated_by() -> None:
    runtime = EngineeringObjectRuntime(project_id="project-qualification")
    schematic = _schematic(runtime)
    evidence = _active(runtime, _evidence(schematic))

    with pytest.raises(ValueError, match="qualified evidence"):
        runtime.add_relation(schematic, RelationType.VALIDATED_BY, evidence)


def test_unverified_evidence_cannot_enter_qualified_context() -> None:
    runtime = EngineeringObjectRuntime(project_id="project-qualification")
    schematic = _schematic(runtime)
    evidence = _active(runtime, _evidence(schematic))

    with pytest.raises(ContextResolutionError, match="UNQUALIFIED"):
        runtime.resolve_context(
            ContextRequest.create(
                project_id=runtime.project_id,
                target_object_type="SCHEMATIC_DESIGN",
                required_object_ids=(schematic.object_id,),
                evidence_ids=(evidence.object_id,),
            )
        )


def test_failed_erc_result_is_evidence_but_not_a_pass_claim() -> None:
    runtime = EngineeringObjectRuntime(project_id="project-qualification")
    schematic = _schematic(runtime)
    evidence = _active(
        runtime,
        _evidence(
            schematic,
            qualification=EvidenceQualificationStatus.QUALIFIED,
            result="ERC_FAIL",
        ),
    )

    snapshot = runtime.resolve_context(
        ContextRequest.create(
            project_id=runtime.project_id,
            target_object_type="SCHEMATIC_DESIGN",
            required_object_ids=(schematic.object_id,),
            evidence_ids=(evidence.object_id,),
        )
    )

    assert snapshot.evidence_bundle[0].qualification_status is EvidenceQualificationStatus.QUALIFIED
    assert snapshot.evidence_bundle[0].result == "ERC_FAIL"
    assert snapshot.evidence_bundle[0].result != "ERC_PASS"
