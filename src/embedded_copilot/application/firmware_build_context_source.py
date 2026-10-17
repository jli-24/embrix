"""Read-only firmware build context and an explicit, fail-closed binding step.

The catalog, Workspace Apply, target registry and execution store retain ownership
of their facts.  Preview never persists a discovery candidate or proposal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from embedded_copilot.engineering_copilot.contracts import PlanReviewStatus, PlanStatus
from embedded_copilot.engineering_workspace_apply.models import (
    OperationStatus as ApplyStatus,
)
from embedded_copilot.engineering_workspace_apply.models import (
    RecoveryStatus,
    ValidationStatus,
)
from embedded_copilot.execution_worker.contracts import (
    BuildOperation,
    BuildProvenanceType,
    ExecutionProposal,
    ExecutionTarget,
    OperationType,
    TargetBindingStatus,
    TargetDiscoveryCandidate,
)
from embedded_copilot.firmware_engineering import (
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
)
from embedded_copilot.firmware_engineering.models import canonical_fingerprint
from embedded_copilot.firmware_engineering.readiness import (
    create_firmware_build_request,
    is_firmware_build_request,
)
from embedded_copilot.project_catalog.brownfield_build import BrownfieldBuildContext
from embedded_copilot.project_catalog.exceptions import (
    ProjectCatalogConflict,
    ProjectCatalogError,
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
)
from embedded_copilot.project_catalog.models import ProjectCreationMode

from .context_resolution import ContextSnapshot, WorkspaceAuthoritySnapshot
from .task_entry import BuildBindings


def _digest(*parts: object) -> str:
    data = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BuildContextInspection:
    status: str
    reason_code: str
    snapshot: ContextSnapshot | None = None
    context_version: str | None = None


class FirmwareBuildContextSource:
    """Narrow adapter for the existing EngineeringContextResolver source port."""

    def __init__(
        self,
        *,
        catalog: object,
        workspace_apply: object,
        execution: object,
        targets: object,
        tools: object,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._catalog = catalog
        self._workspace = workspace_apply
        self._execution = execution
        self._targets = targets
        self._tools = tools
        self._clock = clock or (lambda: datetime.now(UTC))

    def projects(self, *, tenant_id: str, requester: str) -> tuple:
        if not tenant_id or not requester:
            return ()
        owner_reader = getattr(self._catalog, "get_project_for_requester", None)
        if not callable(owner_reader):
            return ()
        try:
            owned = []
            for item in self._catalog.projects(tenant_id):
                try:
                    project = owner_reader(
                        item.project_id, tenant_id=tenant_id, requester=requester
                    )
                except (ProjectCatalogForbidden, ProjectCatalogNotFound):
                    continue
                if project.project_id != item.project_id or project.tenant_id != tenant_id:
                    return ()
                owned.append(project)
            return tuple(owned)
        except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return ()

    def snapshot(
        self, *, tenant_id: str, requester: str, project_id: str, conversation_id: str | None
    ) -> ContextSnapshot | None:
        inspected = self.inspect(
            tenant_id=tenant_id,
            requester=requester,
            project_id=project_id,
            conversation_id=conversation_id,
        )
        return inspected.snapshot if inspected.status == "READY" else None

    def inspect(
        self, *, tenant_id: str, requester: str, project_id: str, conversation_id: str | None
    ) -> BuildContextInspection:
        try:
            if not all((tenant_id, requester, project_id)):
                return BuildContextInspection("BLOCKED", "IDENTITY_MISSING")
            project = self._catalog.get_project_for_requester(
                project_id, tenant_id=tenant_id, requester=requester
            )
            detail = None if conversation_id is None else self._catalog.conversation(
                project_id, conversation_id, tenant_id=tenant_id, requester=requester
            )
            if project.tenant_id != tenant_id or project.project_id != project_id or (detail is not None and (
                detail.tenant_id != tenant_id
                or detail.project_id != project_id
                or detail.requester != requester
                or detail.conversation_id != conversation_id
            )):
                return BuildContextInspection("BLOCKED", "PROJECT_ACCESS_DENIED")
            if detail is not None and not any(
                is_firmware_build_request(turn.question)
                or turn.question == "帮我检查当前固件是否能构建"
                for turn in detail.turns
            ):
                return BuildContextInspection("BLOCKED", "BUILD_REQUEST_TURN_MISSING")
            brownfield = None
            context_reader = getattr(self._catalog, "brownfield_build_context", None)
            if project.creation_mode is ProjectCreationMode.ATTACH_EXISTING and callable(context_reader):
                try:
                    brownfield = BrownfieldBuildContext.model_validate(
                        context_reader(project_id, tenant_id=tenant_id, requester=requester).model_dump(mode="python")
                    )
                except ProjectCatalogNotFound:
                    return BuildContextInspection("BLOCKED", "APPROVED_PLAN_MISSING")
            if detail is None and brownfield is None:
                return BuildContextInspection("BLOCKED", "CONVERSATION_REQUIRED")
            plan, review = (
                (brownfield.plan, brownfield.review) if brownfield is not None
                else (detail.engineering_plan, detail.engineering_plan_review)
            )
            if plan is None or review is None:
                return BuildContextInspection("BLOCKED", "APPROVED_PLAN_MISSING")
            if (
                plan.project_id != project_id
                or plan.status is not PlanStatus.APPROVED
                or review.project_id != project_id
                or review.plan_id != plan.plan_id
                or review.status is not PlanReviewStatus.APPROVED
                or review.proposal_fingerprint != plan.content_fingerprint
            ):
                return BuildContextInspection("BLOCKED", "PLAN_BINDING_INVALID")
            stages = tuple(
                phase for phase in plan.phases
                if any(output.output_type == "BUILD_ARTIFACT" for output in phase.outputs)
            )
            if len(stages) != 1:
                return BuildContextInspection("BLOCKED", "BUILD_STAGE_UNAVAILABLE")
            spec, implementation, configuration = (
                (brownfield.firmware_spec, brownfield.implementation_spec, brownfield.configuration)
                if brownfield is not None else
                (detail.firmware_spec, detail.implementation_spec, detail.build_configuration)
            )
            if spec is None or implementation is None or configuration is None:
                return BuildContextInspection("BLOCKED", "BUILD_CONFIGURATION_MISSING")
            if not spec.target_platform or not spec.mcu_family or not spec.mcu_exact_part:
                return BuildContextInspection("BLOCKED", "TARGET_IDENTITY_MISSING")
            if configuration.target_kind != spec.mcu_exact_part:
                return BuildContextInspection("BLOCKED", "TARGET_CONFIGURATION_CONFLICT")
            if not spec.source_layout:
                return BuildContextInspection("BLOCKED", "SOURCE_LAYOUT_MISSING")
            workspace = self._workspace.snapshot(tenant_id=tenant_id, project_id=project_id)
            if workspace.tenant_id != tenant_id or workspace.project_id != project_id:
                return BuildContextInspection("BLOCKED", "WORKSPACE_IDENTITY_INVALID")
            if brownfield is not None and (
                brownfield.project_fingerprint != project.fingerprint
                or brownfield.workspace_fingerprint != workspace.project_fingerprint
                or brownfield.workspace_target_id != workspace.target_id
                or brownfield.workspace_target_fingerprint != workspace.target_fingerprint
            ):
                return BuildContextInspection("BLOCKED", "CATALOG_CONTEXT_STALE")
            files = {item.relative_file_reference: item for item in workspace.files}
            if len(files) != len(workspace.files):
                return BuildContextInspection("BLOCKED", "WORKSPACE_SOURCE_AMBIGUOUS")
            selected = tuple(files.get(path) for path in spec.source_layout)
            if any(item is None or not item.exists or item.size > 16 * 1024 * 1024 for item in selected):
                return BuildContextInspection("BLOCKED", "SOURCE_CONTENT_UNAVAILABLE")
            selected_revision_material = tuple(
                (item.relative_file_reference, item.content_fingerprint) for item in selected
            )
            revision = "sha256:" + hashlib.sha256(
                json.dumps(
                    selected_revision_material, ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            sources = tuple(
                FirmwareSourceArtifact(
                    relative_path=item.relative_file_reference,
                    content_fingerprint=item.content_fingerprint,
                    size_bytes=item.size,
                    source_revision=revision,
                )
                for item in selected
            )
            if brownfield is not None:
                if spec.source_revision != revision:
                    return BuildContextInspection("BLOCKED", "SOURCE_REVISION_STALE")
                build = FirmwareBuildRequest.create(
                    project_id=project_id, conversation_id=conversation_id,
                    firmware_reference=spec.firmware_spec_id,
                    build_profile=configuration.profile,
                    firmware_spec_id=spec.firmware_spec_id,
                    firmware_spec_fingerprint=spec.fingerprint,
                    implementation_id=implementation.implementation_id,
                    implementation_fingerprint=implementation.fingerprint,
                    target_kind=configuration.target_kind,
                    build_system=configuration.build_system,
                    toolchain_id=configuration.toolchain_id,
                    toolchain_version=configuration.toolchain_version,
                    configuration=configuration, source_artifacts=sources,
                    expected_source_revision=revision, actual_source_revision=revision,
                )
            else:
                build = create_firmware_build_request(
                    spec, implementation, configuration=configuration,
                    source_artifacts=sources,
                    expected_source_revision=revision,
                    actual_source_revision=revision,
                )
            tool = self._tools.resolve(
                configuration.toolchain_id,
                target_kind=configuration.target_kind,
                operation=OperationType.BUILD,
            )
            if (
                tool.status != "AVAILABLE"
                or not tool.version_verified
                or tool.executable is None
                or tool.definition.tool_version != configuration.toolchain_version
            ):
                return BuildContextInspection("BLOCKED", "TOOLCHAIN_UNAVAILABLE")
            snapshot_id = "workspace-snapshot-" + hashlib.sha256(
                workspace.project_fingerprint.encode("utf-8")
            ).hexdigest()[:24]
            target = self._targets.get_bound(
                tenant_id=tenant_id, project_id=project_id, target_id=workspace.target_id
            )
            proposal = None
            bindings = None
            if target is not None:
                proposal_id = self._proposal_id(
                    requester, target, snapshot_id, workspace.project_fingerprint,
                    build.fingerprint, plan.content_fingerprint, review.fingerprint,
                )
                proposal = self._execution.store.get_proposal(
                    tenant_id, project_id, proposal_id
                )
                if proposal is not None:
                    bindings = BuildBindings(
                        target_id=target.target_id,
                        target_fingerprint=target.fingerprint,
                        workspace_snapshot_id=snapshot_id,
                        workspace_fingerprint=workspace.project_fingerprint,
                        proposal_id=proposal.proposal_id,
                        proposal_fingerprint=proposal.fingerprint,
                        expires_at=self._clock() + timedelta(minutes=15),
                        resolved_toolchain_version=configuration.toolchain_version,
                    )
            snapshot = ContextSnapshot(
                project=project, conversation=detail, build_request=build,
                bindings=bindings, target=target, proposal=proposal,
                workspace=WorkspaceAuthoritySnapshot(snapshot_id, workspace),
                current_configuration=configuration,
                authoritative_plan=plan if brownfield is not None else None,
                authoritative_review=review if brownfield is not None else None,
            )
            context_version = "sha256:" + _digest(
                project.fingerprint, plan.content_fingerprint, review.fingerprint,
                spec.target_platform, spec.mcu_family, spec.mcu_exact_part,
                workspace.project_fingerprint, workspace.target_fingerprint,
                tuple((source.relative_path, source.content_fingerprint, source.source_revision) for source in sources),
                build.fingerprint, configuration.model_dump(mode="json"),
                brownfield.fingerprint if brownfield is not None else None,
                tool.definition.tool_id, tool.definition.tool_version,
                target.fingerprint if target is not None else None,
                proposal.fingerprint if proposal is not None else None,
            )
            if target is None:
                return BuildContextInspection("BLOCKED", "BOUND_TARGET_MISSING", snapshot, context_version)
            if target.stable_identity_hash != self._target_identity(
                tenant_id, project_id, workspace.target_id, spec, configuration,
                plan.content_fingerprint, review.fingerprint,
            ):
                return BuildContextInspection("BLOCKED", "BOUND_TARGET_CONFLICT", snapshot, context_version)
            if proposal is None:
                return BuildContextInspection("BLOCKED", "PROPOSAL_MISSING", snapshot, context_version)
            if not self._matches(target, proposal, workspace, build, requester):
                return BuildContextInspection("BLOCKED", "BINDING_STALE", snapshot, context_version)
            if brownfield is not None and not self._matches_brownfield(proposal, brownfield):
                return BuildContextInspection("BLOCKED", "CATALOG_CONTEXT_STALE", snapshot, context_version)
            if proposal.provenance is BuildProvenanceType.APPLIED_CHANGE:
                receipt = self._workspace.get_receipt(
                    project_id, proposal.approved_workspace_operation_id
                )
                if (
                    receipt is None
                    or receipt.fingerprint != proposal.approved_workspace_receipt_fingerprint
                    or not self._valid_receipt(receipt, workspace, tenant_id)
                ):
                    return BuildContextInspection("BLOCKED", "WORKSPACE_RECEIPT_STALE", snapshot, context_version)
            return BuildContextInspection("READY", "CURRENT_BINDING", snapshot, context_version)
        except ProjectCatalogConflict:
            return BuildContextInspection("BLOCKED", "CATALOG_CONTEXT_STALE")
        except (ProjectCatalogForbidden, ProjectCatalogNotFound):
            return BuildContextInspection("BLOCKED", "PROJECT_ACCESS_DENIED")
        except (ProjectCatalogError, AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            return BuildContextInspection("BLOCKED", "CONTEXT_SOURCE_UNAVAILABLE")

    @staticmethod
    def _proposal_id(
        requester: str, target: ExecutionTarget, snapshot_id: str,
        workspace_fingerprint: str, build_fingerprint: str,
        plan_fingerprint: str, review_fingerprint: str,
    ) -> str:
        return "build-proposal:" + _digest(
            requester, target.fingerprint, snapshot_id, workspace_fingerprint,
            build_fingerprint, plan_fingerprint, review_fingerprint,
        )[:48]

    @staticmethod
    def _target_identity(
        tenant_id: str, project_id: str, workspace_target_id: str,
        spec, configuration, plan_fingerprint: str, review_fingerprint: str,
    ) -> str:
        return "sha256:" + _digest(
            tenant_id, project_id, workspace_target_id,
            spec.target_platform, spec.mcu_family, spec.mcu_exact_part,
            configuration.toolchain_id, configuration.toolchain_version,
            plan_fingerprint, review_fingerprint,
        )

    @staticmethod
    def _matches(target, proposal, workspace, build, requester: str) -> bool:
        config = build.configuration
        return bool(
            target.tenant_id == workspace.tenant_id
            and target.project_id == workspace.project_id
            and target.target_id == workspace.target_id
            and target.target_kind == config.target_kind
            and proposal.tenant_id == workspace.tenant_id
            and proposal.project_id == workspace.project_id
            and proposal.requester_id == requester
            and proposal.target_id == target.target_id
            and proposal.target_fingerprint == target.fingerprint
            and proposal.workspace_fingerprint == workspace.project_fingerprint
            and proposal.operation_type is OperationType.BUILD
            and proposal.toolchain_id == config.toolchain_id
            and proposal.toolchain_version == config.toolchain_version
            and proposal.build_configuration == config.profile
            and proposal.source_revision == build.expected_source_revision
            and proposal.firmware_build_request_fingerprint == build.fingerprint
        )

    @staticmethod
    def _matches_brownfield(
        proposal: ExecutionProposal, context: BrownfieldBuildContext
    ) -> bool:
        return bool(
            proposal.plan_id == context.plan.plan_id
            and proposal.plan_fingerprint == context.plan.content_fingerprint
            and proposal.build_configuration_fingerprint
            == canonical_fingerprint(context.configuration)
            and proposal.catalog_context_fingerprint == context.fingerprint
        )

    def _valid_receipt(self, receipt, workspace, tenant_id: str) -> bool:
        if (
            receipt.project_id != workspace.project_id
            or receipt.status is not ApplyStatus.APPLIED
            or receipt.recovery_status is not RecoveryStatus.AVAILABLE
            or receipt.validation_status is not ValidationStatus.PASSED
            or receipt.after_project_fingerprint != workspace.project_fingerprint
        ):
            return False
        change = self._workspace.get_changeset(
            tenant_id, workspace.project_id, receipt.changeset_id
        )
        return bool(
            change is not None
            and change.tenant_id == tenant_id
            and change.project_id == workspace.project_id
            and change.target.target_id == workspace.target_id
            and change.fingerprint == receipt.changeset_fingerprint
        )

    def revalidate_operation(self, operation: BuildOperation) -> bool:
        """Check the current authoritative inputs immediately before Build."""
        try:
            if not isinstance(operation, BuildOperation):
                return False
            request = operation.firmware_build_request
            if request is None:
                return False
            inspected = self.inspect(
                tenant_id=operation.tenant_id,
                requester=operation.requester_id,
                project_id=operation.project_id,
                conversation_id=request.conversation_id,
            )
            snapshot = inspected.snapshot
            return bool(
                inspected.status == "READY"
                and snapshot is not None
                and snapshot.build_request == request
                and snapshot.proposal is not None
                and snapshot.proposal.proposal_id == operation.proposal_id
                and snapshot.proposal.fingerprint == operation.proposal_fingerprint
                and snapshot.target is not None
                and snapshot.target.target_id == operation.target_id
                and snapshot.target.fingerprint == operation.target_fingerprint
                and snapshot.workspace is not None
                and snapshot.workspace.snapshot_id == operation.workspace_snapshot_id
                and snapshot.workspace.snapshot.project_fingerprint
                == operation.workspace_fingerprint
                and snapshot.current_configuration is not None
                and snapshot.current_configuration.profile == operation.configuration
            )
        except Exception:  # noqa: BLE001 - the execution boundary fails closed
            return False

    def prepare(
        self, *, tenant_id: str, requester: str, project_id: str, conversation_id: str | None,
        expected_context_version: str | None = None,
    ) -> BuildContextInspection:
        """Explicit review action; never called by preview or task confirmation."""
        inspected = self.inspect(
            tenant_id=tenant_id, requester=requester,
            project_id=project_id, conversation_id=conversation_id,
        )
        if inspected.snapshot is None:
            return inspected
        if (
            expected_context_version is not None
            and inspected.context_version != expected_context_version
        ):
            return BuildContextInspection(
                "BLOCKED", "CONTEXT_STALE", inspected.snapshot, inspected.context_version
            )
        if inspected.status == "READY":
            return inspected
        allowed_reasons = {"BOUND_TARGET_MISSING", "PROPOSAL_MISSING"}
        if inspected.snapshot.authoritative_plan is not None:
            allowed_reasons.add("BOUND_TARGET_CONFLICT")
        if inspected.reason_code not in allowed_reasons:
            return inspected
        snapshot = inspected.snapshot
        detail = snapshot.conversation
        brownfield = None
        if snapshot.authoritative_plan is not None:
            try:
                brownfield = self._catalog.brownfield_build_context(
                    project_id, tenant_id=tenant_id, requester=requester
                )
            except (ProjectCatalogError, OSError, RuntimeError, TypeError, ValueError):
                return BuildContextInspection(
                    "BLOCKED", "CATALOG_CONTEXT_STALE", snapshot,
                    inspected.context_version,
                )
        spec = brownfield.firmware_spec if brownfield is not None else detail.firmware_spec
        build = snapshot.build_request
        workspace = snapshot.workspace.snapshot
        configuration = snapshot.current_configuration
        plan, review = (
            (brownfield.plan, brownfield.review) if brownfield is not None
            else (detail.engineering_plan, detail.engineering_plan_review)
        )
        try:
            receipts = self._workspace.list_receipts(project_id, limit=100)
            valid = [
                receipt for receipt in receipts
                if self._valid_receipt(receipt, workspace, tenant_id)
            ]
            if brownfield is None and not valid:
                return BuildContextInspection("BLOCKED", "APPROVED_WORKSPACE_RECEIPT_MISSING", snapshot, inspected.context_version)
            if len(valid) > 1:
                return BuildContextInspection("BLOCKED", "WORKSPACE_RECEIPT_AMBIGUOUS", snapshot, inspected.context_version)
            # Re-read before the first write; a changed source must not receive a
            # proposal for the earlier snapshot.
            current = self._workspace.snapshot(tenant_id=tenant_id, project_id=project_id)
            if current.project_fingerprint != workspace.project_fingerprint or current.target_fingerprint != workspace.target_fingerprint:
                return BuildContextInspection("BLOCKED", "CONTEXT_STALE", snapshot, inspected.context_version)
            again = self.inspect(
                tenant_id=tenant_id, requester=requester,
                project_id=project_id, conversation_id=conversation_id,
            )
            if again.context_version != inspected.context_version:
                return BuildContextInspection("BLOCKED", "CONTEXT_STALE", snapshot, again.context_version)
            identity = self._target_identity(
                tenant_id, project_id, workspace.target_id, spec, configuration,
                plan.content_fingerprint, review.fingerprint,
            )
            candidate_id = "build-candidate:" + identity[7:55]
            target = self._targets.get_bound(
                tenant_id=tenant_id, project_id=project_id, target_id=workspace.target_id
            )
            if target is None or (
                brownfield is not None
                and (target.stable_identity_hash != identity or target.target_kind != configuration.target_kind)
            ):
                candidate = self._targets.get_candidate(
                    tenant_id=tenant_id, project_id=project_id, candidate_id=candidate_id
                )
                if candidate is None:
                    candidate = self._targets.discover(TargetDiscoveryCandidate(
                        candidate_id=candidate_id, tenant_id=tenant_id,
                        project_id=project_id, target_kind=configuration.target_kind,
                        model=spec.target_platform, observed_identity_hash=identity,
                        discovered_at=self._clock(),
                    ))
                if candidate.observed_identity_hash != identity or candidate.target_kind != configuration.target_kind:
                    return BuildContextInspection("BLOCKED", "TARGET_CANDIDATE_CONFLICT", snapshot, inspected.context_version)
                target = self._targets.bind(ExecutionTarget(
                    target_id=workspace.target_id, tenant_id=tenant_id,
                    project_id=project_id, target_kind=configuration.target_kind,
                    board_family=spec.target_platform, mcu_family=spec.mcu_family,
                    stable_identity_hash=identity, candidate_id=candidate.candidate_id,
                    binding_status=TargetBindingStatus.BOUND, bound_by=requester,
                    bound_at=self._clock(),
                ))
            elif (
                target.stable_identity_hash != identity
                or target.target_kind != configuration.target_kind
                or target.candidate_id != candidate_id
            ):
                return BuildContextInspection("BLOCKED", "BOUND_TARGET_CONFLICT", snapshot, inspected.context_version)
            proposal_id = self._proposal_id(
                requester, target, snapshot.workspace.snapshot_id,
                workspace.project_fingerprint, build.fingerprint,
                plan.content_fingerprint, review.fingerprint,
            )
            existing = self._execution.store.get_proposal(tenant_id, project_id, proposal_id)
            if existing is None:
                receipt = valid[0] if valid else None
                proposal = ExecutionProposal(
                    proposal_id=proposal_id, tenant_id=tenant_id, project_id=project_id,
                    requester_id=requester, target_id=target.target_id,
                    target_fingerprint=target.fingerprint,
                    workspace_snapshot_id=snapshot.workspace.snapshot_id,
                    workspace_fingerprint=workspace.project_fingerprint,
                    approved_workspace_operation_id=None if receipt is None else receipt.operation_id,
                    approved_workspace_receipt_fingerprint=None if receipt is None else receipt.fingerprint,
                    operation_type=OperationType.BUILD,
                    toolchain_id=configuration.toolchain_id,
                    toolchain_version=configuration.toolchain_version,
                    build_configuration=configuration.profile,
                    created_at=self._clock(),
                    source_revision=build.expected_source_revision,
                    firmware_build_request_fingerprint=build.fingerprint,
                    provenance=(
                        BuildProvenanceType.APPLIED_CHANGE if receipt is not None
                        else BuildProvenanceType.EXISTING_SNAPSHOT
                    ),
                    plan_id=plan.plan_id if brownfield is not None else None,
                    plan_fingerprint=plan.content_fingerprint if brownfield is not None else None,
                    build_configuration_fingerprint=(
                        canonical_fingerprint(configuration) if brownfield is not None else None
                    ),
                    catalog_context_fingerprint=(
                        brownfield.fingerprint if brownfield is not None else None
                    ),
                )
                self._execution.create_proposal(proposal)
            return self.inspect(
                tenant_id=tenant_id, requester=requester,
                project_id=project_id, conversation_id=conversation_id,
            )
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            return BuildContextInspection("BLOCKED", "CONTEXT_PREPARATION_UNAVAILABLE", snapshot, inspected.context_version)


__all__ = ("BuildContextInspection", "FirmwareBuildContextSource")
