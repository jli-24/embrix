from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_validation import (
    ValidationArtifactReference,
    ValidationFinding,
    ValidationFindingStatus,
    ValidationReport,
    ValidationRequest,
    ValidationRule,
    ValidationSeverity,
)


def context_snapshot(*, project_id: str = "project-1") -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id=project_id,
        task_description="validate demo board",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=1.0,
        provenance=(),
        verification_status="APPROVED",
    )


def artifact(**overrides: object) -> ValidationArtifactReference:
    values: dict[str, object] = {
        "artifact_id": "demo-board",
        "project_id": "project-1",
        "artifact_type": "pcb_board",
        "source_reference": "artifact://demo-board",
    }
    values.update(overrides)
    return ValidationArtifactReference.create(**values)


def request(**overrides: object) -> ValidationRequest:
    values: dict[str, object] = {
        "request_id": "validation-request-1",
        "project_id": "project-1",
        "artifact_reference": artifact(),
        "context_snapshot": context_snapshot(),
        "knowledge_refs": ("knowledge://power-rule",),
        "validation_scope": ("hardware_power",),
    }
    values.update(overrides)
    return ValidationRequest.create(**values)


def finding(**overrides: object) -> ValidationFinding:
    values: dict[str, object] = {
        "finding_id": "finding-power-1",
        "project_id": "project-1",
        "rule_id": "POWER-001",
        "category": "hardware_power",
        "severity": ValidationSeverity.WARNING,
        "status": ValidationFindingStatus.OPEN,
        "message": "possible voltage mismatch",
        "evidence_refs": ("knowledge://power-rule",),
    }
    values.update(overrides)
    return ValidationFinding.create(**values)


def report(**overrides: object) -> ValidationReport:
    values: dict[str, object] = {
        "report_id": "report-validation-1",
        "project_id": "project-1",
        "findings": (finding(),),
        "summary": "findings=1; INFO=0; WARNING=1; ERROR=0; CRITICAL=0",
    }
    values.update(overrides)
    return ValidationReport.create(**values)


def test_contracts_are_frozen_strict_and_forbid_extra_fields() -> None:
    value = artifact()
    with pytest.raises(ValidationError):
        value.artifact_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ValidationArtifactReference.model_validate(
            {**value.model_dump(mode="python"), "unexpected": "value"}
        )
    with pytest.raises(ValidationError):
        ValidationFinding.model_validate(
            {
                **finding().model_dump(mode="python"),
                "severity": "WARNING",
            }
        )


def test_artifact_fingerprint_is_deterministic_dto_identity() -> None:
    first = artifact()
    second = artifact()
    changed = artifact(source_reference="workspace://demo-board")

    assert first == second
    assert first.fingerprint.startswith("sha256:")
    assert first.fingerprint != changed.fingerprint
    assert not hasattr(first, "content")

    with pytest.raises(ValidationError):
        ValidationArtifactReference.model_validate(
            {**first.model_dump(mode="python"), "fingerprint": "sha256:" + "f" * 64}
        )


@pytest.mark.parametrize(
    "source_reference",
    (
        "http://example.com/board",
        "https://example.com/board",
        "file:///tmp/board",
        "C:/private/board",
        r"\\server\share\board",
        "data:text/plain;base64,AAAA",
        "artifact://token=secret",
        "artifact://demo-board?credential=value",
        "artifact://demo%2fboard",
        "Artifact://demo-board",
    ),
)
def test_artifact_rejects_unsafe_source_references(source_reference: str) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        artifact(source_reference=source_reference)


def test_request_has_computed_fingerprint_outside_schema_and_revalidates_snapshot() -> None:
    snapshot = context_snapshot()
    value = request(context_snapshot=snapshot)
    repeated = request(context_snapshot=snapshot)

    assert value.fingerprint == repeated.fingerprint
    assert value.fingerprint.startswith("sha256:")
    assert "fingerprint" not in value.model_dump(mode="python")
    assert "fingerprint" not in ValidationRequest.model_json_schema()["properties"]
    assert value.context_snapshot == snapshot
    assert value.context_snapshot is not snapshot

    payload = value.model_dump(mode="python")
    payload["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError):
        ValidationRequest.model_validate(payload)


def test_request_requires_tuple_only_unique_sorted_references_and_scope() -> None:
    value = request(
        knowledge_refs=("knowledge://z-rule", "knowledge://a-rule"),
        validation_scope=("power_supply", "hardware_power"),
    )
    assert value.knowledge_refs == ("knowledge://a-rule", "knowledge://z-rule")
    assert value.validation_scope == ("hardware_power", "power_supply")

    with pytest.raises((TypeError, ValidationError)):
        request(knowledge_refs=["knowledge://power-rule"])
    with pytest.raises((TypeError, ValidationError)):
        request(validation_scope=["hardware_power"])
    with pytest.raises((ValueError, ValidationError)):
        request(knowledge_refs=("knowledge://power-rule", "knowledge://power-rule"))
    with pytest.raises((ValueError, ValidationError)):
        request(validation_scope=("hardware_power", "hardware_power"))
    with pytest.raises((ValueError, ValidationError)):
        request(knowledge_refs=("https://example.com/rule",))


def test_request_rejects_cross_project_artifact_and_snapshot() -> None:
    with pytest.raises((ValueError, ValidationError)):
        request(artifact_reference=artifact(project_id="project-2"))
    with pytest.raises((ValueError, ValidationError)):
        request(context_snapshot=context_snapshot(project_id="project-2"))


def test_text_is_nfc_normalized_and_raw_model_output_is_rejected() -> None:
    value = ValidationRule.create(
        rule_id="POWER-001",
        category="hardware_power",
        description="cafe\u0301 voltage rule",
    )
    assert value.description == "caf\u00e9 voltage rule"

    with pytest.raises((ValueError, ValidationError)):
        finding(message="raw model output: hidden")


def test_rule_finding_and_report_reject_fingerprint_tampering() -> None:
    rule = ValidationRule.create(
        rule_id="POWER-001",
        category="hardware_power",
        description="detect possible voltage mismatch",
    )
    for value, model in (
        (rule, ValidationRule),
        (finding(), ValidationFinding),
        (report(), ValidationReport),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(
                {
                    **value.model_dump(mode="python"),
                    "fingerprint": "sha256:" + "a" * 64,
                }
            )


def test_finding_fingerprint_excludes_human_decision_status() -> None:
    opened = finding()
    confirmed = ValidationFinding.model_validate(
        {
            **opened.model_dump(mode="python"),
            "status": ValidationFindingStatus.CONFIRMED,
        }
    )
    rejected = ValidationFinding.model_validate(
        {
            **opened.model_dump(mode="python"),
            "status": ValidationFindingStatus.REJECTED,
        }
    )

    assert confirmed.fingerprint == opened.fingerprint
    assert rejected.fingerprint == opened.fingerprint


def test_report_owns_immutable_revalidated_sorted_findings() -> None:
    later = finding(finding_id="finding-z")
    earlier = finding(finding_id="finding-a", message="possible rail mismatch")
    value = report(findings=(later, earlier))

    assert tuple(item.finding_id for item in value.findings) == (
        "finding-a",
        "finding-z",
    )
    assert value.findings[0] is not earlier
    assert copy.deepcopy(value) == value

    with pytest.raises((TypeError, ValidationError)):
        report(findings=[earlier, later])
    with pytest.raises((ValueError, ValidationError)):
        report(findings=(earlier, earlier))
