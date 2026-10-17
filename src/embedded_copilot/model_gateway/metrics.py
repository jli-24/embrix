"""Secret-free usage, reliability and task-acceptance projections."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .contracts import ModelGatewayContract, ModelUsage, identifier, safe_text
from .profiles import BillingClass, RoutingStrategy, TaskType, UsageSnapshotStatus


class UsageSource(StrEnum):
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"


def cache_hit_rate(input_tokens: int | None, cached_input_tokens: int | None) -> float | None:
    """Return a cache ratio only when both provider values are meaningful."""

    if input_tokens is None or cached_input_tokens is None or input_tokens <= 0:
        return None
    if cached_input_tokens < 0 or cached_input_tokens > input_tokens:
        return None
    return cached_input_tokens / input_tokens


class AccountUsageSnapshot(ModelGatewayContract):
    provider_id: str
    endpoint_id: str
    connection_id: str | None = None
    credential_ref: str | None = None
    credential_revision: str | None = None
    billing_class: BillingClass = BillingClass.UNKNOWN
    # ``balance`` remains readable for persisted/legacy callers.  New code
    # must use the explicit current/last-known split below.
    balance: float | None = Field(default=None, ge=0.0)
    current_balance: float | None = Field(default=None, ge=0.0)
    last_known_balance: float | None = Field(default=None, ge=0.0)
    last_success_at: datetime | None = None
    currency: str | None = None
    usage_today: int | None = Field(default=None, ge=0)
    usage_month: int | None = Field(default=None, ge=0)
    budget_total: float | None = Field(default=None, ge=0.0)
    budget_remaining: float | None = Field(default=None, ge=0.0)
    quota_total: int | None = Field(default=None, ge=0)
    quota_remaining: int | None = Field(default=None, ge=0)
    reset_at: datetime | None = None
    source: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: UsageSnapshotStatus = UsageSnapshotStatus.UNKNOWN

    @field_validator("billing_class", mode="before")
    @classmethod
    def _billing_class(cls, value: object) -> BillingClass:
        return value if isinstance(value, BillingClass) else BillingClass(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> UsageSnapshotStatus:
        return value if isinstance(value, UsageSnapshotStatus) else UsageSnapshotStatus(value)

    @field_validator("provider_id", "endpoint_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("connection_id", "credential_revision", mode="before")
    @classmethod
    def _optional_identity(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value.strip()):
            raise ValueError("credential_ref is invalid")
        return value.strip()

    @field_validator("currency", "source", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str | None:
        return None if value is None else safe_text(value, field=info.field_name)

    @field_validator("reset_at", "updated_at", mode="before")
    @classmethod
    def _dates(cls, value: object, info) -> datetime:
        if value is None and info.field_name == "reset_at":
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError(f"{info.field_name} is invalid") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{info.field_name} is invalid")
        return value.astimezone(UTC)

    @field_validator("last_success_at", mode="before")
    @classmethod
    def _last_success_at(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("last_success_at is invalid") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("last_success_at is invalid")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _balance_contract(self) -> AccountUsageSnapshot:
        current = self.current_balance if self.current_balance is not None else self.balance
        if self.current_balance is not None and self.balance is not None and self.current_balance != self.balance:
            raise ValueError("balance and current_balance disagree")
        if self.status is UsageSnapshotStatus.AVAILABLE:
            if current is not None:
                object.__setattr__(self, "current_balance", current)
                object.__setattr__(self, "balance", current)
                object.__setattr__(self, "last_known_balance", current)
                if self.last_success_at is None:
                    object.__setattr__(self, "last_success_at", self.updated_at)
            return self
        if current is not None and self.last_known_balance is None:
            object.__setattr__(self, "last_known_balance", current)
        object.__setattr__(self, "current_balance", None)
        object.__setattr__(self, "balance", None)
        return self


class ModelReliability(ModelGatewayContract):
    model_profile_id: str
    request_attempts: int = Field(default=0, ge=0)
    request_successes: int = Field(default=0, ge=0)
    request_failures: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    timeouts: int = Field(default=0, ge=0)
    rate_limits: int = Field(default=0, ge=0)
    primary_route_attempts: int = Field(default=0, ge=0)
    primary_route_successes: int = Field(default=0, ge=0)

    @field_validator("model_profile_id", mode="before")
    @classmethod
    def _model_id(cls, value: object) -> str:
        return identifier(value, field="model_profile_id")

    @model_validator(mode="after")
    def _counts(self) -> ModelReliability:
        if self.request_successes + self.request_failures > self.request_attempts:
            raise ValueError("request outcome counts are invalid")
        if self.primary_route_successes > self.primary_route_attempts:
            raise ValueError("primary route counts are invalid")
        return self

    @property
    def request_success_rate(self) -> float | None:
        return None if self.request_attempts == 0 else self.request_successes / self.request_attempts

    @property
    def retry_rate(self) -> float | None:
        return None if self.request_attempts == 0 else self.retry_count / self.request_attempts

    @property
    def fallback_rate(self) -> float | None:
        return None if self.request_attempts == 0 else self.fallback_count / self.request_attempts

    @property
    def primary_route_success_rate(self) -> float | None:
        return None if self.primary_route_attempts == 0 else self.primary_route_successes / self.primary_route_attempts

    @property
    def sample_status(self) -> str:
        return "INSUFFICIENT_DATA" if self.request_attempts < 5 else "AVAILABLE"


class ModelTaskMetrics(ModelGatewayContract):
    model_profile_id: str
    task_type: TaskType
    attempts: int = Field(default=0, ge=0)
    model_response_successes: int = Field(default=0, ge=0)
    structured_output_pass: int = Field(default=0, ge=0)
    tool_call_pass: int = Field(default=0, ge=0)
    task_accepted: int = Field(default=0, ge=0)
    task_failed: int = Field(default=0, ge=0)
    task_blocked: int = Field(default=0, ge=0)

    @field_validator("task_type", mode="before")
    @classmethod
    def _task_type(cls, value: object) -> TaskType:
        return value if isinstance(value, TaskType) else TaskType(value)

    @field_validator("model_profile_id", mode="before")
    @classmethod
    def _model_id(cls, value: object) -> str:
        return identifier(value, field="model_profile_id")

    @model_validator(mode="after")
    def _counts(self) -> ModelTaskMetrics:
        for name in ("model_response_successes", "structured_output_pass", "tool_call_pass", "task_accepted", "task_failed", "task_blocked"):
            if getattr(self, name) > self.attempts:
                raise ValueError(f"{name} exceeds attempts")
        return self

    @property
    def task_acceptance_rate(self) -> float | None:
        denominator = self.task_accepted + self.task_failed
        return None if denominator == 0 else self.task_accepted / denominator

    @property
    def sample_status(self) -> str:
        return "INSUFFICIENT_DATA" if self.attempts < 5 else "AVAILABLE"


class ModelExecutionRecord(ModelGatewayContract):
    execution_id: str
    request_id: str
    project_id: str
    conversation_id: str | None = None
    task_type: TaskType
    routing_strategy: RoutingStrategy | None = None
    requested_model: str | None = None
    selected_model: str
    actual_model: str | None = None
    provider_id: str
    endpoint_id: str
    credential_ref: str | None = None
    required_capabilities: tuple[str, ...] = ()
    retry_count: int = Field(default=0, ge=0)
    fallback_used: bool = False
    fallback_reason: str | None = None
    usage: ModelUsage | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)
    status: ExecutionStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> ExecutionStatus:
        return value if isinstance(value, ExecutionStatus) else ExecutionStatus(value)

    @field_validator("execution_id", "request_id", "project_id", "selected_model", "provider_id", "endpoint_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("conversation_id", "requested_model", "actual_model", "fallback_reason", mode="before")
    @classmethod
    def _optional_text(cls, value: object, info) -> str | None:
        return None if value is None else safe_text(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value.strip()):
            raise ValueError("credential_ref is invalid")
        return value.strip()

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object) -> tuple[str, ...]:
        if type(value) is not tuple:
            raise TypeError("required_capabilities must be a tuple")
        return tuple(safe_text(item, field="required_capability") for item in value)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("timestamp is invalid") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("timestamp is invalid")
        return value.astimezone(UTC)


__all__ = (
    "AccountUsageSnapshot",
    "ExecutionStatus",
    "ModelExecutionRecord",
    "ModelReliability",
    "ModelTaskMetrics",
    "UsageSource",
    "cache_hit_rate",
)
