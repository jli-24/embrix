from __future__ import annotations

import copy

from embedded_copilot.engineering_review import ReviewRecord
from embedded_copilot.failure_analysis import FailureAnalysis
from embedded_copilot.repair_proposal import RepairProposal

from .models import (
    ApplyReceipt,
    ApplyRequest,
    ArtifactStateReference,
    PatchProposal,
    RepairBuildReceipt,
    RepairBuildRequest,
    RepairEvent,
    RepairPlan,
    RepairPreparation,
    RepairRequest,
    RepairResult,
    RepairReviewRecord,
    RepairReviewRequest,
    RepairVerificationReceipt,
    RepairVerificationRequest,
)


def _exact(value: object, model: type, *, message: str):
    if type(value) is not model:
        raise TypeError(message)
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_failure_analysis(value: object) -> FailureAnalysis:
    return _exact(value, FailureAnalysis, message="failure analysis is invalid")


def validate_existing_repair_proposal(value: object) -> RepairProposal:
    return _exact(value, RepairProposal, message="existing repair proposal is invalid")


def validate_legacy_review_record(value: object) -> ReviewRecord:
    return _exact(value, ReviewRecord, message="legacy review record is invalid")


def validate_repair_request(value: object) -> RepairRequest:
    return _exact(value, RepairRequest, message="repair request is invalid")


def validate_artifact_state(value: object) -> ArtifactStateReference:
    return _exact(value, ArtifactStateReference, message="artifact state is invalid")


def validate_repair_plan(value: object) -> RepairPlan:
    return _exact(value, RepairPlan, message="repair plan is invalid")


def validate_patch_proposal(value: object) -> PatchProposal:
    return _exact(value, PatchProposal, message="patch proposal is invalid")


def validate_preparation(value: object) -> RepairPreparation:
    return _exact(value, RepairPreparation, message="repair preparation is invalid")


def validate_review_request(value: object) -> RepairReviewRequest:
    return _exact(value, RepairReviewRequest, message="repair review request is invalid")


def validate_review_record(value: object) -> RepairReviewRecord:
    return _exact(value, RepairReviewRecord, message="repair review record is invalid")


def validate_apply_request(value: object) -> ApplyRequest:
    return _exact(value, ApplyRequest, message="repair apply request is invalid")


def validate_apply_receipt(value: object) -> ApplyReceipt:
    return _exact(value, ApplyReceipt, message="repair apply receipt is invalid")


def validate_build_request(value: object) -> RepairBuildRequest:
    return _exact(value, RepairBuildRequest, message="repair build request is invalid")


def validate_build_receipt(value: object) -> RepairBuildReceipt:
    return _exact(value, RepairBuildReceipt, message="repair build receipt is invalid")


def validate_verification_request(value: object) -> RepairVerificationRequest:
    return _exact(
        value,
        RepairVerificationRequest,
        message="repair verification request is invalid",
    )


def validate_verification_receipt(value: object) -> RepairVerificationReceipt:
    return _exact(
        value,
        RepairVerificationReceipt,
        message="repair verification receipt is invalid",
    )


def validate_repair_result(value: object) -> RepairResult:
    return _exact(value, RepairResult, message="repair result is invalid")


def validate_repair_event(value: object) -> RepairEvent:
    return _exact(value, RepairEvent, message="repair event is invalid")


__all__ = tuple(name for name in globals() if name.startswith("validate_"))
