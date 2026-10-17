"""Secret-free, append-only evidence for production provider probes."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.model_gateway.governance import BoundModelIdentity

_AUTO_FINGERPRINT = "sha256:" + "0" * 64
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CREDENTIAL_REF_RE = re.compile(r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_UNKNOWN = Literal["UNKNOWN"]
_NOT_OBTAINED = Literal["NOT_OBTAINED"]
TokenValue = int | _UNKNOWN
NumericValue = float | _UNKNOWN
HttpStatus = int | _NOT_OBTAINED
ModelAvailability = bool | _UNKNOWN


class VerificationStatus(StrEnum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


class ResponseParseStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GoldenEvaluationStatus(StrEnum):
    PASS = "PASS"
    STALE = "STALE"
    NOT_GOLDEN = "NOT_GOLDEN"
    BLOCKED = "BLOCKED"


def _field_name(info: object | None, field: str | None) -> str:
    return field or str(getattr(info, "field_name", "value"))


def _identifier(value: object, info: object | None = None, *, field: str | None = None) -> str:
    name = _field_name(info, field)
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value.strip()):
        raise ValueError(f"{name} is invalid")
    return value.strip()


def _optional_identifier(value: object, info: object | None = None, *, field: str | None = None) -> str | None:
    return None if value is None else _identifier(value, info, field=field)


def _code(value: object, info: object | None = None, *, field: str | None = None) -> str | None:
    return None if value is None else _identifier(value, info, field=field)


def _credential_ref(value: object, info: object | None = None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _CREDENTIAL_REF_RE.fullmatch(value.strip()):
        raise ValueError("credential_ref is invalid")
    return value.strip()


def _safe_url(value: object, info: object | None = None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("request_url_safe is invalid")
    checked = value.strip()
    from urllib.parse import urlsplit

    parsed = urlsplit(checked)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path
        or len(checked) > 512
        or (parsed.scheme == "http" and parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("request_url_safe is invalid")
    return checked


def _aware_datetime(value: object, info: object | None = None, *, field: str | None = None) -> datetime:
    name = _field_name(info, field)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{name} is invalid") from error
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _hash_payload(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def projection_revision(value: object) -> str:
    """Derive a stable revision from an existing endpoint/model projection."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    if not isinstance(value, Mapping):
        raise TypeError("projection is invalid")
    return _hash_payload(dict(value))


class ProviderVerificationTarget(BaseModel):
    """The current non-secret configuration identity used for Golden checks."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    provider_id: str
    connection_id: str | None = None
    endpoint_id: str
    model_id: str
    credential_revision: str
    endpoint_revision: str
    model_revision: str
    configuration_fingerprint: str | None = None

    _validate_ids = field_validator("provider_id", "endpoint_id", "model_id", mode="before")(_identifier)
    _validate_connection = field_validator("connection_id", mode="before")(_optional_identifier)
    _validate_revisions = field_validator(
        "credential_revision", "endpoint_revision", "model_revision", mode="before"
    )(_identifier)
    _validate_configuration = field_validator("configuration_fingerprint", mode="before")(
        lambda value: None
        if value is None
        else _identifier(value, field="configuration_fingerprint")
    )


class ProviderVerificationReceipt(BaseModel):
    """One sanitized result of one connection probe."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    verification_id: str
    provider_id: str
    connection_id: str | None = None
    endpoint_id: str | None = None
    model_id: str | None = None
    credential_ref: str | None = None
    credential_revision: str = "UNKNOWN"
    endpoint_revision: str = "UNKNOWN"
    model_revision: str = "UNKNOWN"
    configuration_fingerprint: str = "UNKNOWN"
    protocol_family: str = "UNKNOWN"
    requested_at: datetime
    completed_at: datetime
    latency_ms: float | None = Field(default=None, ge=0.0)
    request_method: Literal["POST"] = "POST"
    request_url_safe: str | None = None
    http_status: HttpStatus = "NOT_OBTAINED"
    provider_request_id: str | None = None
    requested_model: str | None = None
    verified_model: str | None = None
    response_parse_status: ResponseParseStatus = ResponseParseStatus.NOT_APPLICABLE
    model_available: ModelAvailability = "UNKNOWN"
    connection_status: str
    provider_error_code: str | None = None
    normalized_error_code: str | None = None
    input_tokens: TokenValue = "UNKNOWN"
    output_tokens: TokenValue = "UNKNOWN"
    cached_input_tokens: TokenValue = "UNKNOWN"
    reasoning_tokens: TokenValue = "UNKNOWN"
    total_tokens: TokenValue = "UNKNOWN"
    usage_source: str = "UNKNOWN"
    cost: NumericValue = "UNKNOWN"
    currency: str | _UNKNOWN = "UNKNOWN"
    cost_source: str = "UNKNOWN"
    cache_status: str = "UNKNOWN"
    verification_status: VerificationStatus = VerificationStatus.BLOCKED
    evidence_origin: str = "LIVE_RUNTIME"
    probe_type: str = "minimal_text"
    message_count: int = Field(default=1, ge=0, le=8)
    request_shape: str = "single_user_message"
    persistence_error_code: str | None = None
    evidence_version: str = "provider-verification-v1"
    schema_version: int = Field(default=1, ge=1)
    fingerprint: str = _AUTO_FINGERPRINT

    _validate_verification_id = field_validator("verification_id", mode="before")(_identifier)
    _validate_provider = field_validator("provider_id", mode="before")(_identifier)
    _validate_connection = field_validator("connection_id", mode="before")(_optional_identifier)
    _validate_endpoint = field_validator("endpoint_id", "model_id", "requested_model", "verified_model", mode="before")(
        _optional_identifier
    )
    _validate_credential_ref = field_validator("credential_ref", mode="before")(_credential_ref)
    _validate_revisions = field_validator(
        "credential_revision", "endpoint_revision", "model_revision", mode="before"
    )(_identifier)
    _validate_configuration = field_validator("configuration_fingerprint", mode="before")(
        lambda value: _identifier(value, field="configuration_fingerprint")
    )
    _validate_protocol = field_validator("protocol_family", "connection_status", "usage_source", "cost_source", "cache_status", "evidence_origin", "probe_type", "request_shape", "evidence_version", mode="before")(_identifier)
    _validate_url = field_validator("request_url_safe", mode="before")(_safe_url)
    _validate_provider_codes = field_validator("provider_error_code", "normalized_error_code", "persistence_error_code", mode="before")(_code)
    _validate_request_id = field_validator("provider_request_id", mode="before")(_optional_identifier)
    _validate_dates = field_validator("requested_at", "completed_at", mode="before")(_aware_datetime)

    @field_validator("http_status")
    @classmethod
    def _validate_http_status(cls, value: object) -> HttpStatus:
        if value == "NOT_OBTAINED":
            return value
        if type(value) is not int or not 100 <= value <= 599:
            raise ValueError("http_status is invalid")
        return value

    @field_validator("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens")
    @classmethod
    def _validate_tokens(cls, value: object) -> TokenValue:
        if value == "UNKNOWN":
            return value
        if type(value) is not int or value < 0:
            raise ValueError("token count is invalid")
        return value

    @field_validator("cost")
    @classmethod
    def _validate_cost(cls, value: object) -> NumericValue:
        if value == "UNKNOWN":
            return value
        if type(value) not in {int, float} or value < 0:
            raise ValueError("cost is invalid")
        return float(value)

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: object) -> str | _UNKNOWN:
        if value == "UNKNOWN":
            return value
        return _identifier(value, field="currency")

    @model_validator(mode="after")
    def _verify(self) -> ProviderVerificationReceipt:
        if self.completed_at < self.requested_at:
            raise ValueError("completed_at precedes requested_at")
        expected = _hash_payload(self.model_dump(mode="json", exclude={"fingerprint"}))
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("provider verification receipt fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: Any) -> ProviderVerificationReceipt:
        now = values.pop("completed_at", datetime.now(UTC))
        values.setdefault("requested_at", now)
        values["completed_at"] = now
        values.setdefault("verification_id", f"verification-{uuid4()}")
        return cls.model_validate(values)


class ConnectionTestRecord(BaseModel):
    """Immutable, secret-free history projection for one connection test.

    This is deliberately derived from :class:`ProviderVerificationReceipt`.
    The receipt remains the canonical evidence store; the record is the
    product-facing history contract and never creates a second state source.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    test_id: str
    connection_id: str
    provider_id: str
    endpoint_id: str
    credential_ref: str | None = None
    credential_revision: str = "UNKNOWN"
    protocol: str = "UNKNOWN"
    tested_at: datetime
    latency_ms: float | None = Field(default=None, ge=0.0)
    status: str
    error_code: str | None = None
    model_id: str | None = None
    fingerprint: str = _AUTO_FINGERPRINT

    _validate_ids = field_validator(
        "test_id", "connection_id", "provider_id", "endpoint_id", "model_id", mode="before"
    )(_optional_identifier)
    _validate_credential_ref = field_validator("credential_ref", mode="before")(_credential_ref)
    _validate_revision = field_validator("credential_revision", mode="before")(_identifier)
    _validate_protocol_status = field_validator("protocol", "status", mode="before")(_identifier)
    _validate_error = field_validator("error_code", mode="before")(_code)
    _validate_tested_at = field_validator("tested_at", mode="before")(_aware_datetime)

    @model_validator(mode="after")
    def _verify(self) -> ConnectionTestRecord:
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(
                self,
                "fingerprint",
                _hash_payload(self.model_dump(mode="json", exclude={"fingerprint"})),
            )
        elif self.fingerprint != _hash_payload(self.model_dump(mode="json", exclude={"fingerprint"})):
            raise ValueError("connection test record fingerprint mismatch")
        return self

    @classmethod
    def from_receipt(cls, receipt: ProviderVerificationReceipt) -> ConnectionTestRecord:
        if receipt.connection_id is None or receipt.endpoint_id is None:
            raise ValueError("connection test receipt is not connection-scoped")
        is_success = (
            receipt.connection_status == "AVAILABLE"
            and getattr(receipt.verification_status, "value", receipt.verification_status) == "PASS"
        )
        record = cls(
            test_id=receipt.verification_id,
            connection_id=receipt.connection_id,
            provider_id=receipt.provider_id,
            endpoint_id=receipt.endpoint_id,
            credential_ref=receipt.credential_ref,
            credential_revision=receipt.credential_revision,
            protocol=receipt.protocol_family,
            tested_at=receipt.completed_at,
            latency_ms=receipt.latency_ms,
            status=receipt.connection_status,
            error_code=(
                None
                if is_success
                else receipt.provider_error_code or receipt.normalized_error_code
            ),
            model_id=receipt.model_id,
        )
        _assert_secret_free(record.model_dump(mode="json"))
        return record


class ModelRuntimeHealth(BaseModel):
    """Read-only health projection for a complete provider-scoped identity."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    identity: BoundModelIdentity
    endpoint_status: str = "UNKNOWN"
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_ms: float | None = Field(default=None, ge=0.0)
    quota_status: str = "UNKNOWN"
    observed_at: datetime | None = None
    evidence_freshness: str = "UNKNOWN"
    sample_count: int = Field(default=0, ge=0)

    @classmethod
    def from_receipts(
        cls,
        *,
        identity: BoundModelIdentity | Mapping[str, str],
        receipts: tuple[ProviderVerificationReceipt, ...],
        credential_revision: str | None = None,
        endpoint_revision: str | None = None,
        model_revision: str | None = None,
    ) -> ModelRuntimeHealth:
        bound_identity = (
            identity
            if isinstance(identity, BoundModelIdentity)
            else BoundModelIdentity.model_validate(dict(identity))
        )
        current = tuple(
            item
            for item in receipts
            if item.connection_id is not None
            and item.provider_id == bound_identity.provider_id
            and item.endpoint_id == bound_identity.endpoint_id
            and item.credential_ref == bound_identity.credential_ref
            and item.model_id == bound_identity.model_id
            and (credential_revision is None or item.credential_revision == credential_revision)
            and (endpoint_revision is None or item.endpoint_revision == endpoint_revision)
            and (model_revision is None or item.model_revision == model_revision)
        )
        identity_scoped = tuple(
            item
            for item in receipts
            if item.connection_id is not None
            and item.provider_id == bound_identity.provider_id
            and item.endpoint_id == bound_identity.endpoint_id
            and item.credential_ref == bound_identity.credential_ref
            and item.model_id == bound_identity.model_id
        )
        successes = tuple(
            item for item in current if getattr(item.verification_status, "value", item.verification_status) == "PASS"
        )
        failures = tuple(
            item for item in current if getattr(item.verification_status, "value", item.verification_status) != "PASS"
        )
        latest = current[0] if current else None
        latencies = tuple(item.latency_ms for item in current if item.latency_ms is not None)
        quota_status = "UNKNOWN"
        if latest is not None and (
            latest.connection_status in {"RATE_LIMITED", "QUOTA_EXHAUSTED"}
            or latest.normalized_error_code == "PROVIDER_RATE_LIMITED"
        ):
            quota_status = "EXHAUSTED"
        elif latest is not None and latest.connection_status == "AVAILABLE":
            quota_status = "AVAILABLE"
        return cls(
            identity=bound_identity,
            endpoint_status=latest.connection_status if latest is not None else "UNKNOWN",
            last_success_at=max(item.completed_at for item in successes) if successes else None,
            last_failure_at=max(item.completed_at for item in failures) if failures else None,
            error_rate=(len(failures) / len(current)) if current else None,
            latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
            quota_status=quota_status,
            observed_at=latest.completed_at if latest is not None else None,
            evidence_freshness=(
                "CURRENT" if current else "STALE" if identity_scoped else "UNKNOWN"
            ),
            sample_count=len(current),
        )


class GoldenEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    status: GoldenEvaluationStatus
    is_golden: bool
    reason_code: str


def evaluate_provider_golden(
    receipt: ProviderVerificationReceipt,
    current_configuration: ProviderVerificationTarget,
    *,
    verification_persisted: bool = True,
) -> GoldenEvaluation:
    """Deterministically evaluate a receipt without an LLM or provider access."""

    if not verification_persisted:
        return GoldenEvaluation(status=GoldenEvaluationStatus.BLOCKED, is_golden=False, reason_code="RECEIPT_NOT_PERSISTED")
    if any(
        (
            receipt.provider_id != current_configuration.provider_id,
            receipt.connection_id != current_configuration.connection_id,
            receipt.endpoint_id != current_configuration.endpoint_id,
            receipt.model_id != current_configuration.model_id,
            receipt.credential_revision != current_configuration.credential_revision,
            receipt.endpoint_revision != current_configuration.endpoint_revision,
            receipt.model_revision != current_configuration.model_revision,
            current_configuration.configuration_fingerprint is not None
            and receipt.configuration_fingerprint != current_configuration.configuration_fingerprint,
        )
    ):
        return GoldenEvaluation(status=GoldenEvaluationStatus.STALE, is_golden=False, reason_code="REVISION_MISMATCH")
    if receipt.verification_status is VerificationStatus.BLOCKED:
        return GoldenEvaluation(status=GoldenEvaluationStatus.BLOCKED, is_golden=False, reason_code="VERIFICATION_BLOCKED")
    if not isinstance(receipt.http_status, int) or not 200 <= receipt.http_status < 300:
        return GoldenEvaluation(status=GoldenEvaluationStatus.NOT_GOLDEN, is_golden=False, reason_code="HTTP_NOT_2XX")
    if receipt.response_parse_status is not ResponseParseStatus.PASS:
        return GoldenEvaluation(status=GoldenEvaluationStatus.NOT_GOLDEN, is_golden=False, reason_code="RESPONSE_PARSE_NOT_PASS")
    if receipt.requested_model != current_configuration.model_id:
        return GoldenEvaluation(status=GoldenEvaluationStatus.STALE, is_golden=False, reason_code="REQUESTED_MODEL_MISMATCH")
    if receipt.verified_model != receipt.requested_model:
        return GoldenEvaluation(status=GoldenEvaluationStatus.NOT_GOLDEN, is_golden=False, reason_code="VERIFIED_MODEL_MISMATCH")
    if receipt.model_available is not True:
        return GoldenEvaluation(status=GoldenEvaluationStatus.NOT_GOLDEN, is_golden=False, reason_code="MODEL_NOT_AVAILABLE")
    if receipt.connection_status != "AVAILABLE":
        return GoldenEvaluation(status=GoldenEvaluationStatus.NOT_GOLDEN, is_golden=False, reason_code="CONNECTION_NOT_VERIFIED")
    if receipt.verification_status is not VerificationStatus.PASS:
        return GoldenEvaluation(status=GoldenEvaluationStatus.NOT_GOLDEN, is_golden=False, reason_code="RECEIPT_NOT_PASS")
    return GoldenEvaluation(status=GoldenEvaluationStatus.PASS, is_golden=True, reason_code="REAL_PROVIDER_2XX_GOLDEN")


class ReceiptConflict(ValueError):
    """A receipt id was reused; append-only storage never overwrites it."""


class ProviderVerificationReceiptStore:
    """SQLite-backed append-only store for secret-free provider evidence."""

    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or path.name in {"", ".", ".."}:
            raise ValueError("verification receipt path is invalid")
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_verification_receipts (
                    verification_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    model_id TEXT,
                    requested_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_provider_verification_latest ON provider_verification_receipts(provider_id, model_id, completed_at DESC)"
            )

    @property
    def path(self) -> Path:
        return self._path

    def append(self, receipt: ProviderVerificationReceipt) -> ProviderVerificationReceipt:
        if type(receipt) is not ProviderVerificationReceipt:
            raise TypeError("verification receipt is invalid")
        payload = receipt.model_dump(mode="json")
        _assert_secret_free(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            with sqlite3.connect(self._path) as connection:
                connection.execute(
                    "INSERT INTO provider_verification_receipts(verification_id, provider_id, model_id, requested_at, completed_at, schema_version, fingerprint, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        receipt.verification_id,
                        receipt.provider_id,
                        receipt.model_id,
                        receipt.requested_at.isoformat(),
                        receipt.completed_at.isoformat(),
                        self._SCHEMA_VERSION,
                        receipt.fingerprint,
                        encoded,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ReceiptConflict("verification receipt already exists") from error
        return receipt

    def get(self, verification_id: str) -> ProviderVerificationReceipt | None:
        checked = _identifier(verification_id, field="verification_id")
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT schema_version, fingerprint, payload FROM provider_verification_receipts WHERE verification_id = ?",
                (checked,),
            ).fetchone()
        return None if row is None else self._decode(row)

    def list_recent(
        self,
        limit: int = 100,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        connection_id: str | None = None,
    ) -> tuple[ProviderVerificationReceipt, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("receipt limit is invalid")
        provider = _optional_identifier(provider_id, field="provider_id")
        model = _optional_identifier(model_id, field="model_id")
        query = "SELECT schema_version, fingerprint, payload FROM provider_verification_receipts"
        parameters: list[object] = []
        checked_connection = _optional_identifier(connection_id, field="connection_id")
        if provider is not None or model is not None or checked_connection is not None:
            clauses: list[str] = []
            if provider is not None:
                clauses.append("provider_id = ?")
                parameters.append(provider)
            if model is not None:
                clauses.append("model_id = ?")
                parameters.append(model)
            if checked_connection is not None:
                clauses.append("json_extract(payload, '$.connection_id') = ?")
                parameters.append(checked_connection)
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY completed_at DESC, verification_id DESC LIMIT ?"
        parameters.append(limit)
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        decoded = tuple(self._decode(row) for row in rows)
        return decoded

    def list_test_history(
        self, connection_id: str, *, limit: int = 100
    ) -> tuple[ConnectionTestRecord, ...]:
        """Return recent connection-test projections from canonical receipts."""

        checked_connection = _identifier(connection_id, field="connection_id")
        receipts = self.list_recent(limit, connection_id=checked_connection)
        return tuple(ConnectionTestRecord.from_receipt(item) for item in receipts)

    def latest_for_provider(self, provider_id: str) -> ProviderVerificationReceipt | None:
        values = self.list_recent(1, provider_id=provider_id)
        return values[0] if values else None

    def latest_for_provider_model(self, provider_id: str, model_id: str) -> ProviderVerificationReceipt | None:
        values = self.list_recent(1, provider_id=provider_id, model_id=model_id)
        return values[0] if values else None

    def latest_for_connection(
        self, connection_id: str, *, model_id: str | None = None
    ) -> ProviderVerificationReceipt | None:
        values = self.list_recent(
            1000,
            model_id=model_id,
            connection_id=connection_id,
        )
        return values[0] if values else None

    def find_current_for_configuration(
        self,
        configuration: ProviderVerificationTarget,
    ) -> ProviderVerificationReceipt | None:
        for receipt in self.list_recent(
            1000,
            provider_id=configuration.provider_id,
            model_id=configuration.model_id,
        ):
            if all(
                (
                    receipt.connection_id == configuration.connection_id,
                    receipt.endpoint_id == configuration.endpoint_id,
                    receipt.credential_revision == configuration.credential_revision,
                    receipt.endpoint_revision == configuration.endpoint_revision,
                    receipt.model_revision == configuration.model_revision,
                    configuration.configuration_fingerprint is None
                    or receipt.configuration_fingerprint == configuration.configuration_fingerprint,
                )
            ):
                return receipt
        return None

    def _decode(self, row: tuple[object, ...]) -> ProviderVerificationReceipt:
        schema_version, fingerprint, encoded = row
        if schema_version != self._SCHEMA_VERSION or not isinstance(fingerprint, str) or not isinstance(encoded, str):
            raise ValueError("verification receipt schema is invalid")
        payload = json.loads(encoded)
        _assert_secret_free(payload)
        if not isinstance(payload, dict) or payload.get("fingerprint") != fingerprint:
            raise ValueError("verification receipt fingerprint mismatch")
        return ProviderVerificationReceipt.model_validate_json(encoded)


def _assert_secret_free(value: object) -> None:
    banned_keys = {
        "api_key",
        "authorization",
        "credential",
        "credential_value",
        "headers",
        "messages",
        "password",
        "prompt",
        "raw_secret",
        "response_body",
        "secret",
        "secret_blob",
    }
    if isinstance(value, dict):
        if banned_keys.intersection(value):
            raise ValueError("verification receipt contains a secret or body field")
        for item in value.values():
            _assert_secret_free(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_secret_free(item)
    elif isinstance(value, str) and re.search(r"(?i)bearer\s+\S+", value):
        raise ValueError("verification receipt contains an authorization value")


__all__ = (
    "ConnectionTestRecord",
    "GoldenEvaluation",
    "GoldenEvaluationStatus",
    "ProviderVerificationReceipt",
    "ProviderVerificationReceiptStore",
    "ProviderVerificationTarget",
    "ModelRuntimeHealth",
    "ReceiptConflict",
    "ResponseParseStatus",
    "VerificationStatus",
    "evaluate_provider_golden",
    "projection_revision",
)
