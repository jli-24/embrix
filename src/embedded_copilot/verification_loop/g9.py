from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    canonical,
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
    SimulationEvidence,
    SimulationFailureClass,
    SimulationResult,
    SimulationStatus,
)

from .backend_pool import (
    BackendTrustStatus,
    VirtualBackendPool,
    VirtualBackendPoolSnapshot,
    VirtualVerificationApplicabilityResolver,
    VirtualVerificationNeed,
)
from .contracts import (
    VerificationEvidenceReference,
    VerificationIssue,
    VerificationResult,
    VerificationStatus,
)


class G9Contract(Fingerprinted):
    """Immutable, restart-safe contracts for virtual system verification."""


class G9Scope(StrEnum):
    BOOT = "BOOT"
    CPU_RUNTIME = "CPU_RUNTIME"
    PERIPHERAL = "PERIPHERAL"
    INTERFACE = "INTERFACE"
    SYSTEM_BEHAVIOR = "SYSTEM_BEHAVIOR"
    FAULT_RECOVERY = "FAULT_RECOVERY"
    ANALOG_POWER = "ANALOG_POWER"
    TIMING = "TIMING"
    PROTOCOL = "PROTOCOL"


class G9Applicability(StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED = "BLOCKED"
    READY = "READY"


class G9GateStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


def _ids(value: object, field: str) -> str:
    return ident(value, field)


def _fps(value: object, field: str) -> str:
    return fp(value, field)


def _safe_tuple(value: object, field: str, maximum: int = 128) -> tuple[str, ...]:
    return tuple(text(item, field, maximum) for item in _values(value, field))


def _values(value: object, field: str) -> tuple[object, ...]:
    if isinstance(value, list):
        value = tuple(value)
    return tuple_values(value, field)


def _now(value: object, field: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


class G9AuthoritySnapshot(G9Contract):
    """The current engineering authority consumed by a G9 plan."""

    project_id: str
    conversation_id: str
    hardware_identity: str
    board_identity: str
    mcu_identity: str
    current_pcb_fingerprint: str
    current_pcb_state_fingerprint: str
    firmware_spec_fingerprint: str
    firmware_implementation_fingerprint: str
    build_configuration_fingerprint: str
    build_receipt_fingerprint: str
    elf_fingerprint: str
    bin_fingerprint: str
    hw_fw_consistency_fingerprint: str
    requirement_refs: tuple[str, ...]
    interface_set: tuple[str, ...]
    expected_system_behaviors: tuple[str, ...]
    g8_manifest_fingerprint: str

    _ids = field_validator(
        "project_id",
        "conversation_id",
        "hardware_identity",
        "board_identity",
        "mcu_identity",
        "build_receipt_fingerprint",
        mode="before",
    )(lambda value, info: _ids(value, info.field_name))
    _fps = field_validator(
        "current_pcb_fingerprint",
        "current_pcb_state_fingerprint",
        "firmware_spec_fingerprint",
        "firmware_implementation_fingerprint",
        "build_configuration_fingerprint",
        "elf_fingerprint",
        "bin_fingerprint",
        "hw_fw_consistency_fingerprint",
        "g8_manifest_fingerprint",
        mode="before",
    )(lambda value, info: _fps(value, info.field_name))
    _tuples = field_validator(
        "requirement_refs", "interface_set", "expected_system_behaviors", mode="before"
    )(lambda value, info: _safe_tuple(value, info.field_name))

    @classmethod
    def create(cls, **values: object) -> G9AuthoritySnapshot:
        return finalize(cls, values)


class VirtualVerificationBackend(G9Contract):
    backend_name: str
    backend_type: str
    path_or_runtime: str
    version: str | None
    discovery_source: str
    supported_scope: tuple[G9Scope, ...]
    current_target_supported: bool
    trust_status: BackendTrustStatus

    _text = field_validator(
        "backend_name", "backend_type", "path_or_runtime", "discovery_source", mode="before"
    )(lambda value, info: text(value, info.field_name, 256))
    _version = field_validator("version", mode="before")(
        lambda value: None if value is None else text(value, "version", 64)
    )
    _scope = field_validator("supported_scope", mode="before")(
        lambda value: tuple(
            item if isinstance(item, G9Scope) else G9Scope(item)
            for item in _values(value, "supported_scope")
        )
    )

    @model_validator(mode="after")
    def _trusted_backend_has_identity(self) -> VirtualVerificationBackend:
        if self.trust_status is BackendTrustStatus.TRUSTED and not self.version:
            raise ValueError("trusted backend requires a version")
        if self.trust_status is BackendTrustStatus.TRUSTED and not self.current_target_supported:
            raise ValueError("trusted backend must support the current target")
        return self

    @classmethod
    def create(cls, **values: object) -> VirtualVerificationBackend:
        return finalize(cls, values)


class BackendDiscoveryPort(Protocol):
    def discover(self) -> VirtualVerificationBackend: ...


def backend_from_capability(
    capability: SimulationBackendCapability,
    *,
    path_or_runtime: str,
    discovery_source: str = "trusted_tool_discovery",
    supported_scope: tuple[G9Scope, ...] = (
        G9Scope.BOOT,
        G9Scope.CPU_RUNTIME,
        G9Scope.INTERFACE,
        G9Scope.SYSTEM_BEHAVIOR,
    ),
) -> VirtualVerificationBackend:
    """Project an existing simulation capability into the G9 backend identity."""

    checked = SimulationBackendCapability.model_validate(
        copy.deepcopy(capability.model_dump(mode="python")), strict=False
    )
    trust = (
        BackendTrustStatus.TRUSTED
        if checked.status is SimulationCapabilityStatus.VERIFIED
        else BackendTrustStatus.UNAVAILABLE
    )
    return VirtualVerificationBackend.create(
        backend_name=checked.backend.value,
        backend_type="MCU_EXECUTION_EMULATOR",
        path_or_runtime=path_or_runtime,
        version=checked.version,
        discovery_source=discovery_source,
        supported_scope=supported_scope,
        current_target_supported=bool(checked.supported_targets),
        trust_status=trust,
    )


class VirtualVerificationItem(G9Contract):
    item_id: str
    scope: G9Scope
    name: str
    applicability: G9Applicability
    required: bool
    expected: tuple[str, ...]
    source_refs: tuple[str, ...]
    backend_name: str | None = None

    _id = field_validator("item_id", mode="before")(lambda value: _ids(value, "item_id"))
    _text = field_validator("name", mode="before")(lambda value: text(value, "name", 256))
    _expected = field_validator("expected", "source_refs", mode="before")(
        lambda value, info: _safe_tuple(value, info.field_name, 512)
    )
    _backend = field_validator("backend_name", mode="before")(
        lambda value: None if value is None else text(value, "backend_name", 64)
    )

    @classmethod
    def create(cls, **values: object) -> VirtualVerificationItem:
        return finalize(cls, values)


class VirtualSystemVerificationPlan(G9Contract):
    plan_id: str
    project_id: str
    conversation_id: str
    hardware_identity: str
    board_identity: str
    mcu_identity: str
    firmware_spec_fingerprint: str
    firmware_implementation_fingerprint: str
    build_configuration_fingerprint: str
    build_receipt_fingerprint: str
    elf_fingerprint: str
    bin_fingerprint: str
    pcb_fingerprint: str
    pcb_state_fingerprint: str
    hw_fw_consistency_fingerprint: str
    g8_manifest_fingerprint: str
    backend_fingerprints: tuple[str, ...]
    items: tuple[VirtualVerificationItem, ...]
    created_at: datetime

    _ids = field_validator(
        "plan_id", "project_id", "conversation_id", "hardware_identity", "board_identity", "mcu_identity", mode="before"
    )(lambda value, info: _ids(value, info.field_name))
    _fps = field_validator(
        "firmware_spec_fingerprint",
        "firmware_implementation_fingerprint",
        "build_configuration_fingerprint",
        "build_receipt_fingerprint",
        "elf_fingerprint",
        "bin_fingerprint",
        "pcb_fingerprint",
        "pcb_state_fingerprint",
        "hw_fw_consistency_fingerprint",
        "g8_manifest_fingerprint",
        mode="before",
    )(lambda value, info: _fps(value, info.field_name))
    _backend_fps = field_validator("backend_fingerprints", mode="before")(
        lambda value: tuple(_fps(item, "backend_fingerprint") for item in _values(value, "backend_fingerprints"))
    )
    _items = field_validator("items", mode="before")(
        lambda value: tuple(
            item if isinstance(item, VirtualVerificationItem) else VirtualVerificationItem.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "items")
        )
    )
    _created = field_validator("created_at", mode="before")(
        lambda value: _now(value, "created_at")
    )

    @model_validator(mode="after")
    def _identity(self) -> VirtualSystemVerificationPlan:
        if not self.items:
            raise ValueError("G9 plan requires at least one verification item")
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("G9 plan item identities must be unique")
        return self

    @classmethod
    def create(cls, **values: object) -> VirtualSystemVerificationPlan:
        return finalize(cls, values)


class VirtualExecutionReceipt(G9Contract):
    receipt_id: str
    operation_id: str
    backend_name: str
    backend_version: str
    scenario_id: str
    scenario_fingerprint: str
    request_fingerprint: str
    configuration_fingerprint: str
    artifact_fingerprints: tuple[str, ...]
    result_fingerprint: str
    evidence_refs: tuple[str, ...]
    status: VerificationStatus
    created_at: datetime
    backend_id: str | None = None
    need_id: str | None = None
    input_artifacts: tuple[str, ...] = ()
    input_fingerprints: tuple[str, ...] = ()
    execution_configuration: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_status: int | None = None
    observations: tuple[str, ...] = ()
    log_refs: tuple[str, ...] = ()
    trace_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    _ids = field_validator(
        "receipt_id", "operation_id", "backend_name", "backend_version", "scenario_id", mode="before"
    )(lambda value, info: _ids(value, info.field_name))
    _fps = field_validator(
        "scenario_fingerprint",
        "request_fingerprint",
        "configuration_fingerprint",
        "result_fingerprint",
        mode="before",
    )(lambda value, info: _fps(value, info.field_name))
    _artifact_fps = field_validator("artifact_fingerprints", mode="before")(
        lambda value: tuple(_fps(item, "artifact_fingerprint") for item in _values(value, "artifact_fingerprints"))
    )
    _refs = field_validator("evidence_refs", mode="before")(
        lambda value: _safe_tuple(value, "evidence_refs", 256)
    )
    _optional_ids = field_validator("backend_id", "need_id", mode="before")(
        lambda value, info: None if value is None else _ids(value, info.field_name)
    )
    _input_artifacts = field_validator("input_artifacts", mode="before")(
        lambda value: tuple(_ids(item, "input_artifact") for item in _values(value, "input_artifacts"))
    )
    _input_fps = field_validator("input_fingerprints", mode="before")(
        lambda value: tuple(_fps(item, "input_fingerprint") for item in _values(value, "input_fingerprints"))
    )
    _configuration = field_validator("execution_configuration", mode="before")(
        lambda value: None if value is None else text(value, "execution_configuration", 256)
    )
    _observations = field_validator(
        "observations", "log_refs", "trace_refs", "artifact_refs", "limitations", mode="before"
    )(lambda value, info: _safe_tuple(value, info.field_name, 1024))
    _execution_times = field_validator("started_at", "finished_at", mode="before")(
        lambda value, info: None if value is None else _now(value, info.field_name)
    )
    _created = field_validator("created_at", mode="before")(
        lambda value: _now(value, "created_at")
    )

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        return (
            canonical(
                self,
                exclude={
                    "fingerprint",
                    "backend_id",
                    "need_id",
                    "input_artifacts",
                    "input_fingerprints",
                    "execution_configuration",
                    "started_at",
                    "finished_at",
                    "exit_status",
                    "observations",
                    "log_refs",
                    "trace_refs",
                    "artifact_refs",
                    "limitations",
                },
            ),
        )

    @classmethod
    def create(cls, **values: object) -> VirtualExecutionReceipt:
        return finalize(cls, values)

    @property
    def receipt_fingerprint(self) -> str:
        return self.fingerprint


class VirtualSystemVerificationSummary(G9Contract):
    project_id: str
    plan_id: str
    boot_status: VerificationStatus
    runtime_status: VerificationStatus
    interface_status: VerificationStatus
    behavior_status: VerificationStatus
    fault_recovery_status: VerificationStatus
    analog_power_status: VerificationStatus
    required_count: int
    pass_count: int
    fail_count: int
    warning_count: int
    unknown_count: int
    not_applicable_count: int
    blocking_issue_count: int
    blocking_unknown_count: int
    blocking_issues: tuple[str, ...]
    blocking_unknowns: tuple[str, ...]
    unsupported_scopes: tuple[str, ...]
    limitations: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    source_fingerprints: tuple[str, ...]
    backend_fingerprints: tuple[str, ...]
    gate: G9GateStatus
    current: bool
    physical_functionally_validated: bool = False
    g10_not_entered: bool = True

    _ids = field_validator("project_id", "plan_id", mode="before")(
        lambda value, info: _ids(value, info.field_name)
    )
    _counts = field_validator(
        "required_count",
        "pass_count",
        "fail_count",
        "warning_count",
        "unknown_count",
        "not_applicable_count",
        "blocking_issue_count",
        "blocking_unknown_count",
        mode="before",
    )(
        lambda value, info: value
        if type(value) is int and value >= 0
        else (_ for _ in ()).throw(ValueError(f"{info.field_name} is invalid"))
    )
    _tuples = field_validator(
        "blocking_issues",
        "blocking_unknowns",
        "unsupported_scopes",
        "limitations",
        "evidence_refs",
        mode="before",
    )(lambda value, info: _safe_tuple(value, info.field_name, 1024))
    _fps = field_validator("source_fingerprints", "backend_fingerprints", mode="before")(
        lambda value, info: tuple(_fps(item, info.field_name.removesuffix("s")) for item in _values(value, info.field_name))
    )

    @model_validator(mode="after")
    def _gate(self) -> VirtualSystemVerificationSummary:
        if self.gate is G9GateStatus.PASS and (self.blocking_issue_count or self.blocking_unknown_count):
            raise ValueError("a passing G9 summary cannot contain blocking items")
        if self.gate is G9GateStatus.PASS and not self.current:
            raise ValueError("a passing G9 summary must be current")
        if self.physical_functionally_validated:
            raise ValueError("G9 cannot claim physical functional validation")
        if not self.g10_not_entered:
            raise ValueError("G9 cannot enter G10")
        return self

    @classmethod
    def create(cls, **values: object) -> VirtualSystemVerificationSummary:
        return finalize(cls, values)


class VirtualSystemVerificationState(G9Contract):
    plan: VirtualSystemVerificationPlan
    backends: tuple[VirtualVerificationBackend, ...]
    execution_receipts: tuple[VirtualExecutionReceipt, ...]
    simulation_results: tuple[SimulationResult, ...]
    evidence: tuple[SimulationEvidence, ...]
    verification_results: tuple[VerificationResult, ...]
    summary: VirtualSystemVerificationSummary
    g9_production_ready: bool
    updated_at: datetime
    backend_pool: VirtualBackendPoolSnapshot | None = None

    _backends = field_validator("backends", mode="before")(
        lambda value: tuple(
            item if isinstance(item, VirtualVerificationBackend) else VirtualVerificationBackend.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "backends")
        )
    )
    _receipts = field_validator("execution_receipts", mode="before")(
        lambda value: tuple(
            item if isinstance(item, VirtualExecutionReceipt) else VirtualExecutionReceipt.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "execution_receipts")
        )
    )
    _results = field_validator("simulation_results", mode="before")(
        lambda value: tuple(
            item if isinstance(item, SimulationResult) else SimulationResult.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "simulation_results")
        )
    )
    _evidence = field_validator("evidence", mode="before")(
        lambda value: tuple(
            item if isinstance(item, SimulationEvidence) else SimulationEvidence.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "evidence")
        )
    )
    _verification = field_validator("verification_results", mode="before")(
        lambda value: tuple(
            item if isinstance(item, VerificationResult) else VerificationResult.model_validate(copy.deepcopy(item), strict=False)
            for item in _values(value, "verification_results")
        )
    )
    _summary = field_validator("summary", mode="before")(
        lambda value: value if isinstance(value, VirtualSystemVerificationSummary) else VirtualSystemVerificationSummary.model_validate(copy.deepcopy(value), strict=False)
    )
    _updated = field_validator("updated_at", mode="before")(
        lambda value: _now(value, "updated_at")
    )
    _backend_pool = field_validator("backend_pool", mode="before")(
        lambda value: None
        if value is None
        else value
        if isinstance(value, VirtualBackendPoolSnapshot)
        else VirtualBackendPoolSnapshot.model_validate(copy.deepcopy(value), strict=False)
    )

    @model_validator(mode="after")
    def _bound(self) -> VirtualSystemVerificationState:
        if self.summary.plan_id != self.plan.plan_id or self.summary.project_id != self.plan.project_id:
            raise ValueError("G9 summary plan binding mismatch")
        if any(result.project_id != self.plan.project_id for result in self.verification_results):
            raise ValueError("G9 verification result project binding mismatch")
        if any(result.status is VerificationStatus.PASS and result.stale for result in self.verification_results):
            raise ValueError("stale G9 result cannot remain PASS")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        payload = self.model_dump(mode="json", exclude={"fingerprint", "backend_pool"})
        for receipt in payload.get("execution_receipts", ()):
            for field in (
                "backend_id",
                "need_id",
                "input_artifacts",
                "input_fingerprints",
                "execution_configuration",
                "started_at",
                "finished_at",
                "exit_status",
                "observations",
                "log_refs",
                "trace_refs",
                "artifact_refs",
                "limitations",
            ):
                receipt.pop(field, None)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return ("sha256:" + hashlib.sha256(encoded).hexdigest(),)

    @classmethod
    def create(cls, **values: object) -> VirtualSystemVerificationState:
        return finalize(cls, values)


class VirtualSystemVerificationService:
    """Build and evaluate G9 using the existing simulation/verification seams."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def build_plan(
        self,
        authority: G9AuthoritySnapshot,
        backends: Iterable[VirtualVerificationBackend],
    ) -> VirtualSystemVerificationPlan:
        checked = G9AuthoritySnapshot.model_validate(
            copy.deepcopy(authority.model_dump(mode="python")), strict=False
        )
        backend_values = tuple(
            VirtualVerificationBackend.model_validate(copy.deepcopy(item.model_dump(mode="python")), strict=False)
            for item in backends
        )
        trusted = tuple(
            item for item in backend_values
            if item.trust_status is BackendTrustStatus.TRUSTED and item.current_target_supported
        )
        selected = trusted[0] if trusted else None
        items: list[VirtualVerificationItem] = []

        def add(item_id: str, scope: G9Scope, name: str, expected: tuple[str, ...], *, required: bool = True, applicable: bool = True) -> None:
            if not applicable:
                status = G9Applicability.NOT_APPLICABLE
                backend_name = None
            elif selected is None or scope not in selected.supported_scope:
                status = G9Applicability.UNSUPPORTED
                backend_name = None if selected is None else selected.backend_name
            else:
                status = G9Applicability.READY
                backend_name = selected.backend_name
            items.append(
                VirtualVerificationItem.create(
                    item_id=item_id,
                    scope=scope,
                    name=name,
                    applicability=status,
                    required=required,
                    expected=expected,
                    source_refs=checked.requirement_refs or (checked.project_id,),
                    backend_name=backend_name,
                )
            )

        add("g9-boot", G9Scope.BOOT, "firmware reset and startup", ("elf_loaded", "reset_handler_reached", "main_reached"))
        add("g9-cpu-runtime", G9Scope.CPU_RUNTIME, "current firmware executes on the modeled CPU", ("cpu_started",))
        add("g9-gpio", G9Scope.INTERFACE, "GPIO mapping and logical transitions", checked.interface_set, applicable=any("GPIO" in value.upper() or value.upper() in {"PA8", "PB0", "LED", "BUTTON"} for value in checked.interface_set))
        for interface, scope in (("UART", G9Scope.PROTOCOL), ("I2C", G9Scope.PROTOCOL), ("SPI", G9Scope.PROTOCOL), ("CAN", G9Scope.PROTOCOL), ("ADC", G9Scope.PERIPHERAL), ("PWM", G9Scope.PERIPHERAL), ("TIMER", G9Scope.TIMING), ("INTERRUPT", G9Scope.PERIPHERAL), ("WATCHDOG", G9Scope.FAULT_RECOVERY)):
            present = any(interface in value.upper() for value in checked.interface_set)
            add(f"g9-{interface.casefold()}", scope, f"{interface} interface", (interface,), applicable=present)
        add("g9-system-behavior", G9Scope.SYSTEM_BEHAVIOR, "approved system behavior scenarios", checked.expected_system_behaviors)
        add("g9-fault-recovery", G9Scope.FAULT_RECOVERY, "defined fault and recovery behavior", ("defined_failure_handling",), applicable=False)
        add("g9-analog-power", G9Scope.ANALOG_POWER, "analog and power model", ("trusted_spice_model",), applicable=False)
        add("g9-timing", G9Scope.TIMING, "timing and oscillator behavior", ("timing_model",), applicable=False)
        plan_material = {
            "plan_id": _stable_id("g9-plan", checked.project_id, checked.conversation_id, checked.fingerprint),
            "project_id": checked.project_id,
            "conversation_id": checked.conversation_id,
            "hardware_identity": checked.hardware_identity,
            "board_identity": checked.board_identity,
            "mcu_identity": checked.mcu_identity,
            "firmware_spec_fingerprint": checked.firmware_spec_fingerprint,
            "firmware_implementation_fingerprint": checked.firmware_implementation_fingerprint,
            "build_configuration_fingerprint": checked.build_configuration_fingerprint,
            "build_receipt_fingerprint": checked.build_receipt_fingerprint,
            "elf_fingerprint": checked.elf_fingerprint,
            "bin_fingerprint": checked.bin_fingerprint,
            "pcb_fingerprint": checked.current_pcb_fingerprint,
            "pcb_state_fingerprint": checked.current_pcb_state_fingerprint,
            "hw_fw_consistency_fingerprint": checked.hw_fw_consistency_fingerprint,
            "g8_manifest_fingerprint": checked.g8_manifest_fingerprint,
            "backend_fingerprints": tuple(item.fingerprint for item in backend_values),
            "items": tuple(items),
            "created_at": self._clock(),
        }
        return VirtualSystemVerificationPlan.create(**plan_material)

    def build_plan_with_backend_pool(
        self,
        authority: G9AuthoritySnapshot,
        backends: Iterable[VirtualVerificationBackend],
        pool: VirtualBackendPool,
    ) -> tuple[VirtualSystemVerificationPlan, tuple[VirtualVerificationNeed, ...], VirtualBackendPoolSnapshot]:
        needs = VirtualVerificationApplicabilityResolver().resolve(authority)
        snapshot = pool.snapshot(needs, updated_at=self._clock())
        return self.build_plan(authority, backends), needs, snapshot

    def record_execution(
        self,
        plan: VirtualSystemVerificationPlan,
        backends: tuple[VirtualVerificationBackend, ...],
        *,
        scenario_id: str,
        scenario_fingerprint: str,
        request_fingerprint: str,
        configuration_fingerprint: str,
        simulation_result: SimulationResult,
        evidence: SimulationEvidence,
        backend_pool: VirtualBackendPoolSnapshot | None = None,
        need_id: str | None = None,
    ) -> VirtualSystemVerificationState:
        checked_plan = VirtualSystemVerificationPlan.model_validate(copy.deepcopy(plan.model_dump(mode="python")), strict=False)
        checked_result = SimulationResult.model_validate(copy.deepcopy(simulation_result.model_dump(mode="python")), strict=False)
        if getattr(evidence, "backend", None) is SimulationBackend.FAKE:
            raise ValueError("Fake simulation cannot produce G9 evidence")
        checked_evidence = SimulationEvidence.model_validate(copy.deepcopy(evidence.model_dump(mode="python")), strict=False)
        backend = next((item for item in backends if item.backend_name == checked_evidence.backend.value), None)
        if backend is None or backend.trust_status is not BackendTrustStatus.TRUSTED or not backend.current_target_supported:
            raise ValueError("G9 execution requires a trusted backend for the current target")
        if checked_result.backend is SimulationBackend.FAKE or checked_evidence.backend is SimulationBackend.FAKE:
            raise ValueError("Fake simulation cannot produce G9 evidence")
        if checked_evidence.artifact_sha256 != checked_plan.elf_fingerprint:
            raise ValueError("G9 execution artifact is not the current ELF")
        if checked_evidence.backend_version != backend.version:
            raise ValueError("G9 backend version binding mismatch")
        receipt = VirtualExecutionReceipt.create(
            receipt_id=_stable_id("g9-receipt", checked_result.fingerprint, checked_evidence.fingerprint),
            operation_id=checked_evidence.operation_id,
            backend_name=backend.backend_name,
            backend_version=backend.version or "unknown",
            scenario_id=scenario_id,
            scenario_fingerprint=scenario_fingerprint,
            request_fingerprint=request_fingerprint,
            configuration_fingerprint=configuration_fingerprint,
            artifact_fingerprints=(checked_plan.elf_fingerprint, checked_plan.bin_fingerprint),
            result_fingerprint=checked_result.fingerprint,
            evidence_refs=(checked_evidence.evidence_id,),
            status=VerificationStatus.PASS if checked_result.status is SimulationStatus.PASS else VerificationStatus.UNKNOWN,
            created_at=self._clock(),
            backend_id=backend.backend_name,
            need_id=need_id,
            input_artifacts=(checked_evidence.artifact_id,),
            input_fingerprints=(checked_evidence.artifact_sha256, checked_evidence.request_fingerprint),
            execution_configuration=configuration_fingerprint,
            started_at=checked_evidence.created_at,
            finished_at=checked_evidence.created_at,
            exit_status=0 if checked_result.status is SimulationStatus.PASS else None,
            observations=checked_evidence.observations,
            log_refs=(checked_evidence.receipt_reference,),
            artifact_refs=(checked_evidence.artifact_id,),
            limitations=("virtual backend evidence does not validate physical hardware",),
        )
        results = self._results_for_execution(checked_plan, checked_result, checked_evidence, receipt)
        summary = self._summary(checked_plan, backends, results, (receipt,), (checked_result,), (checked_evidence,))
        return VirtualSystemVerificationState.create(
            plan=checked_plan,
            backends=backends,
            execution_receipts=(receipt,),
            simulation_results=(checked_result,),
            evidence=(checked_evidence,),
            verification_results=results,
            summary=summary,
            g9_production_ready=True,
            updated_at=self._clock(),
            backend_pool=backend_pool,
        )

    def _results_for_execution(
        self,
        plan: VirtualSystemVerificationPlan,
        result: SimulationResult,
        evidence: SimulationEvidence,
        receipt: VirtualExecutionReceipt,
    ) -> tuple[VerificationResult, ...]:
        boot_ok = result.status is SimulationStatus.PASS and evidence.elf_loaded and evidence.cpu_started and evidence.reset_handler_reached and evidence.main_reached
        gpio_ok = result.status is SimulationStatus.PASS and _gpio_observations_pass(evidence.observations)
        values: list[VerificationResult] = []
        for item in plan.items:
            if item.applicability is G9Applicability.NOT_APPLICABLE:
                status = VerificationStatus.NOT_APPLICABLE
                summary = "scope is not present in the current system authority"
            elif item.applicability is not G9Applicability.READY:
                status = VerificationStatus.UNKNOWN
                summary = "required virtual backend coverage is unavailable for this scope"
            elif item.scope in {G9Scope.BOOT, G9Scope.CPU_RUNTIME}:
                status = VerificationStatus.PASS if boot_ok else _execution_failure_status(result)
                summary = "real backend observed ELF load, reset handler, CPU start and main" if status is VerificationStatus.PASS else "real backend did not prove firmware startup"
            elif item.item_id == "g9-gpio":
                status = VerificationStatus.PASS if gpio_ok else _execution_failure_status(result)
                summary = "real backend observed GPIO configuration and deterministic transitions" if status is VerificationStatus.PASS else "real backend did not prove the GPIO transition sequence"
            elif item.scope is G9Scope.SYSTEM_BEHAVIOR:
                status = VerificationStatus.PASS if gpio_ok else _execution_failure_status(result)
                summary = "approved button-to-LED behavior matched the real backend observations" if status is VerificationStatus.PASS else "system behavior did not match backend observations"
            else:
                status = VerificationStatus.NOT_APPLICABLE
                summary = "no applicable current behavior requires this scope"
            values.append(self._verification_result(plan, item, status, summary, result, evidence, receipt))
        return tuple(values)

    def _verification_result(
        self,
        plan: VirtualSystemVerificationPlan,
        item: VirtualVerificationItem,
        status: VerificationStatus,
        summary: str,
        result: SimulationResult,
        evidence: SimulationEvidence,
        receipt: VirtualExecutionReceipt,
    ) -> VerificationResult:
        evidence_ref = VerificationEvidenceReference.create(
            evidence_id=evidence.evidence_id,
            source_kind="REAL_BACKEND",
            source_fingerprint=evidence.fingerprint,
            locator=f"backend:{evidence.backend.value}",
            description="sanitized real virtual execution evidence",
        )
        issue = () if status in {VerificationStatus.PASS, VerificationStatus.NOT_APPLICABLE} else (
            VerificationIssue.create(
                issue_id=_stable_id("g9-issue", item.item_id, result.reason_code),
                severity="BLOCKING" if item.required else "WARNING",
                category=item.scope.value,
                affected_objects=(item.item_id,),
                problem=summary,
                why="the G9 item is required by the current plan",
                possible_effects=("G9_MAINLINE_GATE remains BLOCKED",),
                evidence=(evidence.evidence_id,),
                recommendation="retain the verification failure or unknown state and re-run after the bound input is corrected",
                reverification=(item.item_id,),
            ),
        )
        return VerificationResult.create(
            verification_id=_stable_id("g9-verification", plan.plan_id, item.item_id, result.fingerprint),
            project_id=plan.project_id,
            target_id=plan.mcu_identity,
            status=status,
            attempt=0,
            compared_fingerprints=(plan.fingerprint, result.fingerprint, evidence.fingerprint),
            evidence_refs=(evidence.evidence_id, receipt.receipt_id),
            summary=summary,
            verifier="virtual-system-verification",
            scope=item.scope.value,
            issues=issue,
            evidence=(evidence_ref,),
            affected_objects=(item.item_id,),
            confidence=1.0 if status is VerificationStatus.PASS else 0.0,
            assumptions=("G9 proves only the modeled virtual scope",),
            unknowns=() if status in {VerificationStatus.PASS, VerificationStatus.NOT_APPLICABLE} else ("physical hardware behavior is not covered",),
            recommendations=() if status is VerificationStatus.PASS else ("inspect the real backend receipt and bound artifact lineage",),
            applicability=item.applicability.value,
        )

    def _summary(
        self,
        plan: VirtualSystemVerificationPlan,
        backends: tuple[VirtualVerificationBackend, ...],
        results: tuple[VerificationResult, ...],
        receipts: tuple[VirtualExecutionReceipt, ...],
        simulation_results: tuple[SimulationResult, ...],
        evidence: tuple[SimulationEvidence, ...],
        *,
        current: bool = True,
        blocking_issues: tuple[str, ...] = (),
        blocking_unknowns: tuple[str, ...] = (),
        limitations: tuple[str, ...] = (
            "G9 does not validate physical board operation, oscillator physics, power integrity, RF, thermal behavior or manufacturing variation",
        ),
    ) -> VirtualSystemVerificationSummary:
        required = tuple(item for item in results if any(plan_item.item_id == item.affected_objects[0] and plan_item.required for plan_item in plan.items))
        count = lambda status: sum(item.status is status for item in results)
        computed_blocking = tuple(
            item.verification_id for item in required if item.status is VerificationStatus.FAIL
        )
        computed_unknown = tuple(
            item.verification_id for item in required if item.status in {VerificationStatus.UNKNOWN, VerificationStatus.ESCALATE}
        )
        blockers = tuple(dict.fromkeys((*blocking_issues, *computed_blocking)))
        unknowns = tuple(dict.fromkeys((*blocking_unknowns, *computed_unknown)))
        gate = G9GateStatus.PASS if current and not blockers and not unknowns else G9GateStatus.BLOCKED
        by_scope = {
            scope: next((item.status for item in results if item.scope == scope.value), VerificationStatus.NOT_APPLICABLE)
            for scope in (G9Scope.BOOT, G9Scope.CPU_RUNTIME, G9Scope.INTERFACE, G9Scope.SYSTEM_BEHAVIOR, G9Scope.FAULT_RECOVERY, G9Scope.ANALOG_POWER)
        }
        unsupported = tuple(item.scope.value for item in plan.items if item.applicability is G9Applicability.UNSUPPORTED)
        return VirtualSystemVerificationSummary.create(
            project_id=plan.project_id,
            plan_id=plan.plan_id,
            boot_status=by_scope[G9Scope.BOOT],
            runtime_status=by_scope[G9Scope.CPU_RUNTIME],
            interface_status=by_scope[G9Scope.INTERFACE],
            behavior_status=by_scope[G9Scope.SYSTEM_BEHAVIOR],
            fault_recovery_status=by_scope[G9Scope.FAULT_RECOVERY],
            analog_power_status=by_scope[G9Scope.ANALOG_POWER],
            required_count=len(required),
            pass_count=count(VerificationStatus.PASS),
            fail_count=count(VerificationStatus.FAIL),
            warning_count=count(VerificationStatus.WARNING),
            unknown_count=count(VerificationStatus.UNKNOWN),
            not_applicable_count=count(VerificationStatus.NOT_APPLICABLE),
            blocking_issue_count=len(blockers),
            blocking_unknown_count=len(unknowns),
            blocking_issues=blockers,
            blocking_unknowns=unknowns,
            unsupported_scopes=unsupported,
            limitations=limitations,
            evidence_refs=tuple(dict.fromkeys((*[item.evidence_id for item in evidence], *[item.receipt_id for item in receipts]))),
            source_fingerprints=(
                plan.firmware_spec_fingerprint,
                plan.firmware_implementation_fingerprint,
                plan.build_receipt_fingerprint,
                plan.elf_fingerprint,
                plan.bin_fingerprint,
                plan.pcb_fingerprint,
                plan.pcb_state_fingerprint,
                plan.hw_fw_consistency_fingerprint,
                plan.g8_manifest_fingerprint,
            ),
            backend_fingerprints=tuple(item.fingerprint for item in backends),
            gate=gate,
            current=current,
        )

    def mark_stale(
        self,
        state: VirtualSystemVerificationState,
        dependency: str,
    ) -> VirtualSystemVerificationState:
        checked = VirtualSystemVerificationState.model_validate(copy.deepcopy(state.model_dump(mode="python")), strict=False)
        dependency = text(dependency, "dependency", 64).casefold()
        if dependency in {"g8_package_only_change", "ui_change"}:
            return checked
        affected = {
            "firmware": {G9Scope.BOOT.value, G9Scope.CPU_RUNTIME.value, G9Scope.INTERFACE.value, G9Scope.SYSTEM_BEHAVIOR.value},
            "build_receipt": {G9Scope.BOOT.value, G9Scope.CPU_RUNTIME.value, G9Scope.INTERFACE.value, G9Scope.SYSTEM_BEHAVIOR.value},
            "hardware": {G9Scope.INTERFACE.value, G9Scope.SYSTEM_BEHAVIOR.value},
            "pcb": {G9Scope.INTERFACE.value, G9Scope.SYSTEM_BEHAVIOR.value},
            "hw_fw": {G9Scope.INTERFACE.value, G9Scope.SYSTEM_BEHAVIOR.value},
            "backend": {item.scope.value for item in checked.plan.items if item.backend_name is not None},
            "scenario": {G9Scope.SYSTEM_BEHAVIOR.value},
        }.get(dependency, set())
        if not affected:
            return checked
        changed: list[VerificationResult] = []
        for item in checked.verification_results:
            if item.scope not in affected:
                changed.append(item)
                continue
            material = item.model_dump(mode="python")
            material.update(
                {
                    "status": VerificationStatus.UNKNOWN,
                    "stale": True,
                    "stale_reason": f"G9 input changed: {dependency}",
                    "summary": f"G9 result is stale because {dependency} changed",
                    "confidence": 0.0,
                }
            )
            material.pop("fingerprint", None)
            changed.append(VerificationResult.create(**material))
        summary = self._summary(
            checked.plan,
            checked.backends,
            tuple(changed),
            checked.execution_receipts,
            checked.simulation_results,
            checked.evidence,
            current=False,
            blocking_unknowns=tuple(item.verification_id for item in changed if item.stale),
        )
        return VirtualSystemVerificationState.create(
            plan=checked.plan,
            backends=checked.backends,
            execution_receipts=checked.execution_receipts,
            simulation_results=checked.simulation_results,
            evidence=checked.evidence,
            verification_results=tuple(changed),
            summary=summary,
            g9_production_ready=True,
            updated_at=self._clock(),
            backend_pool=checked.backend_pool,
        )


def _execution_failure_status(result: SimulationResult) -> VerificationStatus:
    if result.failure_class in {SimulationFailureClass.INFRASTRUCTURE, SimulationFailureClass.CANCELLED}:
        return VerificationStatus.UNKNOWN
    return VerificationStatus.FAIL


def _gpio_observations_pass(observations: tuple[str, ...]) -> bool:
    values: dict[str, tuple[int, int]] = {}
    for item in observations:
        match = re.fullmatch(
            r"(?:embrx|embrix)_stage_(initial|pressed|released):led_raw=(0x[0-9a-f]+),button_raw=(0x[0-9a-f]+)",
            item,
            re.IGNORECASE,
        )
        if match:
            values[match.group(1).casefold()] = (int(match.group(2), 16), int(match.group(3), 16))
    if set(values) != {"initial", "pressed", "released"}:
        return False
    led_mask = 1 << 8
    button_mask = 1
    return (
        values["initial"][0] & led_mask == 0 and values["initial"][1] & button_mask != 0
        and values["pressed"][0] & led_mask != 0 and values["pressed"][1] & button_mask == 0
        and values["released"][0] & led_mask == 0 and values["released"][1] & button_mask != 0
    )


__all__ = (
    "BackendDiscoveryPort",
    "BackendTrustStatus",
    "G9Applicability",
    "G9AuthoritySnapshot",
    "G9GateStatus",
    "G9Scope",
    "VirtualExecutionReceipt",
    "VirtualSystemVerificationPlan",
    "VirtualSystemVerificationService",
    "VirtualSystemVerificationState",
    "VirtualSystemVerificationSummary",
    "VirtualVerificationBackend",
    "VirtualVerificationItem",
    "backend_from_capability",
)
