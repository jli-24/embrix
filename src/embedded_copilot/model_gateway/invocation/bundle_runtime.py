"""Private two-operation budget dispatch guard for a held invocation service."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime

from ..exceptions import ModelGatewayRejected
from .budget import reject, wire_target_digest
from .budget_bundle import (
    observe_counter,
    verify_counter_admission,
    verify_generation_bundle,
)
from .contracts import InvocationMode, TaskAuthorityFailure


class BundleInvocationMixin:
    """Sequence durable counter and generation intents around guarded transport."""

    _clock: Callable[[], datetime]

    def _bundle_guard(
        self,
        request,
        profile,
        selection,
        authority_source,
        attempt_state,
        *,
        asynchronous=False,
    ):
        if request.invocation_mode is not InvocationMode.TASK:
            return None

        source = self._budget_bundle_source
        store = self._receipt_store

        def inputs(bound, protocol, payload, parameter, wire_target):
            return {
                "request": request,
                "snapshot": attempt_state[0].snapshot,
                "bound": bound,
                "protocol": protocol,
                "payload": payload,
                "parameter": parameter,
                "wire_target": wire_target,
                "now": self._clock(),
            }

        def current(entry, bound, protocol, payload, parameter, wire_target):
            attempt = attempt_state[0]
            snapshot = attempt.snapshot
            if (
                entry.connection_id != snapshot.connection_id
                or entry.credential_revision != snapshot.credential_revision
                or entry.endpoint_revision != snapshot.endpoint_revision
            ):
                reject("BUDGET_BINDING_STALE")
            try:
                if self._selection.execution_snapshot(profile, selection) != snapshot:
                    reject("BUDGET_BINDING_STALE")
                authority = authority_source.revalidate(request.authority_reference)
                if (
                    authority != request.authority_reference
                    or authority != attempt.authority_reference
                ):
                    reject("TASK_AUTHORITY_STALE")
            except ModelGatewayRejected:
                raise
            except TaskAuthorityFailure as error:
                reject(error.code)
            except Exception:  # noqa: BLE001 - caller authority fails closed
                reject("BUDGET_BINDING_STALE")
            return inputs(bound, protocol, payload, parameter, wire_target)

        def check_counter(
            entry, envelope, admission, bound, protocol, payload, parameter, wire_target
        ):
            kwargs = current(entry, bound, protocol, payload, parameter, wire_target)
            try:
                qualification = source.qualify_counter(
                    envelope=envelope, admission=admission, **kwargs
                )
                return verify_counter_admission(
                    envelope=envelope,
                    admission=admission,
                    qualification=qualification,
                    **kwargs,
                )
            except ModelGatewayRejected:
                raise
            except Exception:  # noqa: BLE001 - host qualification fails closed
                reject("BUDGET_BINDING_STALE")

        def check_generation(
            entry,
            envelope,
            admission,
            observation,
            evidence,
            bound,
            protocol,
            payload,
            parameter,
            wire_target,
        ):
            kwargs = current(entry, bound, protocol, payload, parameter, wire_target)
            try:
                qualification = source.qualify_generation(
                    envelope=envelope,
                    admission=admission,
                    observation=observation,
                    evidence=evidence,
                    **kwargs,
                )
                return verify_generation_bundle(
                    envelope=envelope,
                    admission=admission,
                    observation=observation,
                    evidence=evidence,
                    qualification=qualification,
                    **kwargs,
                )
            except ModelGatewayRejected:
                raise
            except Exception:  # noqa: BLE001 - host qualification fails closed
                reject("BUDGET_BINDING_STALE")

        def record_observation(envelope, admission, response):
            observation = observe_counter(
                request=request,
                snapshot=attempt_state[0].snapshot,
                envelope=envelope,
                admission=admission,
                response=response,
                now=self._clock(),
            )
            try:
                attempt_state[0] = store.record_counter_observation(
                    attempt_state[0], observation
                )
            except Exception:  # noqa: BLE001 - durable observation is mandatory
                reject()
            if observation.observation_status != "COUNTED":
                reject()
            if observation.input_tokens > envelope.input_admission_limit:
                reject("BUDGET_LIMIT_EXCEEDED")
            return observation

        def consumption_kwargs(
            envelope, bound, protocol, payload, parameter, wire_target
        ):
            hook = getattr(source, "phase_authorization", None)
            if not callable(hook):
                return {}
            if getattr(source, "consumption_store", None) is not store:
                reject("BUDGET_BINDING_STALE")
            try:
                authorization = hook(
                    envelope,
                    principal_id=getattr(authority_source, "requester", None),
                    **inputs(bound, protocol, payload, parameter, wire_target),
                )
                if type(authorization) is not tuple or len(authorization) != 2:
                    reject("BUDGET_BINDING_STALE")
                return {
                    "_consumption_authorization": authorization,
                    "_consumption_now": self._clock(),
                }
            except ModelGatewayRejected:
                raise
            except Exception:  # noqa: BLE001 - trusted host consent fails closed
                reject("BUDGET_BINDING_STALE")

        def claim_counter(
            envelope, admission, bound, protocol, payload, parameter, wire_target
        ):
            kwargs = consumption_kwargs(
                envelope, bound, protocol, payload, parameter, wire_target
            )
            try:
                attempt_state[0] = store.claim_counter_intent(
                    attempt_state[0], envelope, admission, **kwargs
                )
            except ValueError as error:
                reject(
                    "BUDGET_ENVELOPE_CLAIMED"
                    if str(error) == "BUDGET_ENVELOPE_CLAIMED"
                    else "BUDGET_EVIDENCE_UNAVAILABLE"
                )
            except Exception:  # noqa: BLE001 - durable claim is mandatory
                reject()

        def claim_generation(
            envelope, evidence, bound, protocol, payload, parameter, wire_target
        ):
            kwargs = consumption_kwargs(
                envelope, bound, protocol, payload, parameter, wire_target
            )
            try:
                attempt_state[0] = store.claim_generation_intent(
                    attempt_state[0], evidence, **kwargs
                )
            except Exception:  # noqa: BLE001 - durable claim is mandatory
                reject()

        def prepare_counter(bound, protocol, payload, parameter, wire_target):
            if source is None or store is None:
                reject()
            try:
                return source.prepare_counter(
                    **inputs(bound, protocol, payload, parameter, wire_target)
                )
            except ModelGatewayRejected:
                raise
            except Exception:  # noqa: BLE001 - host preparation fails closed
                reject()

        def prepare_generation(
            envelope,
            admission,
            observation,
            bound,
            protocol,
            payload,
            parameter,
            wire_target,
        ):
            try:
                return source.prepare_generation(
                    envelope=envelope,
                    admission=admission,
                    observation=observation,
                    **inputs(bound, protocol, payload, parameter, wire_target),
                )
            except ModelGatewayRejected:
                raise
            except Exception:  # noqa: BLE001 - host preparation fails closed
                reject()

        def counter_bytes(admission, raw, target):
            try:
                expected = base64.b64decode(
                    admission.count_request_base64, validate=True
                )
                if (
                    raw != expected
                    or wire_target_digest(target)
                    != admission.counter_wire_target_sha256
                ):
                    reject("BUDGET_BINDING_STALE")
            except ModelGatewayRejected:
                raise
            except Exception:  # noqa: BLE001 - count target must remain exact
                reject("BUDGET_BINDING_STALE")

        def guard(bound, entry, protocol, payload, parameter, wire_target):
            envelope, admission = prepare_counter(
                bound, protocol, payload, parameter, wire_target
            )
            envelope, admission = check_counter(
                entry,
                envelope,
                admission,
                bound,
                protocol,
                payload,
                parameter,
                wire_target,
            )
            claim_counter(
                envelope, admission, bound, protocol, payload, parameter, wire_target
            )

            def count_guard(raw, target):
                counter_bytes(admission, raw, target)
                check_counter(
                    entry,
                    envelope,
                    admission,
                    bound,
                    protocol,
                    payload,
                    parameter,
                    wire_target,
                )

            count = getattr(self._gateway, "_count_bound_with_metadata", None)
            if not callable(count):
                reject()
            response = count(
                entry,
                base64.b64decode(admission.count_request_base64, validate=True),
                dispatch_guard=count_guard,
            )
            observation = record_observation(envelope, admission, response)
            evidence = prepare_generation(
                envelope,
                admission,
                observation,
                bound,
                protocol,
                payload,
                parameter,
                wire_target,
            )
            check_generation(
                entry,
                envelope,
                admission,
                observation,
                evidence,
                bound,
                protocol,
                payload,
                parameter,
                wire_target,
            )
            claim_generation(
                envelope, evidence, bound, protocol, payload, parameter, wire_target
            )
            check_generation(
                entry,
                envelope,
                admission,
                observation,
                evidence,
                bound,
                protocol,
                payload,
                parameter,
                wire_target,
            )

        async def aguard(bound, entry, protocol, payload, parameter, wire_target):
            envelope, admission = prepare_counter(
                bound, protocol, payload, parameter, wire_target
            )
            envelope, admission = check_counter(
                entry,
                envelope,
                admission,
                bound,
                protocol,
                payload,
                parameter,
                wire_target,
            )
            claim_counter(
                envelope, admission, bound, protocol, payload, parameter, wire_target
            )

            async def count_guard(raw, target):
                counter_bytes(admission, raw, target)
                check_counter(
                    entry,
                    envelope,
                    admission,
                    bound,
                    protocol,
                    payload,
                    parameter,
                    wire_target,
                )

            count = getattr(self._gateway, "_acount_bound_with_metadata", None)
            if not callable(count):
                reject()
            response = await count(
                entry,
                base64.b64decode(admission.count_request_base64, validate=True),
                dispatch_guard=count_guard,
            )
            observation = record_observation(envelope, admission, response)
            evidence = prepare_generation(
                envelope,
                admission,
                observation,
                bound,
                protocol,
                payload,
                parameter,
                wire_target,
            )
            check_generation(
                entry,
                envelope,
                admission,
                observation,
                evidence,
                bound,
                protocol,
                payload,
                parameter,
                wire_target,
            )
            claim_generation(
                envelope, evidence, bound, protocol, payload, parameter, wire_target
            )
            check_generation(
                entry,
                envelope,
                admission,
                observation,
                evidence,
                bound,
                protocol,
                payload,
                parameter,
                wire_target,
            )

        return aguard if asynchronous else guard

    @staticmethod
    def _bundle_role_projection(attempt, response, http_status, send_count_override):
        if attempt is None or attempt.attempt_schema_version != 4:
            return {}
        generation_count = attempt.generation_transport_send_count
        if attempt.budget_phase == "GENERATION_INTENT":
            if (
                response is not None
                or http_status is not None
                or send_count_override == 1
            ):
                generation_count = 1
            elif send_count_override == 0:
                generation_count = 0
            else:
                generation_count = None
        counter_count = attempt.counter_transport_send_count
        total = (
            None
            if counter_count is None or generation_count is None
            else counter_count + generation_count
        )
        return {
            "budget_phase": "TERMINAL",
            "counter_admission_fingerprint": attempt.counter_admission_fingerprint,
            "counter_observation_fingerprint": attempt.counter_observation_fingerprint,
            "counter_status": (
                "OUTCOME_UNKNOWN"
                if attempt.budget_phase == "COUNTER_INTENT"
                else attempt.counter_status
            ),
            "counter_transport_send_count": counter_count,
            "counter_response_observed": attempt.counter_response_observed,
            "generation_transport_send_count": generation_count,
            "total_external_request_count": total,
            "counter_http_status": attempt.counter_http_status,
            "counter_input_tokens": attempt.counter_input_tokens,
        }
