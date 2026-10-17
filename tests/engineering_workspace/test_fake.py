from __future__ import annotations

import pytest

from embedded_copilot.engineering_workspace import WorkspaceUnavailable
from embedded_copilot.engineering_workspace.adapters import (
    FakeWorkspaceReferenceSourceAdapter,
)

from .conftest import reference, resolved


def test_fake_source_returns_revalidated_deep_copy() -> None:
    value = resolved()
    source = FakeWorkspaceReferenceSourceAdapter((value,))
    first = source.resolve(value.reference)
    second = source.resolve(value.reference)
    assert first == value
    assert second == value
    assert first is not second


def test_fake_source_supports_missing_and_failure_injection() -> None:
    source = FakeWorkspaceReferenceSourceAdapter(())
    assert source.resolve(reference()) is None
    source.fail = True
    with pytest.raises(WorkspaceUnavailable):
        source.resolve(reference())
