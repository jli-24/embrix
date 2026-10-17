"""SQLite role transitions for an approved count-plus-generation budget bundle.

The mixin uses the existing attempt, claim, receipt, and outcome tables. It does
not qualify evidence or send a request; callers must perform currentness checks.
"""

from __future__ import annotations

import json
import sqlite3

from embedded_copilot.model_gateway.contracts import (
    ModelResponse,
    canonical_fingerprint,
)
from embedded_copilot.model_gateway.invocation.bundle_contracts import (
    CounterAdmissionV1,
    CounterObservationV1,
    SingleInvocationBudgetEnvelopeV2,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    AttemptStatus,
    InvocationBudgetEvidenceV1,
    InvocationMode,
    ModelInvocationAttempt,
    ModelInvocationReceipt,
)

_CLAIM_KEYS = frozenset(
    {"schema_version", "admission", "observation", "generation_evidence"}
)
_ROLE_FIELDS = (
    "counter_admission_fingerprint",
    "counter_observation_fingerprint",
    "counter_status",
    "counter_transport_send_count",
    "counter_response_observed",
    "generation_transport_send_count",
    "total_external_request_count",
    "counter_http_status",
    "counter_input_tokens",
)


def _encode(value: object) -> str:
    # Import at call time: model_gateway_storage imports this mixin during its
    # class definition, while its existing secret-field guard is defined later.
    from embedded_copilot.model_gateway_storage import _assert_secret_free

    _assert_secret_free(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _changed(
    attempt: ModelInvocationAttempt, **fields: object
) -> ModelInvocationAttempt:
    return ModelInvocationAttempt.model_validate(
        {
            **attempt.model_dump(mode="python", exclude={"fingerprint"}),
            **fields,
        }
    )


def _attempt_row(
    connection: sqlite3.Connection, attempt: ModelInvocationAttempt
) -> str:
    row = connection.execute(
        "SELECT payload FROM model_invocation_attempts WHERE project_id=? AND request_id=?",
        (attempt.project_id, attempt.request_id),
    ).fetchone()
    if row is None or ModelInvocationAttempt.model_validate_json(row[0]) != attempt:
        raise ValueError("bundle attempt changed")
    return row[0]


def _read_claim(connection: sqlite3.Connection, attempt: ModelInvocationAttempt):
    row = connection.execute(
        "SELECT envelope,evidence FROM model_invocation_budget_claims "
        "WHERE envelope_id=? AND reservation_id=? AND project_id=? AND request_id=?",
        (
            attempt.budget_envelope_id,
            attempt.budget_reservation_id,
            attempt.project_id,
            attempt.request_id,
        ),
    ).fetchone()
    if row is None:
        raise ValueError("bundle claim missing")
    envelope = SingleInvocationBudgetEnvelopeV2.model_validate_json(row[0])
    claim = json.loads(row[1])
    if (
        type(claim) is not dict
        or set(claim) != _CLAIM_KEYS
        or claim["schema_version"] != 2
    ):
        raise ValueError("bundle claim version invalid")
    # The contracts intentionally require Python tuples/enums while accepting
    # canonical JSON arrays/strings through their JSON validation path.
    admission = CounterAdmissionV1.model_validate_json(json.dumps(claim["admission"]))
    observation = (
        None
        if claim["observation"] is None
        else CounterObservationV1.model_validate_json(json.dumps(claim["observation"]))
    )
    evidence = (
        None
        if claim["generation_evidence"] is None
        else InvocationBudgetEvidenceV1.model_validate_json(
            json.dumps(claim["generation_evidence"])
        )
    )
    if (
        envelope.envelope_id != attempt.budget_envelope_id
        or envelope.project_id != attempt.project_id
        or admission.envelope_fingerprint != envelope.fingerprint
        or admission.fingerprint != attempt.counter_admission_fingerprint
        or (None if observation is None else observation.fingerprint)
        != attempt.counter_observation_fingerprint
        or (None if evidence is None else evidence.fingerprint)
        != attempt.budget_evidence_fingerprint
    ):
        raise ValueError("bundle claim binding mismatch")
    return row[1], envelope, claim, admission, observation, evidence


def _update_attempt(
    connection: sqlite3.Connection, before: str, attempt: ModelInvocationAttempt
) -> None:
    result = connection.execute(
        "UPDATE model_invocation_attempts SET payload=? "
        "WHERE project_id=? AND request_id=? AND payload=?",
        (
            _encode(attempt.model_dump(mode="json")),
            attempt.project_id,
            attempt.request_id,
            before,
        ),
    )
    if result.rowcount != 1:
        raise ValueError("bundle attempt CAS rejected")


def _update_claim(
    connection: sqlite3.Connection,
    attempt: ModelInvocationAttempt,
    before: str,
    claim: dict[str, object],
) -> None:
    result = connection.execute(
        "UPDATE model_invocation_budget_claims SET evidence=? "
        "WHERE envelope_id=? AND reservation_id=? AND project_id=? AND request_id=? AND evidence=?",
        (
            _encode(claim),
            attempt.budget_envelope_id,
            attempt.budget_reservation_id,
            attempt.project_id,
            attempt.request_id,
            before,
        ),
    )
    if result.rowcount != 1:
        raise ValueError("bundle claim CAS rejected")


class CounterBundleStoreMixin:
    """Serial, append-only role CAS on the existing SQLite ledger."""

    def claim_counter_intent(
        self,
        attempt: ModelInvocationAttempt,
        envelope: SingleInvocationBudgetEnvelopeV2,
        admission: CounterAdmissionV1,
        *,
        _consumption_authorization=None,
        _consumption_now=None,
    ) -> ModelInvocationAttempt:
        if (
            type(attempt) is not ModelInvocationAttempt
            or attempt.attempt_schema_version != 4
            or attempt.status is not AttemptStatus.PREPARED
            or attempt.budget_phase != "PREPARED"
            or type(envelope) is not SingleInvocationBudgetEnvelopeV2
            or type(admission) is not CounterAdmissionV1
        ):
            raise ValueError("bundle counter intent invalid")
        authority = attempt.authority_reference
        snapshot = attempt.snapshot
        if (
            attempt.invocation_mode is not InvocationMode.TASK
            or authority is None
            or envelope.project_id != attempt.project_id
            or envelope.tenant_id != authority.tenant_id
            or envelope.task_id != authority.engineering_task_id
            or envelope.task_authority_fingerprint
            != canonical_fingerprint(authority, exclude=set())
            or envelope.engineering_stage != snapshot.stage_id
            or admission.envelope_fingerprint != envelope.fingerprint
            or admission.execution_snapshot_fingerprint != snapshot.fingerprint
            or admission.task_authority_fingerprint
            != envelope.task_authority_fingerprint
            or admission.policy_fingerprint != snapshot.policy_fingerprint
            or admission.selected_model != snapshot.selected_model
            or admission.connection_id != snapshot.connection_id
            or admission.connection_revision != snapshot.connection_revision
            or admission.credential_revision != snapshot.credential_revision
            or admission.endpoint_revision != snapshot.endpoint_revision
        ):
            raise ValueError("bundle counter binding invalid")
        reservation_id = "budget-" + admission.fingerprint.removeprefix("sha256:")
        intent = _changed(
            attempt,
            status=AttemptStatus.DISPATCH_INTENT,
            budget_phase="COUNTER_INTENT",
            budget_envelope_id=envelope.envelope_id,
            budget_reservation_id=reservation_id,
            counter_admission_fingerprint=admission.fingerprint,
            counter_status="DISPATCH_INTENT",
            counter_transport_send_count=None,
            generation_transport_send_count=0,
            total_external_request_count=None,
        )
        claim = {
            "schema_version": 2,
            "admission": admission.model_dump(mode="json"),
            "observation": None,
            "generation_evidence": None,
        }
        envelope_json = _encode(envelope.model_dump(mode="json"))
        claim_json = _encode(claim)
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if _consumption_authorization is not None:
                from embedded_copilot.model_gateway.invocation.consumption_store import (
                    validate_phase_grant,
                )

                scope, account = _consumption_authorization
                validate_phase_grant(
                    connection,
                    envelope=envelope,
                    scope=scope,
                    account_authority=account,
                    phase="COUNTER",
                    now=_consumption_now,
                )
            before = _attempt_row(connection, attempt)
            try:
                connection.execute(
                    "INSERT INTO model_invocation_budget_claims"
                    "(envelope_id,tenant_id,project_id,request_id,reservation_id,envelope,evidence) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        envelope.envelope_id,
                        envelope.tenant_id,
                        envelope.project_id,
                        attempt.request_id,
                        reservation_id,
                        envelope_json,
                        claim_json,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("BUDGET_ENVELOPE_CLAIMED") from error
            _update_attempt(connection, before, intent)
        return intent

    def bundle_binding_for_attempt(self, attempt: ModelInvocationAttempt):
        if (
            type(attempt) is not ModelInvocationAttempt
            or attempt.attempt_schema_version != 4
        ):
            raise ValueError("bundle attempt required")
        with sqlite3.connect(self._path) as connection:
            _attempt_row(connection, attempt)
            _, envelope, _, admission, observation, evidence = _read_claim(
                connection, attempt
            )
        return envelope, admission, observation, evidence

    def record_counter_observation(
        self,
        attempt: ModelInvocationAttempt,
        observation: CounterObservationV1,
    ) -> ModelInvocationAttempt:
        if (
            type(attempt) is not ModelInvocationAttempt
            or attempt.attempt_schema_version != 4
            or attempt.status is not AttemptStatus.DISPATCH_INTENT
            or attempt.budget_phase != "COUNTER_INTENT"
            or type(observation) is not CounterObservationV1
        ):
            raise ValueError("bundle counter observation invalid")
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = _attempt_row(connection, attempt)
            claim_before, envelope, claim, admission, previous, evidence = _read_claim(
                connection, attempt
            )
            if (
                previous is not None
                or evidence is not None
                or (
                    observation.envelope_id != envelope.envelope_id
                    or observation.project_id != attempt.project_id
                    or observation.request_id != attempt.request_id
                    or observation.counter_admission_fingerprint
                    != admission.fingerprint
                    or observation.execution_snapshot_fingerprint
                    != attempt.snapshot.fingerprint
                    or observation.task_authority_fingerprint
                    != admission.task_authority_fingerprint
                    or observation.selected_model != admission.selected_model
                    or observation.connection_id != admission.connection_id
                    or observation.connection_revision != admission.connection_revision
                    or observation.credential_revision != admission.credential_revision
                    or observation.endpoint_revision != admission.endpoint_revision
                    or observation.final_generation_bytes_sha256
                    != admission.final_generation_bytes_sha256
                    or observation.count_request_sha256
                    != admission.count_request_sha256
                    or observation.count_request_base64
                    != admission.count_request_base64
                    or observation.counter_wire_target_sha256
                    != admission.counter_wire_target_sha256
                )
            ):
                raise ValueError("bundle counter observation binding invalid")
            observed = _changed(
                attempt,
                budget_phase="COUNTER_OBSERVED",
                counter_observation_fingerprint=observation.fingerprint,
                counter_status=observation.observation_status,
                counter_transport_send_count=1,
                counter_response_observed=True,
                generation_transport_send_count=0,
                total_external_request_count=1,
                counter_http_status=observation.http_status,
                counter_input_tokens=observation.input_tokens,
            )
            claim["observation"] = observation.model_dump(mode="json")
            _update_claim(connection, attempt, claim_before, claim)
            _update_attempt(connection, before, observed)
        return observed

    def claim_generation_intent(
        self,
        attempt: ModelInvocationAttempt,
        evidence: InvocationBudgetEvidenceV1,
        *,
        _consumption_authorization=None,
        _consumption_now=None,
    ) -> ModelInvocationAttempt:
        if (
            type(attempt) is not ModelInvocationAttempt
            or attempt.attempt_schema_version != 4
            or attempt.status is not AttemptStatus.DISPATCH_INTENT
            or attempt.budget_phase != "COUNTER_OBSERVED"
            or attempt.counter_status != "COUNTED"
            or type(evidence) is not InvocationBudgetEvidenceV1
        ):
            raise ValueError("bundle generation intent invalid")
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = _attempt_row(connection, attempt)
            claim_before, envelope, claim, admission, observation, previous = (
                _read_claim(connection, attempt)
            )
            if _consumption_authorization is not None:
                from embedded_copilot.model_gateway.invocation.consumption_store import (
                    validate_phase_grant,
                )

                scope, account = _consumption_authorization
                validate_phase_grant(
                    connection,
                    envelope=envelope,
                    scope=scope,
                    account_authority=account,
                    phase="GENERATION",
                    now=_consumption_now,
                )
            if (
                observation is None
                or previous is not None
                or observation.observation_status != "COUNTED"
                or observation.input_tokens is None
                or observation.input_tokens > envelope.input_admission_limit
                or evidence.envelope_fingerprint != envelope.fingerprint
                or evidence.request_fingerprint != admission.request_fingerprint
                or evidence.execution_snapshot_fingerprint
                != attempt.snapshot.fingerprint
                or evidence.final_transport_sha256
                != admission.final_generation_bytes_sha256
                or evidence.selected_model != admission.selected_model
                or evidence.connection_id != admission.connection_id
                or evidence.connection_revision != admission.connection_revision
                or evidence.credential_revision != admission.credential_revision
                or evidence.endpoint_revision != admission.endpoint_revision
                or evidence.count_kind != "EXACT"
                or evidence.input_token_bound != observation.input_tokens
                or evidence.qualification_evidence_fingerprint
                != observation.fingerprint
                or evidence.output_token_limit != envelope.total_output_limit
            ):
                raise ValueError("bundle generation binding invalid")
            intent = _changed(
                attempt,
                budget_phase="GENERATION_INTENT",
                budget_evidence_fingerprint=evidence.fingerprint,
                generation_transport_send_count=None,
                total_external_request_count=None,
            )
            claim["generation_evidence"] = evidence.model_dump(mode="json")
            _update_claim(connection, attempt, claim_before, claim)
            _update_attempt(connection, before, intent)
        return intent

    def mark_bundle_unknown(
        self, attempt: ModelInvocationAttempt
    ) -> ModelInvocationAttempt:
        if (
            type(attempt) is not ModelInvocationAttempt
            or attempt.attempt_schema_version != 4
            or attempt.status is not AttemptStatus.DISPATCH_INTENT
            or attempt.budget_phase
            not in {
                "COUNTER_INTENT",
                "COUNTER_OBSERVED",
                "GENERATION_INTENT",
            }
        ):
            raise ValueError("active bundle attempt required")
        changes = {"status": AttemptStatus.OUTCOME_UNKNOWN, "budget_phase": "TERMINAL"}
        if attempt.budget_phase == "COUNTER_INTENT":
            changes["counter_status"] = "OUTCOME_UNKNOWN"
        unknown = _changed(attempt, **changes)
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = _attempt_row(connection, attempt)
            _read_claim(connection, attempt)
            _update_attempt(connection, before, unknown)
        return unknown

    def complete_bundle_attempt(
        self,
        attempt: ModelInvocationAttempt,
        receipt: ModelInvocationReceipt,
        response: ModelResponse | None = None,
        http_failure=None,
    ) -> ModelInvocationReceipt:
        if (
            type(attempt) is not ModelInvocationAttempt
            or attempt.attempt_schema_version != 4
            or (attempt.status, attempt.budget_phase)
            not in {
                (AttemptStatus.PREPARED, "PREPARED"),
                (AttemptStatus.DISPATCH_INTENT, "COUNTER_INTENT"),
                (AttemptStatus.DISPATCH_INTENT, "COUNTER_OBSERVED"),
                (AttemptStatus.DISPATCH_INTENT, "GENERATION_INTENT"),
                (AttemptStatus.OUTCOME_UNKNOWN, "TERMINAL"),
            }
            or (
                attempt.status is AttemptStatus.OUTCOME_UNKNOWN
                and attempt.receipt_fingerprint is not None
            )
            or type(receipt) is not ModelInvocationReceipt
            or receipt.schema_version != 7
            or receipt.budget_phase != "TERMINAL"
            or receipt.project_id != attempt.project_id
            or receipt.request_id != attempt.request_id
            or receipt.stage_id != attempt.snapshot.stage_id
            or receipt.selected_model != attempt.snapshot.selected_model
            or receipt.policy_source != attempt.snapshot.policy_source
            or receipt.connection_id != attempt.snapshot.connection_id
            or receipt.invocation_mode != attempt.invocation_mode
            or receipt.authority_reference != attempt.authority_reference
            or receipt.budget_envelope_id != attempt.budget_envelope_id
            or receipt.budget_reservation_id != attempt.budget_reservation_id
            or receipt.budget_evidence_fingerprint
            != attempt.budget_evidence_fingerprint
        ):
            raise ValueError("bundle terminal receipt binding invalid")
        phase = attempt.budget_phase
        if phase == "PREPARED":
            if (
                receipt.counter_status != "NOT_ATTEMPTED"
                or receipt.counter_transport_send_count != 0
                or receipt.generation_transport_send_count != 0
                or receipt.total_external_request_count != 0
                or receipt.counter_response_observed
                or response is not None
            ):
                raise ValueError("unclaimed bundle terminal cannot claim an operation")
        else:
            unchanged = _ROLE_FIELDS
            if phase == "COUNTER_INTENT":
                unchanged = tuple(
                    name for name in unchanged if name != "counter_status"
                )
            unchanged = tuple(
                name
                for name in unchanged
                if name
                not in {
                    "counter_transport_send_count",
                    "generation_transport_send_count",
                    "total_external_request_count",
                }
            )
            if (
                any(
                    getattr(receipt, name) != getattr(attempt, name)
                    for name in unchanged
                )
                or receipt.counter_transport_send_count
                != attempt.counter_transport_send_count
                or receipt.generation_transport_send_count
                not in {
                    0,
                    1,
                    None,
                }
            ):
                raise ValueError("bundle terminal role facts changed")
            if phase == "COUNTER_INTENT":
                if (
                    receipt.counter_status != "OUTCOME_UNKNOWN"
                    or receipt.generation_transport_send_count != 0
                    or response is not None
                ):
                    raise ValueError("uncertain counter terminal invalid")
            elif phase == "COUNTER_OBSERVED":
                if receipt.generation_transport_send_count != 0 or response is not None:
                    raise ValueError("counter-only terminal cannot claim generation")
            elif (
                phase == "GENERATION_INTENT"
                and attempt.budget_evidence_fingerprint is None
            ):
                raise ValueError("generation intent evidence missing")
            if phase == "TERMINAL" and (
                attempt.status is not AttemptStatus.OUTCOME_UNKNOWN
                or receipt.counter_status != attempt.counter_status
                or receipt.generation_transport_send_count
                != attempt.generation_transport_send_count
                or response is not None
            ):
                raise ValueError("recovered bundle terminal facts changed")
            if receipt.total_external_request_count != (
                None
                if receipt.counter_transport_send_count is None
                or receipt.generation_transport_send_count is None
                else receipt.counter_transport_send_count
                + receipt.generation_transport_send_count
            ):
                raise ValueError("bundle terminal total cannot hide UNKNOWN")
        if response is not None:
            if (
                type(response) is not ModelResponse
                or receipt.budget_reconciliation_state != "BOUND_VIOLATION"
                or receipt.result.value != "FAILED"
                or receipt.generation_transport_send_count != 1
                or len(response.content.encode("utf-8")) > 4 * 1024 * 1024
            ):
                raise ValueError("bundle violation original invalid")
        elif receipt.budget_reconciliation_state == "BOUND_VIOLATION":
            raise ValueError("bundle violation original missing")
        http_material = None
        if http_failure is not None:
            from embedded_copilot.model_gateway.invocation.bundle_http import (
                GenerationHTTPFailure,
            )

            if (
                type(http_failure) is not GenerationHTTPFailure
                or response is not None
                or receipt.result.value == "SUCCESS"
                or receipt.generation_transport_send_count != 1
                or receipt.http_status != http_failure.http_status
                or phase != "GENERATION_INTENT"
            ):
                raise ValueError("generation HTTP failure binding invalid")
            _, admission, _, evidence = self.bundle_binding_for_attempt(attempt)
            if (
                http_failure.request_sha256 != admission.final_generation_bytes_sha256
                or http_failure.wire_target_sha256
                != admission.generation_wire_target_sha256
                or evidence is None
            ):
                raise ValueError("generation HTTP failure identity changed")
            http_material = http_failure.material()
        unknown_outcome = (
            response is not None
            or attempt.status is AttemptStatus.OUTCOME_UNKNOWN
            or receipt.counter_status == "OUTCOME_UNKNOWN"
            or receipt.counter_transport_send_count is None
            or receipt.generation_transport_send_count is None
        )
        if unknown_outcome and receipt.result.value == "SUCCESS":
            raise ValueError("uncertain bundle cannot be successful")
        terminal = _changed(
            attempt,
            status=(
                AttemptStatus.OUTCOME_UNKNOWN
                if unknown_outcome
                else AttemptStatus.COMPLETED
            ),
            budget_phase="TERMINAL",
            receipt_fingerprint=receipt.fingerprint,
            counter_status=receipt.counter_status,
            counter_transport_send_count=receipt.counter_transport_send_count,
            generation_transport_send_count=receipt.generation_transport_send_count,
            total_external_request_count=receipt.total_external_request_count,
            gateway_call_count=receipt.gateway_call_count,
            gateway_response_observed=receipt.gateway_response_observed,
            transport_send_count=receipt.transport_send_count,
        )
        receipt_json = _encode(receipt.model_dump(mode="json"))
        response_json = (
            None if response is None else _encode(response.model_dump(mode="json"))
        )
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = _attempt_row(connection, attempt)
            if phase == "PREPARED":
                conflicting = connection.execute(
                    "SELECT 1 FROM model_invocation_budget_claims WHERE project_id=? AND request_id=?",
                    (attempt.project_id, attempt.request_id),
                ).fetchone()
                if conflicting is not None:
                    raise ValueError("unclaimed bundle has a persisted claim")
            else:
                _, _, _, _, observation, evidence = _read_claim(connection, attempt)
                if (
                    (
                        phase == "COUNTER_INTENT"
                        and (observation is not None or evidence is not None)
                    )
                    or (
                        phase == "COUNTER_OBSERVED"
                        and (observation is None or evidence is not None)
                    )
                    or (
                        phase == "GENERATION_INTENT"
                        and (observation is None or evidence is None)
                    )
                ):
                    raise ValueError("bundle terminal claim binding invalid")
            connection.execute(
                "INSERT INTO model_invocation_receipts"
                "(fingerprint,schema_version,payload,created_at) VALUES (?,?,?,?)",
                (
                    receipt.fingerprint,
                    receipt.schema_version,
                    receipt_json,
                    receipt.completed_at.isoformat(),
                ),
            )
            if response is not None:
                connection.execute(
                    "INSERT INTO model_invocation_budget_outcomes"
                    "(project_id,request_id,receipt_fingerprint,response_fingerprint,response) "
                    "VALUES (?,?,?,?,?)",
                    (
                        attempt.project_id,
                        attempt.request_id,
                        receipt.fingerprint,
                        response.fingerprint,
                        response_json,
                    ),
                )
            if http_material is not None:
                from embedded_copilot.model_gateway.invocation.budget import digest

                connection.execute(
                    "INSERT INTO model_invocation_budget_outcomes"
                    "(project_id,request_id,receipt_fingerprint,response_fingerprint,response) VALUES (?,?,?,?,?)",
                    (
                        attempt.project_id,
                        attempt.request_id,
                        receipt.fingerprint,
                        digest(_encode(http_material).encode("utf-8")),
                        _encode(http_material),
                    ),
                )
            _update_attempt(connection, before, terminal)
        return ModelInvocationReceipt.model_validate_json(receipt_json)

    def generation_http_failure_for_attempt(self, attempt):
        import base64
        import hashlib

        from embedded_copilot.model_gateway.invocation.bundle_http import (
            GenerationHTTPFailure,
        )

        _, admission, _, evidence = self.bundle_binding_for_attempt(attempt)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT receipt_fingerprint,response_fingerprint,response FROM model_invocation_budget_outcomes WHERE project_id=? AND request_id=?",
                (attempt.project_id, attempt.request_id),
            ).fetchone()
        if row is None:
            return None
        material = json.loads(row[2])
        if material.get("kind") != "GENERATION_HTTP_FAILURE":
            return None
        raw = base64.b64decode(material["raw_response_base64"], validate=True)
        if (
            material["schema_version"] != 2
            or row[0] != attempt.receipt_fingerprint
            or hashlib.sha256(_encode(material).encode("utf-8")).hexdigest()
            != row[1].removeprefix("sha256:")
            or material["raw_response_sha256"]
            != "sha256:" + hashlib.sha256(raw).hexdigest()
            or material["request_sha256"] != admission.final_generation_bytes_sha256
            or material["wire_target_sha256"] != admission.generation_wire_target_sha256
            or evidence is None
        ):
            raise ValueError("generation HTTP original changed")
        return GenerationHTTPFailure(
            material["http_status"],
            material["request_sha256"],
            material["wire_target_sha256"],
            raw,
        )
