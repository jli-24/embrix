from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_runtime import (
    EvidenceObject,
    HardwareArchitectureObject,
    LifecycleStatus,
    Provenance,
    RequirementObject,
)


def provenance(source_ref: str = "user:req-001") -> Provenance:
    return Provenance.create(
        source="USER_INPUT",
        source_ref=source_ref,
        created_by="test-user",
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


def test_requirement_contract_contains_runtime_identity_and_fingerprint() -> None:
    requirement = RequirementObject.create(
        object_id="req-001",
        project_id="project-001",
        description="Design an STM32 control board.",
        source="user",
        acceptance_criteria=("3.3V supply", "CAN communication", "SWD debug"),
        verification_method="review and scoped bench verification",
        provenance=provenance(),
    )

    assert requirement.requirement_id == "req-001"
    assert requirement.status is LifecycleStatus.DRAFT
    assert requirement.fingerprint.startswith("sha256:")
    assert requirement.revision == "r1"


def test_runtime_object_rejects_unbound_project_provenance() -> None:
    with pytest.raises(ValueError):
        HardwareArchitectureObject.create(
            object_id="hw-001",
            project_id="project-001",
            mcu="STM32F103",
            peripherals=("CAN",),
            interfaces=("CAN", "SWD"),
            power_domains=("3V3",),
            constraints=("use approved MCU evidence",),
            provenance=provenance("project-002:hardware"),
        )


def test_evidence_requires_scope_and_timestamp() -> None:
    evidence = EvidenceObject.create(
        object_id="evidence-erc-001",
        project_id="project-001",
        source="KiCad",
        tool="ERC",
        result="PASS",
        timestamp=datetime(2026, 9, 21, tzinfo=UTC),
        scope="Electrical rule check only",
        canonical_evidence_ref="engineering-evidence:erc-001",
        provenance=provenance("tool:kicad-erc"),
    )

    assert evidence.status is LifecycleStatus.DRAFT
    assert evidence.scope == "Electrical rule check only"
    assert evidence.canonical_evidence_ref == "engineering-evidence:erc-001"
