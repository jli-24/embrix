from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_workspace import (
    EngineeringWorkspaceRequest,
    WorkspaceLineageLink,
    WorkspaceObjectReference,
    WorkspaceResolvedReference,
)

from .conftest import PROJECT, reference, resolved


def test_workspace_contracts_are_frozen_strict_and_fingerprint_bound() -> None:
    value = reference()
    assert value.project_id == PROJECT
    with pytest.raises(ValidationError):
        value.source_object_id = "changed"
    with pytest.raises(ValidationError):
        WorkspaceObjectReference.model_validate(
            {**value.model_dump(mode="python"), "source_object_id": "changed"}
        )
    with pytest.raises(ValidationError):
        WorkspaceObjectReference.model_validate(
            {**value.model_dump(mode="python"), "extra": "forbidden"}
        )


def test_reference_rejects_sensitive_identity_and_list_collections() -> None:
    with pytest.raises(ValidationError):
        reference(object_id="C:\\private\\board.kicad_pcb")
    with pytest.raises(ValidationError):
        EngineeringWorkspaceRequest(
            workspace_id="workspace-1",
            project_id=PROJECT,
            references=[reference()],
        )


def test_lineage_binds_full_upstream_identity_and_self_reference_is_rejected() -> None:
    value = reference()
    with pytest.raises(ValidationError):
        WorkspaceResolvedReference.model_validate(
            {
                **resolved(value).model_dump(mode="python"),
                "lineage": (
                    WorkspaceLineageLink(
                        source_domain=value.source_domain,
                        source_kind=value.source_kind,
                        source_object_id=value.source_object_id,
                        source_fingerprint=value.source_fingerprint,
                        relation_type="DERIVED_FROM",
                    ),
                ),
            }
        )


def test_resolved_reference_requires_nonempty_lineage() -> None:
    with pytest.raises(ValidationError):
        WorkspaceResolvedReference(reference=reference(), lineage=())


def test_request_rejects_duplicate_reference_identity_including_exact_duplicates() -> (
    None
):
    value = reference()
    with pytest.raises(ValidationError):
        EngineeringWorkspaceRequest(
            workspace_id="workspace-1",
            project_id=PROJECT,
            references=(value, value),
        )
