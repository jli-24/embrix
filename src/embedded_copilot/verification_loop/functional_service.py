from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime

from embedded_copilot.engineering_copilot.common import text
from embedded_copilot.simulation_runtime.contracts import (
    SimulationBackend,
    SimulationStatus,
)

from .contracts import (
    ElectricalSimulationStatus,
    ExpectedBehavior,
    FunctionalCheckStatus,
    FunctionalVerificationEvidence,
    FunctionalVerificationRecord,
    FunctionalVerificationRequest,
    FunctionalVerificationResult,
    FunctionalVerificationStatus,
    L4GateStatus,
    ModelScopeAssessment,
    ModelScopeStatus,
    VerificationCheck,
    VerificationComparisonRule,
    _functional_truth_fingerprint,
    functional_source_fingerprint,
)
from .exceptions import VerificationLoopError, VerificationRejected
from .ports import FunctionalVerificationStorePort

_STAGES = ("initial", "pressed", "released")
_UNSUPPORTED_SCOPE = re.compile(
    r"\b(rcc|clock|timing|frequency|delay|latency)\b|时钟|时序",
    re.IGNORECASE,
)
_ELECTRICAL_SCOPE = re.compile(
    r"\b(analog|voltage|power|adc|ngspice)\b|电源|电压|模拟",
    re.IGNORECASE,
)


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _copy_request(request: FunctionalVerificationRequest) -> FunctionalVerificationRequest:
    if type(request) is not FunctionalVerificationRequest:
        raise TypeError("functional verification request is invalid")
    return FunctionalVerificationRequest.model_validate(
        copy.deepcopy(request.model_dump(mode="python"))
    )


def _pin_label(pin) -> str:
    return str(getattr(pin, "pin_name", None) or getattr(pin, "signal", ""))


def _pin_number(pin) -> str:
    return str(getattr(pin, "pin_number", None) or getattr(pin, "mcu_pin", ""))


def _pin(truth, keyword: str, fallback: str):
    matches = tuple(
        pin
        for pin in truth.pins
        if keyword in f"{_pin_label(pin)} {pin.function} {getattr(pin, 'signal_id', '') or ''}".upper()
    )
    if matches:
        return matches[0]
    return next((pin for pin in truth.pins if _pin_label(pin).upper() == fallback), None)


def _port_pin(pin) -> tuple[str, int] | None:
    if pin is None:
        return None
    gpio_port = getattr(pin, "gpio_port", None)
    gpio_number = getattr(pin, "gpio_number", None)
    if isinstance(gpio_port, str) and gpio_port.upper().startswith("GPIO") and type(gpio_number) is int:
        return gpio_port.upper().removeprefix("GPIO"), gpio_number
    match = re.fullmatch(r"P([A-Z])(\d{1,2})", _pin_label(pin).upper())
    return None if match is None else (match.group(1), int(match.group(2)))


def _refs(request: FunctionalVerificationRequest) -> tuple[str, ...]:
    values = [
        request.verification_specification.verification_id,
        request.verification_specification.fingerprint,
        request.design_truth.truth_id,
        _functional_truth_fingerprint(request.design_truth),
        request.firmware_specification.firmware_spec_id,
        request.firmware_specification.fingerprint,
        request.virtual_assembly.assembly_id,
        request.virtual_assembly.assembly_fingerprint,
        request.simulation_scenario.scenario_id,
        request.simulation_scenario.fingerprint,
    ]
    if request.requirement_specification is not None:
        values.extend(("requirements", request.requirement_specification.fingerprint))
    return tuple(dict.fromkeys(values))


def _behavior_overrides(request: FunctionalVerificationRequest) -> dict[str, ExpectedBehavior]:
    values: dict[str, ExpectedBehavior] = {}
    for item in request.expected_behavior:
        for key in (item.behavior_id.casefold(), item.category.casefold()):
            values[key] = item
    return values


def _requirements(request: FunctionalVerificationRequest) -> tuple[str, ...]:
    if request.requirement_specification is None:
        return request.simulation_scenario.acceptance_refs
    return tuple(
        dict.fromkeys(
            (
                *(item.requirement_id for item in request.requirement_specification.requirements),
                *(item.criterion_id for item in request.requirement_specification.acceptance_criteria),
                *request.simulation_scenario.acceptance_refs,
            )
        )
    )


def _expected_values(request: FunctionalVerificationRequest) -> dict[str, tuple[object, bool, tuple[str, ...]]]:
    led = _pin(request.design_truth, "LED", "PA8")
    button = _pin(request.design_truth, "BUTTON", "PB0")
    led_port_pin = _port_pin(led)
    button_port_pin = _port_pin(button)
    source_refs = _refs(request)
    defaults: dict[str, tuple[object, bool, tuple[str, ...]]] = {
        "boot_startup": (
            {"elf_loaded": True, "cpu_started": True, "reset_handler_reached": True, "main_reached": True},
            True,
            source_refs,
        ),
        "firmware_execution": (True, True, source_refs),
        "gpio_configuration": (
            {
                "led_pin": _pin_label(led) if led is not None else "UNAVAILABLE",
                "button_pin": _pin_label(button) if button is not None else "UNAVAILABLE",
                "led_direction": "OUTPUT",
                "button_direction": "INPUT",
            },
            True,
            source_refs,
        ),
        "input_stimulus": (("RELEASED", "PRESSED", "RELEASED"), True, source_refs),
        "output_response": (("OFF", "ON", "OFF"), True, source_refs),
        "functional_scenario": (
            (("RELEASED", "OFF"), ("PRESSED", "ON"), ("RELEASED", "OFF")),
            True,
            source_refs,
        ),
        "artifact_lineage": (
            {
                "elf_artifact_id": request.virtual_assembly.elf_artifact_id or "UNAVAILABLE",
                "elf_sha256": request.virtual_assembly.elf_sha256 or "sha256:" + "0" * 64,
                "simulation_result_id": request.simulation_result.simulation_id,
                "simulation_evidence_id": request.simulation_evidence.evidence_id,
                "scenario_id": request.simulation_scenario.scenario_id,
                "scenario_fingerprint": request.simulation_scenario.fingerprint,
            },
            True,
            source_refs,
        ),
        "model_scope": (True, True, source_refs),
        "electrical_simulation": (ElectricalSimulationStatus.NOT_APPLICABLE.value, False, source_refs),
    }
    if led_port_pin is None or button_port_pin is None:
        defaults["gpio_configuration"] = (
            {"led_pin": "UNAVAILABLE", "button_pin": "UNAVAILABLE", "led_direction": "UNKNOWN", "button_direction": "UNKNOWN"},
            True,
            source_refs,
        )
    overrides = _behavior_overrides(request)
    for key, item in tuple(defaults.items()):
        override = overrides.get(key)
        if override is None:
            continue
        value = item[0]
        if isinstance(value, dict) and isinstance(override.expected, dict):
            merged = dict(value)
            merged.update(override.expected)
            value = merged
        else:
            value = override.expected
        defaults[key] = (value, override.required, override.source_refs or source_refs)
    # A custom explicit electrical claim is never silently treated as N/A.
    electrical_claim = overrides.get("electrical_simulation") or overrides.get("electrical")
    if electrical_claim is not None and electrical_claim.expected != ElectricalSimulationStatus.NOT_APPLICABLE.value:
        defaults["electrical_simulation"] = (electrical_claim.expected, electrical_claim.required, electrical_claim.source_refs or source_refs)
    known = set(defaults)
    for item in request.expected_behavior:
        key = item.behavior_id.casefold()
        if key not in known:
            defaults[key] = (item.expected, item.required, item.source_refs or source_refs)
    return defaults


def _scope(request: FunctionalVerificationRequest, expected: dict[str, tuple[object, bool, tuple[str, ...]]]) -> ModelScopeAssessment:
    source_fingerprints = tuple(
        item
        for item in (
            request.requirement_specification.fingerprint if request.requirement_specification is not None else None,
            _functional_truth_fingerprint(request.design_truth),
            request.firmware_specification.fingerprint,
            request.verification_specification.fingerprint,
            request.virtual_assembly.assembly_fingerprint,
            request.simulation_scenario.fingerprint,
            request.simulation_result.fingerprint,
            request.simulation_evidence.fingerprint,
        )
        if item is not None
    )
    claims = tuple(
        text(key, "required_claim", 256)
        for key, (_, required, _) in expected.items()
        if required
    )
    scope_text = " ".join(
        (
            request.simulation_scenario.title,
            *request.simulation_scenario.expected_outputs,
            *(case.method for case in request.verification_specification.cases),
            *(case.expected_behavior[0] for case in request.verification_specification.cases if case.expected_behavior),
        )
    )
    unsupported = tuple(
        key for key in claims if _UNSUPPORTED_SCOPE.search(f"{key} {scope_text}") is not None
    )
    electrical_required = any(
        key.casefold() in {"electrical", "electrical_simulation"}
        and required
        for key, (_, required, _) in expected.items()
    ) or any(
        _ELECTRICAL_SCOPE.search(value) is not None
        for value in request.simulation_scenario.expected_outputs
    )
    if electrical_required:
        status = ModelScopeStatus.INSUFFICIENT
        sufficient = False
        electrical = ElectricalSimulationStatus.UNAVAILABLE
        rationale = "required electrical or analog claim has no approved electrical simulation backend"
    elif unsupported:
        status = ModelScopeStatus.INSUFFICIENT
        sufficient = False
        electrical = ElectricalSimulationStatus.NOT_APPLICABLE
        rationale = "model scope does not cover: " + ", ".join(unsupported)
    elif request.simulation_evidence.model_fidelity in {"FULL", "FULL_RCC", "PARTIAL_RCC"}:
        status = ModelScopeStatus.SUFFICIENT
        sufficient = True
        electrical = ElectricalSimulationStatus.NOT_APPLICABLE
        rationale = "Renode model scope is sufficient for the requested non-timing functional claims"
    else:
        status = ModelScopeStatus.UNKNOWN
        sufficient = False
        electrical = ElectricalSimulationStatus.NOT_APPLICABLE
        rationale = "simulation model fidelity was not recognized"
    return ModelScopeAssessment.create(
        assessment_id=_stable_id("model-scope", request.simulation_result.simulation_id, request.simulation_evidence.fingerprint),
        backend=request.simulation_evidence.backend,
        backend_version=request.simulation_evidence.backend_version,
        model_fidelity=request.simulation_evidence.model_fidelity,
        status=status,
        sufficient=sufficient,
        required_claims=claims,
        unsupported_claims=unsupported,
        electrical_simulation=electrical,
        rationale=rationale,
        source_fingerprints=source_fingerprints,
    )


def _stale_reasons(request: FunctionalVerificationRequest) -> tuple[str, ...]:
    reasons: list[str] = []
    if request.source_fingerprint is not None and request.source_fingerprint != functional_source_fingerprint(request):
        reasons.append("source fingerprint changed")
    assembly = request.virtual_assembly
    evidence = request.simulation_evidence
    result = request.simulation_result
    if assembly.firmware_specification_id not in {None, request.firmware_specification.firmware_spec_id}:
        reasons.append("assembly firmware identity is stale")
    if assembly.firmware_specification_fingerprint not in {None, request.firmware_specification.fingerprint}:
        reasons.append("assembly firmware fingerprint is stale")
    if assembly.elf_artifact_id is None or assembly.elf_sha256 is None:
        reasons.append("assembly ELF lineage is unavailable")
    elif assembly.elf_artifact_id != evidence.artifact_id or assembly.elf_sha256 != evidence.artifact_sha256:
        reasons.append("simulation ELF lineage is stale")
    if f"artifact:{evidence.artifact_id}" not in result.artifact_references:
        reasons.append("simulation result artifact lineage is incomplete")
    if evidence.evidence_id not in result.artifact_references:
        reasons.append("simulation evidence is not referenced by the result")
    return tuple(dict.fromkeys(reasons))


def _parse_config(request: FunctionalVerificationRequest) -> dict[str, object] | None:
    if len(request.simulation_evidence.configuration_observations) != 1:
        return None
    match = re.fullmatch(
        r"gpio_configuration:([a-z])_crh=(0x[0-9a-f]+),([a-z])_crl=(0x[0-9a-f]+)",
        request.simulation_evidence.configuration_observations[0],
        re.IGNORECASE,
    )
    if match is None:
        return None
    registers = {
        f"{match.group(1).upper()}_CRH": int(match.group(2), 16),
        f"{match.group(3).upper()}_CRL": int(match.group(4), 16),
    }
    led = _port_pin(_pin(request.design_truth, "LED", "PA8"))
    button = _port_pin(_pin(request.design_truth, "BUTTON", "PB0"))
    if led is None or button is None:
        return None

    def direction(port_pin: tuple[str, int]) -> str:
        port, pin = port_pin
        register = registers.get(f"{port}_CRH" if pin >= 8 else f"{port}_CRL")
        if register is None:
            return "UNKNOWN"
        nibble = (register >> ((pin % 8) * 4)) & 0xF
        return "OUTPUT" if nibble & 0x3 else "INPUT"

    return {
        "led_pin": f"P{led[0]}{led[1]}",
        "button_pin": f"P{button[0]}{button[1]}",
        "led_direction": direction(led),
        "button_direction": direction(button),
    }


def _parse_states(request: FunctionalVerificationRequest) -> tuple[tuple[str, str], ...] | None:
    led = _port_pin(_pin(request.design_truth, "LED", "PA8"))
    button = _port_pin(_pin(request.design_truth, "BUTTON", "PB0"))
    if led is None or button is None:
        return None
    led_mask = 1 << led[1]
    button_mask = 1 << button[1]
    values: dict[str, tuple[int, int]] = {}
    for observation in request.simulation_evidence.observations:
        match = re.fullmatch(
            r"(?:embrx|embrix)_stage_(initial|pressed|released):led_raw=(0x[0-9a-f]+),button_raw=(0x[0-9a-f]+)",
            observation,
            re.IGNORECASE,
        )
        if match is not None:
            values[match.group(1).casefold()] = (int(match.group(2), 16), int(match.group(3), 16))
    if any(stage not in values for stage in _STAGES):
        return None
    return tuple(
        (
            "PRESSED" if not values[stage][1] & button_mask else "RELEASED",
            "ON" if values[stage][0] & led_mask else "OFF",
        )
        for stage in _STAGES
    )


def _parse_stimulus(request: FunctionalVerificationRequest) -> tuple[str, ...] | None:
    values: list[str] = []
    for item in request.simulation_evidence.stimuli:
        lowered = item.casefold()
        if "released" in lowered:
            values.append("RELEASED")
        elif "pressed" in lowered:
            values.append("PRESSED")
        else:
            return None
    return tuple(values) if values else None


def _observed_values(
    request: FunctionalVerificationRequest,
    scope: ModelScopeAssessment,
) -> dict[str, object | None]:
    evidence = request.simulation_evidence
    result = request.simulation_result
    return {
        "boot_startup": {
            "elf_loaded": evidence.elf_loaded,
            "cpu_started": evidence.cpu_started,
            "reset_handler_reached": evidence.reset_handler_reached,
            "main_reached": evidence.main_reached,
        },
        "firmware_execution": evidence.main_reached,
        "gpio_configuration": _parse_config(request),
        "input_stimulus": _parse_stimulus(request),
        "output_response": (
            None
            if (states := _parse_states(request)) is None
            else tuple(item[1] for item in states)
        ),
        "functional_scenario": _parse_states(request),
        "artifact_lineage": {
            "elf_artifact_id": evidence.artifact_id,
            "elf_sha256": evidence.artifact_sha256,
            "simulation_result_id": result.simulation_id,
            "simulation_evidence_id": evidence.evidence_id,
            "scenario_id": request.simulation_scenario.scenario_id,
            "scenario_fingerprint": request.simulation_scenario.fingerprint,
        },
        "model_scope": scope.sufficient,
        "electrical_simulation": scope.electrical_simulation.value,
    }


def _compare(expected: object, observed: object | None, rule: VerificationComparisonRule) -> bool:
    if rule is VerificationComparisonRule.PRESENT:
        return observed is not None
    if rule is VerificationComparisonRule.CONTAINS:
        if isinstance(expected, dict) and isinstance(observed, dict):
            return all(key in observed and observed[key] == value for key, value in expected.items())
        if isinstance(observed, (tuple, list)):
            return expected in observed
        return False
    return expected == observed


def _check(
    request: FunctionalVerificationRequest,
    key: str,
    expected: object,
    required: bool,
    source_refs: tuple[str, ...],
    observed: object | None,
    scope: ModelScopeAssessment,
    stale_reasons: tuple[str, ...],
) -> VerificationCheck:
    check_id = _stable_id("functional-check", request.verification_specification.verification_id, key)
    if stale_reasons:
        status = FunctionalCheckStatus.STALE
        reason = "; ".join(stale_reasons)
    elif key == "electrical_simulation" and expected == ElectricalSimulationStatus.NOT_APPLICABLE.value:
        status = FunctionalCheckStatus.NOT_APPLICABLE
        reason = "no required analog or power behavior is in the functional verification scope"
    elif key == "model_scope" and not scope.sufficient:
        status = FunctionalCheckStatus.BLOCKED
        reason = scope.rationale
    elif request.simulation_result.status is not SimulationStatus.PASS:
        status = (
            FunctionalCheckStatus.BLOCKED
            if request.simulation_result.failure_class.value in {"INFRASTRUCTURE_FAILURE", "CANCELLED"}
            else FunctionalCheckStatus.FAIL
        )
        reason = "simulation result is not PASS: " + request.simulation_result.reason_code
    elif not scope.sufficient and key != "model_scope":
        status = FunctionalCheckStatus.BLOCKED
        reason = scope.rationale
    elif observed is None:
        status = FunctionalCheckStatus.UNKNOWN
        reason = "backend evidence does not contain the required observed state"
    elif _compare(expected, observed, VerificationComparisonRule.CONTAINS if key == "gpio_configuration" else VerificationComparisonRule.EQUAL):
        status = FunctionalCheckStatus.PASS
        reason = "expected behavior equals backend observation"
    else:
        status = FunctionalCheckStatus.FAIL
        reason = "backend observation does not satisfy expected behavior"
    return VerificationCheck.create(
        check_id=check_id,
        verification_specification_id=request.verification_specification.verification_id,
        simulation_scenario_id=request.simulation_scenario.scenario_id,
        simulation_scenario_fingerprint=request.simulation_scenario.fingerprint,
        category=key.upper(),
        title=key.replace("_", " ").title(),
        requirement_refs=_requirements(request),
        design_truth_refs=(request.design_truth.truth_id,),
        source_refs=source_refs,
        expected=expected,
        observed=observed,
        comparison_rule=VerificationComparisonRule.CONTAINS if key == "gpio_configuration" else VerificationComparisonRule.EQUAL,
        required=required,
        requires_model_scope=key != "model_scope",
        status=status,
        reason=reason,
        evidence_refs=(f"simulation-evidence:{request.simulation_evidence.evidence_id}",),
    )


def _counts(checks: tuple[VerificationCheck, ...]) -> dict[str, int]:
    return {
        "required_check_count": sum(item.required for item in checks),
        "pass_count": sum(item.status is FunctionalCheckStatus.PASS for item in checks),
        "fail_count": sum(item.status is FunctionalCheckStatus.FAIL for item in checks),
        "unknown_count": sum(item.status is FunctionalCheckStatus.UNKNOWN for item in checks),
        "not_applicable_count": sum(item.status is FunctionalCheckStatus.NOT_APPLICABLE for item in checks),
        "blocked_count": sum(item.status is FunctionalCheckStatus.BLOCKED for item in checks),
        "stale_count": sum(item.status is FunctionalCheckStatus.STALE for item in checks),
    }


def _overall(checks: tuple[VerificationCheck, ...]) -> FunctionalVerificationStatus:
    required = tuple(item for item in checks if item.required)
    statuses = {item.status for item in required}
    if FunctionalCheckStatus.STALE in statuses:
        return FunctionalVerificationStatus.STALE
    if FunctionalCheckStatus.FAIL in statuses:
        return FunctionalVerificationStatus.FAIL
    if FunctionalCheckStatus.BLOCKED in statuses:
        return FunctionalVerificationStatus.BLOCKED
    if FunctionalCheckStatus.UNKNOWN in statuses:
        return FunctionalVerificationStatus.INCOMPLETE
    return FunctionalVerificationStatus.PASS


class FunctionalVerificationService:
    """Deterministic Expected-vs-Observed validation over one real run."""

    def __init__(
        self,
        *,
        store: FunctionalVerificationStorePort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def verify(self, request: FunctionalVerificationRequest) -> FunctionalVerificationResult:
        try:
            checked = _copy_request(request)
        except (TypeError, ValueError) as error:
            raise VerificationRejected() from error
        if checked.simulation_request.backend is SimulationBackend.FAKE or checked.simulation_evidence.backend is SimulationBackend.FAKE:
            raise VerificationRejected()
        expected = _expected_values(checked)
        scope = _scope(checked, expected)
        stale_reasons = _stale_reasons(checked)
        observed = _observed_values(checked, scope)
        checks = tuple(
            _check(checked, key, value, required, refs, observed.get(key), scope, stale_reasons)
            for key, (value, required, refs) in expected.items()
        )
        counts = _counts(checks)
        status = _overall(checks)
        lineage_fresh = not stale_reasons
        evidence_complete = lineage_fresh and bool(checked.simulation_evidence.evidence_id) and bool(checked.simulation_result.simulation_id)
        l4 = (
            L4GateStatus.VIRTUAL_FUNCTIONAL_VERIFIED
            if status is FunctionalVerificationStatus.PASS
            and scope.sufficient
            and lineage_fresh
            and evidence_complete
            and all(item.status in {FunctionalCheckStatus.PASS, FunctionalCheckStatus.NOT_APPLICABLE} for item in checks)
            else L4GateStatus.NOT_ACHIEVED
        )
        return FunctionalVerificationResult.create(
            verification_result_id=_stable_id(
                "functional-verification",
                checked.verification_specification.verification_id,
                checked.simulation_result.fingerprint,
                checked.simulation_scenario.fingerprint,
            ),
            project_id=checked.project_id,
            verification_specification_id=checked.verification_specification.verification_id,
            verification_specification_fingerprint=checked.verification_specification.fingerprint,
            simulation_scenario_id=checked.simulation_scenario.scenario_id,
            simulation_scenario_fingerprint=checked.simulation_scenario.fingerprint,
            simulation_result_id=checked.simulation_result.simulation_id,
            simulation_result_fingerprint=checked.simulation_result.fingerprint,
            simulation_evidence_id=checked.simulation_evidence.evidence_id,
            simulation_evidence_fingerprint=checked.simulation_evidence.fingerprint,
            virtual_assembly_id=checked.virtual_assembly.assembly_id,
            virtual_assembly_fingerprint=checked.virtual_assembly.assembly_fingerprint,
            design_truth_id=checked.design_truth.truth_id,
            design_truth_fingerprint=_functional_truth_fingerprint(checked.design_truth),
            firmware_specification_id=checked.firmware_specification.firmware_spec_id,
            firmware_specification_fingerprint=checked.firmware_specification.fingerprint,
            requirement_context_fingerprint=(
                checked.requirement_specification.fingerprint
                if checked.requirement_specification is not None
                else None
            ),
            status=status,
            checks=checks,
            model_scope=scope,
            evidence_refs=(f"functional-verification-evidence:{checked.simulation_evidence.evidence_id}",),
            **counts,
            lineage_fresh=lineage_fresh,
            evidence_complete=evidence_complete,
            l4_gate=l4,
            summary=(
                "virtual functional verification passed with a fresh real-backend evidence chain"
                if l4 is L4GateStatus.VIRTUAL_FUNCTIONAL_VERIFIED
                else f"virtual functional verification status is {status.value}"
            ),
            created_at=self._clock(),
        )

    def verify_and_persist(self, request: FunctionalVerificationRequest) -> FunctionalVerificationRecord:
        if self._store is None:
            raise VerificationLoopError("functional verification store is unavailable")
        result = self.verify(request)
        evidence = FunctionalVerificationEvidence.create(
            evidence_id=f"functional-verification-evidence:{request.simulation_evidence.evidence_id}",
            verification_result_id=result.verification_result_id,
            verification_result_fingerprint=result.fingerprint,
            project_id=result.project_id,
            verification_specification_id=result.verification_specification_id,
            verification_specification_fingerprint=result.verification_specification_fingerprint,
            requirement_context_fingerprint=result.requirement_context_fingerprint,
            design_truth_id=result.design_truth_id,
            design_truth_fingerprint=result.design_truth_fingerprint,
            firmware_specification_id=result.firmware_specification_id,
            firmware_specification_fingerprint=result.firmware_specification_fingerprint,
            virtual_assembly_id=result.virtual_assembly_id,
            virtual_assembly_fingerprint=result.virtual_assembly_fingerprint,
            simulation_scenario_id=result.simulation_scenario_id,
            simulation_scenario_fingerprint=result.simulation_scenario_fingerprint,
            simulation_result_id=result.simulation_result_id,
            simulation_result_fingerprint=result.simulation_result_fingerprint,
            simulation_evidence_id=result.simulation_evidence_id,
            simulation_evidence_fingerprint=result.simulation_evidence_fingerprint,
            backend=request.simulation_evidence.backend,
            backend_version=request.simulation_evidence.backend_version,
            model_fidelity=request.simulation_evidence.model_fidelity,
            artifact_id=request.simulation_evidence.artifact_id,
            artifact_sha256=request.simulation_evidence.artifact_sha256,
            source_kind="REAL_BACKEND",
            observed_from_backend=True,
            synthetic=False,
            check_ids=tuple(item.check_id for item in result.checks),
            evidence_summary="sanitized Expected-vs-Observed functional verification evidence",
            created_at=result.created_at,
        )
        return self._store.put(FunctionalVerificationRecord.create(result=result, evidence=evidence))


__all__ = ("FunctionalVerificationService",)
