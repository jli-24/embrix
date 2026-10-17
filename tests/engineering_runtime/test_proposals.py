from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_runtime import (
    EngineeringObjectRuntime,
    LifecycleActor,
    LifecycleStatus,
    LifecycleViolation,
    Provenance,
    RequirementObject,
)


def provenance(ref: str) -> Provenance:
    return Provenance.create(
        source="test",
        source_ref=ref,
        created_by="test-user",
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


def activate(
    runtime: EngineeringObjectRuntime, obj: RequirementObject
) -> RequirementObject:
    runtime.register_object(obj)
    runtime.transition_object(
        obj.object_id, obj.revision, LifecycleStatus.REVIEW, LifecycleActor.RUNTIME
    )
    runtime.transition_object(
        obj.object_id, obj.revision, LifecycleStatus.APPROVED, LifecycleActor.REVIEWER
    )
    return runtime.transition_object(
        obj.object_id, obj.revision, LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME
    )


def test_proposal_requires_approval_and_apply_creates_new_active_revision() -> None:
    runtime = EngineeringObjectRuntime(project_id="project-001")
    current = activate(
        runtime,
        RequirementObject.create(
            object_id="req-001",
            project_id="project-001",
            description="STM32 control board",
            source="user",
            acceptance_criteria=("3V3", "CAN", "SWD"),
            verification_method="review",
            provenance=provenance("user:req"),
        ),
    )
    proposed = RequirementObject.create(
        object_id=current.object_id,
        project_id=current.project_id,
        revision="r2",
        description="STM32 control board with explicit CAN fault observability",
        source="proposal",
        acceptance_criteria=current.acceptance_criteria,
        verification_method=current.verification_method,
        provenance=provenance("proposal:req-r2"),
    )
    runtime.create_proposal(
        proposed,
        context_fingerprint="sha256:" + "1" * 64,
        proposal_id="proposal-001",
        proposer="circuit-agent",
    )

    with pytest.raises(ValueError, match="approval"):
        runtime.apply_proposal("proposal-001")

    approval = runtime.approve_proposal(
        "proposal-001",
        approval_id="approval-001",
        reviewer="human-reviewer",
    )
    assert approval.proposal_id == "proposal-001"
    receipt = runtime.apply_proposal("proposal-001")

    assert receipt.status == "APPLIED"
    assert runtime.get_active_revision(current.object_id).revision == "r2"
    assert (
        runtime.get_active_revision(current.object_id).status is LifecycleStatus.ACTIVE
    )
    assert (
        runtime.get_object(current.object_id, "r1").status is LifecycleStatus.SUPERSEDED
    )


def test_agent_cannot_bypass_lifecycle_for_active_object() -> None:
    runtime = EngineeringObjectRuntime(project_id="project-001")
    current = activate(
        runtime,
        RequirementObject.create(
            object_id="req-002",
            project_id="project-001",
            description="Control board",
            source="user",
            acceptance_criteria=("3V3",),
            verification_method="review",
            provenance=provenance("user:req-002"),
        ),
    )
    with pytest.raises(LifecycleViolation):
        runtime.transition_object(
            current.object_id,
            current.revision,
            LifecycleStatus.STALE,
            LifecycleActor.AGENT,
        )
