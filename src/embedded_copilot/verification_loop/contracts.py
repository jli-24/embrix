from __future__ import annotations

import copy
import hashlib
import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_consistency.contracts import HardwareFirmwareTruth
from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    finalize,
    fp,
    ident,
    text,
    tuple_values,
)
from embedded_copilot.engineering_copilot.contracts import (
    DesignTruth,
    FirmwareSpecification,
    SimulationScenario,
    SimulationSpecification,
    VerificationSpecification,
)
from embedded_copilot.engineering_requirements.models import RequirementContext
from embedded_copilot.execution import (
    BuildResult,
    DebugObservation,
    FlashResult,
)
from embedded_copilot.simulation_runtime.contracts import (
    SimulationBackend,
    SimulationEvidence,
    SimulationRequest,
    SimulationResult,
)
from embedded_copilot.virtual_assembly.contracts import VirtualBoardAssembly


def _contract_values(value: object, field: str) -> tuple[object, ...]:
    """Accept JSON lists when rehydrating the frozen tuple wire contract."""

    if isinstance(value, list):
        value = tuple(value)
    return tuple_values(value, field)


class VerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ESCALATE = "ESCALATE"


class VerificationContract(Fingerprinted):
    pass


class VerificationEvidenceReference(VerificationContract):
    evidence_id: str
    source_kind: str
    source_fingerprint: str
    locator: str
    description: str | None = None

    @field_validator("evidence_id", "source_kind", "locator", mode="before")
    @classmethod
    def _evidence_text(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value: object) -> str:
        return fp(value, "source_fingerprint")

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: object) -> str | None:
        return None if value is None else text(value, "description", 512)

    @classmethod
    def create(cls, **values: object) -> VerificationEvidenceReference:
        return finalize(cls, values)


class VerificationIssue(VerificationContract):
    issue_id: str
    severity: str
    category: str
    location: str | None = None
    affected_objects: tuple[str, ...] = ()
    problem: str
    why: str
    possible_effects: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    recommendation: str
    reverification: tuple[str, ...] = ()

    @field_validator("issue_id", mode="before")
    @classmethod
    def _issue_id(cls, value: object) -> str:
        return ident(value, "issue_id")

    @field_validator("severity", "category", mode="before")
    @classmethod
    def _issue_labels(cls, value: object, info) -> str:
        return text(value, info.field_name, 128)

    @field_validator("location", mode="before")
    @classmethod
    def _location(cls, value: object) -> str | None:
        return None if value is None else text(value, "location", 256)

    @field_validator("affected_objects", "possible_effects", "evidence", "reverification", mode="before")
    @classmethod
    def _issue_tuples(cls, value: object, info) -> tuple[str, ...]:
        return tuple(text(item, info.field_name, 512) for item in _contract_values(value, info.field_name))

    @field_validator("problem", "why", "recommendation", mode="before")
    @classmethod
    def _issue_text(cls, value: object, info) -> str:
        return text(value, info.field_name, 2048)

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        """Compatibility view for callers that use the result-level name."""
        return self.evidence

    @classmethod
    def create(cls, **values: object) -> VerificationIssue:
        return finalize(cls, values)


class VerificationRequest(VerificationContract):
    verification_id: str
    project_id: str
    target_id: str
    repair_proposal_fingerprint: str
    prior_failure_fingerprint: str
    build_request_id: str | None = None
    flash_request_id: str | None = None
    debug_request_id: str | None = None
    build_result: BuildResult | None = None
    flash_result: FlashResult | None = None
    debug_observation: DebugObservation | None = None

    @field_validator("verification_id", "project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator(
        "repair_proposal_fingerprint", "prior_failure_fingerprint", mode="before"
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fp(value, info.field_name)

    @field_validator(
        "build_request_id", "flash_request_id", "debug_request_id", mode="before"
    )
    @classmethod
    def _optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else ident(value, info.field_name)

    @field_validator("build_result", mode="before")
    @classmethod
    def _build(cls, value: object) -> BuildResult | None:
        return (
            None if value is None else BuildResult.model_validate(copy.deepcopy(value))
        )

    @field_validator("flash_result", mode="before")
    @classmethod
    def _flash(cls, value: object) -> FlashResult | None:
        return (
            None if value is None else FlashResult.model_validate(copy.deepcopy(value))
        )

    @field_validator("debug_observation", mode="before")
    @classmethod
    def _debug(cls, value: object) -> DebugObservation | None:
        return (
            None
            if value is None
            else DebugObservation.model_validate(copy.deepcopy(value))
        )

    @model_validator(mode="after")
    def _bound(self) -> VerificationRequest:
        if (
            self.build_result is None
            and self.flash_result is None
            and self.debug_observation is None
        ):
            raise ValueError("verification requires an execution observation")
        if (
            self.build_result is not None
            and self.build_request_id != self.build_result.request_id
        ):
            raise ValueError("build result request binding mismatch")
        if (
            self.flash_result is not None
            and self.flash_request_id != self.flash_result.request_id
        ):
            raise ValueError("flash result request binding mismatch")
        if self.debug_observation is not None:
            if self.debug_request_id != self.debug_observation.request_id:
                raise ValueError("debug observation request binding mismatch")
            if (
                self.debug_observation.project_id != self.project_id
                or self.debug_observation.target_id != self.target_id
            ):
                raise ValueError("debug observation target binding mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> VerificationRequest:
        return finalize(cls, values)


class VerificationResult(VerificationContract):
    verification_id: str
    project_id: str
    target_id: str
    status: VerificationStatus
    attempt: int
    compared_fingerprints: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    summary: str
    verifier: str = "execution"
    scope: str = "execution"
    issues: tuple[VerificationIssue, ...] = ()
    evidence: tuple[VerificationEvidenceReference, ...] = ()
    affected_objects: tuple[str, ...] = ()
    confidence: float | None = None
    assumptions: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    measurement_refs: tuple[str, ...] = ()
    revision: str | None = None
    applicability: str | None = None
    stale: bool = False
    stale_reason: str | None = None
    required_reverification: tuple[str, ...] = ()

    @field_validator("verification_id", "project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("attempt", mode="before")
    @classmethod
    def _attempt(cls, value: object) -> int:
        if type(value) is not int or not 0 <= value <= 3:
            raise ValueError("attempt is invalid")
        return value

    @field_validator("compared_fingerprints", mode="before")
    @classmethod
    def _fingerprints(cls, value: object) -> tuple[str, ...]:
        values = tuple(
            fp(item, "compared_fingerprint")
            for item in _contract_values(value, "compared_fingerprints")
        )
        if not values:
            raise ValueError("compared fingerprints are required")
        return tuple(sorted(set(values)))

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    ident(item, "evidence_ref")
                    for item in _contract_values(value, "evidence_refs")
                }
            )
        )

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return text(value, "summary", 512)

    @field_validator("verifier", "scope", "applicability", mode="before")
    @classmethod
    def _labels(cls, value: object, info) -> str | None:
        return None if value is None else text(value, info.field_name, 128)

    @field_validator("issues", mode="before")
    @classmethod
    def _issues(cls, value: object) -> tuple[VerificationIssue, ...]:
        return tuple(
            item if isinstance(item, VerificationIssue) else VerificationIssue.model_validate(copy.deepcopy(item))
            for item in _contract_values(value, "issues")
        )

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_objects(cls, value: object) -> tuple[VerificationEvidenceReference, ...]:
        return tuple(
            item if isinstance(item, VerificationEvidenceReference) else VerificationEvidenceReference.model_validate(copy.deepcopy(item))
            for item in _contract_values(value, "evidence")
        )

    @field_validator("affected_objects", "assumptions", "unknowns", "recommendations", mode="before")
    @classmethod
    def _result_tuples(cls, value: object, info) -> tuple[str, ...]:
        return tuple(text(item, info.field_name, 1024) for item in _contract_values(value, info.field_name))

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float | None:
        if value is None:
            return None
        if type(value) not in (int, float) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
            raise ValueError("confidence is invalid")
        return float(value)

    @field_validator("revision", mode="before")
    @classmethod
    def _revision(cls, value: object) -> str | None:
        return None if value is None else fp(value, "revision")

    @field_validator("stale_reason", mode="before")
    @classmethod
    def _stale_reason(cls, value: object) -> str | None:
        return None if value is None else text(value, "stale_reason", 512)

    @field_validator("required_reverification", mode="before")
    @classmethod
    def _required_reverification(cls, value: object) -> tuple[str, ...]:
        return tuple(text(item, "required_reverification", 256) for item in _contract_values(value, "required_reverification"))

    @field_validator("measurement_refs", mode="before")
    @classmethod
    def _measurement_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(ident(item, "measurement_ref") for item in _contract_values(value, "measurement_refs"))

    @model_validator(mode="after")
    def _result_evidence_binding(self) -> VerificationResult:
        if self.stale and self.stale_reason is None:
            raise ValueError("stale verification requires a reason")
        if not self.stale and self.stale_reason is not None:
            raise ValueError("current verification cannot have a stale reason")
        evidence_ids = set(self.evidence_refs)
        if any(item.evidence_id not in evidence_ids for item in self.evidence):
            raise ValueError("verification evidence object is not referenced")
        if any(
            not set(item.evidence).issubset(evidence_ids)
            for item in self.issues
        ):
            raise ValueError("verification issue evidence is not referenced")
        return self

    @classmethod
    def create(cls, **values: object) -> VerificationResult:
        return finalize(cls, values)


__all__ = (
    "VerificationContract",
    "VerificationEvidenceReference",
    "VerificationIssue",
    "VerificationRequest",
    "VerificationResult",
    "VerificationStatus",
)


# Functional verification is an evidence comparison layer on top of the
# existing execution verification contract.  It deliberately lives in this
# package so it cannot become a second simulation or agent framework.
class FunctionalVerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class FunctionalCheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class VerificationComparisonRule(StrEnum):
    EQUAL = "EQUAL"
    EXACT = "EQUAL"
    SEQUENCE = "SEQUENCE"
    CONTAINS = "CONTAINS"
    PRESENT = "PRESENT"


class ModelScopeStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class ElectricalSimulationStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REQUIRED = "REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class L4GateStatus(StrEnum):
    NOT_ACHIEVED = "NOT_ACHIEVED"
    VIRTUAL_FUNCTIONAL_VERIFIED = "VIRTUAL_FUNCTIONAL_VERIFIED"


def _functional_nested(value: object, model: type[Any], field: str) -> Any:
    if type(value) is model:
        value = value.model_dump(mode="python")
    elif type(value) is not dict:
        raise TypeError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(value), strict=False)


def _functional_truth(value: object) -> DesignTruth | HardwareFirmwareTruth:
    raw = value.model_dump(mode="python") if isinstance(value, (DesignTruth, HardwareFirmwareTruth)) else value
    if type(raw) is not dict:
        raise TypeError("design_truth is invalid")
    errors: list[Exception] = []
    for model in (DesignTruth, HardwareFirmwareTruth):
        try:
            return model.model_validate(copy.deepcopy(raw), strict=False)
        except (TypeError, ValueError) as error:  # pragma: no cover - both branches are exercised by contract tests
            errors.append(error)
    raise ValueError("design_truth is invalid") from errors[-1]


def _functional_truth_fingerprint(value: DesignTruth | HardwareFirmwareTruth) -> str:
    if isinstance(value, HardwareFirmwareTruth):
        return value.design_truth_fingerprint
    return value.fingerprint


def _functional_tuple(value: object, field: str) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple_values(value, field)


def _functional_refs(value: object, field: str) -> tuple[str, ...]:
    refs = tuple(ident(item, field.removesuffix("s")) for item in _functional_tuple(value, field))
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _functional_value(value: object, field: str, *, allow_none: bool = True) -> object:
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field} is required")
    if type(value) is bool or type(value) is int or type(value) is str:
        if type(value) is str:
            return text(value, field, 1024)
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field} is invalid")
        return value
    if type(value) is list:
        return tuple(_functional_value(item, field) for item in value)
    if type(value) is tuple:
        return tuple(_functional_value(item, field) for item in value)
    if type(value) is dict:
        if len(value) > 64 or any(type(key) is not str for key in value):
            raise ValueError(f"{field} is invalid")
        return {
            text(key, f"{field}_key", 96): _functional_value(item, field)
            for key, item in value.items()
        }
    raise TypeError(f"{field} is invalid")


def _functional_time(value: object, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} is invalid") from error
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class ExpectedBehavior(Fingerprinted):
    behavior_id: str
    category: str
    expected: object
    source_refs: tuple[str, ...] = ()
    required: bool = True

    _ids = field_validator("behavior_id", mode="before")(
        lambda value: ident(value, "behavior_id")
    )
    _category = field_validator("category", mode="before")(
        lambda value: text(value, "category", 128)
    )
    _expected = field_validator("expected", mode="before")(
        lambda value: _functional_value(value, "expected", allow_none=False)
    )
    _refs = field_validator("source_refs", mode="before")(
        lambda value: _functional_refs(value, "source_refs")
    )

    @field_validator("required", mode="before")
    @classmethod
    def _required(cls, value: object) -> bool:
        if type(value) is not bool:
            raise TypeError("required is invalid")
        return value

    @classmethod
    def create(cls, **values: object) -> ExpectedBehavior:
        return finalize(cls, values)


class VerificationCheck(Fingerprinted):
    _fingerprint_exclude = frozenset({"observed", "status", "reason", "evidence_refs"})

    check_id: str
    verification_specification_id: str
    simulation_scenario_id: str
    simulation_scenario_fingerprint: str
    category: str
    title: str
    requirement_refs: tuple[str, ...] = ()
    design_truth_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    expected: object
    observed: object | None = None
    comparison_rule: VerificationComparisonRule = VerificationComparisonRule.EQUAL
    required: bool = True
    requires_model_scope: bool = True
    status: FunctionalCheckStatus = FunctionalCheckStatus.UNKNOWN
    reason: str = "observation is not available"
    evidence_refs: tuple[str, ...] = ()

    _ids = field_validator(
        "check_id", "verification_specification_id", "simulation_scenario_id", mode="before"
    )(
        lambda value, info: ident(value, info.field_name)
    )
    _scenario_fp = field_validator("simulation_scenario_fingerprint", mode="before")(
        lambda value: fp(value, "simulation_scenario_fingerprint")
    )
    _texts = field_validator("category", "title", "reason", mode="before")(
        lambda value, info: text(value, info.field_name, 512)
    )
    _refs = field_validator("requirement_refs", "design_truth_refs", "evidence_refs", mode="before")(
        lambda value, info: _functional_refs(value, info.field_name)
    )
    _source_refs = field_validator("source_refs", mode="before")(
        lambda value: _functional_refs(value, "source_refs")
    )
    _expected = field_validator("expected", mode="before")(
        lambda value: _functional_value(value, "expected", allow_none=False)
    )
    _observed = field_validator("observed", mode="before")(
        lambda value: _functional_value(value, "observed")
    )

    @field_validator("comparison_rule", mode="before")
    @classmethod
    def _rule(cls, value: object) -> VerificationComparisonRule:
        return value if isinstance(value, VerificationComparisonRule) else VerificationComparisonRule(value)

    @field_validator("required", "requires_model_scope", mode="before")
    @classmethod
    def _bools(cls, value: object) -> bool:
        if type(value) is not bool:
            raise TypeError("verification check boolean is invalid")
        return value

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> FunctionalCheckStatus:
        return value if isinstance(value, FunctionalCheckStatus) else FunctionalCheckStatus(value)

    @classmethod
    def create(cls, **values: object) -> VerificationCheck:
        return finalize(cls, values)


class FunctionalVerificationRequest(Fingerprinted):
    project_id: str
    requirement_specification: RequirementContext | None
    design_truth: DesignTruth | HardwareFirmwareTruth
    firmware_specification: FirmwareSpecification
    verification_specification: VerificationSpecification
    simulation_specification: SimulationSpecification | None = None
    virtual_assembly: VirtualBoardAssembly
    simulation_scenario: SimulationScenario
    simulation_request: SimulationRequest
    simulation_result: SimulationResult
    simulation_evidence: SimulationEvidence
    expected_behavior: tuple[ExpectedBehavior, ...] = ()
    source_fingerprint: str | None = None

    _project = field_validator("project_id", mode="before")(
        lambda value: ident(value, "project_id")
    )
    @field_validator("requirement_specification", mode="before")
    @classmethod
    def _requirement(cls, value: object) -> RequirementContext | None:
        return None if value is None else _functional_nested(value, RequirementContext, "requirement_specification")

    @field_validator("design_truth", mode="before")
    @classmethod
    def _truth(cls, value: object) -> DesignTruth | HardwareFirmwareTruth:
        return _functional_truth(value)

    @field_validator("firmware_specification", mode="before")
    @classmethod
    def _firmware(cls, value: object) -> FirmwareSpecification:
        return _functional_nested(value, FirmwareSpecification, "firmware_specification")

    @field_validator("verification_specification", mode="before")
    @classmethod
    def _verification(cls, value: object) -> VerificationSpecification:
        return _functional_nested(value, VerificationSpecification, "verification_specification")

    @field_validator("simulation_specification", mode="before")
    @classmethod
    def _simulation_spec(cls, value: object) -> SimulationSpecification | None:
        return None if value is None else _functional_nested(value, SimulationSpecification, "simulation_specification")

    @field_validator("virtual_assembly", mode="before")
    @classmethod
    def _assembly(cls, value: object) -> VirtualBoardAssembly:
        return _functional_nested(value, VirtualBoardAssembly, "virtual_assembly")

    @field_validator("simulation_scenario", mode="before")
    @classmethod
    def _scenario(cls, value: object) -> SimulationScenario:
        return _functional_nested(value, SimulationScenario, "simulation_scenario")

    @field_validator("simulation_request", mode="before")
    @classmethod
    def _request(cls, value: object) -> SimulationRequest:
        return _functional_nested(value, SimulationRequest, "simulation_request")

    @field_validator("simulation_result", mode="before")
    @classmethod
    def _result(cls, value: object) -> SimulationResult:
        return _functional_nested(value, SimulationResult, "simulation_result")

    @field_validator("simulation_evidence", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> SimulationEvidence:
        return _functional_nested(value, SimulationEvidence, "simulation_evidence")

    @field_validator("expected_behavior", mode="before")
    @classmethod
    def _behaviors(cls, value: object) -> tuple[ExpectedBehavior, ...]:
        return tuple(
            _functional_nested(item, ExpectedBehavior, "expected_behavior")
            for item in _functional_tuple(value, "expected_behavior")
        )

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fp(cls, value: object) -> str | None:
        return None if value is None else fp(value, "source_fingerprint")

    @model_validator(mode="after")
    def _identity(self) -> FunctionalVerificationRequest:
        if self.requirement_specification is not None and self.requirement_specification.project_id != self.project_id:
            raise ValueError("requirement project binding mismatch")
        if any(
            item.project_id != self.project_id
            for item in (
                self.design_truth,
                self.firmware_specification,
                self.verification_specification,
                self.virtual_assembly,
                self.simulation_request,
                self.simulation_result,
            )
        ):
            raise ValueError("functional verification project binding mismatch")
        if self.firmware_specification.design_truth_id not in {None, self.design_truth.truth_id}:
            raise ValueError("firmware DesignTruth identity mismatch")
        if self.firmware_specification.design_truth_fingerprint not in {None, _functional_truth_fingerprint(self.design_truth)}:
            raise ValueError("firmware DesignTruth fingerprint mismatch")
        if self.virtual_assembly.design_truth_id != self.design_truth.truth_id:
            raise ValueError("assembly DesignTruth identity mismatch")
        if self.virtual_assembly.design_truth_fingerprint != _functional_truth_fingerprint(self.design_truth):
            raise ValueError("assembly DesignTruth fingerprint mismatch")
        if self.verification_specification.firmware_spec_id != self.firmware_specification.firmware_spec_id:
            raise ValueError("verification firmware identity mismatch")
        if self.verification_specification.firmware_spec_fingerprint != self.firmware_specification.fingerprint:
            raise ValueError("verification firmware fingerprint mismatch")
        if self.simulation_scenario.simulation_spec_id != self.verification_specification.simulation_spec_id:
            raise ValueError("verification scenario identity mismatch")
        if self.simulation_specification is not None:
            if self.simulation_specification.project_id != self.project_id or self.simulation_specification.simulation_spec_id != self.simulation_scenario.simulation_spec_id:
                raise ValueError("simulation specification binding mismatch")
            if self.simulation_specification.scenarios and self.simulation_scenario.scenario_id not in {
                item.scenario_id for item in self.simulation_specification.scenarios
            }:
                raise ValueError("simulation scenario is not part of the simulation specification")
        if self.simulation_request.project_id != self.project_id or self.simulation_request.simulation_id != self.simulation_result.simulation_id:
            raise ValueError("simulation request identity mismatch")
        if self.simulation_request.scenario not in {self.simulation_scenario.scenario_id, self.simulation_scenario.title}:
            raise ValueError("simulation scenario binding mismatch")
        if self.simulation_result.request_fingerprint != self.simulation_request.fingerprint or self.simulation_evidence.request_fingerprint != self.simulation_request.fingerprint:
            raise ValueError("simulation request fingerprint mismatch")
        if self.simulation_result.backend != self.simulation_request.backend or self.simulation_evidence.backend != self.simulation_request.backend:
            raise ValueError("simulation backend binding mismatch")
        if self.simulation_evidence.simulation_id != self.simulation_result.simulation_id:
            raise ValueError("simulation evidence identity mismatch")
        if self.simulation_result.operation_id != self.simulation_evidence.operation_id:
            raise ValueError("simulation operation binding mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> FunctionalVerificationRequest:
        return finalize(cls, values)


class ModelScopeAssessment(Fingerprinted):
    assessment_id: str
    backend: SimulationBackend
    backend_version: str
    model_fidelity: str
    status: ModelScopeStatus
    sufficient: bool
    required_claims: tuple[str, ...] = ()
    unsupported_claims: tuple[str, ...] = ()
    electrical_simulation: ElectricalSimulationStatus = ElectricalSimulationStatus.NOT_APPLICABLE
    rationale: str
    source_fingerprints: tuple[str, ...]

    _ids = field_validator("assessment_id", "backend_version", "model_fidelity", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _claims = field_validator("required_claims", "unsupported_claims", mode="before")(
        lambda value, info: tuple(text(item, info.field_name.removesuffix("s"), 256) for item in _functional_tuple(value, info.field_name))
    )
    _rationale = field_validator("rationale", mode="before")(
        lambda value: text(value, "rationale", 1024)
    )
    _fps = field_validator("source_fingerprints", mode="before")(
        lambda value: tuple(fp(item, "source_fingerprint") for item in _functional_tuple(value, "source_fingerprints"))
    )

    @field_validator("backend", mode="before")
    @classmethod
    def _backend(cls, value: object) -> SimulationBackend:
        return value if isinstance(value, SimulationBackend) else SimulationBackend(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> ModelScopeStatus:
        return value if isinstance(value, ModelScopeStatus) else ModelScopeStatus(value)

    @field_validator("electrical_simulation", mode="before")
    @classmethod
    def _electrical(cls, value: object) -> ElectricalSimulationStatus:
        return value if isinstance(value, ElectricalSimulationStatus) else ElectricalSimulationStatus(value)

    @field_validator("sufficient", mode="before")
    @classmethod
    def _sufficient(cls, value: object) -> bool:
        if type(value) is not bool:
            raise TypeError("sufficient is invalid")
        return value

    @model_validator(mode="after")
    def _scope_binding(self) -> ModelScopeAssessment:
        if (self.status is ModelScopeStatus.SUFFICIENT) != self.sufficient:
            raise ValueError("model scope status binding mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> ModelScopeAssessment:
        return finalize(cls, values)


class FunctionalVerificationResult(Fingerprinted):
    verification_result_id: str
    project_id: str
    verification_specification_id: str
    verification_specification_fingerprint: str
    simulation_scenario_id: str
    simulation_scenario_fingerprint: str
    simulation_result_id: str
    simulation_result_fingerprint: str
    simulation_evidence_id: str
    simulation_evidence_fingerprint: str
    virtual_assembly_id: str
    virtual_assembly_fingerprint: str
    design_truth_id: str
    design_truth_fingerprint: str
    firmware_specification_id: str
    firmware_specification_fingerprint: str
    requirement_context_fingerprint: str | None = None
    status: FunctionalVerificationStatus
    checks: tuple[VerificationCheck, ...]
    model_scope: ModelScopeAssessment
    evidence_refs: tuple[str, ...]
    required_check_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    not_applicable_count: int
    blocked_count: int
    stale_count: int
    lineage_fresh: bool
    evidence_complete: bool
    l4_gate: L4GateStatus
    summary: str
    created_at: datetime

    _ids = field_validator(
        "verification_result_id", "project_id", "verification_specification_id",
        "simulation_scenario_id", "simulation_result_id", "simulation_evidence_id",
        "virtual_assembly_id", "design_truth_id", "firmware_specification_id", mode="before"
    )(lambda value, info: ident(value, info.field_name))
    _fps = field_validator(
        "verification_specification_fingerprint", "simulation_scenario_fingerprint",
        "simulation_result_fingerprint", "simulation_evidence_fingerprint",
        "virtual_assembly_fingerprint", "design_truth_fingerprint",
        "firmware_specification_fingerprint", mode="before"
    )(lambda value, info: fp(value, info.field_name))
    _optional_fp = field_validator("requirement_context_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "requirement_context_fingerprint")
    )
    @field_validator("checks", mode="before")
    @classmethod
    def _checks(cls, value: object) -> tuple[VerificationCheck, ...]:
        return tuple(_functional_nested(item, VerificationCheck, "checks") for item in _functional_tuple(value, "checks"))

    @field_validator("model_scope", mode="before")
    @classmethod
    def _model_scope(cls, value: object) -> ModelScopeAssessment:
        return _functional_nested(value, ModelScopeAssessment, "model_scope")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _functional_refs(value, "evidence_refs")

    @field_validator(
        "required_check_count", "pass_count", "fail_count", "unknown_count",
        "not_applicable_count", "blocked_count", "stale_count", mode="before"
    )
    @classmethod
    def _counts(cls, value: object) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("functional verification count is invalid")
        return value

    @field_validator("lineage_fresh", "evidence_complete", mode="before")
    @classmethod
    def _flags(cls, value: object) -> bool:
        if type(value) is not bool:
            raise TypeError("functional verification flag is invalid")
        return value

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> FunctionalVerificationStatus:
        return value if isinstance(value, FunctionalVerificationStatus) else FunctionalVerificationStatus(value)

    @field_validator("l4_gate", mode="before")
    @classmethod
    def _l4(cls, value: object) -> L4GateStatus:
        return value if isinstance(value, L4GateStatus) else L4GateStatus(value)

    _summary = field_validator("summary", mode="before")(
        lambda value: text(value, "summary", 1024)
    )
    _created = field_validator("created_at", mode="before")(
        lambda value: _functional_time(value, "created_at")
    )

    @model_validator(mode="after")
    def _result_binding(self) -> FunctionalVerificationResult:
        ids = tuple(item.check_id for item in self.checks)
        if len(ids) != len(set(ids)):
            raise ValueError("functional verification check ids must be unique")
        if self.required_check_count != sum(item.required for item in self.checks):
            raise ValueError("functional verification required count mismatch")
        if (
            self.l4_gate is L4GateStatus.VIRTUAL_FUNCTIONAL_VERIFIED
            and (
                self.status is not FunctionalVerificationStatus.PASS
                or not self.lineage_fresh
                or not self.evidence_complete
                or not self.model_scope.sufficient
            )
        ):
            raise ValueError("L4 gate binding mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> FunctionalVerificationResult:
        return finalize(cls, values)


class FunctionalVerificationEvidence(Fingerprinted):
    evidence_id: str
    verification_result_id: str
    verification_result_fingerprint: str
    project_id: str
    verification_specification_id: str
    verification_specification_fingerprint: str
    requirement_context_fingerprint: str | None = None
    design_truth_id: str
    design_truth_fingerprint: str
    firmware_specification_id: str
    firmware_specification_fingerprint: str
    virtual_assembly_id: str
    virtual_assembly_fingerprint: str
    simulation_scenario_id: str
    simulation_scenario_fingerprint: str
    simulation_result_id: str
    simulation_result_fingerprint: str
    simulation_evidence_id: str
    simulation_evidence_fingerprint: str
    backend: SimulationBackend
    backend_version: str
    model_fidelity: str
    artifact_id: str
    artifact_sha256: str
    source_kind: str
    observed_from_backend: bool
    synthetic: bool
    check_ids: tuple[str, ...]
    evidence_summary: str
    created_at: datetime

    _ids = field_validator(
        "evidence_id", "verification_result_id", "project_id", "verification_specification_id",
        "design_truth_id", "firmware_specification_id", "virtual_assembly_id",
        "simulation_scenario_id", "simulation_result_id", "simulation_evidence_id",
        "backend_version", "model_fidelity", "artifact_id", "source_kind", mode="before"
    )(lambda value, info: ident(value, info.field_name))
    _fps = field_validator(
        "verification_result_fingerprint", "verification_specification_fingerprint",
        "design_truth_fingerprint", "firmware_specification_fingerprint",
        "virtual_assembly_fingerprint", "simulation_scenario_fingerprint",
        "simulation_result_fingerprint", "simulation_evidence_fingerprint", "artifact_sha256", mode="before"
    )(lambda value, info: fp(value, info.field_name))
    _optional_fp = field_validator("requirement_context_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "requirement_context_fingerprint")
    )
    _checks = field_validator("check_ids", mode="before")(
        lambda value: _functional_refs(value, "check_ids")
    )
    _summary = field_validator("evidence_summary", mode="before")(
        lambda value: text(value, "evidence_summary", 1024)
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _functional_time(value, "created_at")
    )

    @field_validator("backend", mode="before")
    @classmethod
    def _backend(cls, value: object) -> SimulationBackend:
        return value if isinstance(value, SimulationBackend) else SimulationBackend(value)

    @field_validator("observed_from_backend", "synthetic", mode="before")
    @classmethod
    def _bools(cls, value: object) -> bool:
        if type(value) is not bool:
            raise TypeError("functional evidence flag is invalid")
        return value

    @model_validator(mode="after")
    def _evidence_binding(self) -> FunctionalVerificationEvidence:
        if self.backend is SimulationBackend.FAKE or self.synthetic or not self.observed_from_backend:
            raise ValueError("functional evidence must be real backend evidence")
        return self

    @classmethod
    def create(cls, **values: object) -> FunctionalVerificationEvidence:
        return finalize(cls, values)


class FunctionalVerificationRecord(Fingerprinted):
    result: FunctionalVerificationResult
    evidence: FunctionalVerificationEvidence

    @field_validator("result", mode="before")
    @classmethod
    def _result(cls, value: object) -> FunctionalVerificationResult:
        return _functional_nested(value, FunctionalVerificationResult, "result")

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> FunctionalVerificationEvidence:
        return _functional_nested(value, FunctionalVerificationEvidence, "evidence")

    @model_validator(mode="after")
    def _bind(self) -> FunctionalVerificationRecord:
        if self.evidence.verification_result_id != self.result.verification_result_id or self.evidence.verification_result_fingerprint != self.result.fingerprint:
            raise ValueError("functional verification evidence result binding mismatch")
        if self.evidence.project_id != self.result.project_id or self.evidence.check_ids != tuple(item.check_id for item in self.result.checks):
            raise ValueError("functional verification evidence scope mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> FunctionalVerificationRecord:
        return finalize(cls, values)


def functional_source_fingerprint(request: FunctionalVerificationRequest) -> str:
    values = (
        request.requirement_specification.fingerprint if request.requirement_specification is not None else "none",
        _functional_truth_fingerprint(request.design_truth),
        request.firmware_specification.fingerprint,
        request.verification_specification.fingerprint,
        request.virtual_assembly.assembly_fingerprint,
        request.simulation_scenario.fingerprint,
        request.simulation_request.fingerprint,
    )
    return "sha256:" + hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


# Compatibility names used by the verification vocabulary in design docs.
VerificationOverallStatus = FunctionalVerificationStatus
VerificationEvidence = FunctionalVerificationEvidence
RequirementSpecification = RequirementContext

__all__ += (
    "ElectricalSimulationStatus",
    "ExpectedBehavior",
    "FunctionalCheckStatus",
    "FunctionalVerificationEvidence",
    "FunctionalVerificationRecord",
    "FunctionalVerificationRequest",
    "FunctionalVerificationResult",
    "FunctionalVerificationStatus",
    "L4GateStatus",
    "ModelScopeAssessment",
    "ModelScopeStatus",
    "RequirementSpecification",
    "VerificationCheck",
    "VerificationComparisonRule",
    "VerificationEvidence",
    "VerificationOverallStatus",
    "functional_source_fingerprint",
)
