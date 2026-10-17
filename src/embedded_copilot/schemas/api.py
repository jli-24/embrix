from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from embedded_copilot.schemas.result import (
    AgentResult,
    ContractModel,
    ErrorDetail,
    SourceCitation,
)
from embedded_copilot.schemas.state import AgentName


class ChatRequest(ContractModel):
    message: str = Field(min_length=1, max_length=20_000)

    @field_validator("message", mode="before")
    @classmethod
    def strip_message(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ChatResponse(ContractModel):
    answer: str
    agents_used: list[AgentName] = Field(default_factory=list)
    sources: list[SourceCitation] = Field(default_factory=list)
    trace_id: str = Field(min_length=1)
    result: AgentResult | None = None
    error: ErrorDetail | None = None


class HealthResponse(ContractModel):
    status: Literal["ok", "degraded", "unavailable"]
    version: Literal["7.1.0"] = "7.1.0"
    mode: Literal["offline", "llm"]
