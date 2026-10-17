from __future__ import annotations

import copy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    finalize,
    fp,
    ident,
    text,
    tuple_values,
)
from embedded_copilot.simulation_runtime.contracts import (
    SimulationBackend,
    SimulationBackendCapability,
    SimulationCapabilityStatus,
)


def _values(value: object, field: str) -> tuple[object, ...]:
    if isinstance(value, list):
        value = tuple(value)
    return tuple_values(value, field)


def _strings(value: object, field: str, maximum: int = 256) -> tuple[str, ...]:
    return tuple(text(item, field, maximum) for item in _values(value, field))


def _fingerprints(value: object, field: str) -> tuple[str, ...]:
    return tuple(fp(item, field.removesuffix("s")) for item in _values(value, field))


def _timestamp(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class BackendTrustStatus(StrEnum):
    TRUSTED = "TRUSTED"
    UNAVAILABLE = "UNAVAILABLE"
    UNTRUSTED = "UNTRUSTED"


class VirtualNeedScope(StrEnum):
    BOOT = "BOOT"
    CPU_RUNTIME = "CPU_RUNTIME"
    GPIO = "GPIO"
    UART = "UART"
    I2C = "I2C"
    SPI = "SPI"
    CAN = "CAN"
    ADC = "ADC"
    PWM = "PWM"
    TIMER = "TIMER"
    INTERRUPT = "INTERRUPT"
    WATCHDOG = "WATCHDOG"
    SYSTEM_BEHAVIOR = "SYSTEM_BEHAVIOR"
    FAULT_RECOVERY = "FAULT_RECOVERY"
    ANALOG_POWER = "ANALOG_POWER"
    PROTOCOL = "PROTOCOL"


class VirtualNeedDisposition(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"


class BackendAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    KNOWN_NOT_CONNECTED = "KNOWN_NOT_CONNECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"


class BackendProbeStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    NOT_PROBED = "NOT_PROBED"


class VirtualVerificationNeed(Fingerprinted):
    need_id: str
    scope: VirtualNeedScope
    target: str
    required: bool
    required_capabilities: tuple[str, ...]
    source_dependencies: tuple[str, ...]
    expected_evidence: tuple[str, ...]
    blocking_if_unverified: bool
    disposition: VirtualNeedDisposition

    _ids = field_validator("need_id", "target", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _tuples = field_validator(
        "required_capabilities", "source_dependencies", "expected_evidence", mode="before"
    )(
        lambda value, info: (
            _fingerprints(value, info.field_name)
            if info.field_name == "source_dependencies"
            else _strings(value, info.field_name)
        )
    )

    @classmethod
    def create(cls, **values: object) -> VirtualVerificationNeed:
        return finalize(cls, values)


class VirtualBackendDescriptor(Fingerprinted):
    backend_id: str
    backend_type: str
    tool_name: str
    tool_path: str | None
    tool_version: str | None
    availability: BackendAvailability
    trust_status: BackendTrustStatus
    supported_targets: tuple[str, ...]
    supported_architectures: tuple[str, ...]
    supported_scopes: tuple[VirtualNeedScope, ...]
    supported_capabilities: tuple[str, ...]
    limitations: tuple[str, ...]
    discovery_source: str
    probe_status: BackendProbeStatus
    last_probe_at: datetime | None
    failure_reason: str | None = None

    _ids = field_validator("backend_id", "backend_type", "tool_name", "discovery_source", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _optional_text = field_validator("tool_path", "tool_version", "failure_reason", mode="before")(
        lambda value, info: None if value is None else text(value, info.field_name, 256)
    )
    _targets = field_validator(
        "supported_targets", "supported_architectures", "supported_capabilities", "limitations", mode="before"
    )(lambda value, info: _strings(value, info.field_name))
    _scopes = field_validator("supported_scopes", mode="before")(
        lambda value: tuple(
            item if isinstance(item, VirtualNeedScope) else VirtualNeedScope(item)
            for item in _values(value, "supported_scopes")
        )
    )
    _probe_time = field_validator("last_probe_at", mode="before")(
        lambda value: _timestamp(value, "last_probe_at")
    )

    @model_validator(mode="after")
    def _availability_binding(self) -> VirtualBackendDescriptor:
        if self.availability is BackendAvailability.AVAILABLE:
            if self.trust_status is not BackendTrustStatus.TRUSTED:
                raise ValueError("available backend must be trusted")
            if self.probe_status is not BackendProbeStatus.VERIFIED:
                raise ValueError("available backend must have a verified probe")
            if not self.tool_version:
                raise ValueError("available backend requires a tool version")
        return self

    def supports(self, need: VirtualVerificationNeed) -> bool:
        if self.availability is not BackendAvailability.AVAILABLE:
            return False
        if self.trust_status is not BackendTrustStatus.TRUSTED:
            return False
        if self.probe_status is not BackendProbeStatus.VERIFIED:
            return False
        if self.supported_targets and need.target not in self.supported_targets:
            return False
        if need.scope not in self.supported_scopes:
            return False
        return set(need.required_capabilities).issubset(self.supported_capabilities) and set(
            need.expected_evidence
        ).issubset(self.supported_capabilities)

    @classmethod
    def create(cls, **values: object) -> VirtualBackendDescriptor:
        return finalize(cls, values)


class VirtualBackendAdapterPort(Protocol):
    """Thin adapter seam; execution remains owned by the existing worker."""

    def discover(self) -> VirtualBackendDescriptor: ...

    def probe(self) -> VirtualBackendDescriptor: ...

    def supports(self, need: VirtualVerificationNeed) -> bool: ...

    def prepare(self, request: object) -> object: ...

    def execute(self, prepared: object) -> object: ...

    def collect_evidence(self, execution: object) -> object: ...


class VirtualBackendFailure(Fingerprinted):
    backend_id: str
    availability: BackendAvailability
    reason: str
    exit_status: int | None
    log_refs: tuple[str, ...]
    engineering_failure: bool = False

    _id = field_validator("backend_id", mode="before")(lambda value: ident(value, "backend_id"))
    _reason = field_validator("reason", mode="before")(lambda value: ident(value, "reason"))
    _refs = field_validator("log_refs", mode="before")(
        lambda value: tuple(ident(item, "log_ref") for item in _values(value, "log_refs"))
    )

    @model_validator(mode="after")
    def _backend_boundary(self) -> VirtualBackendFailure:
        if self.reason == "BACKEND_PROCESS_CRASH" and self.engineering_failure:
            raise ValueError("backend process crash cannot be an engineering failure")
        return self

    @classmethod
    def create(cls, **values: object) -> VirtualBackendFailure:
        return finalize(cls, values)


class VirtualBackendSelection(Fingerprinted):
    need_id: str
    disposition: VirtualNeedDisposition
    candidate_backend_ids: tuple[str, ...]
    selected_backend_id: str | None
    evidence_requirement_satisfied: bool
    reason: str

    _id = field_validator("need_id", mode="before")(lambda value: ident(value, "need_id"))
    _candidates = field_validator("candidate_backend_ids", mode="before")(
        lambda value: tuple(ident(item, "candidate_backend_id") for item in _values(value, "candidate_backend_ids"))
    )
    _selected = field_validator("selected_backend_id", mode="before")(
        lambda value: None if value is None else ident(value, "selected_backend_id")
    )
    _reason = field_validator("reason", mode="before")(lambda value: text(value, "reason", 512))

    @classmethod
    def create(cls, **values: object) -> VirtualBackendSelection:
        return finalize(cls, values)


class VirtualBackendPoolSnapshot(Fingerprinted):
    registered: tuple[VirtualBackendDescriptor, ...]
    selections: tuple[VirtualBackendSelection, ...]
    failures: tuple[VirtualBackendFailure, ...]
    blocking_unknowns: tuple[str, ...]
    limitations: tuple[str, ...]
    updated_at: datetime

    _registered = field_validator("registered", mode="before")(
        lambda value: tuple(
            item if isinstance(item, VirtualBackendDescriptor) else VirtualBackendDescriptor.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "registered")
        )
    )
    _selections = field_validator("selections", mode="before")(
        lambda value: tuple(
            item if isinstance(item, VirtualBackendSelection) else VirtualBackendSelection.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "selections")
        )
    )
    _failures = field_validator("failures", mode="before")(
        lambda value: tuple(
            item if isinstance(item, VirtualBackendFailure) else VirtualBackendFailure.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "failures")
        )
    )
    _unknowns = field_validator("blocking_unknowns", mode="before")(
        lambda value: tuple(ident(item, "blocking_unknown") for item in _values(value, "blocking_unknowns"))
    )
    _limitations = field_validator("limitations", mode="before")(
        lambda value: _strings(value, "limitations", 1024)
    )
    _updated = field_validator("updated_at", mode="before")(
        lambda value: _timestamp(value, "updated_at")
    )

    @classmethod
    def create(cls, **values: object) -> VirtualBackendPoolSnapshot:
        return finalize(cls, values)


class VirtualVerificationApplicabilityResolver:
    """Derive needs from typed engineering authority; no model decides applicability."""

    def resolve(self, authority) -> tuple[VirtualVerificationNeed, ...]:
        interfaces = {item.upper() for item in authority.interface_set}
        dependencies = (
            authority.firmware_spec_fingerprint,
            authority.firmware_implementation_fingerprint,
            authority.build_receipt_fingerprint,
            authority.current_pcb_fingerprint,
            authority.current_pcb_state_fingerprint,
            authority.hw_fw_consistency_fingerprint,
        )
        needs: list[VirtualVerificationNeed] = []

        def add(
            scope: VirtualNeedScope,
            target: str,
            *,
            required: bool,
            capabilities: tuple[str, ...],
            evidence: tuple[str, ...],
            applicable: bool,
            blocking: bool,
        ) -> None:
            disposition = (
                VirtualNeedDisposition.NOT_APPLICABLE
                if not applicable
                else VirtualNeedDisposition.REQUIRED
                if required
                else VirtualNeedDisposition.OPTIONAL
            )
            needs.append(
                VirtualVerificationNeed.create(
                    need_id=f"need-{scope.value.casefold()}-{target.casefold().replace('_', '-')}",
                    scope=scope,
                    target=target,
                    required=required,
                    required_capabilities=capabilities,
                    source_dependencies=dependencies,
                    expected_evidence=evidence,
                    blocking_if_unverified=blocking,
                    disposition=disposition,
                )
            )

        add(
            VirtualNeedScope.BOOT,
            authority.mcu_identity,
            required=True,
            capabilities=("elf_loaded", "reset_handler_reached", "main_reached"),
            evidence=("elf_loaded", "reset_handler_reached", "main_reached"),
            applicable=True,
            blocking=True,
        )
        add(
            VirtualNeedScope.CPU_RUNTIME,
            authority.mcu_identity,
            required=True,
            capabilities=("cpu_started",),
            evidence=("cpu_started",),
            applicable=True,
            blocking=True,
        )
        gpio_present = any(item in interfaces for item in ("GPIO", "PA8", "PB0", "LED", "BUTTON"))
        add(
            VirtualNeedScope.GPIO,
            authority.mcu_identity,
            required=True,
            capabilities=("gpio_configuration", "gpio_transition"),
            evidence=("gpio_configuration", "gpio_transition"),
            applicable=gpio_present,
            blocking=True,
        )
        for name, scope in (
            ("UART", VirtualNeedScope.UART),
            ("I2C", VirtualNeedScope.I2C),
            ("SPI", VirtualNeedScope.SPI),
            ("CAN", VirtualNeedScope.CAN),
            ("ADC", VirtualNeedScope.ADC),
            ("PWM", VirtualNeedScope.PWM),
            ("TIMER", VirtualNeedScope.TIMER),
            ("INTERRUPT", VirtualNeedScope.INTERRUPT),
            ("WATCHDOG", VirtualNeedScope.WATCHDOG),
        ):
            present = any(name in item for item in interfaces)
            add(
                scope,
                authority.mcu_identity,
                required=present,
                capabilities=(name.casefold(),),
                evidence=(name.casefold(),),
                applicable=present,
                blocking=present,
            )
        add(
            VirtualNeedScope.SYSTEM_BEHAVIOR,
            authority.mcu_identity,
            required=bool(authority.expected_system_behaviors),
            capabilities=("state_transition",),
            evidence=("state_transition",),
            applicable=bool(authority.expected_system_behaviors),
            blocking=bool(authority.expected_system_behaviors),
        )
        add(
            VirtualNeedScope.FAULT_RECOVERY,
            authority.project_id,
            required=False,
            capabilities=("fault_recovery",),
            evidence=("fault_recovery",),
            applicable=any("FAULT" in item.upper() for item in authority.expected_system_behaviors),
            blocking=False,
        )
        add(
            VirtualNeedScope.ANALOG_POWER,
            authority.board_identity,
            required=False,
            capabilities=("trusted_spice_model",),
            evidence=("trusted_spice_model",),
            applicable=False,
            blocking=False,
        )
        return tuple(needs)


class VirtualBackendSelector:
    def select(
        self,
        need: VirtualVerificationNeed,
        descriptors: tuple[VirtualBackendDescriptor, ...],
    ) -> VirtualBackendSelection:
        candidates = tuple(item for item in descriptors if item.supports(need))
        candidate_ids = tuple(item.backend_id for item in candidates)
        if need.disposition is VirtualNeedDisposition.NOT_APPLICABLE:
            return VirtualBackendSelection.create(
                need_id=need.need_id,
                disposition=need.disposition,
                candidate_backend_ids=(),
                selected_backend_id=None,
                evidence_requirement_satisfied=True,
                reason="NEED_NOT_APPLICABLE",
            )
        if candidates:
            selected = candidates[0]
            return VirtualBackendSelection.create(
                need_id=need.need_id,
                disposition=need.disposition,
                candidate_backend_ids=candidate_ids,
                selected_backend_id=selected.backend_id,
                evidence_requirement_satisfied=True,
                reason=f"RIGHT_BACKEND_FOR_{need.scope.value}",
            )
        disposition = (
            VirtualNeedDisposition.UNSUPPORTED
            if need.required
            else VirtualNeedDisposition.OPTIONAL
        )
        return VirtualBackendSelection.create(
            need_id=need.need_id,
            disposition=disposition,
            candidate_backend_ids=(),
            selected_backend_id=None,
            evidence_requirement_satisfied=False,
            reason="REQUIRED_BACKEND_UNAVAILABLE" if need.required else "OPTIONAL_BACKEND_UNAVAILABLE",
        )


class VirtualBackendPool:
    """A registry/selector projection over existing execution adapters."""

    def __init__(self, descriptors: tuple[VirtualBackendDescriptor, ...] = ()) -> None:
        self._descriptors = tuple(descriptors)
        if len({item.backend_id for item in self._descriptors}) != len(self._descriptors):
            raise ValueError("backend ids must be unique")
        self._selector = VirtualBackendSelector()

    @property
    def descriptors(self) -> tuple[VirtualBackendDescriptor, ...]:
        return self._descriptors

    def select(self, need: VirtualVerificationNeed) -> VirtualBackendSelection:
        return self._selector.select(need, self._descriptors)

    def snapshot(
        self,
        needs: tuple[VirtualVerificationNeed, ...],
        *,
        failures: tuple[VirtualBackendFailure, ...] = (),
        updated_at: datetime | None = None,
    ) -> VirtualBackendPoolSnapshot:
        selections = tuple(self.select(need) for need in needs)
        blocking_unknowns = tuple(
            selection.need_id
            for need, selection in zip(needs, selections, strict=True)
            if need.blocking_if_unverified and not selection.evidence_requirement_satisfied
        )
        limitations = tuple(
            f"{selection.need_id}:{selection.reason}"
            for need, selection in zip(needs, selections, strict=True)
            if not need.blocking_if_unverified and not selection.evidence_requirement_satisfied
        )
        return VirtualBackendPoolSnapshot.create(
            registered=self._descriptors,
            selections=selections,
            failures=failures,
            blocking_unknowns=blocking_unknowns,
            limitations=limitations,
            updated_at=updated_at or datetime.now(UTC),
        )


def backend_descriptor_from_capability(
    capability: SimulationBackendCapability,
    *,
    backend_id: str,
    tool_path: str,
    supported_scopes: tuple[VirtualNeedScope, ...],
    supported_capabilities: tuple[str, ...],
    limitations: tuple[str, ...] = (),
    discovery_source: str = "trusted_tool_discovery",
    last_probe_at: datetime | None = None,
) -> VirtualBackendDescriptor:
    checked = SimulationBackendCapability.model_validate(
        copy.deepcopy(capability.model_dump(mode="python")), strict=False
    )
    verified = checked.status is SimulationCapabilityStatus.VERIFIED
    return VirtualBackendDescriptor.create(
        backend_id=backend_id,
        backend_type="MCU_SYSTEM_EMULATOR" if checked.backend is SimulationBackend.RENODE else "SIMULATION_BACKEND",
        tool_name=checked.backend.value,
        tool_path=tool_path,
        tool_version=checked.version,
        availability=BackendAvailability.AVAILABLE if verified else BackendAvailability.UNAVAILABLE,
        trust_status=BackendTrustStatus.TRUSTED if verified else BackendTrustStatus.UNAVAILABLE,
        supported_targets=checked.supported_targets,
        supported_architectures=("ARM Cortex-M3",) if verified else (),
        supported_scopes=supported_scopes if verified else (),
        supported_capabilities=supported_capabilities if verified else (),
        limitations=limitations,
        discovery_source=discovery_source,
        probe_status=BackendProbeStatus.VERIFIED if verified else BackendProbeStatus.UNAVAILABLE,
        last_probe_at=last_probe_at,
        failure_reason=None if verified else checked.reason_code,
    )


def classify_backend_process_failure(
    descriptor: VirtualBackendDescriptor,
    *,
    exit_status: int,
    log_refs: tuple[str, ...] = (),
) -> VirtualBackendFailure:
    if exit_status == 0:
        raise ValueError("a successful process cannot be classified as a backend crash")
    return VirtualBackendFailure.create(
        backend_id=descriptor.backend_id,
        availability=BackendAvailability.ERROR,
        reason="BACKEND_PROCESS_CRASH",
        exit_status=exit_status,
        log_refs=log_refs,
        engineering_failure=False,
    )


__all__ = (
    "BackendAvailability",
    "BackendProbeStatus",
    "BackendTrustStatus",
    "VirtualBackendAdapterPort",
    "VirtualBackendDescriptor",
    "VirtualBackendFailure",
    "VirtualBackendPool",
    "VirtualBackendPoolSnapshot",
    "VirtualBackendSelection",
    "VirtualNeedDisposition",
    "VirtualNeedScope",
    "VirtualVerificationApplicabilityResolver",
    "VirtualVerificationNeed",
    "backend_descriptor_from_capability",
    "classify_backend_process_failure",
)
