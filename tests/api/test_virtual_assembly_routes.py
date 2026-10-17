from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.api.copilot_virtual_assembly_routes import (
    VirtualAssemblyNaturalLanguageRequest,
    _recognized,
)


@pytest.mark.parametrize(
    "input_text",
    ("请建立虚拟装配", "检查 virtual assembly", "生成 populated board"),
)
def test_virtual_assembly_natural_language_trigger_is_explicit(input_text: str) -> None:
    assert _recognized(input_text) is True
    assert VirtualAssemblyNaturalLanguageRequest(request=input_text).request == input_text


def test_unrelated_natural_language_is_not_routed_to_virtual_assembly() -> None:
    assert _recognized("分析这段日志") is False


def test_virtual_assembly_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        VirtualAssemblyNaturalLanguageRequest.model_validate(
            {"request": "请建立虚拟装配", "project_id": "unexpected"}
        )
