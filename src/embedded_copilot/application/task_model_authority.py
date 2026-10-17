"""Caller-bound, read-only Catalog authority for approved Plan model operations."""

from __future__ import annotations

import copy
import hashlib
from typing import Protocol

from embedded_copilot.engineering_copilot.contracts import (
    ArchitectureDesignStatus,
    ArchitectureReviewStatus,
    PlanPhaseStatus,
    PlanReviewStatus,
    PlanStatus,
    PlanTaskStatus,
)
from embedded_copilot.engineering_task_runtime.contracts import (
    CoordinationMetadata,
    EngineeringTaskCoordination,
    PlanReference,
    ProjectReference,
    RequestReference,
    StageReference,
    StageSourceReference,
    TaskIdentity,
    validate_coordination,
)
from embedded_copilot.engineering_task_runtime.models import EngineeringTaskRequest
from embedded_copilot.model_gateway.governance import EngineeringStage
from embedded_copilot.model_gateway.invocation.contracts import (
    TaskAuthorityFailure,
    TaskAuthorityReference,
)
from embedded_copilot.project_catalog.exceptions import (
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
)
from embedded_copilot.project_catalog.models import ConversationDetail


class CurrentConversationPort(Protocol):
    def conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str) -> ConversationDetail: ...


class CatalogTaskAuthoritySource:
    """Re-derives a Task from the current owned Catalog conversation on every read."""

    def __init__(
        self, catalog: CurrentConversationPort, *, tenant_id: str, requester: str,
        project_id: str, conversation_id: str, plan_id: str, phase_id: str,
        plan_task_id: str, operation_id: str,
    ) -> None:
        self._catalog = catalog
        self.bound_tenant_id = tenant_id
        self.bound_principal_id = requester
        self.bound_project_id = project_id
        self._conversation_id = conversation_id
        self._plan_id = plan_id
        self._phase_id = phase_id
        self._plan_task_id = plan_task_id
        self._operation_id = operation_id
        identity = f"{tenant_id}|{project_id}|{conversation_id}|{plan_id}|{phase_id}|{plan_task_id}|{operation_id}"
        self._task_id = "model-task-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    def _current(self):
        try:
            detail = self._catalog.conversation(
                self.bound_project_id, self._conversation_id,
                tenant_id=self.bound_tenant_id, requester=self.bound_principal_id,
            )
        except (ProjectCatalogForbidden, ProjectCatalogNotFound) as error:
            raise TaskAuthorityFailure("TASK_AUTHORITY_OWNER_UNAVAILABLE") from error
        except Exception as error:
            raise TaskAuthorityFailure("TASK_AUTHORITY_STALE") from error
        try:
            detail = ConversationDetail.model_validate(copy.deepcopy(detail.model_dump(mode="python")))
        except Exception as error:
            raise TaskAuthorityFailure("TASK_AUTHORITY_STALE") from error
        if (detail.tenant_id, detail.project_id, detail.requester, detail.conversation_id) != (
            self.bound_tenant_id, self.bound_project_id, self.bound_principal_id, self._conversation_id,
        ):
            raise TaskAuthorityFailure("TASK_AUTHORITY_STALE")
        plan, review, requirement = detail.engineering_plan, detail.engineering_plan_review, detail.requirement_context
        if (plan is None or review is None or requirement is None or plan.model_operation_schema_version != 2
            or plan.plan_id != self._plan_id or plan.project_id != self.bound_project_id
            or plan.context_fingerprint != requirement.fingerprint
            or plan.status is not PlanStatus.APPROVED or review.status is not PlanReviewStatus.APPROVED
            or review.plan_id != plan.plan_id or review.project_id != plan.project_id
            or review.proposal_fingerprint != plan.content_fingerprint):
            raise TaskAuthorityFailure("TASK_MODEL_OPERATION_UNAVAILABLE")
        architecture, architecture_review, workflow = (
            detail.architecture_design, detail.architecture_review, detail.engineering_workflow,
        )
        if (architecture is None or architecture_review is None or workflow is None
            or architecture.status is not ArchitectureDesignStatus.APPROVED
            or architecture_review.status is not ArchitectureReviewStatus.APPROVED
            or architecture.plan_id != plan.plan_id or architecture.plan_fingerprint != plan.fingerprint
            or architecture.context_fingerprint != requirement.fingerprint
            or architecture_review.architecture_id != architecture.architecture_id
            or architecture_review.plan_id != plan.plan_id
            or architecture_review.project_id != self.bound_project_id
            or architecture_review.proposal_fingerprint != architecture.content_fingerprint
            or workflow.plan_id != plan.plan_id or workflow.architecture_id != architecture.architecture_id
            or workflow.project_id != self.bound_project_id or workflow.conversation_id != self._conversation_id):
            raise TaskAuthorityFailure("TASK_AUTHORITY_STALE")
        phase = next((item for item in plan.phases if item.phase_id == self._phase_id), None)
        task = None if phase is None else next((item for item in phase.tasks if item.task_id == self._plan_task_id), None)
        if (phase is None or task is None or phase.status not in {PlanPhaseStatus.PENDING, PlanPhaseStatus.IN_PROGRESS}
            or task.status not in {PlanTaskStatus.PENDING, PlanTaskStatus.IN_PROGRESS}):
            raise TaskAuthorityFailure("TASK_MODEL_OPERATION_UNAVAILABLE")
        matches = [item for item in plan.model_operation_bindings if (
            item.phase_id, item.plan_task_id, item.operation_id
        ) == (phase.phase_id, task.task_id, self._operation_id)]
        if len(matches) != 1:
            raise TaskAuthorityFailure("TASK_MODEL_OPERATION_UNAVAILABLE")
        return detail, plan, review, phase, task, matches[0]

    def resolve_task_request(self, task_id: str) -> EngineeringTaskRequest | None:
        if task_id != self._task_id:
            return None
        detail, _, _, _, _, _ = self._current()
        sources = (detail.requirement_context, detail.architecture_design, detail.engineering_workflow)
        constraints = tuple(f"source:{index}:{source.fingerprint}" for index, source in enumerate(sources) if source is not None)
        return EngineeringTaskRequest(
            task_id=self._task_id, tenant_id=self.bound_tenant_id, project_id=self.bound_project_id,
            objective=self._operation_id, constraints=constraints, created_at=detail.created_at,
        )

    def resolve_plan(self, task_id: str, plan_id: str):
        if task_id != self._task_id or plan_id != self._plan_id:
            return None
        _, plan, review, _, _, _ = self._current()
        return plan, review

    def resolve_current(self) -> tuple[TaskAuthorityReference, EngineeringStage]:
        _, plan, review, phase, _, binding = self._current()
        request = self.resolve_task_request(self._task_id)
        assert request is not None
        plan_reference = PlanReference(
            plan_id=plan.plan_id, content_fingerprint=plan.content_fingerprint,
            review_fingerprint=review.fingerprint,
        )
        coordination = EngineeringTaskCoordination(
            task_identity=TaskIdentity(task_id=self._task_id, tenant_id=self.bound_tenant_id, project_id=self.bound_project_id),
            request_reference=RequestReference(task_id=self._task_id, request_fingerprint=request.fingerprint),
            project_reference=ProjectReference(tenant_id=self.bound_tenant_id, project_id=self.bound_project_id),
            plan_reference=plan_reference,
            current_stage_reference=StageReference(
                stage_id=phase.phase_id, plan_reference=plan_reference,
                source_reference=StageSourceReference(
                    source_type="APPROVED_PLAN_PHASE", phase_id=phase.phase_id, phase_fingerprint=phase.fingerprint,
                ),
            ),
            coordination_metadata=CoordinationMetadata(reference_id=self._task_id),
        )
        try:
            validate_coordination(coordination, self)
        except Exception as error:
            raise TaskAuthorityFailure("TASK_AUTHORITY_STALE") from error
        return TaskAuthorityReference(
            tenant_id=self.bound_tenant_id, project_id=self.bound_project_id,
            engineering_task_id=self._task_id, task_request_fingerprint=request.fingerprint,
            coordination_fingerprint=coordination.fingerprint, plan_id=plan.plan_id,
            plan_content_fingerprint=plan.content_fingerprint, review_fingerprint=review.fingerprint,
            phase_id=phase.phase_id, phase_fingerprint=phase.fingerprint,
            plan_task_id=self._plan_task_id, operation_id=self._operation_id,
            operation_binding_fingerprint=binding.fingerprint,
        ), binding.stage_id

    def revalidate(self, reference: TaskAuthorityReference) -> TaskAuthorityReference:
        try:
            current, _ = self.resolve_current()
        except TaskAuthorityFailure:
            raise
        except Exception as error:
            raise TaskAuthorityFailure("TASK_AUTHORITY_STALE") from error
        if current != reference:
            raise TaskAuthorityFailure("TASK_AUTHORITY_STALE")
        return current
