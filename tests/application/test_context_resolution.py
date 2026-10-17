"""Focused read-only resolution checks; all sources are injected fakes."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.application.context_resolution import (
    ContextSnapshot,
    EngineeringContextResolver,
    ResolutionStatus,
    TrustedApplicationContext,
    WorkspaceAuthoritySnapshot,
)
from embedded_copilot.application.task_entry import BuildBindings, EngineeringIntent
from embedded_copilot.engineering_workspace_apply.models import (
    WorkspaceFile,
    WorkspaceSnapshot,
)
from embedded_copilot.execution_worker.contracts import (
    ExecutionProposal,
    ExecutionTarget,
    OperationType,
    TargetBindingStatus,
)
from embedded_copilot.firmware_engineering import (
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
)
from embedded_copilot.firmware_engineering.models import canonical_fingerprint
from tests.application.test_task_entry import (
    FP,
    NOW,
    Catalog,
    firmware_request,
    source_detail,
    without_plan,
)


def _build():
    original = firmware_request()
    return FirmwareBuildRequest.create(
        **{
            **original.model_dump(mode="python", exclude={"fingerprint"}),
            "conversation_id": "conversation-1",
            "configuration": original.configuration,
            "source_artifacts": (
                FirmwareSourceArtifact(
                    relative_path="main.c",
                    content_fingerprint=FP,
                    source_revision=FP,
                    size_bytes=10,
                ),
            ),
        }
    )


def _source():
    project = Catalog().get_project("project-1", tenant_id="tenant-1")
    build = _build()
    target = ExecutionTarget(
        target_id="target-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_kind="STM32F446RE",
        board_family="Nucleo-F446RE",
        mcu_family="STM32F4",
        stable_identity_hash="stable-1",
        candidate_id="candidate-1",
        binding_status=TargetBindingStatus.BOUND,
        bound_by="user-1",
        bound_at=NOW,
    )
    workspace = WorkspaceSnapshot(
        tenant_id="tenant-1",
        project_id="project-1",
        target_id="target-1",
        project_fingerprint="sha256:" + "a" * 64,
        target_fingerprint="sha256:" + "b" * 64,
        files=(
            WorkspaceFile(
                relative_file_reference="main.c",
                exists=True,
                content_fingerprint=FP,
                size=10,
            ),
        ),
        captured_at=NOW,
    )
    proposal = ExecutionProposal(
        proposal_id="proposal-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="user-1",
        target_id=target.target_id,
        target_fingerprint=target.fingerprint,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=workspace.project_fingerprint,
        approved_workspace_operation_id="workspace-operation-1",
        approved_workspace_receipt_fingerprint=FP,
        operation_type=OperationType.BUILD,
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="13",
        build_configuration="DEBUG",
        created_at=NOW,
        source_revision=FP,
        firmware_build_request_fingerprint=build.fingerprint,
    )
    bindings = BuildBindings(
        target_id=target.target_id,
        target_fingerprint=target.fingerprint,
        workspace_snapshot_id=proposal.workspace_snapshot_id,
        workspace_fingerprint=workspace.project_fingerprint,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )

    class Source:
        def __init__(self):
            self.projects_result = (project,)
            self.value = ContextSnapshot(
                project,
                source_detail(),
                build,
                bindings,
                target,
                proposal,
                WorkspaceAuthoritySnapshot("snapshot-1", workspace),
                build.configuration,
            )
            self.calls = []

        def projects(self, *, tenant_id, requester):
            self.calls.append(("projects", tenant_id, requester))
            return self.projects_result

        def snapshot(self, *, tenant_id, requester, project_id, conversation_id):
            self.calls.append(
                ("snapshot", tenant_id, requester, project_id, conversation_id)
            )
            return self.value

    return Source()


def _intent(project="project-1"):
    return EngineeringIntent(
        objective="帮我检查当前固件是否能构建",
        project_reference=project,
        engineering_domain="FIRMWARE",
        expected_outcome="检查固件构建结果",
        constraints=(),
        source_context=(
            "turn-1",
            "request-1",
            "帮我检查当前固件是否能构建",
            NOW.isoformat(),
            "conversation-1",
        ),
        correlation_id="request-1",
    )


def _app(current="project-1"):
    return TrustedApplicationContext("tenant-1", "user-1", "conversation-1", current)


def test_unique_context_is_reference_only_and_safe_to_confirm():
    source = _source()
    result = EngineeringContextResolver(source).resolve(_intent(), _app())
    assert result.status is ResolutionStatus.RESOLVED
    assert result.context is not None
    assert result.context.source_reference == "firmware-1"
    assert result.context.build_configuration == canonical_fingerprint(
        source.value.build_request.configuration
    )
    assert result.context.confirmation.project == "Project"
    assert result.context.confirmation.source_version == FP
    assert result.context.confirmation.plan_stage == "Firmware"
    assert "main.c" not in str(result.context.confirmation)
    with pytest.raises(FrozenInstanceError):
        result.context.project_reference = "other"
    assert source.calls == [
        ("projects", "tenant-1", "user-1"),
        ("snapshot", "tenant-1", "user-1", "project-1", "conversation-1"),
    ]


def test_brownfield_approved_catalog_plan_resolves_without_conversation_plan():
    source = _source()
    approved = source_detail()
    source.value = replace(
        source.value,
        conversation=without_plan(),
        authoritative_plan=approved.engineering_plan,
        authoritative_review=approved.engineering_plan_review,
    )
    result = EngineeringContextResolver(source).resolve(_intent(), _app())
    assert result.status is ResolutionStatus.RESOLVED
    assert result.context is not None
    assert result.context.plan_reference == approved.engineering_plan.plan_id


def test_multiple_projects_are_ambiguous_without_trusted_current_project():
    source = _source()
    source.projects_result = (
        source.value.project,
        replace_project(source.value.project),
    )
    result = EngineeringContextResolver(source).resolve(_intent(), _app(None))
    assert result.status is ResolutionStatus.AMBIGUOUS_CONTEXT
    assert len(source.calls) == 1


def replace_project(project):
    return type(project).model_validate(
        {
            **project.model_dump(mode="python", exclude={"fingerprint"}),
            "project_id": "project-2",
        }
    )


def test_missing_configuration_requests_clarification():
    source = _source()
    source.value = replace(source.value, build_request=None)
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.CLARIFICATION_REQUIRED
    )


def test_unversioned_latest_source_requests_clarification():
    source = _source()
    build = source.value.build_request
    assert build is not None and build.configuration is not None
    changed = FirmwareBuildRequest.create(
        **{
            **build.model_dump(mode="python", exclude={"fingerprint"}),
            "configuration": build.configuration,
            "source_artifacts": (
                FirmwareSourceArtifact(
                    relative_path="main.c",
                    content_fingerprint=FP,
                    source_revision="latest",
                    size_bytes=10,
                ),
            ),
        }
    )
    source.value = replace(source.value, build_request=changed)
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.CLARIFICATION_REQUIRED
    )


def test_current_configuration_mismatch_is_stale():
    source = _source()
    build = source.value.build_request
    assert build is not None and build.configuration is not None
    other = build.configuration.model_copy(update={"compiler_flags": ("-O2",)})
    source.value = replace(source.value, current_configuration=other)
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.CONTEXT_STALE
    )


def test_workspace_source_digest_and_binding_are_revalidated():
    source = _source()
    authority = source.value.workspace
    assert authority is not None
    workspace = WorkspaceSnapshot(
        tenant_id="tenant-1",
        project_id="project-1",
        target_id="target-1",
        project_fingerprint=source.value.project.fingerprint,
        target_fingerprint=source.value.target.fingerprint,
        files=(
            WorkspaceFile(
                relative_file_reference="main.c",
                exists=True,
                content_fingerprint="sha256:" + "2" * 64,
                size=10,
            ),
        ),
        captured_at=NOW,
    )
    source.value = replace(
        source.value, workspace=WorkspaceAuthoritySnapshot("snapshot-1", workspace)
    )
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.CONTEXT_STALE
    )


def test_workspace_binding_uses_project_fingerprint_not_capture_fingerprint():
    source = _source()
    authority = source.value.workspace
    bindings = source.value.bindings
    assert authority is not None and bindings is not None
    assert authority.snapshot.fingerprint != authority.snapshot.project_fingerprint
    assert authority.snapshot.project_fingerprint != source.value.project.fingerprint
    assert authority.snapshot.target_fingerprint != source.value.target.fingerprint
    assert bindings.workspace_fingerprint == authority.snapshot.project_fingerprint
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.RESOLVED
    )


def test_workspace_binding_with_wrong_project_fingerprint_is_stale():
    source = _source()
    proposal = source.value.proposal
    bindings = source.value.bindings
    assert proposal is not None and bindings is not None
    changed_proposal = ExecutionProposal.model_validate(
        {
            **proposal.model_dump(mode="python", exclude={"fingerprint"}),
            "workspace_fingerprint": FP,
        }
    )
    source.value = replace(
        source.value,
        proposal=changed_proposal,
        bindings=replace(
            bindings,
            workspace_fingerprint=FP,
            proposal_fingerprint=changed_proposal.fingerprint,
        ),
    )
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.CONTEXT_STALE
    )


def test_workspace_snapshot_id_mismatch_is_stale():
    source = _source()
    authority = source.value.workspace
    assert authority is not None
    source.value = replace(
        source.value,
        workspace=WorkspaceAuthoritySnapshot("snapshot-2", authority.snapshot),
    )
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.CONTEXT_STALE
    )


def test_missing_workspace_authority_blocks_resolution():
    source = _source()
    source.value = replace(source.value, workspace=None)
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.BLOCKED
    )


def test_unavailable_source_is_blocked_without_leaking_details():
    source = _source()

    def unavailable(**_kwargs):
        raise OSError("private workspace location")

    source.snapshot = unavailable
    result = EngineeringContextResolver(source).resolve(_intent(), _app())
    assert result.status is ResolutionStatus.BLOCKED
    assert result.context is None
    assert "private workspace location" not in str(result)


def test_expired_binding_is_stale():
    source = _source()
    source.value = replace(
        source.value,
        bindings=replace(
            source.value.bindings,
            expires_at=NOW,
        ),
    )
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.CONTEXT_STALE
    )


@pytest.mark.parametrize(
    "change,expected",
    [
        ("no_plan", ResolutionStatus.WAITING_FOR_PLAN),
        ("wrong_stage", ResolutionStatus.REJECTED),
        ("bad_review", ResolutionStatus.WAITING_FOR_PLAN),
    ],
)
def test_plan_must_be_approved_and_contain_build_stage(change, expected):
    source = _source()
    from tests.application.test_task_entry import without_plan

    if change == "no_plan":
        detail = without_plan()
    elif change == "wrong_stage":
        detail = source_detail(build_stage=False)
    else:
        detail = source_detail(approved=False)
    source.value = replace(source.value, conversation=detail)
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status is expected
    )


def test_plan_review_fingerprint_mismatch_is_rejected():
    source = _source()
    detail = source.value.conversation
    review = detail.engineering_plan_review
    assert review is not None
    source.value = replace(
        source.value,
        conversation=detail.model_copy(
            update={
                "engineering_plan_review": review.model_copy(
                    update={"proposal_fingerprint": FP}
                )
            }
        ),
    )
    assert (
        EngineeringContextResolver(source).resolve(_intent(), _app()).status
        is ResolutionStatus.REJECTED
    )


@pytest.mark.parametrize("change", ["source", "workspace_source", "configuration"])
def test_previous_context_version_rejects_changed_current_context(change):
    source = _source()
    resolver = EngineeringContextResolver(source)
    first = resolver.resolve(_intent(), _app())
    assert first.context is not None
    if change == "source":
        build = source.value.build_request
        assert build is not None
        altered = FirmwareBuildRequest.create(
            **{
                **build.model_dump(mode="python", exclude={"fingerprint"}),
                "configuration": build.configuration,
                "source_artifacts": (
                    FirmwareSourceArtifact(
                        relative_path="main.c",
                        content_fingerprint=FP,
                        source_revision="revision-2",
                        size_bytes=10,
                    ),
                ),
            }
        )
        source.value = replace(source.value, build_request=altered)
    elif change == "workspace_source":
        old = source.value.proposal
        authority = source.value.workspace
        build = source.value.build_request
        assert old is not None and authority is not None and build is not None
        old_snapshot = authority.snapshot
        changed_content_fingerprint = "sha256:" + "2" * 64
        snapshot = WorkspaceSnapshot(
            tenant_id=old_snapshot.tenant_id,
            project_id=old_snapshot.project_id,
            target_id=old_snapshot.target_id,
            project_fingerprint=old_snapshot.project_fingerprint,
            target_fingerprint=old_snapshot.target_fingerprint,
            files=(
                WorkspaceFile(
                    relative_file_reference="main.c",
                    exists=True,
                    content_fingerprint=changed_content_fingerprint,
                    size=10,
                ),
            ),
            captured_at=NOW + timedelta(seconds=1),
        )
        changed_build = FirmwareBuildRequest.create(
            **{
                **build.model_dump(mode="python", exclude={"fingerprint"}),
                "configuration": build.configuration,
                "source_artifacts": (
                    FirmwareSourceArtifact(
                        relative_path="main.c",
                        content_fingerprint=changed_content_fingerprint,
                        source_revision="revision-2",
                        size_bytes=10,
                    ),
                ),
            }
        )
        proposal = ExecutionProposal.model_validate(
            {
                **old.model_dump(mode="python", exclude={"fingerprint"}),
                "workspace_snapshot_id": "snapshot-2",
                "workspace_fingerprint": snapshot.project_fingerprint,
            }
        )
        bindings = replace(
            source.value.bindings,
            workspace_snapshot_id="snapshot-2",
            workspace_fingerprint=snapshot.project_fingerprint,
            proposal_fingerprint=proposal.fingerprint,
        )
        source.value = replace(
            source.value,
            build_request=changed_build,
            proposal=proposal,
            bindings=bindings,
            workspace=WorkspaceAuthoritySnapshot("snapshot-2", snapshot),
        )
    else:
        build = source.value.build_request
        assert build is not None and build.configuration is not None
        # Preserve the distinction between a missing value and the literal text.
        config = build.configuration.model_copy(update={"linker_script": "None"})
        altered = FirmwareBuildRequest.create(
            **{
                **build.model_dump(mode="python", exclude={"fingerprint"}),
                "configuration": config,
                "source_artifacts": build.source_artifacts,
            }
        )
        source.value = replace(
            source.value, build_request=altered, current_configuration=config
        )
    assert (
        resolver.resolve(
            _intent(), _app(), expected_context_version=first.context.context_version
        ).status
        is ResolutionStatus.CONTEXT_STALE
    )


def test_untrusted_intent_project_cannot_choose_project():
    source = _source()
    result = EngineeringContextResolver(source).resolve(_intent("project-2"), _app())
    assert result.status is ResolutionStatus.REJECTED
    assert all(call[0] != "snapshot" for call in source.calls)


def test_resolver_contains_no_task_or_execution_imports():
    import ast
    from pathlib import Path

    import embedded_copilot.application.context_resolution as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not {"submit", "prepare", "execute", "run", "create_operation"} & names
