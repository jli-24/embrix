import pytest

from embedded_copilot.application import ApplicationRunStatus
from embedded_copilot.engineering_desktop import (
    DesktopApplicationFailed,
    DesktopApplicationRejected,
    DesktopInteractionRequest,
    DesktopInteractionType,
    DesktopOpenProjectRequest,
    DesktopQuestionRequest,
    DesktopRejected,
    DesktopSessionStatus,
    FakeApplicationAdapter,
)

from .conftest import NOW, application_request, application_result


def open_project(runtime, expiry):
    return runtime.open_project(
        DesktopOpenProjectRequest(
            session_id="session1",
            project_id="project1",
            requester="engineer1",
            expires_at=expiry,
        )
    )


def test_completed_application_creates_a_redacted_answer_view(desktop_runtime, expiry):
    runtime, _, _, _ = desktop_runtime
    open_project(runtime, expiry)
    answer = runtime.ask(
        DesktopQuestionRequest(
            session_id="session1",
            project_id="project1",
            application_request=application_request(),
            timestamp=NOW,
        )
    )
    assert answer.answer_reference.reference_id.startswith("answer-")
    assert answer.answer_reference.source_domain == "APPLICATION"


@pytest.mark.parametrize(
    ("status", "exception"),
    (
        (ApplicationRunStatus.REJECTED, DesktopApplicationRejected),
        (ApplicationRunStatus.FAILED, DesktopApplicationFailed),
    ),
)
def test_non_completed_application_never_creates_answer_view(
    desktop_runtime, expiry, status, exception
):
    runtime, _, _, _ = desktop_runtime
    runtime._applications = FakeApplicationAdapter(application_result(status))
    open_project(runtime, expiry)
    with pytest.raises(exception, match=status.value):
        runtime.ask(
            DesktopQuestionRequest(
                session_id="session1",
                project_id="project1",
                application_request=application_request(),
                timestamp=NOW,
            )
        )


def test_read_time_expiry_persists_expired_session_and_audits(desktop_runtime, expiry):
    runtime, clock, sessions, _ = desktop_runtime
    project = open_project(runtime, expiry)
    clock.set(expiry)
    with pytest.raises(DesktopRejected, match="expired"):
        runtime.get_workspace(
            DesktopInteractionRequest(
                session_id="session1",
                project_id="project1",
                request_type=DesktopInteractionType.VIEW_WORKSPACE,
                reference=project.workspace.workspace_snapshot_reference,
                timestamp=NOW,
            )
        )
    assert sessions.get("session1").status is DesktopSessionStatus.EXPIRED


def test_capability_port_is_read_only_status_projection(desktop_runtime, expiry):
    runtime, _, _, _ = desktop_runtime
    project = open_project(runtime, expiry)
    status = runtime.get_runtime_status(
        DesktopInteractionRequest(
            session_id="session1",
            project_id="project1",
            request_type=DesktopInteractionType.VIEW_WORKSPACE,
            reference=project.workspace.workspace_snapshot_reference,
            timestamp=NOW,
        )
    )
    assert len(status.components) == 5
