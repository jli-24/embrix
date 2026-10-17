from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from embedded_copilot.desktop_copilot_chat import (
    ChatProjectReference,
    DesktopChatRequest,
)

from .factories import NOW, PROJECT, project, question, reference


def test_contracts_are_frozen_deep_validated_and_fingerprinted() -> None:
    value = project()
    duplicate = ChatProjectReference.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )
    assert duplicate == value
    assert duplicate.fingerprint == value.fingerprint
    with pytest.raises((TypeError, ValidationError)):
        value.project_id = "other-project"  # type: ignore[misc]
    material = value.model_dump(mode="python")
    material["project_name_reference"] = "different projection"
    with pytest.raises(ValidationError, match="fingerprint"):
        ChatProjectReference.model_validate(material)


def test_request_rejects_extra_sensitive_fields_and_non_tuple_context() -> None:
    with pytest.raises(ValidationError):
        ChatProjectReference(
            tenant_id="tenant-1",
            project_id=PROJECT,
            project_name_reference="safe name",
            workspace_reference="workspace:project-1",
            prompt="hidden material",  # type: ignore[call-arg]
        )
    with pytest.raises((TypeError, ValidationError)):
        DesktopChatRequest(
            session_id="session-1",
            project_reference=project(),
            application_question=question(),
            context_references=[reference()],  # type: ignore[arg-type]
            requested_at=NOW,
        )
