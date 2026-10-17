from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_repair import (
    ArtifactStateReference,
    ChangeSetReference,
    EvidenceReference,
    PatchProposal,
    RepairPlanReference,
    RepairRequest,
    RepairRisk,
)

from ._factories import FP_B, NOW, analysis, evidence, request, state


def test_contracts_are_frozen_strict_and_extra_forbidden() -> None:
    value = evidence()
    with pytest.raises((ValidationError, FrozenInstanceError, TypeError)):
        value.evidence_id = "changed"
    with pytest.raises(ValidationError):
        EvidenceReference.model_validate(
            {**value.model_dump(mode="python"), "unknown": "value"}
        )
    with pytest.raises(ValidationError):
        EvidenceReference.model_validate(
            {
                "evidence_id": 12,
                "source": "test",
                "source_fingerprint": FP_B,
            }
        )


def test_text_is_trimmed_and_nfc_normalized() -> None:
    value = RepairRequest.create(
        **{
            **request().model_dump(mode="python", exclude={"fingerprint", "reason"}),
            "reason": "  cafe\u0301 repair  ",
        }
    )
    assert value.reason == "café repair"


def test_timestamp_requires_utc() -> None:
    values = request().model_dump(mode="python", exclude={"fingerprint"})
    values["requested_at"] = datetime(
        2026, 1, 2, 3, 4, tzinfo=timezone(timedelta(hours=8))
    )
    with pytest.raises(ValidationError):
        RepairRequest.create(**values)


def test_collections_are_tuple_only_and_require_evidence() -> None:
    values = request().model_dump(mode="python", exclude={"fingerprint"})
    values["evidence_refs"] = [evidence()]
    with pytest.raises(ValidationError):
        RepairRequest.create(**values)
    values["evidence_refs"] = ()
    with pytest.raises(ValidationError):
        RepairRequest.create(**values)


def test_nested_inputs_are_deep_copied() -> None:
    raw = request().model_dump(mode="python", exclude={"fingerprint"})
    value = RepairRequest.create(**raw)
    raw["target_reference"]["artifact_id"] = "artifact-changed"
    assert value.target_reference.artifact_id == "artifact-1"


def test_fingerprint_is_deterministic_and_tamper_is_rejected() -> None:
    first = request()
    second = request()
    assert first.fingerprint == second.fingerprint
    with pytest.raises(ValidationError):
        RepairRequest.model_validate(
            {**first.model_dump(mode="python"), "requester": "attacker"}
        )


def test_reference_project_and_target_bindings_fail_closed() -> None:
    raw = request().model_dump(mode="python", exclude={"fingerprint"})
    raw["project_id"] = "project-2"
    with pytest.raises(ValidationError):
        RepairRequest.create(**raw)


def _patch(description: str, reason: str) -> PatchProposal:
    before = state()
    after = ArtifactStateReference.create(
        project_id=before.project_id,
        artifact_id=before.artifact_id,
        revision_id="revision-2",
        content_fingerprint=FP_B,
    )
    return PatchProposal.create(
        patch_id="patch-1",
        project_id="project-1",
        plan_reference=RepairPlanReference.create(
            plan_id="plan-1", plan_fingerprint=FP_B
        ),
        target_artifact="artifact-1",
        before_state=before,
        after_state=after,
        change_set_reference=ChangeSetReference.create(
            change_set_id="change-set-1", change_set_fingerprint=FP_B
        ),
        change_description=description,
        reason=reason,
        risk=RepairRisk.LOW,
        evidence_refs=(evidence(analysis()),),
        actor="engineer-1",
        created_at=NOW,
    )


def test_patch_contract_has_no_directly_applicable_fields() -> None:
    assert {
        "source",
        "source_code",
        "diff",
        "unified_diff",
        "path",
        "command",
        "operations",
    }.isdisjoint(PatchProposal.model_fields)


@pytest.mark.parametrize(
    "description",
    (
        "diff --git a/main.c b/main.c",
        "@@ -1,2 +1,2 @@",
        "```c code ```",
        "#include <stdio.h>",
        "git apply change.patch",
    ),
)
def test_patch_rejects_diff_source_and_apply_content(description: str) -> None:
    with pytest.raises(ValidationError):
        _patch(description, "Controlled repair description")


def test_patch_rejects_local_paths_and_credentials() -> None:
    with pytest.raises(ValidationError):
        _patch("Update C:\\private\\main.c", "Controlled repair")
    with pytest.raises(ValidationError):
        _patch("Reference state change", "password: secret")
