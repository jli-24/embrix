from __future__ import annotations

import copy
from datetime import timedelta

from .audit import append_hardware_audit
from .contracts import deterministic_id
from .exceptions import (
    HardwareApprovalRequired,
    HardwareRejected,
    HardwareResourceDenied,
    HardwareUnavailable,
)
from .models import (
    FirmwareArtifactReference,
    HardwareAuditEventType,
    HardwareBuildReceipt,
    HardwareBuildRequest,
    HardwareCapability,
    HardwareFlashReceipt,
    HardwareFlashRequest,
    HardwareObservation,
    HardwareObservationRequest,
    HardwareOperationPlan,
    HardwareOperationType,
    HardwareReviewRecord,
    HardwareReviewRequest,
    HardwareRisk,
    HardwareTargetReference,
    approval_from_record,
)
from .ports import (
    HardwareAuditSinkPort,
    HardwareBuildPort,
    HardwareClockPort,
    HardwareFlashPort,
    HardwareMeasurementPort,
    HardwareOperationPlanStorePort,
    HardwareResourcePolicyPort,
    HardwareReviewPort,
    HardwareTargetSourcePort,
)


class HardwareRuntimeService:
    __slots__ = (
        "_audit",
        "_build",
        "_clock",
        "_flash",
        "_measure",
        "_plans",
        "_policy",
        "_reviews",
        "_targets",
    )

    def __init__(
        self,
        *,
        targets: HardwareTargetSourcePort,
        resource_policy: HardwareResourcePolicyPort,
        build_port: HardwareBuildPort,
        flash_port: HardwareFlashPort,
        measurement_port: HardwareMeasurementPort,
        reviews: HardwareReviewPort,
        plans: HardwareOperationPlanStorePort,
        audit: HardwareAuditSinkPort,
        clock: HardwareClockPort,
    ) -> None:
        dependencies = (
            (targets, HardwareTargetSourcePort),
            (resource_policy, HardwareResourcePolicyPort),
            (build_port, HardwareBuildPort),
            (flash_port, HardwareFlashPort),
            (measurement_port, HardwareMeasurementPort),
            (reviews, HardwareReviewPort),
            (plans, HardwareOperationPlanStorePort),
            (audit, HardwareAuditSinkPort),
            (clock, HardwareClockPort),
        )
        if any(not isinstance(value, port) for value, port in dependencies):
            raise TypeError("hardware runtime dependency is invalid")
        self._targets = targets
        self._policy = resource_policy
        self._build = build_port
        self._flash = flash_port
        self._measure = measurement_port
        self._reviews = reviews
        self._plans = plans
        self._audit = audit
        self._clock = clock

    @staticmethod
    def _checked(value, model, field: str):
        if type(value) is not model:
            raise HardwareRejected(f"{field} is invalid")
        try:
            return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        except Exception as error:
            raise HardwareRejected(f"{field} is invalid") from error

    def _target(self, expected: HardwareTargetReference) -> HardwareTargetReference:
        try:
            raw = self._targets.get_target(expected.project_id, expected.device_id)
            if raw is None:
                raise HardwareUnavailable("hardware target unavailable")
            target = self._checked(raw, HardwareTargetReference, "target")
        except HardwareUnavailable:
            raise
        except Exception as error:
            raise HardwareUnavailable("hardware target unavailable") from error
        if target != expected:
            raise HardwareRejected("hardware target drift")
        return target

    def observe(self, request: HardwareObservationRequest) -> HardwareObservation:
        checked = self._checked(
            request, HardwareObservationRequest, "observation request"
        )
        self._target(checked.target)
        capability = checked.observation_type.capability
        try:
            grant = self._policy.authorize(copy.deepcopy(checked), capability)
        except Exception as error:
            raise HardwareResourceDenied("hardware resource denied") from error
        if (
            grant.project_id != checked.project_id
            or grant.requester != checked.requester
            or grant.target_fingerprint != checked.target.fingerprint
            or capability not in grant.capabilities
        ):
            raise HardwareResourceDenied("hardware resource grant mismatch")
        try:
            observation = self._checked(
                self._measure.observe(copy.deepcopy(checked)),
                HardwareObservation,
                "observation",
            )
        except HardwareRejected:
            raise
        except Exception as error:
            raise HardwareUnavailable("hardware observation unavailable") from error
        if (
            observation.request_fingerprint != checked.fingerprint
            or observation.project_id != checked.project_id
            or observation.target != checked.target
            or observation.observation_type is not checked.observation_type
        ):
            raise HardwareRejected("hardware observation mismatch")
        append_hardware_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=HardwareAuditEventType.OBSERVATION_COMPLETED,
            project_id=checked.project_id,
            operation_fingerprint=checked.fingerprint,
            target_fingerprint=checked.target.fingerprint,
            result_fingerprint=observation.fingerprint,
            timestamp=observation.observed_at,
        )
        return observation

    def prepare_build(
        self,
        *,
        project_id: str,
        target: HardwareTargetReference,
        actor: str,
        reason: str,
        risk: HardwareRisk,
    ) -> HardwareOperationPlan:
        checked_target = self._target(
            self._checked(target, HardwareTargetReference, "target")
        )
        if HardwareCapability.BUILD not in checked_target.available_capabilities:
            raise HardwareRejected("build capability unavailable")
        plan = HardwareOperationPlan(
            plan_id=deterministic_id(
                "hardware-plan",
                project_id,
                target.fingerprint,
                "BUILD",
                self._clock.now().isoformat(),
            ),
            project_id=project_id,
            operation=HardwareOperationType.BUILD,
            target=checked_target,
            firmware_artifact=None,
            risk=risk,
            actor=actor,
            reason=reason,
            created_at=self._clock.now(),
        )
        return self._checked(
            self._plans.save(plan), HardwareOperationPlan, "stored plan"
        )

    def prepare_flash(
        self,
        *,
        artifact: FirmwareArtifactReference,
        actor: str,
        reason: str,
        risk: HardwareRisk,
    ) -> HardwareOperationPlan:
        checked_artifact = self._checked(
            artifact, FirmwareArtifactReference, "firmware artifact"
        )
        target = self._target(checked_artifact.target)
        if HardwareCapability.FLASH not in target.available_capabilities:
            raise HardwareRejected("flash capability unavailable")
        plan = HardwareOperationPlan(
            plan_id=deterministic_id(
                "hardware-plan",
                artifact.fingerprint,
                "FLASH",
                self._clock.now().isoformat(),
            ),
            project_id=artifact.project_id,
            operation=HardwareOperationType.FLASH,
            target=target,
            firmware_artifact=checked_artifact,
            risk=risk,
            actor=actor,
            reason=reason,
            created_at=self._clock.now(),
        )
        return self._checked(
            self._plans.save(plan), HardwareOperationPlan, "stored plan"
        )

    def submit_review(self, plan: HardwareOperationPlan) -> HardwareReviewRecord:
        checked = self._checked(plan, HardwareOperationPlan, "operation plan")
        stored = self._plans.get(checked.plan_id)
        if stored is None or stored != checked:
            raise HardwareRejected("operation plan drift")
        request = HardwareReviewRequest(
            review_id=deterministic_id("hardware-review", checked.fingerprint),
            project_id=checked.project_id,
            plan_fingerprint=checked.fingerprint,
            operation=checked.operation,
            target_fingerprint=checked.target.fingerprint,
            firmware_fingerprint=None
            if checked.firmware_artifact is None
            else checked.firmware_artifact.firmware_fingerprint,
            requester=checked.actor,
            requested_at=self._clock.now(),
        )
        record = self._checked(
            self._reviews.submit(request), HardwareReviewRecord, "review record"
        )
        if record.request != request:
            raise HardwareRejected("hardware review mismatch")
        append_hardware_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=HardwareAuditEventType.REVIEW_SUBMITTED,
            project_id=checked.project_id,
            operation_fingerprint=request.fingerprint,
            target_fingerprint=checked.target.fingerprint,
            result_fingerprint=record.fingerprint,
            timestamp=request.requested_at,
        )
        return record

    def _approval(self, plan_id: str, review_id: str, operation: HardwareOperationType):
        raw_plan = self._plans.get(plan_id)
        raw_record = self._reviews.get_record(review_id)
        if raw_plan is None or raw_record is None:
            raise HardwareApprovalRequired("hardware approval unavailable")
        plan = self._checked(raw_plan, HardwareOperationPlan, "operation plan")
        record = self._checked(raw_record, HardwareReviewRecord, "review record")
        if plan.operation is not operation:
            raise HardwareApprovalRequired("hardware approval capability mismatch")
        try:
            approval = approval_from_record(
                record, maximum_age=timedelta(days=7), now=self._clock.now()
            )
        except ValueError as error:
            raise HardwareApprovalRequired("hardware approval required") from error
        if (
            approval.plan_fingerprint != plan.fingerprint
            or approval.target_fingerprint != plan.target.fingerprint
        ):
            raise HardwareApprovalRequired("hardware approval mismatch")
        self._target(plan.target)
        return plan, approval

    def authorize_build(self, plan_id: str, review_id: str) -> HardwareBuildRequest:
        plan, approval = self._approval(plan_id, review_id, HardwareOperationType.BUILD)
        return HardwareBuildRequest(
            request_id=deterministic_id(
                "build", plan.fingerprint, approval.fingerprint
            ),
            project_id=plan.project_id,
            plan=plan,
            approval=approval,
            requested_at=self._clock.now(),
        )

    def build(self, request: HardwareBuildRequest) -> HardwareBuildReceipt:
        checked = self._checked(request, HardwareBuildRequest, "build request")
        self._target(checked.plan.target)
        record = self._reviews.get_record(checked.approval.review_id)
        if record is None or record.fingerprint != checked.approval.record_fingerprint:
            raise HardwareApprovalRequired("build approval changed")
        receipt = self._checked(
            self._build.build(copy.deepcopy(checked)),
            HardwareBuildReceipt,
            "build receipt",
        )
        if (
            receipt.request_fingerprint != checked.fingerprint
            or receipt.target_fingerprint != checked.plan.target.fingerprint
        ):
            raise HardwareRejected("build receipt mismatch")
        append_hardware_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=HardwareAuditEventType.BUILD_COMPLETED,
            project_id=checked.project_id,
            operation_fingerprint=checked.fingerprint,
            target_fingerprint=checked.plan.target.fingerprint,
            result_fingerprint=receipt.fingerprint,
            timestamp=receipt.completed_at,
        )
        return receipt

    def authorize_flash(
        self, plan_id: str, review_id: str, build_receipt: HardwareBuildReceipt
    ) -> HardwareFlashRequest:
        plan, approval = self._approval(plan_id, review_id, HardwareOperationType.FLASH)
        checked_build = self._checked(
            build_receipt, HardwareBuildReceipt, "build receipt"
        )
        return HardwareFlashRequest(
            request_id=deterministic_id(
                "flash",
                plan.fingerprint,
                approval.fingerprint,
                checked_build.fingerprint,
            ),
            project_id=plan.project_id,
            plan=plan,
            approval=approval,
            build_receipt=checked_build,
            requested_at=self._clock.now(),
        )

    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt:
        checked = self._checked(request, HardwareFlashRequest, "flash request")
        self._target(checked.plan.target)
        record = self._reviews.get_record(checked.approval.review_id)
        if record is None or record.fingerprint != checked.approval.record_fingerprint:
            raise HardwareApprovalRequired("flash approval changed")
        receipt = self._checked(
            self._flash.flash(copy.deepcopy(checked)),
            HardwareFlashReceipt,
            "flash receipt",
        )
        firmware = checked.plan.firmware_artifact
        if (
            firmware is None
            or receipt.request_fingerprint != checked.fingerprint
            or receipt.target_fingerprint != checked.plan.target.fingerprint
            or receipt.firmware_fingerprint != firmware.firmware_fingerprint
        ):
            raise HardwareRejected("flash receipt mismatch")
        append_hardware_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=HardwareAuditEventType.FLASH_COMPLETED,
            project_id=checked.project_id,
            operation_fingerprint=checked.fingerprint,
            target_fingerprint=checked.plan.target.fingerprint,
            result_fingerprint=receipt.fingerprint,
            timestamp=receipt.completed_at,
        )
        return receipt


__all__ = ("HardwareRuntimeService",)
