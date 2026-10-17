"""Secret-free correlation identity shared across runtime boundaries."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SENSITIVE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|credential|password|secret|token)\s*[:=]",
    re.IGNORECASE,
)


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = value.strip()
    if not _IDENTIFIER.fullmatch(checked) or _SENSITIVE.search(checked):
        raise ValueError(f"{field} is invalid")
    return checked


class CorrelationContext(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    request_id: str
    trace_id: str
    project_id: str
    workflow_run_id: str | None = None
    task_id: str | None = None
    operation_id: str | None = None
    approval_id: str | None = None

    @field_validator(
        "request_id",
        "trace_id",
        "project_id",
        "workflow_run_id",
        "task_id",
        "operation_id",
        "approval_id",
        mode="before",
    )
    @classmethod
    def _ids(cls, value: object, info) -> str | None:
        return None if value is None else _identifier(value, field=info.field_name)

    @model_validator(mode="after")
    def _operation_binding(self) -> "CorrelationContext":
        if self.approval_id is not None and self.operation_id is None:
            raise ValueError("correlation approval requires operation")
        return self


__all__ = ("CorrelationContext",)
