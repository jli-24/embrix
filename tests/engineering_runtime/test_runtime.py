from __future__ import annotations

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


def provenance() -> Provenance:
    return Provenance.create(
        source="USER_INPUT",
        source_ref="user:req-001",
        created_by="test-user",
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


def make_requirement() -> RequirementObject:
    return RequirementObject.create(
        object_id="req-001",
        project_id="project-001",
        description="Design an STM32 control board.",
        source="user",
        acceptance_criteria=("3.3V supply", "CAN communication", "SWD debug"),
        verification_method="review",
        provenance=provenance(),
    )


def activate(runtime: EngineeringObjectRuntime, requirement: RequirementObject):
    runtime.register_object(requirement)
    runtime.transition_object(
        requirement.object_id,
        requirement.revision,
        LifecycleStatus.REVIEW,
        LifecycleActor.RUNTIME,
    )
    runtime.transition_object(
        requirement.object_id,
        requirement.revision,
        LifecycleStatus.APPROVED,
        LifecycleActor.REVIEWER,
    )
    return runtime.transition_object(
        requirement.object_id,
        requirement.revision,
        LifecycleStatus.ACTIVE,
        LifecycleActor.RUNTIME,
    )


def test_lifecycle_blocks_agent_from_modifying_active_object() -> None:
    runtime = EngineeringObjectRuntime(project_id="project-001")
    requirement = activate(runtime, make_requirement())

    with pytest.raises(LifecycleViolation):
        runtime.transition_object(
            requirement.object_id,
            requirement.revision,
            LifecycleStatus.STALE,
            LifecycleActor.AGENT,
        )

    active = runtime.get_active_revision(requirement.object_id)
    assert active.status is LifecycleStatus.ACTIVE
    assert active.fingerprint == requirement.fingerprint


def test_revision_manager_creates_new_draft_revision_without_overwriting_active() -> (
    None
):
    runtime = EngineeringObjectRuntime(project_id="project-001")
    requirement = activate(runtime, make_requirement())

    revised = runtime.create_revision(
        requirement.object_id,
        {
            "description": "Design an STM32 control board with an observable CAN fault path.",
        },
        provenance=provenance(),
    )

    assert revised.revision == "r2"
    assert revised.status is LifecycleStatus.DRAFT
    assert revised.fingerprint != requirement.fingerprint
    assert runtime.get_active_revision(requirement.object_id).revision == "r1"
    assert len(runtime.list_revision(requirement.object_id)) == 2
