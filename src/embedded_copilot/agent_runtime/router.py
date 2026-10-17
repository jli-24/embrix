from __future__ import annotations

import asyncio
import copy

from .exceptions import (
    CapabilityAuditConflict,
    CapabilityAuditUnavailable,
    CapabilityBindingRejected,
)
from .models import (
    AgentCapabilityRequest,
    AgentCapabilityResult,
    CapabilityAsyncMode,
    CapabilityAuditEvent,
    CapabilityAuditEventType,
    CapabilityAuditReference,
    CapabilityBindingReference,
    CapabilityErrorCode,
    CapabilityOutput,
    CapabilityPolicyDecision,
    CapabilityResultStatus,
    CapabilityRouteDecision,
    validate_capability_output,
)
from .policies import classify_request
from .ports import (
    CapabilityAuditSinkPort,
    CapabilityBindingPort,
    CapabilityClockPort,
    CapabilityPolicyPort,
    CapabilityRegistryPort,
)


class CapabilityRouter:
    __slots__ = ("_audit_sink", "_clock", "_policy", "_registry")

    def __init__(
        self,
        *,
        policy: CapabilityPolicyPort,
        registry: CapabilityRegistryPort,
        audit_sink: CapabilityAuditSinkPort,
        clock: CapabilityClockPort,
    ) -> None:
        if not isinstance(policy, CapabilityPolicyPort):
            raise TypeError("capability policy port is invalid")
        if not isinstance(registry, CapabilityRegistryPort):
            raise TypeError("capability registry port is invalid")
        if not isinstance(audit_sink, CapabilityAuditSinkPort):
            raise TypeError("capability audit sink is invalid")
        if not isinstance(clock, CapabilityClockPort):
            raise TypeError("capability clock is invalid")
        self._policy = policy
        self._registry = registry
        self._audit_sink = audit_sink
        self._clock = clock

    @staticmethod
    def _request(value: object) -> AgentCapabilityRequest:
        if type(value) is not AgentCapabilityRequest:
            raise TypeError("agent capability request is invalid")
        return AgentCapabilityRequest.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )

    def _policy_decision(
        self, request: AgentCapabilityRequest, route: CapabilityRouteDecision
    ) -> CapabilityPolicyDecision | None:
        try:
            raw = self._policy.evaluate(copy.deepcopy(request), copy.deepcopy(route))
            checked = CapabilityPolicyDecision.model_validate(
                copy.deepcopy(raw.model_dump(mode="python"))
            )
        except Exception:  # noqa: BLE001 - untrusted policy boundary
            return None
        if (
            checked.request_fingerprint != request.fingerprint
            or checked.route_fingerprint != route.fingerprint
            or checked.capability is not route.selected_capability
        ):
            return None
        return checked

    def _binding(
        self, route: CapabilityRouteDecision
    ) -> tuple[CapabilityBindingPort, CapabilityBindingReference] | None:
        capability = route.selected_capability
        if capability is None:
            return None
        try:
            binding = self._registry.resolve(capability)
            if binding is None or not isinstance(binding, CapabilityBindingPort):
                return None
            reference = CapabilityBindingReference.model_validate(
                copy.deepcopy(binding.reference.model_dump(mode="python"))
            )
        except Exception:  # noqa: BLE001 - untrusted registry boundary
            return None
        if reference.capability is not capability:
            return None
        return binding, reference

    @staticmethod
    def _event_type(status: CapabilityResultStatus) -> CapabilityAuditEventType:
        return {
            CapabilityResultStatus.COMPLETED: CapabilityAuditEventType.CAPABILITY_COMPLETED,
            CapabilityResultStatus.REJECTED: CapabilityAuditEventType.CAPABILITY_REJECTED,
            CapabilityResultStatus.FAILED: CapabilityAuditEventType.CAPABILITY_FAILED,
        }[status]

    def _audit(
        self,
        *,
        request: AgentCapabilityRequest,
        route: CapabilityRouteDecision,
        policy: CapabilityPolicyDecision | None,
        binding_reference: CapabilityBindingReference | None,
        event_type: CapabilityAuditEventType,
        output: CapabilityOutput | None,
        error_code: CapabilityErrorCode | None,
    ) -> CapabilityAuditEvent:
        event_id = CapabilityAuditEvent.event_id_for(
            request.fingerprint, route.fingerprint, event_type
        )
        try:
            existing_raw = self._audit_sink.get(event_id)
            if existing_raw is not None:
                existing = CapabilityAuditEvent.model_validate(
                    copy.deepcopy(existing_raw.model_dump(mode="python"))
                )
                expected = (
                    event_type,
                    request.application_request.request_id,
                    request.application_request.trace_id,
                    request.application_request.project_id,
                    request.application_request.requester,
                    request.fingerprint,
                    request.payload.fingerprint,
                    route.fingerprint,
                    policy.fingerprint if policy is not None else None,
                    binding_reference,
                    route.selected_capability,
                    output.fingerprint if output is not None else None,
                    error_code,
                )
                actual = (
                    existing.event_type,
                    existing.request_id,
                    existing.trace_id,
                    existing.project_id,
                    existing.requester,
                    existing.request_fingerprint,
                    existing.payload_fingerprint,
                    existing.route_fingerprint,
                    existing.policy_decision_fingerprint,
                    existing.binding_reference,
                    existing.selected_capability,
                    existing.output_fingerprint,
                    existing.error_code,
                )
                if actual != expected:
                    raise CapabilityAuditConflict()
                return existing
            timestamp = self._clock.now()
            if timestamp < request.application_request.requested_at:
                raise ValueError("capability clock precedes request")
            event = CapabilityAuditEvent(
                event_id=event_id,
                event_type=event_type,
                request_id=request.application_request.request_id,
                trace_id=request.application_request.trace_id,
                project_id=request.application_request.project_id,
                requester=request.application_request.requester,
                request_fingerprint=request.fingerprint,
                payload_fingerprint=request.payload.fingerprint,
                route_fingerprint=route.fingerprint,
                policy_decision_fingerprint=(
                    policy.fingerprint if policy is not None else None
                ),
                binding_reference=binding_reference,
                selected_capability=route.selected_capability,
                output_fingerprint=output.fingerprint if output is not None else None,
                error_code=error_code,
                timestamp=timestamp,
            )
            saved_raw = self._audit_sink.append(copy.deepcopy(event))
            saved = CapabilityAuditEvent.model_validate(
                copy.deepcopy(saved_raw.model_dump(mode="python"))
            )
            if saved != event:
                raise CapabilityAuditUnavailable()
            return saved
        except CapabilityAuditConflict:
            raise
        except Exception as error:
            raise CapabilityAuditUnavailable() from error

    def _result(
        self,
        *,
        request: AgentCapabilityRequest,
        route: CapabilityRouteDecision,
        policy: CapabilityPolicyDecision | None,
        binding_reference: CapabilityBindingReference | None,
        status: CapabilityResultStatus,
        output: CapabilityOutput | None,
        error_code: CapabilityErrorCode | None,
    ) -> AgentCapabilityResult:
        event = self._audit(
            request=request,
            route=route,
            policy=policy,
            binding_reference=binding_reference,
            event_type=self._event_type(status),
            output=output,
            error_code=error_code,
        )
        return AgentCapabilityResult(
            request_id=request.application_request.request_id,
            request_fingerprint=request.fingerprint,
            route=route,
            policy=policy,
            binding_reference=binding_reference,
            status=status,
            output=output,
            error_code=error_code,
            audit=CapabilityAuditReference.from_event(event),
            completed_at=event.timestamp,
        )

    def _prepare(
        self, request: AgentCapabilityRequest
    ) -> tuple[
        CapabilityRouteDecision,
        CapabilityPolicyDecision | None,
        tuple[CapabilityBindingPort, CapabilityBindingReference] | None,
        AgentCapabilityResult | None,
    ]:
        route = classify_request(request)
        if route.selected_capability is None:
            result = self._result(
                request=request,
                route=route,
                policy=None,
                binding_reference=None,
                status=CapabilityResultStatus.REJECTED,
                output=None,
                error_code=CapabilityErrorCode.ROUTE_NOT_FOUND,
            )
            return route, None, None, result
        if route.selected_capability is not request.payload.capability:
            result = self._result(
                request=request,
                route=route,
                policy=None,
                binding_reference=None,
                status=CapabilityResultStatus.REJECTED,
                output=None,
                error_code=CapabilityErrorCode.PAYLOAD_ROUTE_MISMATCH,
            )
            return route, None, None, result
        policy = self._policy_decision(request, route)
        if policy is None or not policy.allowed:
            result = self._result(
                request=request,
                route=route,
                policy=policy,
                binding_reference=None,
                status=CapabilityResultStatus.REJECTED,
                output=None,
                error_code=CapabilityErrorCode.POLICY_DENIED,
            )
            return route, policy, None, result
        resolved = self._binding(route)
        if resolved is None:
            result = self._result(
                request=request,
                route=route,
                policy=policy,
                binding_reference=None,
                status=CapabilityResultStatus.FAILED,
                output=None,
                error_code=CapabilityErrorCode.BINDING_UNAVAILABLE,
            )
            return route, policy, None, result
        return route, policy, resolved, None

    def invoke(self, request: AgentCapabilityRequest) -> AgentCapabilityResult:
        checked = self._request(request)
        route, policy, resolved, terminal = self._prepare(checked)
        if terminal is not None:
            return terminal
        assert resolved is not None
        binding, reference = resolved
        try:
            output = validate_capability_output(binding.invoke(copy.deepcopy(checked)))
            if output.capability is not route.selected_capability:
                raise CapabilityBindingRejected()
        except CapabilityBindingRejected:
            return self._result(
                request=checked,
                route=route,
                policy=policy,
                binding_reference=reference,
                status=CapabilityResultStatus.REJECTED,
                output=None,
                error_code=CapabilityErrorCode.BINDING_REJECTED,
            )
        except Exception:  # noqa: BLE001 - binding errors are redacted
            return self._result(
                request=checked,
                route=route,
                policy=policy,
                binding_reference=reference,
                status=CapabilityResultStatus.FAILED,
                output=None,
                error_code=CapabilityErrorCode.BINDING_FAILED,
            )
        return self._result(
            request=checked,
            route=route,
            policy=policy,
            binding_reference=reference,
            status=CapabilityResultStatus.COMPLETED,
            output=output,
            error_code=None,
        )

    async def _cancelled(
        self,
        *,
        request: AgentCapabilityRequest,
        route: CapabilityRouteDecision,
        policy: CapabilityPolicyDecision,
        reference: CapabilityBindingReference,
        output: CapabilityOutput | None,
    ) -> None:
        self._audit(
            request=request,
            route=route,
            policy=policy,
            binding_reference=reference,
            event_type=CapabilityAuditEventType.CAPABILITY_CANCELLED,
            output=output,
            error_code=CapabilityErrorCode.CANCELLED,
        )

    async def ainvoke(self, request: AgentCapabilityRequest) -> AgentCapabilityResult:
        checked = self._request(request)
        route, policy, resolved, terminal = self._prepare(checked)
        if terminal is not None:
            return terminal
        assert resolved is not None and policy is not None
        binding, reference = resolved
        try:
            if reference.async_mode is CapabilityAsyncMode.NATIVE_ASYNC:
                raw = await binding.ainvoke(copy.deepcopy(checked))
            else:
                worker = asyncio.create_task(
                    asyncio.to_thread(binding.invoke, copy.deepcopy(checked))
                )
                try:
                    raw = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    output = None
                    try:
                        output = validate_capability_output(await worker)
                    except Exception:  # noqa: BLE001 - cancellation terminal state
                        output = None
                    await self._cancelled(
                        request=checked,
                        route=route,
                        policy=policy,
                        reference=reference,
                        output=output,
                    )
                    raise
            output = validate_capability_output(raw)
            if output.capability is not route.selected_capability:
                raise CapabilityBindingRejected()
        except asyncio.CancelledError:
            if reference.async_mode is CapabilityAsyncMode.NATIVE_ASYNC:
                await self._cancelled(
                    request=checked,
                    route=route,
                    policy=policy,
                    reference=reference,
                    output=None,
                )
            raise
        except CapabilityBindingRejected:
            return self._result(
                request=checked,
                route=route,
                policy=policy,
                binding_reference=reference,
                status=CapabilityResultStatus.REJECTED,
                output=None,
                error_code=CapabilityErrorCode.BINDING_REJECTED,
            )
        except Exception:  # noqa: BLE001 - binding errors are redacted
            return self._result(
                request=checked,
                route=route,
                policy=policy,
                binding_reference=reference,
                status=CapabilityResultStatus.FAILED,
                output=None,
                error_code=CapabilityErrorCode.BINDING_FAILED,
            )
        return self._result(
            request=checked,
            route=route,
            policy=policy,
            binding_reference=reference,
            status=CapabilityResultStatus.COMPLETED,
            output=output,
            error_code=None,
        )


__all__ = ("CapabilityRouter",)
