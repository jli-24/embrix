from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_memory.foundation import (
    DebugMemoryPayload,
    DecisionMemoryPayload,
    EvidenceMemoryPayload,
    EvidenceResultReference,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryQuery,
    FoundationMemoryType,
    ProjectMemoryPayload,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
FP = "sha256:" + "a" * 64


def _project_payload() -> ProjectMemoryPayload:
    return ProjectMemoryPayload(
        project_id="project-1",
        project_type="STM32",
        hardware_target="STM32F407",
        requirement_summary="PWM output control",
        architecture_summary="FreeRTOS task architecture",
    )


def _decision_payload() -> DecisionMemoryPayload:
    return DecisionMemoryPayload(
        reason="Peripheral compatibility",
        context="Motor control board",
        choice="STM32F407",
        timestamp=NOW,
    )


def _debug_payload() -> DebugMemoryPayload:
    return DebugMemoryPayload(
        symptom="PWM output abnormal",
        root_cause="TIM configuration mismatch",
        fix="Updated CubeMX configuration",
        verification="Build PASS",
    )


def _evidence_payload() -> EvidenceMemoryPayload:
    return EvidenceMemoryPayload(
        validation_result=EvidenceResultReference(
            status="PASS",
            source_reference="validation:E0-E5",
            fingerprint=FP,
            artifact_reference="artifact:validation-1",
        )
    )


def _record(**changes: object) -> FoundationEngineeringMemoryRecord:
    values: dict[str, object] = {
        "memory_id": "memory-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "memory_type": FoundationMemoryType.PROJECT,
        "payload": _project_payload(),
        "created_at": NOW,
        "source_references": ("source:project-1",),
    }
    values.update(changes)
    return FoundationEngineeringMemoryRecord.create(**values)


def test_all_four_foundation_payloads_are_valid() -> None:
    assert _record().memory_type is FoundationMemoryType.PROJECT
    assert _record(
        memory_id="memory-2",
        memory_type=FoundationMemoryType.DECISION,
        payload=_decision_payload(),
    ).memory_type is FoundationMemoryType.DECISION
    assert _record(
        memory_id="memory-3",
        memory_type=FoundationMemoryType.DEBUG,
        payload=_debug_payload(),
    ).memory_type is FoundationMemoryType.DEBUG
    assert _record(
        memory_id="memory-4",
        memory_type=FoundationMemoryType.EVIDENCE,
        payload=_evidence_payload(),
    ).memory_type is FoundationMemoryType.EVIDENCE


def test_evidence_requires_at_least_one_result() -> None:
    with pytest.raises(ValidationError):
        EvidenceMemoryPayload()


def test_models_are_strict_frozen_and_reject_extra_fields() -> None:
    record = _record()
    with pytest.raises((TypeError, ValidationError)):
        record.memory_id = "changed"
    with pytest.raises(ValidationError):
        ProjectMemoryPayload(
            **_project_payload().model_dump(),
            unexpected="x",
        )
    with pytest.raises(ValidationError):
        FoundationMemoryQuery(
            tenant_id="tenant-1",
            project_id="project-1",
            limit=True,
        )


def test_created_at_and_decision_timestamp_are_normalized_to_utc() -> None:
    plus_eight = datetime(2026, 8, 27, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    payload = DecisionMemoryPayload(
        reason="reason",
        context="context",
        choice="choice",
        timestamp=plus_eight,
    )
    record = _record(
        memory_type=FoundationMemoryType.DECISION,
        payload=payload,
        created_at=plus_eight,
    )
    assert record.created_at == NOW
    assert record.payload.timestamp == NOW


def test_source_references_are_deduplicated_and_sorted() -> None:
    record = _record(source_references=("source:z", "source:a", "source:z"))
    assert record.source_references == ("source:a", "source:z")


def test_fingerprint_is_deterministic_and_tampering_is_rejected() -> None:
    first = _record()
    second = _record()
    assert first.fingerprint == second.fingerprint
    tampered = first.model_dump(mode="python")
    tampered["fingerprint"] = "sha256:" + "b" * 64
    with pytest.raises(ValidationError):
        FoundationEngineeringMemoryRecord.model_validate(tampered)
    object.__setattr__(first.payload, "project_id", "project-2")
    with pytest.raises(ValidationError):
        FoundationEngineeringMemoryRecord.model_validate(first)


def test_payload_identity_must_match_record_project() -> None:
    with pytest.raises(ValidationError):
        _record(
            payload=ProjectMemoryPayload(
                **_project_payload().model_dump(mode="python") | {"project_id": "project-2"}
            )
        )


def test_sensitive_fields_and_raw_artifacts_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceResultReference(
            status="PASS",
            source_reference="source:1",
            api_key="secret-value",
        )
    with pytest.raises(ValidationError):
        EvidenceResultReference(
            status="raw stdout from test",
            source_reference="source:1",
        )
    with pytest.raises(ValidationError):
        EvidenceResultReference(
            status="PASS",
            source_reference="C:\\private\\result.log",
        )
    with pytest.raises(ValidationError):
        DebugMemoryPayload(
            symptom="stdout: raw process output",
            root_cause="timer mismatch",
            fix="updated configuration",
            verification="Build PASS",
        )


def test_control_characters_and_unbounded_source_references_are_rejected() -> None:
    with pytest.raises(ValidationError):
        DebugMemoryPayload(
            symptom="timer\x1b mismatch",
            root_cause="timer mismatch",
            fix="updated configuration",
            verification="Build PASS",
        )
    with pytest.raises(ValidationError):
        _record(
            source_references=tuple(f"source:{index}" for index in range(65))
        )


def test_normal_engineering_words_are_not_rejected_as_sensitive() -> None:
    payload = ProjectMemoryPayload(
        project_id="project-1",
        project_type="runtime controller",
        hardware_target="provider-independent target",
        requirement_summary="Use a token bucket for scheduling",
        architecture_summary="Prompt-independent engineering runtime",
    )
    assert payload.project_type == "runtime controller"
