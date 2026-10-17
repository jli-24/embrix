from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.execution_worker.contracts import ArtifactIdentity

_ZERO = "sha256:" + "0" * 64
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}$")
_UNSAFE = re.compile(
    r"(?:\x00|\r|\n|\.\.[\\/]|[A-Za-z]:[\\/]|\\\\|file://|"
    r"[;&|`$<>]|(?:api[_ -]?key|access[_ -]?token|authorization|credential|"
    r"password|secret|token)\s*[:=]|(?:raw\s+)?(?:stdout|stderr|provider output|prompt)\b)",
    re.IGNORECASE,
)

INFRASTRUCTURE_TIMEOUT_REASONS = frozenset(
    {"BACKEND_START_TIMEOUT", "PROCESS_CONTROL_TIMEOUT", "WORKSPACE_TIMEOUT"}
)
ENGINEERING_TIMEOUT_REASONS = frozenset(
    {
        "ASSERTION_DEADLINE_EXCEEDED",
        "EXPECTED_UART_NOT_OBSERVED",
        "FIRMWARE_BOOT_DEADLINE_EXCEEDED",
    }
)


class SimulationContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if _IDENTIFIER.fullmatch(checked) is None or _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def _text(value: object, *, field: str, maximum: int = 1024) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def _fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _utc(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field} must be timezone-aware") from None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical(value: BaseModel, *, exclude: set[str] | frozenset[str]) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class FingerprintedSimulationContract(SimulationContract):
    _fingerprint_exclude: ClassVar[frozenset[str]] = frozenset()
    fingerprint: str = _ZERO

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _validate_fingerprint(cls, value: object) -> str:
        return _fingerprint(value)

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        expected = _canonical(self, exclude={"fingerprint", *self._fingerprint_exclude})
        if self.fingerprint == _ZERO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected and self.fingerprint not in self._legacy_fingerprint_candidates():
            raise ValueError("fingerprint mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        return ()

    @classmethod
    def create(cls, **values: object):
        material = copy.deepcopy(values)
        material.setdefault("fingerprint", _ZERO)
        return cls.model_validate(material)


class SimulationBackend(StrEnum):
    RENODE = "RENODE"
    QEMU = "QEMU"
    WOKWI = "WOKWI"
    FAKE = "FAKE"


class SimulationCapabilityStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class SimulationExecutionMode(StrEnum):
    TEST_ONLY = "TEST_ONLY"
    LOCAL_PROCESS = "LOCAL_PROCESS"
    EXTERNAL_CLOUD = "EXTERNAL_CLOUD"


class SimulationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    CANCELLED = "CANCELLED"


class SimulationFailureClass(StrEnum):
    NONE = "NONE"
    ENGINEERING_FAILURE = "ENGINEERING_FAILURE"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    CANCELLED = "CANCELLED"
    # Short aliases keep the private adapter code readable while exposing the
    # frozen wire values under their explicit failure-class names.
    ENGINEERING = ENGINEERING_FAILURE
    INFRASTRUCTURE = INFRASTRUCTURE_FAILURE


class SimulationAssertionKind(StrEnum):
    UART_CONTAINS = "UART_CONTAINS"
    UART_NOT_CONTAINS = "UART_NOT_CONTAINS"
    EXIT_STATUS = "EXIT_STATUS"


class SimulationBackendCapability(FingerprintedSimulationContract):
    backend: SimulationBackend
    status: SimulationCapabilityStatus
    version: str | None = None
    execution_mode: SimulationExecutionMode
    supported_targets: tuple[str, ...] = ()
    reason_code: str

    _reason = field_validator("reason_code", mode="before")(
        lambda value: _identifier(value, field="reason_code")
    )

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="version", maximum=64)

    @field_validator("supported_targets", mode="before")
    @classmethod
    def _targets(cls, value: object) -> tuple[str, ...]:
        if type(value) is not tuple or len(value) > 64:
            raise TypeError("supported_targets must be a tuple")
        checked = tuple(_identifier(item, field="supported_target") for item in value)
        if len(checked) != len(set(checked)):
            raise ValueError("supported_targets must be unique")
        return checked

    @model_validator(mode="after")
    def _availability_is_factual(self):
        if self.status is SimulationCapabilityStatus.VERIFIED:
            if self.backend is SimulationBackend.FAKE:
                if self.execution_mode is not SimulationExecutionMode.TEST_ONLY:
                    raise ValueError("fake backend must be test-only")
            elif self.execution_mode is SimulationExecutionMode.TEST_ONLY:
                raise ValueError("real backend cannot be test-only")
            elif not self.version:
                raise ValueError("real backend capability requires a verified version")
        return self


class SimulationAssertion(FingerprintedSimulationContract):
    assertion_id: str
    kind: SimulationAssertionKind
    target: str
    expected: str | int
    tolerance: float | None = None

    _ids = field_validator("assertion_id", "target", mode="before")(
        lambda value, info: _identifier(value, field=info.field_name)
    )

    @field_validator("expected", mode="before")
    @classmethod
    def _expected(cls, value: object) -> str | int:
        if type(value) not in {str, int}:
            raise TypeError("expected value is invalid")
        return value

    @field_validator("tolerance", mode="before")
    @classmethod
    def _tolerance(cls, value: object) -> float | None:
        if value is None:
            return None
        if type(value) is not float or not 0.0 <= value <= 1_000_000.0:
            raise ValueError("tolerance is invalid")
        return value

    @model_validator(mode="after")
    def _shape(self):
        if self.kind in {
            SimulationAssertionKind.UART_CONTAINS,
            SimulationAssertionKind.UART_NOT_CONTAINS,
        }:
            if type(self.expected) is not str:
                raise ValueError("expected value does not match assertion kind")
            _text(self.expected, field="expected", maximum=256)
            if self.tolerance is not None:
                raise ValueError("UART assertion does not accept tolerance")
        elif type(self.expected) is not int or not -255 <= self.expected <= 255:
            raise ValueError("expected value does not match assertion kind")
        elif self.tolerance is not None:
            raise ValueError("exit status assertion does not accept tolerance")
        return self


class SimulationAssertionResult(FingerprintedSimulationContract):
    assertion_id: str
    kind: SimulationAssertionKind
    passed: bool
    observed_summary: str
    evidence_reference: str

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value: object) -> SimulationAssertionKind:
        return value if isinstance(value, SimulationAssertionKind) else SimulationAssertionKind(value)

    _ids = field_validator("assertion_id", "evidence_reference", mode="before")(
        lambda value, info: _identifier(value, field=info.field_name)
    )
    _summary = field_validator("observed_summary", mode="before")(
        lambda value: _text(value, field="observed_summary", maximum=512)
    )


class SimulationEvidence(FingerprintedSimulationContract):
    """Sanitized, restart-safe evidence produced by a real backend run."""

    evidence_id: str
    operation_id: str
    simulation_id: str
    backend: SimulationBackend
    backend_version: str
    model_fidelity: str
    artifact_id: str
    artifact_sha256: str
    request_fingerprint: str
    elf_loaded: bool
    cpu_started: bool
    reset_handler_reached: bool
    main_reached: bool
    observations: tuple[str, ...]
    stimuli: tuple[str, ...]
    configuration_observations: tuple[str, ...] = ()
    evidence_summary: str
    receipt_reference: str
    created_at: datetime

    _ids = field_validator(
        "evidence_id",
        "operation_id",
        "simulation_id",
        "backend_version",
        "model_fidelity",
        "artifact_id",
        "receipt_reference",
        mode="before",
    )(lambda value, info: _identifier(value, field=info.field_name))
    _fps = field_validator("artifact_sha256", "request_fingerprint", mode="before")(
        lambda value, info: _fingerprint(value, field=info.field_name)
    )
    @field_validator(
        "observations", "stimuli", "configuration_observations", mode="before"
    )
    @classmethod
    def _safe_items(cls, value: object, info) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or len(value) > 64:
            raise ValueError(f"{info.field_name} is invalid")
        return tuple(_text(item, field=info.field_name, maximum=256) for item in value)

    _summary = field_validator("evidence_summary", mode="before")(
        lambda value: _text(value, field="evidence_summary", maximum=1024)
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, field="created_at")
    )

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        """Keep pre-functional-verification evidence readable after the additive field."""

        return (
            _canonical(
                self,
                exclude={"fingerprint", "configuration_observations"},
            ),
        )


class SimulationExecutionBinding(FingerprintedSimulationContract):
    requester_id: str
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    created_at: datetime
    expires_at: datetime
    nonce: str

    _ids = field_validator(
        "requester_id",
        "target_id",
        "workspace_snapshot_id",
        "proposal_id",
        "nonce",
        mode="before",
    )(lambda value, info: _identifier(value, field=info.field_name))
    _fps = field_validator(
        "target_fingerprint",
        "workspace_fingerprint",
        "proposal_fingerprint",
        mode="before",
    )(lambda value, info: _fingerprint(value, field=info.field_name))
    _times = field_validator("created_at", "expires_at", mode="before")(
        lambda value, info: _utc(value, field=info.field_name)
    )

    @model_validator(mode="after")
    def _lifetime(self):
        if self.expires_at <= self.created_at:
            raise ValueError("execution binding lifetime is invalid")
        return self


class SimulationRequest(FingerprintedSimulationContract):
    simulation_id: str
    tenant_id: str
    project_id: str
    backend: SimulationBackend
    firmware_artifact: ArtifactIdentity
    platform_descriptor: str
    scenario: str
    assertions: tuple[SimulationAssertion, ...]
    timeout_seconds: int = Field(ge=1, le=900)
    execution_binding: SimulationExecutionBinding

    _ids = field_validator(
        "simulation_id",
        "tenant_id",
        "project_id",
        "platform_descriptor",
        "scenario",
        mode="before",
    )(lambda value, info: _identifier(value, field=info.field_name))

    @field_validator("firmware_artifact", mode="before")
    @classmethod
    def _artifact(cls, value: object) -> ArtifactIdentity:
        if type(value) is ArtifactIdentity:
            material = value.model_dump(mode="python")
        elif type(value) is dict:
            material = copy.deepcopy(value)
        else:
            raise TypeError("firmware_artifact is invalid")
        return ArtifactIdentity.model_validate(material)

    @field_validator("execution_binding", mode="before")
    @classmethod
    def _binding(cls, value: object) -> SimulationExecutionBinding:
        if type(value) is SimulationExecutionBinding:
            material = value.model_dump(mode="python")
        elif type(value) is dict:
            material = copy.deepcopy(value)
        else:
            raise TypeError("execution_binding is invalid")
        return SimulationExecutionBinding.model_validate(material)

    @field_validator("assertions", mode="before")
    @classmethod
    def _assertions(cls, value: object) -> tuple[SimulationAssertion, ...]:
        if type(value) is not tuple or not value or len(value) > 64:
            raise ValueError("assertions must be a non-empty bounded tuple")
        checked = tuple(
            SimulationAssertion.model_validate(
                copy.deepcopy(item.model_dump(mode="python"))
            )
            if type(item) is SimulationAssertion
            else SimulationAssertion.model_validate(copy.deepcopy(item))
            for item in value
        )
        if len({item.assertion_id for item in checked}) != len(checked):
            raise ValueError("assertion identities must be unique")
        return checked

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _timeout(cls, value: object) -> int:
        if type(value) is not int:
            raise TypeError("timeout_seconds is invalid")
        return value

    @model_validator(mode="after")
    def _identity_binding(self):
        artifact = self.firmware_artifact
        binding = self.execution_binding
        for field in (
            "artifact_id",
            "tenant_id",
            "project_id",
            "target_id",
            "build_operation_id",
            "toolchain_id",
            "toolchain_version",
            "build_configuration",
        ):
            _identifier(getattr(artifact, field), field=f"firmware_artifact.{field}")
        if (
            artifact.tenant_id != self.tenant_id
            or artifact.project_id != self.project_id
            or artifact.target_id != binding.target_id
            or artifact.workspace_fingerprint != binding.workspace_fingerprint
        ):
            raise ValueError("artifact identity binding mismatch")
        return self


class PreparedSimulation(FingerprintedSimulationContract):
    prepared_id: str
    request: SimulationRequest
    session_model: str = "ADAPTER_INTERNAL_EPHEMERAL"

    _prepared = field_validator("prepared_id", mode="before")(
        lambda value: _identifier(value, field="prepared_id")
    )

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> SimulationRequest:
        if type(value) is SimulationRequest:
            material = value.model_dump(mode="python")
        elif type(value) is dict:
            material = copy.deepcopy(value)
        else:
            raise TypeError("prepared request is invalid")
        return SimulationRequest.model_validate(material)

    @field_validator("session_model", mode="before")
    @classmethod
    def _session_model(cls, value: object) -> str:
        if value != "ADAPTER_INTERNAL_EPHEMERAL":
            raise ValueError("persistent simulation sessions are not supported")
        return str(value)


class SimulationResult(FingerprintedSimulationContract):
    simulation_id: str
    operation_id: str | None
    tenant_id: str
    project_id: str
    backend: SimulationBackend
    status: SimulationStatus
    failure_class: SimulationFailureClass
    reason_code: str
    started_at: datetime | None
    finished_at: datetime | None
    assertion_results: tuple[SimulationAssertionResult, ...]
    artifact_references: tuple[str, ...]
    telemetry_references: tuple[str, ...]
    diagnostic_summary: str
    request_fingerprint: str

    _ids = field_validator(
        "simulation_id", "tenant_id", "project_id", "reason_code", mode="before"
    )(lambda value, info: _identifier(value, field=info.field_name))

    @field_validator("operation_id", mode="before")
    @classmethod
    def _operation_id(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, field="operation_id")

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def _optional_time(cls, value: object, info) -> datetime | None:
        return None if value is None else _utc(value, field=info.field_name)

    @field_validator("assertion_results", mode="before")
    @classmethod
    def _assertion_results(cls, value: object) -> tuple[SimulationAssertionResult, ...]:
        if not isinstance(value, (tuple, list)) or len(value) > 64:
            raise TypeError("assertion_results must be a bounded tuple")
        checked = tuple(
            SimulationAssertionResult.model_validate(
                copy.deepcopy(item.model_dump(mode="python"))
            )
            if type(item) is SimulationAssertionResult
            else SimulationAssertionResult.model_validate(copy.deepcopy(item))
            for item in value
        )
        if len({item.assertion_id for item in checked}) != len(checked):
            raise ValueError("assertion result identities must be unique")
        return checked

    @field_validator("artifact_references", "telemetry_references", mode="before")
    @classmethod
    def _references(cls, value: object, info) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or len(value) > 64:
            raise TypeError(f"{info.field_name} must be a bounded tuple")
        checked = tuple(_identifier(item, field=info.field_name) for item in value)
        if len(checked) != len(set(checked)):
            raise ValueError(f"{info.field_name} must be unique")
        return checked

    _diagnostic = field_validator("diagnostic_summary", mode="before")(
        lambda value: _text(value, field="diagnostic_summary", maximum=1024)
    )
    _request_fp = field_validator("request_fingerprint", mode="before")(
        lambda value: _fingerprint(value, field="request_fingerprint")
    )

    @model_validator(mode="after")
    def _outcome(self):
        terminal = self.status not in {
            SimulationStatus.PENDING,
            SimulationStatus.RUNNING,
        }
        if self.status is SimulationStatus.PENDING:
            if (
                self.failure_class is not SimulationFailureClass.NONE
                or self.finished_at is not None
            ):
                raise ValueError("pending simulation result is invalid")
        elif self.status is SimulationStatus.RUNNING:
            if (
                self.failure_class is not SimulationFailureClass.NONE
                or self.operation_id is None
                or self.started_at is None
                or self.finished_at is not None
            ):
                raise ValueError("running simulation result is invalid")
        elif self.status is SimulationStatus.PASS:
            if (
                self.failure_class is not SimulationFailureClass.NONE
                or not self.assertion_results
                or any(not item.passed for item in self.assertion_results)
            ):
                raise ValueError("passing simulation result is invalid")
        elif self.status is SimulationStatus.FAIL:
            if self.failure_class is not SimulationFailureClass.ENGINEERING:
                raise ValueError("engineering simulation failure is invalid")
        elif self.status is SimulationStatus.INFRASTRUCTURE_ERROR:
            if self.failure_class is not SimulationFailureClass.INFRASTRUCTURE:
                raise ValueError("infrastructure simulation failure is invalid")
        elif self.status is SimulationStatus.CANCELLED:
            if (
                self.failure_class is not SimulationFailureClass.CANCELLED
                or self.reason_code != "SIMULATION_CANCELLED"
            ):
                raise ValueError("cancelled simulation result is invalid")
        else:
            expected = (
                SimulationFailureClass.INFRASTRUCTURE
                if self.reason_code in INFRASTRUCTURE_TIMEOUT_REASONS
                else SimulationFailureClass.ENGINEERING
                if self.reason_code in ENGINEERING_TIMEOUT_REASONS
                else None
            )
            if expected is None or self.failure_class is not expected:
                raise ValueError("timeout failure classification mismatch")
        if terminal:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("terminal simulation result requires timestamps")
            if self.finished_at < self.started_at:
                raise ValueError("simulation timestamps are invalid")
        return self


class SimulationTransition(SimulationContract):
    previous: SimulationStatus
    current: SimulationStatus

    @model_validator(mode="after")
    def _transition(self):
        allowed = {
            SimulationStatus.PENDING: {
                SimulationStatus.RUNNING,
                SimulationStatus.INFRASTRUCTURE_ERROR,
                SimulationStatus.CANCELLED,
            },
            SimulationStatus.RUNNING: {
                SimulationStatus.PASS,
                SimulationStatus.FAIL,
                SimulationStatus.TIMEOUT,
                SimulationStatus.INFRASTRUCTURE_ERROR,
                SimulationStatus.CANCELLED,
            },
        }
        if self.current not in allowed.get(self.previous, set()):
            raise ValueError("simulation status transition is invalid")
        return self


def validate_request(value: object) -> SimulationRequest:
    if type(value) is not SimulationRequest:
        raise TypeError("simulation request is invalid")
    return SimulationRequest.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def validate_result(value: object) -> SimulationResult:
    if type(value) is not SimulationResult:
        raise TypeError("simulation result is invalid")
    return SimulationResult.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


__all__ = (
    "ENGINEERING_TIMEOUT_REASONS",
    "INFRASTRUCTURE_TIMEOUT_REASONS",
    "PreparedSimulation",
    "SimulationAssertion",
    "SimulationAssertionKind",
    "SimulationAssertionResult",
    "SimulationBackend",
    "SimulationBackendCapability",
    "SimulationCapabilityStatus",
    "SimulationContract",
    "SimulationEvidence",
    "SimulationExecutionBinding",
    "SimulationExecutionMode",
    "SimulationFailureClass",
    "SimulationRequest",
    "SimulationResult",
    "SimulationStatus",
    "SimulationTransition",
    "validate_request",
    "validate_result",
)
