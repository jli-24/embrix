"""Deterministic in-process usage aggregation fed by sanitized execution records."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator

from .contracts import ModelGatewayContract, identifier
from .metrics import (
    AccountUsageSnapshot,
    ExecutionStatus,
    ModelExecutionRecord,
    ModelReliability,
    ModelTaskMetrics,
    cache_hit_rate,
)
from .profiles import EndpointProfile, TaskType, UsageSnapshotStatus


class ModelUsageAggregate(ModelGatewayContract):
    model_profile_id: str
    provider_id: str
    endpoint_id: str
    project_id: str | None = None
    task_type: TaskType | None = None
    request_count: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    provider_reported_cost: float | None = Field(default=None, ge=0.0)
    estimated_cost: float | None = Field(default=None, ge=0.0)
    currency: str | None = None
    billing_class: str | None = None
    usage_source: str = "UNKNOWN"

    @field_validator("model_profile_id", "provider_id", "endpoint_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @property
    def cache_hit_rate(self) -> float | None:
        return cache_hit_rate(self.input_tokens, self.cached_input_tokens)


class ModelUsageAggregator:
    """Aggregate only typed execution records; it never accepts prompt content."""

    def __init__(self) -> None:
        self._usage: dict[tuple[str, str, str, str | None, TaskType | None], ModelUsageAggregate] = {}
        self._reliability: dict[str, ModelReliability] = {}
        self._task_metrics: dict[tuple[str, TaskType], ModelTaskMetrics] = {}

    def record(self, record: ModelExecutionRecord, *, task_accepted: bool | None = None) -> None:
        if type(record) is not ModelExecutionRecord:
            raise TypeError("model execution record is invalid")
        key = (record.model_profile_id if hasattr(record, "model_profile_id") else record.selected_model, record.provider_id, record.endpoint_id, record.project_id, record.task_type)
        previous = self._usage.get(key)
        usage = record.usage
        aggregate = previous or ModelUsageAggregate(
            model_profile_id=key[0],
            provider_id=key[1],
            endpoint_id=key[2],
            project_id=key[3],
            task_type=key[4],
        )
        self._usage[key] = aggregate.model_copy(
            update={
                "request_count": aggregate.request_count + 1,
                "input_tokens": _sum_optional(aggregate.input_tokens, usage.input_tokens if usage else None),
                "output_tokens": _sum_optional(aggregate.output_tokens, usage.output_tokens if usage else None),
                "cached_input_tokens": _sum_optional(aggregate.cached_input_tokens, usage.cached_input_tokens if usage else None),
                "cache_write_tokens": _sum_optional(aggregate.cache_write_tokens, usage.cache_write_tokens if usage else None),
                "reasoning_tokens": _sum_optional(aggregate.reasoning_tokens, usage.reasoning_tokens if usage else None),
                "total_tokens": _sum_optional(aggregate.total_tokens, usage.total_tokens if usage else None),
                "provider_reported_cost": _sum_optional_float(aggregate.provider_reported_cost, usage.provider_reported_cost if usage else None),
                "estimated_cost": _sum_optional_float(aggregate.estimated_cost, usage.estimated_cost if usage else None),
                "currency": aggregate.currency or (usage.currency if usage else None),
                "billing_class": aggregate.billing_class or (usage.billing_class if usage else None),
                "usage_source": aggregate.usage_source if aggregate.usage_source != "UNKNOWN" else (usage.usage_source if usage else "UNKNOWN"),
            }
        )
        self._record_reliability(record)
        if task_accepted is not None:
            self._record_task(record, task_accepted)

    def usage(self) -> tuple[ModelUsageAggregate, ...]:
        return tuple(self._usage.values())

    def reliability(self) -> tuple[ModelReliability, ...]:
        return tuple(self._reliability.values())

    def task_metrics(self) -> tuple[ModelTaskMetrics, ...]:
        return tuple(self._task_metrics.values())

    def restore(
        self,
        usage: tuple[ModelUsageAggregate, ...] = (),
        reliability: tuple[ModelReliability, ...] = (),
        task_metrics: tuple[ModelTaskMetrics, ...] = (),
    ) -> None:
        if any(type(item) is not ModelUsageAggregate for item in usage):
            raise TypeError("usage aggregate is invalid")
        if any(type(item) is not ModelReliability for item in reliability):
            raise TypeError("reliability metric is invalid")
        if any(type(item) is not ModelTaskMetrics for item in task_metrics):
            raise TypeError("task metric is invalid")
        self._usage = {
            (item.model_profile_id, item.provider_id, item.endpoint_id, item.project_id, item.task_type): item
            for item in usage
        }
        self._reliability = {item.model_profile_id: item for item in reliability}
        self._task_metrics = {(item.model_profile_id, item.task_type): item for item in task_metrics}

    def _record_reliability(self, record: ModelExecutionRecord) -> None:
        model_id = record.selected_model
        previous = self._reliability.get(model_id) or ModelReliability(model_profile_id=model_id)
        succeeded = record.status is ExecutionStatus.SUCCEEDED
        primary = not record.fallback_used
        self._reliability[model_id] = previous.model_copy(
            update={
                "request_attempts": previous.request_attempts + 1,
                "request_successes": previous.request_successes + int(succeeded),
                "request_failures": previous.request_failures + int(not succeeded),
                "retry_count": previous.retry_count + record.retry_count,
                "fallback_count": previous.fallback_count + int(record.fallback_used),
                "primary_route_attempts": previous.primary_route_attempts + int(primary),
                "primary_route_successes": previous.primary_route_successes + int(primary and succeeded),
            }
        )

    def _record_task(self, record: ModelExecutionRecord, accepted: bool) -> None:
        key = (record.selected_model, record.task_type)
        previous = self._task_metrics.get(key) or ModelTaskMetrics(
            model_profile_id=record.selected_model,
            task_type=record.task_type,
        )
        self._task_metrics[key] = previous.model_copy(
            update={
                "attempts": previous.attempts + 1,
                "model_response_successes": previous.model_response_successes + int(record.status is ExecutionStatus.SUCCEEDED),
                "task_accepted": previous.task_accepted + int(accepted),
                "task_failed": previous.task_failed + int(not accepted),
            }
        )


class UsageQueryFailure(RuntimeError):
    """Safe provider usage-query failure without provider response details."""


@runtime_checkable
class AccountUsagePort(Protocol):
    def read(
        self,
        endpoint: EndpointProfile,
        credential_ref: str | None,
        *,
        connection_id: str | None = None,
        credential_revision: str | None = None,
    ) -> AccountUsageSnapshot: ...


class AccountUsageSnapshotCache:
    """TTL cache with explicit refresh and non-blocking failure projection."""

    def __init__(
        self,
        port: AccountUsagePort,
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(port, AccountUsagePort):
            raise TypeError("account usage port is invalid")
        if type(ttl_seconds) not in (int, float) or ttl_seconds <= 0:
            raise ValueError("usage cache ttl is invalid")
        self._port = port
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._cache: dict[
            tuple[str | None, str, str, str | None, str | None],
            tuple[AccountUsageSnapshot, float],
        ] = {}

    def read(
        self,
        endpoint: EndpointProfile,
        *,
        credential_ref: str | None = None,
        connection_id: str | None = None,
        credential_revision: str | None = None,
        force: bool = False,
    ) -> AccountUsageSnapshot:
        if type(endpoint) is not EndpointProfile:
            raise TypeError("endpoint profile is invalid")
        key = (
            connection_id,
            endpoint.provider_id,
            endpoint.endpoint_id,
            credential_ref,
            credential_revision,
        )
        now = self._clock()
        cached = self._cache.get(key)
        if not force and cached is not None and now - cached[1] <= self._ttl_seconds:
            return cached[0]
        try:
            read_parameters = inspect.signature(self._port.read).parameters
            if "connection_id" in read_parameters or any(
                item.kind is inspect.Parameter.VAR_KEYWORD
                for item in read_parameters.values()
            ):
                result = self._port.read(
                    endpoint,
                    credential_ref,
                    connection_id=connection_id,
                    credential_revision=credential_revision,
                )
            else:
                result = self._port.read(endpoint, credential_ref)
            if (
                type(result) is not AccountUsageSnapshot
                or result.provider_id != endpoint.provider_id
                or result.endpoint_id != endpoint.endpoint_id
                or result.connection_id != connection_id
                or result.credential_revision != credential_revision
            ):
                raise UsageQueryFailure()
        except (UsageQueryFailure, TimeoutError, ConnectionError, ValueError, TypeError):
            if cached is not None:
                previous = cached[0]
                stale = previous.model_copy(
                    update={
                        "status": UsageSnapshotStatus.STALE,
                        "balance": None,
                        "current_balance": None,
                        "last_known_balance": (
                            previous.last_known_balance
                            if previous.last_known_balance is not None
                            else previous.current_balance
                            if previous.current_balance is not None
                            else previous.balance
                        ),
                    }
                )
                self._cache[key] = (stale, now)
                return stale
            return AccountUsageSnapshot(
                provider_id=endpoint.provider_id,
                endpoint_id=endpoint.endpoint_id,
                connection_id=connection_id,
                credential_ref=credential_ref,
                credential_revision=credential_revision,
                status=UsageSnapshotStatus.UNAVAILABLE,
            )
        self._cache[key] = (result, now)
        return result

    def refresh(
        self,
        endpoint: EndpointProfile,
        *,
        credential_ref: str | None = None,
        connection_id: str | None = None,
        credential_revision: str | None = None,
    ) -> AccountUsageSnapshot:
        return self.read(
            endpoint,
            credential_ref=credential_ref,
            connection_id=connection_id,
            credential_revision=credential_revision,
            force=True,
        )

    def invalidate(
        self,
        *,
        connection_id: str | None = None,
        endpoint_id: str | None = None,
    ) -> None:
        for key, (snapshot, timestamp) in tuple(self._cache.items()):
            if connection_id is not None and key[0] != connection_id:
                continue
            if endpoint_id is not None and key[2] != endpoint_id:
                continue
            self._cache[key] = (
                snapshot.model_copy(
                    update={"status": UsageSnapshotStatus.STALE, "current_balance": None, "balance": None}
                ),
                timestamp,
            )

    def cached(self) -> tuple[AccountUsageSnapshot, ...]:
        return tuple(item[0] for item in self._cache.values())

    def restore(self, snapshots: tuple[AccountUsageSnapshot, ...]) -> None:
        if any(type(item) is not AccountUsageSnapshot for item in snapshots):
            raise TypeError("usage snapshot is invalid")
        now = self._clock()
        self._cache = {
            (
                item.connection_id,
                item.provider_id,
                item.endpoint_id,
                item.credential_ref,
                item.credential_revision,
            ): (item, now)
            for item in snapshots
        }


def _sum_optional(first: int | None, second: int | None) -> int | None:
    if first is None:
        return second
    if second is None:
        return first
    return first + second


def _sum_optional_float(first: float | None, second: float | None) -> float | None:
    if first is None:
        return second
    if second is None:
        return first
    return first + second


__all__ = (
    "AccountUsagePort",
    "AccountUsageSnapshotCache",
    "ModelUsageAggregate",
    "ModelUsageAggregator",
    "UsageQueryFailure",
)
