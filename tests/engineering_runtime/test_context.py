from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_runtime import (
    BudgetLedgerObject,
    ContextRequest,
    ContextResolutionError,
    EngineeringObjectRuntime,
    EvidenceObject,
    EvidenceQualificationStatus,
    HardwareArchitectureObject,
    LifecycleActor,
    LifecycleStatus,
    Provenance,
    RelationType,
    RequirementObject,
)


def p(ref: str) -> Provenance:
    return Provenance.create(
        source="TEST_FIXTURE",
        source_ref=ref,
        created_by="test",
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


def activate(runtime: EngineeringObjectRuntime, obj):
    runtime.register_object(obj)
    for target, actor in (
        (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
        (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
        (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
    ):
        obj = runtime.transition_object(obj.object_id, obj.revision, target, actor)
    return obj


def test_circuit_context_contains_coherent_objects_evidence_and_constraints() -> None:
    runtime = EngineeringObjectRuntime(project_id="project-001")
    requirement = activate(
        runtime,
        RequirementObject.create(
            object_id="req-001",
            project_id="project-001",
            description="STM32 control board",
            source="user",
            acceptance_criteria=("3.3V supply", "CAN", "SWD"),
            verification_method="review",
            provenance=p("user:req"),
        ),
    )
    hardware = activate(
        runtime,
        HardwareArchitectureObject.create(
            object_id="hw-001",
            project_id="project-001",
            mcu="STM32F103",
            peripherals=("CAN",),
            interfaces=("CAN", "SWD"),
            power_domains=("3V3",),
            constraints=("3V3 rail", "SWD observability"),
            provenance=p("architecture:hw"),
        ),
    )
    budget = activate(
        runtime,
        BudgetLedgerObject.create(
            object_id="budget-001",
            project_id="project-001",
            entries=("POWER:3V3 rail", "TIMING:CAN service"),
            provenance=p("budget:power"),
        ),
    )
    _evidence = activate(
        runtime,
        EvidenceObject.create(
            object_id="evidence-datasheet-001",
            project_id="project-001",
            source="Datasheet",
            tool="document-review",
            result="MCU supply and CAN capability require exact-part verification",
            timestamp=datetime(2026, 9, 21, tzinfo=UTC),
            scope="MCU and CAN architecture constraints only",
            canonical_evidence_ref="engineering-evidence:datasheet-001",
            qualification_status=EvidenceQualificationStatus.QUALIFIED,
            provenance=p("datasheet:stm32"),
        ),
    )

    runtime.add_relation(requirement, RelationType.SATISFIED_BY, hardware)
    runtime.add_relation(hardware, RelationType.BOUNDED_BY, budget)

    snapshot = runtime.resolve_context(
        ContextRequest.create(
            project_id="project-001",
            target_object_type="CIRCUIT_INTENT",
            required_object_ids=("req-001", "hw-001", "budget-001"),
            evidence_ids=("evidence-datasheet-001",),
            constraints=("create power and CAN CircuitIntent",),
        )
    )

    assert snapshot.project_id == "project-001"
    assert {item.object_id for item in snapshot.objects} == {
        "req-001",
        "hw-001",
        "budget-001",
    }
    assert (
        snapshot.evidence_bundle[0].canonical_evidence_ref
        == "engineering-evidence:datasheet-001"
    )
    assert "3V3 rail" in snapshot.constraints
    assert snapshot.fingerprint.startswith("sha256:")

    runtime.mark_stale("hw-001")
    with pytest.raises(ContextResolutionError, match="STALE"):
        runtime.resolve_context(
            ContextRequest.create(
                project_id="project-001",
                target_object_type="CIRCUIT_INTENT",
                required_object_ids=("req-001", "hw-001", "budget-001"),
                evidence_ids=("evidence-datasheet-001",),
                constraints=("create power and CAN CircuitIntent",),
            )
        )
