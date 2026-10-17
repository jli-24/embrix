from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_desktop import DesktopOpenProjectRequest

from .conftest import NOW


def test_open_request_is_frozen_and_deterministic(expiry):
    first = DesktopOpenProjectRequest(
        session_id="session1",
        project_id="project1",
        requester="engineer1",
        expires_at=expiry,
    )
    second = DesktopOpenProjectRequest(
        session_id="session1",
        project_id="project1",
        requester="engineer1",
        expires_at=expiry,
    )
    assert first.fingerprint == second.fingerprint
    with pytest.raises((FrozenInstanceError, ValidationError, TypeError)):
        first.project_id = "other"


def test_open_request_rejects_extra_unsafe_and_naive_values(expiry):
    with pytest.raises(ValidationError):
        DesktopOpenProjectRequest(
            session_id="session1",
            project_id="project1",
            requester="api_key",
            expires_at=expiry,
        )
    with pytest.raises(ValidationError):
        DesktopOpenProjectRequest(
            session_id="session1",
            project_id="project1",
            requester="engineer1",
            expires_at=NOW.replace(tzinfo=None),
            unexpected="value",
        )
