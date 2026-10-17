from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextRequest,
    EngineeringContextService,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
    ReadOnlyProjectionSource,
)
from embedded_copilot.engineering_memory.foundation import (
    DebugMemoryPayload,
    DecisionMemoryPayload,
    EvidenceMemoryPayload,
    EvidenceResultReference,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryType,
    ProjectMemoryPayload,
)
from embedded_copilot.engineering_memory.foundation_repository import (
    InMemoryEngineeringMemoryRepository,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryContextProjectionAdapter,
    EngineeringMemoryService,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
FP = "sha256:" + "f" * 64


def _record(
    memory_id: str,
    memory_type: FoundationMemoryType,
    payload: object,
) -> FoundationEngineeringMemoryRecord:
    return FoundationEngineeringMemoryRecord.create(
        memory_id=memory_id,
        tenant_id="tenant-1",
        project_id="project-1",
        memory_type=memory_type,
        payload=payload,
        created_at=NOW,
        source_references=(f"foundation:{memory_id}",),
    )


def _request() -> EngineeringContextRequest:
    return EngineeringContextRequest(
        tenant_id="tenant-1",
        project_id="project-1",
        task="STM32 PWM",
    )


def test_all_four_foundation_types_project_into_existing_evidence_channel() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    values = (
        _record(
            "project-memory",
            FoundationMemoryType.PROJECT,
            ProjectMemoryPayload(
                project_id="project-1",
                project_type="STM32",
                hardware_target="STM32F407",
                requirement_summary="PWM output",
                architecture_summary="task based control",
            ),
        ),
        _record(
            "decision-memory",
            FoundationMemoryType.DECISION,
            DecisionMemoryPayload(
                reason="availability",
                context="motor board",
                choice="STM32F407",
                timestamp=NOW,
            ),
        ),
        _record(
            "debug-memory",
            FoundationMemoryType.DEBUG,
            DebugMemoryPayload(
                symptom="PWM abnormal",
                root_cause="timer mismatch",
                fix="updated configuration",
                verification="build PASS",
            ),
        ),
        _record(
            "evidence-memory",
            FoundationMemoryType.EVIDENCE,
            EvidenceMemoryPayload(
                validation_result=EvidenceResultReference(
                    status="PASS",
                    source_reference="validation:E0",
                    fingerprint=FP,
                    artifact_reference="artifact:validation-1",
                )
            ),
        ),
    )
    for value in values:
        repository.save(value)
    service = EngineeringMemoryService(repository)
    adapter = EngineeringMemoryContextProjectionAdapter(
        service=service,
        source=ReadOnlyProjectionSource(),
    )
    snapshot = EngineeringContextService(source_port=adapter).get_context(_request())
    assert snapshot is not None
    assert len(snapshot.memory_evidence) == 4
    assert all(
        item.source_type is ContextSourceType.ENGINEERING_MEMORY
        and item.verification_status is ContextVerificationStatus.REQUIRES_REVIEW
        and item.trust_level is EvidenceTrustLevel.CANDIDATE
        and item.evidence_status is EvidenceVerificationStatus.CANDIDATE
        for item in snapshot.memory_evidence
    )


def test_candidate_memory_is_supplemental_and_does_not_override_source_evidence() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    repository.save(
        _record(
            "decision-memory",
            FoundationMemoryType.DECISION,
            DecisionMemoryPayload(
                reason="history",
                context="board",
                choice="STM32F407",
                timestamp=NOW,
            ),
        )
    )
    source = ReadOnlyProjectionSource()
    adapter = EngineeringMemoryContextProjectionAdapter(service=EngineeringMemoryService(repository), source=source)
    snapshot = EngineeringContextService(source_port=adapter).get_context(_request())
    assert snapshot is not None
    assert snapshot.memory_evidence[0].trust_level is EvidenceTrustLevel.CANDIDATE
    assert snapshot.memory_evidence[0].provenance == ("foundation-candidate",)
    assert snapshot.memory_evidence[0].verification_status is ContextVerificationStatus.REQUIRES_REVIEW


def test_without_foundation_injection_existing_source_remains_empty() -> None:
    snapshot = EngineeringContextService(source_port=ReadOnlyProjectionSource()).get_context(
        _request()
    )
    assert snapshot is None
