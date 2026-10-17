"""Application-facing integration for the Engineering Object Runtime MVP.

This module is an adapter around the existing engineering-runtime contracts.  It
does not define a second object model or persistence store: source objects are
materialized only as project-scoped runtime projections, while CircuitIntent is
the sole newly proposed engineering object in this phase.
"""

from __future__ import annotations

import copy
import hashlib
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
)
from embedded_copilot.engineering_context import (
    EngineeringContextSnapshot as ApplicationContextSnapshot,
)
from embedded_copilot.engineering_copilot import (
    ArchitectureDesignStatus,
    DesignTruth,
    SchematicPlan,
    SchematicPlanningStatus,
)
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementStatus,
)
from embedded_copilot.engineering_runtime import (
    ApprovalRecord,
    CalculationRecord,
    CircuitBlock,
    CircuitDecision,
    CircuitIntentObject,
    CircuitSchematicBinding,
    ContextRequest,
    EngineeringContextSnapshot,
    EngineeringObjectRuntime,
    EvidenceObject,
    EvidenceQualificationStatus,
    HardwareArchitectureObject,
    LifecycleActor,
    LifecycleStatus,
    ObjectMutationRecord,
    ObjectProposal,
    ObjectType,
    ProposalStatus,
    Provenance,
    RelationType,
    RequirementObject,
    SchematicDesignObject,
    VerificationRequirement,
)
from embedded_copilot.engineering_runtime.common import (
    RuntimeContract,
    canonical_fingerprint,
    normalize_text,
    validate_fingerprint,
    validate_identifier,
    validate_text_tuple,
)
from embedded_copilot.engineering_runtime.evidence import (
    CanonicalOperationReceiptEvidenceResolver,
    EvidenceReceiptReference,
    EvidenceReceiptResolutionError,
    EvidenceReferenceStatus,
)

from .models import (
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRoutingContext,
)


class EngineeringObjectFlowErrorCode(StrEnum):
    ROUTE_REJECTED = "ROUTE_REJECTED"
    PROJECT_IDENTITY_MISMATCH = "PROJECT_IDENTITY_MISMATCH"
    TENANT_IDENTITY_MISMATCH = "TENANT_IDENTITY_MISMATCH"
    CONTEXT_UNAVAILABLE = "CONTEXT_UNAVAILABLE"
    STALE_CONTEXT = "STALE_CONTEXT"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    BINDING_BLOCKED = "BINDING_BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    PROPOSAL_TAMPERED = "PROPOSAL_TAMPERED"


class EngineeringObjectFlowError(ValueError):
    """Structured fail-closed error at the application integration boundary."""

    def __init__(self, code: EngineeringObjectFlowErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class ObjectProjectionOrigin(StrEnum):
    PROJECTION_REFERENCE = "PROJECTION_REFERENCE"
    NEW_PROPOSAL = "NEW_PROPOSAL"


class PersistenceStatus(StrEnum):
    PROJECTION_ONLY = "PROJECTION_ONLY"
    UNAVAILABLE = "UNAVAILABLE"


class CircuitIntentSpec(RuntimeContract):
    """Explicit design-intent input; it is not a schematic or PCB artifact."""

    function: str
    topology: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    calculation: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    blocks: tuple[CircuitBlock, ...] = ()
    decisions: tuple[CircuitDecision, ...] = ()
    calculations: tuple[CalculationRecord, ...] = ()
    verification_requirements: tuple[VerificationRequirement, ...] = ()
    legacy_verification_requirements: tuple[str, ...] = ()

    _function = field_validator("function")(
        lambda value: normalize_text(value, field="function")
    )
    _tuples = field_validator(
        "topology",
        "parameters",
        "calculation",
        "assumptions",
        "risks",
    )(lambda value: validate_text_tuple(value, field="circuit_intent_spec"))
    _legacy_verification_requirements = field_validator(
        "legacy_verification_requirements"
    )(lambda value: validate_text_tuple(value, field="legacy_verification_requirements"))

    @model_validator(mode="before")
    @classmethod
    def _legacy_verification_input(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        raw = values.get("verification_requirements", ())
        if type(raw) in {tuple, list} and all(isinstance(item, str) for item in raw):
            values = dict(values)
            values["verification_requirements"] = ()
            values["legacy_verification_requirements"] = tuple(raw)
        return values

    @field_validator(
        "blocks",
        "decisions",
        "calculations",
        "verification_requirements",
        mode="before",
    )
    @classmethod
    def _structured_fields(cls, value: object, info) -> tuple[object, ...]:
        contract_types = {
            "blocks": CircuitBlock,
            "decisions": CircuitDecision,
            "calculations": CalculationRecord,
            "verification_requirements": VerificationRequirement,
        }
        if type(value) not in {tuple, list}:
            raise TypeError(f"{info.field_name} must be an immutable sequence")
        contract_type = contract_types[info.field_name]
        return tuple(
            item
            if isinstance(item, contract_type)
            else contract_type.model_validate(item, strict=False)
            for item in value
        )


@runtime_checkable
class EvidenceReceiptReferencePort(Protocol):
    def resolve(
        self,
        request: ApplicationQuestionRequest,
        reference: EvidenceReceiptReference,
    ) -> EvidenceReceiptReference: ...


class OperationReceiptEvidenceResolver:
    """Application adapter that maps canonical Runtime rejection to its error type."""

    __slots__ = ("_resolver",)

    def __init__(self, source: object) -> None:
        self._resolver = CanonicalOperationReceiptEvidenceResolver(source)

    def resolve(
        self,
        request: ApplicationQuestionRequest,
        reference: EvidenceReceiptReference,
    ) -> EvidenceReceiptReference:
        try:
            return self._resolver.resolve(request, reference)
        except EvidenceReceiptResolutionError as error:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE, str(error)
            ) from error


class EngineeringObjectProjection(RuntimeContract):
    object_id: str
    object_type: ObjectType
    origin: ObjectProjectionOrigin
    source_reference: str
    source_fingerprint: str

    _ids = field_validator("object_id", "source_reference")(
        lambda value: validate_identifier(value, field="object_projection")
    )
    _source_fingerprint = field_validator("source_fingerprint")(validate_fingerprint)


class EngineeringObjectFlowResult(RuntimeContract):
    """Typed result for the complete application-facing engineering-object flow."""

    request_id: str
    project_id: str
    tenant_id: str
    route: ApplicationRoute
    runtime_context_snapshot: EngineeringContextSnapshot
    application_context_snapshot: ApplicationContextSnapshot
    requirement: RequirementObject
    hardware_architecture: HardwareArchitectureObject
    circuit_intent: CircuitIntentObject
    schematic_design: SchematicDesignObject
    evidence: EvidenceObject
    proposal: ObjectProposal
    approval: ApprovalRecord | None = None
    apply_receipt: ObjectMutationRecord | None = None
    projections: tuple[EngineeringObjectProjection, ...]
    persistence_status: PersistenceStatus = PersistenceStatus.PROJECTION_ONLY
    evidence_status: EvidenceReferenceStatus = EvidenceReferenceStatus.UNVERIFIED

    _ids = field_validator("request_id", "project_id", "tenant_id")(
        lambda value: validate_identifier(value, field="flow_identity")
    )

    @model_validator(mode="after")
    def _bindings(self) -> EngineeringObjectFlowResult:
        objects = (
            self.requirement,
            self.hardware_architecture,
            self.circuit_intent,
            self.schematic_design,
            self.evidence,
        )
        if any(item.project_id != self.project_id for item in objects):
            raise ValueError("engineering object flow project binding mismatch")
        if self.proposal.project_id != self.project_id:
            raise ValueError("engineering object proposal project mismatch")
        if self.proposal.target_object_id != self.circuit_intent.object_id:
            raise ValueError("engineering object proposal target mismatch")
        if self.approval is not None and (
            self.approval.project_id != self.project_id
            or self.approval.proposal_id != self.proposal.proposal_id
            or self.approval.proposal_fingerprint != self.proposal.fingerprint
        ):
            raise ValueError("engineering object approval binding mismatch")
        if self.apply_receipt is not None and (
            self.apply_receipt.project_id != self.project_id
            or self.apply_receipt.proposal_id != self.proposal.proposal_id
        ):
            raise ValueError("engineering object apply receipt binding mismatch")
        return self


@runtime_checkable
class EngineeringObjectIntegrationPort(Protocol):
    def prepare(
        self,
        request: ApplicationQuestionRequest,
        routing_context: ApplicationRoutingContext,
        circuit_intent: CircuitIntentSpec,
        evidence_reference: EvidenceReceiptReference,
        *,
        route: ApplicationRouteDecision,
        schematic_plan: SchematicPlan | None = None,
        approved_circuit_intent: CircuitIntentObject | None = None,
    ) -> EngineeringObjectFlowResult: ...

    def review(
        self, result: EngineeringObjectFlowResult, *, reviewer: str
    ) -> EngineeringObjectFlowResult: ...

    def commit(self, result: EngineeringObjectFlowResult) -> EngineeringObjectFlowResult: ...


def _short_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _dedupe(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _text_value(value: object) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip()


class EngineeringObjectIntegrationService:
    """Adapter from Application Runtime context into the existing object runtime."""

    __slots__ = ("_context_provider", "_evidence_port", "_runtime")

    def __init__(
        self,
        runtime: EngineeringObjectRuntime,
        *,
        evidence_port: EvidenceReceiptReferencePort,
        context_provider: object,
    ) -> None:
        if not isinstance(runtime, EngineeringObjectRuntime):
            raise TypeError("engineering object runtime is invalid")
        if not isinstance(evidence_port, EvidenceReceiptReferencePort):
            raise TypeError("evidence receipt reference port is invalid")
        if not callable(getattr(context_provider, "get_context", None)):
            raise TypeError("engineering context provider is invalid")
        self._runtime = runtime
        self._evidence_port = evidence_port
        self._context_provider = context_provider

    @staticmethod
    def _binding_blocked(message: str) -> EngineeringObjectFlowError:
        return EngineeringObjectFlowError(
            EngineeringObjectFlowErrorCode.BINDING_BLOCKED,
            f"BLOCKED: {message}",
        )

    @staticmethod
    def _parse_binding_ref(value: str) -> tuple[str, str]:
        kind, separator, payload = value.partition(":")
        if not separator or not kind or not payload:
            raise ValueError("binding refs must use a namespace")
        return kind.casefold(), payload

    @staticmethod
    def _resolve_component_ref(
        payload: str,
        components: tuple[object, ...],
    ) -> tuple[str, str | None]:
        role: str | None = None
        component_id = payload
        if ":" in payload:
            role, component_id = payload.split(":", 1)
            role = role.strip().upper()
        matches = [
            item
            for item in components
            if component_id
            in {
                getattr(item, "instance_id", None),
                getattr(item, "design_truth_component_id", None),
            }
        ]
        if len(matches) != 1:
            raise ValueError(f"component ref is missing or ambiguous: {payload}")
        return matches[0].instance_id, role

    @staticmethod
    def _resolve_pin_ref(
        payload: str,
        pin_bindings: tuple[object, ...],
    ) -> tuple[str, str | None]:
        role: str | None = None
        pin_id = payload
        if ":" in payload:
            role, pin_id = payload.split(":", 1)
            role = role.strip().upper()
        matches = [item for item in pin_bindings if item.binding_id == pin_id]
        if len(matches) != 1:
            raise ValueError(f"pin ref is missing or ambiguous: {payload}")
        return f"{matches[0].component_id}:{matches[0].pin_name}", role

    @staticmethod
    def _semantic_net_names(
        item: object,
        signals: tuple[object, ...],
    ) -> set[str]:
        normalized = str(getattr(item, "name", "")).strip().upper()
        names = {normalized}
        signals_by_id = {
            str(getattr(signal, "signal_id", "")): signal for signal in signals
        }
        signal = signals_by_id.get(str(getattr(item, "signal_id", "")))
        protocol = str(getattr(signal, "protocol", "")).strip().upper()
        if protocol == "CAN":
            can_controller_names = {
                "CAN_TX": "CAN_TX",
                "MCU_CAN_TX": "CAN_TX",
                "CAN_RX": "CAN_RX",
                "MCU_CAN_RX": "CAN_RX",
            }
            canonical = can_controller_names.get(normalized)
            if canonical is not None:
                names.add(canonical)
            if normalized in {"CANH", "CANL"}:
                names.add(normalized)
        elif normalized.rsplit("_", 1)[-1] in {"SWDIO", "SWCLK", "NRST"}:
            names.add(normalized.rsplit("_", 1)[-1])
        return names

    @classmethod
    def _resolve_net_ref(
        cls,
        payload: str,
        nets: tuple[object, ...],
        signals: tuple[object, ...],
    ) -> str:
        requested = payload.strip().upper()

        matches = [
            item.net_id
            for item in nets
            if item.net_id == payload
            or requested in cls._semantic_net_names(item, signals)
        ]
        if requested in {"CAN_TX", "CAN_RX", "CANH", "CANL"}:
            matches = [
                item.net_id
                for item in nets
                if requested in cls._semantic_net_names(item, signals)
            ]
        if len(matches) != 1:
            raise ValueError(f"net ref is missing or ambiguous: {payload}")
        return matches[0]

    @staticmethod
    def _resolve_power_ref(payload: str, power_domains: tuple[object, ...]) -> str:
        requested = payload.strip().casefold()
        exact_matches = [item.domain_id for item in power_domains if item.domain_id == payload]
        if exact_matches:
            return exact_matches[0]
        matches = [
            item
            for item in power_domains
            if requested in {
                str(getattr(item, "name", "")).strip().casefold(),
                f"power-{str(getattr(item, 'name', '')).strip().casefold()}",
            }
        ]
        if len(matches) != 1:
            raise ValueError(f"power-domain ref is missing or ambiguous: {payload}")
        return payload

    @classmethod
    def _materialize_binding(
        cls,
        block: CircuitBlock,
        intent: CircuitIntentObject,
        plan: SchematicPlan,
        schematic: SchematicDesignObject,
    ) -> CircuitSchematicBinding:
        raw_refs = tuple(block.schematic_binding_refs)
        if not raw_refs:
            raise cls._binding_blocked(f"{block.block_id} has no explicit schematic refs")
        try:
            parsed = tuple(cls._parse_binding_ref(value) for value in raw_refs)
        except ValueError as error:
            raise cls._binding_blocked(str(error)) from error

        binding_ids = [payload for kind, payload in parsed if kind == "binding"]
        roles = [payload.strip().upper() for kind, payload in parsed if kind == "role"]
        if len(binding_ids) != 1 or len(set(binding_ids)) != 1:
            raise cls._binding_blocked(
                f"{block.block_id} requires exactly one explicit binding ref"
            )
        if len(roles) > 1 or (roles and len(set(roles)) != 1):
            raise cls._binding_blocked(f"{block.block_id} has ambiguous binding role")
        role = roles[0] if roles else None

        components = tuple(plan.component_instances)
        pins = tuple(plan.pin_bindings)
        nets = tuple(plan.nets)
        power_domains = tuple(plan.power_domains)
        component_ids: list[str] = []
        component_roles: dict[str, str] = {}
        pin_ids: list[str] = []
        selected_pin_binding_ids: set[str] = set()
        pin_roles: dict[str, str] = {}
        net_ids: list[str] = []
        power_ids: list[str] = []
        try:
            for kind, payload in parsed:
                if kind == "component":
                    component_id, component_role = cls._resolve_component_ref(payload, components)
                    component_ids.append(component_id)
                    if component_role is not None:
                        previous = component_roles.get(component_id)
                        if previous is not None and previous != component_role:
                            raise ValueError(f"component role is ambiguous: {payload}")
                        component_roles[component_id] = component_role
                elif kind == "pin":
                    pin_id, pin_role = cls._resolve_pin_ref(payload, pins)
                    pin_ids.append(pin_id)
                    selected_pin_binding_ids.add(payload.split(":", 1)[-1])
                    if pin_role is not None:
                        previous = pin_roles.get(pin_id)
                        if previous is not None and previous != pin_role:
                            raise ValueError(f"pin role is ambiguous: {payload}")
                        pin_roles[pin_id] = pin_role
                elif kind == "net":
                    net_ids.append(cls._resolve_net_ref(payload, nets, tuple(plan.signals)))
                elif kind == "power":
                    power_ids.append(cls._resolve_power_ref(payload, power_domains))
                elif kind not in {"binding", "role"}:
                    raise ValueError(f"unsupported binding ref namespace: {kind}")
        except ValueError as error:
            raise cls._binding_blocked(str(error)) from error

        component_ids = list(dict.fromkeys(component_ids))
        pin_ids = list(dict.fromkeys(pin_ids))
        net_ids = list(dict.fromkeys(net_ids))
        power_ids = list(dict.fromkeys(power_ids))
        if not component_ids or not pin_ids or not net_ids:
            raise cls._binding_blocked(
                f"{block.block_id} requires component, pin, and net refs"
            )
        if not set(component_ids) <= {
            item.instance_id for item in plan.component_instances
        }:
            raise cls._binding_blocked(f"{block.block_id} has an invalid component ref")
        if not set(pin_ids) <= set(schematic.pins):
            raise cls._binding_blocked(f"{block.block_id} has a pin outside the schematic projection")
        if not set(component_ids) <= {
            item.split(":", 1)[0] for item in schematic.symbols
        }:
            raise cls._binding_blocked(f"{block.block_id} has a component outside the schematic projection")
        if not set(net_ids) <= set(schematic.nets):
            raise cls._binding_blocked(f"{block.block_id} has a net outside the schematic projection")

        net_names = {
            item.net_id: str(getattr(item, "name", "")).strip().upper()
            for item in nets
            if item.net_id in net_ids
        }
        semantic_net_names = set(net_names.values())
        selected_nets = tuple(item for item in nets if item.net_id in net_names)
        selected_endpoints = {
            (endpoint.binding_id, endpoint.component_id, endpoint.pin_number)
            for net in selected_nets
            for endpoint in net.endpoints
        }
        for pin in pins:
            if pin.binding_id not in selected_pin_binding_ids:
                continue
            if pin.component_id not in component_ids:
                raise cls._binding_blocked(
                    f"{block.block_id} has a pin outside its selected components"
                )
            if (pin.binding_id, pin.component_id, pin.pin_number) not in selected_endpoints:
                raise cls._binding_blocked(
                    f"{block.block_id} has a pin outside its selected net endpoints"
                )
        for item in selected_nets:
            semantic_net_names.update(
                cls._semantic_net_names(item, tuple(plan.signals))
            )
        net_ids = list(
            dict.fromkeys(
                (
                    *net_ids,
                    *semantic_net_names,
                )
            )
        )
        required_nets = {
            "POWER": {"3V3", "GND"},
            "CAN": {"CAN_TX", "CAN_RX", "CANH", "CANL"},
            "SWD": {"SWDIO", "SWCLK", "GND"},
        }.get(role, set())
        if not required_nets <= semantic_net_names:
            raise cls._binding_blocked(
                f"{block.block_id} is missing required nets for {role or 'generic'}"
            )
        required_component_roles = {
            "CAN": {"MCU", "CAN_TRANSCEIVER", "CONNECTOR"},
            "SWD": {"MCU", "SWD_HEADER"},
        }.get(role, set())
        if required_component_roles and not required_component_roles <= set(component_roles.values()):
            raise cls._binding_blocked(
                f"{block.block_id} is missing explicit component roles for {role}"
            )
        if role == "POWER":
            requested_domains = {
                value.removeprefix("power-").casefold() for value in power_ids
            }
            domain_names = {
                item.name.casefold()
                for item in power_domains
                if item.domain_id in set(power_ids)
                or item.name.casefold() in requested_domains
            }
            if not {"3v3", "gnd"} <= domain_names:
                raise cls._binding_blocked(
                    f"{block.block_id} is missing 3V3/GND power domains"
                )

        return CircuitSchematicBinding.create(
            binding_id=binding_ids[0],
            circuit_intent_id=intent.object_id,
            circuit_intent_revision=intent.revision,
            circuit_intent_fingerprint=intent.fingerprint,
            circuit_block_id=block.block_id,
            component_instance_ids=tuple(component_ids),
            pin_binding_ids=tuple(pin_ids),
            net_ids=tuple(net_ids),
            power_domain_ids=tuple(power_ids),
        )

    def materialize_circuit_bindings(
        self,
        circuit_intent: CircuitIntentObject,
        schematic_plan: SchematicPlan,
        schematic_design: SchematicDesignObject,
        *,
        allow_proposal: bool = False,
    ) -> SchematicDesignObject:
        """Materialize explicit block-to-element bindings in an existing projection."""

        try:
            intent = CircuitIntentObject.model_validate(
                copy.deepcopy(circuit_intent.model_dump(mode="python")), strict=False
            )
            plan = SchematicPlan.model_validate(
                copy.deepcopy(schematic_plan.model_dump(mode="python")), strict=False
            )
            existing = SchematicDesignObject.model_validate(
                copy.deepcopy(schematic_design.model_dump(mode="python")), strict=False
            )
        except Exception as error:
            raise self._binding_blocked("binding input contract is invalid") from error
        allowed_statuses = {LifecycleStatus.APPROVED, LifecycleStatus.ACTIVE}
        if allow_proposal:
            allowed_statuses.add(LifecycleStatus.DRAFT)
        if intent.status not in allowed_statuses:
            raise self._binding_blocked("CircuitIntent is not approved or active")
        if plan.status is not SchematicPlanningStatus.APPROVED:
            raise self._binding_blocked("SchematicPlan is not approved")
        if plan.project_id != intent.project_id or existing.project_id != intent.project_id:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                "binding inputs belong to different projects",
            )
        if (
            plan.circuit_intent_id != intent.object_id
            or plan.circuit_intent_revision != intent.revision
            or plan.circuit_intent_fingerprint != intent.fingerprint
        ):
            raise self._binding_blocked("CircuitIntent fingerprint or revision does not match the approved SchematicPlan")
        blocks_by_id = {item.block_id: item for item in intent.blocks}
        if len(blocks_by_id) != len(intent.blocks):
            raise self._binding_blocked("CircuitIntent contains duplicate block identities")
        bindings = tuple(
            self._materialize_binding(blocks_by_id[block_id], intent, plan, existing)
            for block_id in sorted(blocks_by_id)
        )
        if not bindings:
            raise self._binding_blocked("CircuitIntent has no structured blocks")
        return SchematicDesignObject.create(
            object_id=existing.object_id,
            project_id=existing.project_id,
            revision=existing.revision,
            status=existing.status,
            symbols=existing.symbols,
            pins=existing.pins,
            nets=existing.nets,
            circuit_bindings=bindings,
            erc_result_evidence_id=existing.erc_result_evidence_id,
            artifact_ref=existing.artifact_ref,
            artifact_fingerprint=existing.artifact_fingerprint,
            dependency_fingerprint=existing.dependency_fingerprint,
            provenance=existing.provenance,
        )

    @staticmethod
    def _check_route(
        request: ApplicationQuestionRequest, route: ApplicationRouteDecision
    ) -> None:
        if (
            route.request_id != request.request_id
            or route.request_fingerprint != request.fingerprint
        ):
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.ROUTE_REJECTED,
                "route is not bound to the application request",
            )
        if route.route is not ApplicationRoute.GREENFIELD_DESIGN and route.reason_code not in {
            "engineering_design",
            "hardware_design",
            "schematic_design",
        }:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.ROUTE_REJECTED,
                "engineering object flow requires an explicit design route",
            )

    @staticmethod
    def _check_source_context(
        request: ApplicationQuestionRequest,
        routing_context: ApplicationRoutingContext,
    ) -> tuple[RequirementContext, object, DesignTruth | None, str, str]:
        requirement_context = routing_context.requirement_context
        if requirement_context is None:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.CONTEXT_UNAVAILABLE,
                "RequirementContext is required",
            )
        if requirement_context.project_id != request.project_id:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                "RequirementContext belongs to another project",
            )
        if requirement_context.effective_status is RequirementStatus.DRAFT:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.STALE_CONTEXT,
                "RequirementContext is not ready for design",
            )
        if (
            not requirement_context.requirements
            and not requirement_context.constraints
            and not requirement_context.standard_fields
            and not requirement_context.proposed_items
        ):
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.CONTEXT_UNAVAILABLE,
                "RequirementContext has no project requirements",
            )
        if requirement_context.open_questions or requirement_context.conflicts:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.STALE_CONTEXT,
                "RequirementContext still has open questions or conflicts",
            )

        architecture = routing_context.architecture_design
        truth = routing_context.design_truth
        if architecture is None and truth is None:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.CONTEXT_UNAVAILABLE,
                "ArchitectureDesign or DesignTruth is required",
            )
        if architecture is not None:
            if architecture.project_id != request.project_id:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                    "ArchitectureDesign belongs to another project",
                )
            if architecture.status not in {
                ArchitectureDesignStatus.APPROVED,
                ArchitectureDesignStatus.COMPLETED,
            }:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.STALE_CONTEXT,
                    "ArchitectureDesign is not approved",
                )
        if truth is not None:
            if truth.project_id != request.project_id:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                    "DesignTruth belongs to another project",
                )
            if getattr(truth, "tenant_id", request.tenant_id) != request.tenant_id:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.TENANT_IDENTITY_MISMATCH,
                    "DesignTruth belongs to another tenant",
                )
            if _text_value(truth.status) != "APPROVED":
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.STALE_CONTEXT,
                    "DesignTruth is not approved",
                )
        source = architecture if architecture is not None else truth
        source_fingerprint = str(getattr(source, "fingerprint", ""))
        source_reference = (
            f"{request.project_id}:architecture:{architecture.architecture_id}"
            if architecture is not None
            else f"{request.project_id}:design-truth:{truth.truth_id}"
        )
        return (
            requirement_context,
            source,
            truth,
            source_reference,
            source_fingerprint,
        )

    @staticmethod
    def _requirement_projection(
        request: ApplicationQuestionRequest, context: RequirementContext
    ) -> RequirementObject:
        entries = (
            *context.standard_fields,
            *context.requirements,
            *context.constraints,
            *context.proposed_items,
        )
        descriptions = tuple(
            f"{_text_value(getattr(item, 'category', 'requirement'))}: "
            f"{_text_value(getattr(item, 'normalized_value', None) or getattr(item, 'value', None))}"
            for item in entries
        )
        descriptions = _dedupe(descriptions)
        criteria = tuple(
            f"Given {item.given}; When {item.when}; Then {item.then}"
            for item in context.acceptance_criteria
        )
        verification = _dedupe(
            [item.verification for item in context.acceptance_criteria]
        )
        return RequirementObject.create(
            object_id=_short_id("requirement", request.project_id, context.fingerprint),
            project_id=request.project_id,
            description="; ".join(descriptions),
            source=f"RequirementContext:{context.conversation_id}",
            acceptance_criteria=criteria,
            verification_method=(
                "; ".join(verification) or "RequirementContext review"
            ),
            dependency_fingerprint=context.fingerprint,
            provenance=Provenance.create(
                source="APPLICATION_REQUIREMENT_CONTEXT",
                source_ref=f"{request.project_id}:requirement-context:{context.conversation_id}",
                created_by=request.requester,
                created_at=request.requested_at,
                source_fingerprint=context.fingerprint,
                derived_from=(request.fingerprint,),
            ),
        )

    @staticmethod
    def _hardware_projection(
        request: ApplicationQuestionRequest,
        source: object,
        source_reference: str,
        source_fingerprint: str,
        routing_context: ApplicationRoutingContext,
    ) -> HardwareArchitectureObject:
        architecture_components = tuple(
            getattr(source, "system_components", ())
        )
        truth_components = tuple(getattr(source, "components", ()))
        components = architecture_components or truth_components
        if not components:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.CONTEXT_UNAVAILABLE,
                "hardware source contains no components",
            )
        mcu_candidates: list[str] = []
        component_names: list[str] = []
        for component in components:
            name = _text_value(getattr(component, "name", None))
            category = _text_value(getattr(component, "category", None))
            if name:
                component_names.append(name)
            if any(
                marker in f"{name} {category}".casefold()
                for marker in ("mcu", "controller", "stm32", "esp32")
            ):
                mcu_candidates.append(name)
        if not mcu_candidates:
            mcu_candidates = component_names[:1]
        if not mcu_candidates:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.CONTEXT_UNAVAILABLE,
                "hardware source contains no controller identity",
            )

        peripherals = tuple(
            _text_value(getattr(item, "peripheral", None) or getattr(item, "binding_id", None))
            for item in tuple(getattr(source, "peripherals", ()))
        )
        interfaces = tuple(
            _text_value(getattr(item, "protocol", None) or getattr(item, "name", None) or getattr(item, "interface_id", None))
            for item in tuple(getattr(source, "interfaces", ()))
        )
        power_domains = tuple(
            _text_value(getattr(item, "name", None) or getattr(item, "power_domain_id", None))
            for item in tuple(getattr(source, "power_domains", ()))
        )
        constraints = tuple(getattr(routing_context, "project_type", None) or ())
        constraints = constraints if isinstance(constraints, tuple) else ()
        constraints = _dedupe(
            [
                *constraints,
                *tuple(_text_value(item) for item in getattr(source, "constraints", ())),
            ]
        )
        return HardwareArchitectureObject.create(
            object_id=_short_id("hardware", request.project_id, source_fingerprint),
            project_id=request.project_id,
            mcu=mcu_candidates[0],
            peripherals=_dedupe(list(peripherals)),
            interfaces=_dedupe(list(interfaces)),
            power_domains=_dedupe(list(power_domains)),
            constraints=constraints,
            dependency_fingerprint=source_fingerprint,
            provenance=Provenance.create(
                source="APPLICATION_HARDWARE_PROJECTION",
                source_ref=source_reference,
                created_by=request.requester,
                created_at=request.requested_at,
                source_fingerprint=source_fingerprint,
                derived_from=(request.fingerprint,),
            ),
        )

    def _schematic_projection(
        self,
        request: ApplicationQuestionRequest,
        source: object,
        source_reference: str,
        source_fingerprint: str,
        evidence_id: str,
        artifact_ref: str,
        artifact_fingerprint: str,
        revision: str,
        *,
        schematic_plan: SchematicPlan | None = None,
        circuit_intent: CircuitIntentObject | None = None,
    ) -> SchematicDesignObject:
        if schematic_plan is not None:
            symbols = tuple(
                f"{item.instance_id}:{item.symbol}"
                for item in schematic_plan.component_instances
            )
            pins = tuple(
                f"{item.component_id}:{item.pin_name}"
                for item in schematic_plan.pin_bindings
            )
            nets = tuple(item.net_id for item in schematic_plan.nets)
            nets += tuple(
                alias
                for item in schematic_plan.nets
                for alias in (
                    item.name,
                    (
                        f"CAN_{item.name.rsplit('_', 1)[-1]}"
                        if item.name.rsplit("_", 1)[-1] in {"TX", "RX"}
                        else item.name.rsplit("_", 1)[-1]
                        if item.name.rsplit("_", 1)[-1]
                        in {"CANH", "CANL", "SWDIO", "SWCLK", "NRST"}
                        else None
                    ),
                )
                if alias
            )
        else:
            symbols = tuple(
                _text_value(getattr(item, "name", None) or getattr(item, "component_id", None))
                for item in tuple(
                    getattr(source, "components", ()) or getattr(source, "system_components", ())
                )
            )
            pins = tuple(
                f"{_text_value(getattr(item, 'component_id', ''))}:{_text_value(getattr(item, 'pin_name', None) or getattr(item, 'pin_id', None))}"
                for item in tuple(getattr(source, "pins", ()))
            )
            nets = tuple(
                _text_value(getattr(item, "binding_id", None) or getattr(item, "signal", None) or getattr(item, "net_id", None))
                for item in tuple(getattr(source, "nets", ()))
            )
            if not nets:
                nets = tuple(
                    _text_value(getattr(item, "protocol", None) or getattr(item, "interface_id", None))
                    for item in tuple(getattr(source, "interfaces", ()))
                )
        projected = SchematicDesignObject.create(
            object_id=_short_id("schematic", request.project_id, source_fingerprint),
            project_id=request.project_id,
            revision=revision,
            symbols=_dedupe(list(symbols)),
            pins=_dedupe(list(pins)),
            nets=_dedupe(list(nets)),
            erc_result_evidence_id=evidence_id,
            dependency_fingerprint=source_fingerprint,
            artifact_ref=artifact_ref,
            artifact_fingerprint=artifact_fingerprint,
            provenance=Provenance.create(
                source="APPLICATION_SCHEMATIC_PROJECTION",
                source_ref=source_reference,
                created_by=request.requester,
                created_at=request.requested_at,
                source_fingerprint=source_fingerprint,
                derived_from=(request.fingerprint,),
            ),
        )
        if schematic_plan is not None and circuit_intent is not None:
            return self.materialize_circuit_bindings(
                circuit_intent,
                schematic_plan,
                projected,
                allow_proposal=True,
            )
        return projected

    def _active_projection(self, obj):
        if self._runtime.registry.contains(obj.object_id):
            current = self._runtime.get_active_revision(obj.object_id)
            if current.project_id != obj.project_id:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                    "runtime projection belongs to another project",
                )
            if current.fingerprint != obj.fingerprint:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.STALE_CONTEXT,
                    "runtime projection fingerprint is stale",
                )
            if current.status is not LifecycleStatus.ACTIVE:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.STALE_CONTEXT,
                    "runtime projection is not active",
                )
            return current
        self._runtime.register_object(obj)
        current = obj
        for status, actor in (
            (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
            (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
            (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
        ):
            current = self._runtime.transition_object(
                obj.object_id, obj.revision, status, actor
            )
        return current

    def _resolve_receipt(
        self,
        request: ApplicationQuestionRequest,
        reference: EvidenceReceiptReference,
    ) -> EvidenceReceiptReference:
        if reference.project_id != request.project_id:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                "evidence reference belongs to another project",
            )
        if reference.tenant_id != request.tenant_id:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.TENANT_IDENTITY_MISMATCH,
                "evidence reference belongs to another tenant",
            )
        if reference.verification_status is EvidenceReferenceStatus.VERIFIED:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE,
                "caller cannot self-report VERIFIED evidence",
            )
        try:
            resolved = self._evidence_port.resolve(
                copy.deepcopy(request), copy.deepcopy(reference)
            )
        except EngineeringObjectFlowError:
            raise
        except Exception as error:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE,
                "evidence receipt reference is unavailable",
            ) from error
        if not isinstance(resolved, EvidenceReceiptReference):
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE,
                "evidence receipt adapter returned an invalid reference",
            )
        if (
            resolved.project_id != request.project_id
            or resolved.tenant_id != request.tenant_id
            or resolved.reference_id != reference.reference_id
            or resolved.receipt_id != reference.receipt_id
        ):
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE,
                "evidence receipt identity changed at the adapter boundary",
            )
        return resolved

    def _resolve_application_context(
        self, request: ApplicationQuestionRequest
    ) -> ApplicationContextSnapshot:
        try:
            resolved = self._context_provider.get_context(
                EngineeringContextRequest(
                    project_id=request.project_id,
                    tenant_id=request.tenant_id,
                    task=request.question,
                    constraints=request.constraints,
                    external_search=request.external_search,
                )
            )
        except Exception as error:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.CONTEXT_UNAVAILABLE,
                "EngineeringContextService is unavailable",
            ) from error
        if not isinstance(resolved, ApplicationContextSnapshot):
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.CONTEXT_UNAVAILABLE,
                "EngineeringContextService returned no typed snapshot",
            )
        if resolved.project_id != request.project_id:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                "EngineeringContextService returned another project",
            )
        return resolved

    def prepare(
        self,
        request: ApplicationQuestionRequest,
        routing_context: ApplicationRoutingContext,
        circuit_intent: CircuitIntentSpec,
        evidence_reference: EvidenceReceiptReference,
        *,
        route: ApplicationRouteDecision,
        schematic_plan: SchematicPlan | None = None,
        approved_circuit_intent: CircuitIntentObject | None = None,
    ) -> EngineeringObjectFlowResult:
        checked_request = ApplicationQuestionRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python")), strict=False
        )
        try:
            checked_context = ApplicationRoutingContext.model_validate(
                copy.deepcopy(routing_context.model_dump(mode="python")), strict=False
            )
        except Exception as error:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.STALE_CONTEXT,
                "application routing context is stale or tampered",
            ) from error
        checked_spec = CircuitIntentSpec.model_validate(
            copy.deepcopy(circuit_intent.model_dump(mode="python")), strict=False
        )
        checked_plan: SchematicPlan | None = None
        if schematic_plan is not None:
            try:
                checked_plan = SchematicPlan.model_validate(
                    copy.deepcopy(schematic_plan.model_dump(mode="python")), strict=False
                )
            except Exception as error:
                raise self._binding_blocked("approved SchematicPlan is invalid") from error
            if checked_plan.project_id != checked_request.project_id:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                    "SchematicPlan belongs to another project",
                )
        checked_approved_intent: CircuitIntentObject | None = None
        if approved_circuit_intent is not None:
            try:
                checked_approved_intent = CircuitIntentObject.model_validate(
                    copy.deepcopy(approved_circuit_intent.model_dump(mode="python")),
                    strict=False,
                )
            except Exception as error:
                raise self._binding_blocked(
                    "approved CircuitIntent snapshot is invalid"
                ) from error
            if checked_approved_intent.project_id != checked_request.project_id:
                raise EngineeringObjectFlowError(
                    EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                    "approved CircuitIntent belongs to another project",
                )
            if checked_approved_intent.status not in {
                LifecycleStatus.APPROVED,
                LifecycleStatus.ACTIVE,
            }:
                raise self._binding_blocked(
                    "approved CircuitIntent snapshot is not approved or active"
                )
        if not isinstance(evidence_reference, EvidenceReceiptReference):
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE,
                "an existing Evidence/Receipt reference is required",
            )
        try:
            checked_reference = EvidenceReceiptReference.model_validate(
                copy.deepcopy(evidence_reference.model_dump(mode="python")), strict=False
            )
        except Exception as error:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE,
                "evidence receipt reference is invalid",
            ) from error
        self._check_route(checked_request, route)
        if self._runtime.project_id not in {None, checked_request.project_id}:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH,
                "engineering runtime belongs to another project",
            )
        requirement_context, source, truth, source_reference, source_fingerprint = (
            self._check_source_context(checked_request, checked_context)
        )
        application_context = self._resolve_application_context(checked_request)
        receipt = self._resolve_receipt(checked_request, checked_reference)
        if receipt.verification_status is not EvidenceReferenceStatus.VERIFIED:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE,
                "evidence receipt is not qualified by a canonical resolver",
            )
        if (
            receipt.artifact_ref is None
            or receipt.artifact_fingerprint is None
            or receipt.schematic_revision is None
        ):
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE,
                "qualified evidence is missing schematic binding",
            )
        evidence_id = _short_id(
            "evidence", checked_request.project_id, receipt.reference_id, receipt.receipt_id
        )
        requirement = self._active_projection(
            self._requirement_projection(checked_request, requirement_context)
        )
        hardware = self._active_projection(
            self._hardware_projection(
                checked_request,
                source,
                source_reference,
                source_fingerprint,
                checked_context,
            )
        )
        checked_spec_fingerprint = canonical_fingerprint(checked_spec)
        intent_dependency_fingerprint = canonical_fingerprint(
            ContextRequest.create(
                project_id=checked_request.project_id,
                target_object_type=ObjectType.CIRCUIT_INTENT,
                required_object_ids=(requirement.object_id, hardware.object_id),
                constraints=checked_request.constraints,
            )
        )
        circuit_snapshot = checked_approved_intent or CircuitIntentObject.create(
            object_id=_short_id(
                "circuit-intent",
                checked_request.project_id,
                checked_spec.function,
                checked_spec_fingerprint,
            ),
            project_id=checked_request.project_id,
            function=checked_spec.function,
            topology=checked_spec.topology,
            parameters=checked_spec.parameters,
            calculation=checked_spec.calculation,
            assumptions=checked_spec.assumptions,
            risks=checked_spec.risks,
            blocks=checked_spec.blocks,
            decisions=checked_spec.decisions,
            calculations=checked_spec.calculations,
            verification_requirements=checked_spec.verification_requirements,
            legacy_verification_requirements=checked_spec.legacy_verification_requirements,
            dependency_fingerprint=intent_dependency_fingerprint,
            provenance=Provenance.create(
                source="APPLICATION_CIRCUIT_INTENT_SPEC",
                source_ref=f"{checked_request.project_id}:circuit-intent:{checked_request.request_id}",
                created_by=checked_request.requester,
                created_at=checked_request.requested_at,
                source_fingerprint=checked_spec_fingerprint,
                derived_from=(
                    requirement.fingerprint,
                    hardware.fingerprint,
                ),
            ),
        )
        circuit = circuit_snapshot.model_copy(update={"status": LifecycleStatus.DRAFT})
        schematic = self._active_projection(
            self._schematic_projection(
                checked_request,
                truth or source,
                source_reference,
                receipt.artifact_fingerprint,
                evidence_id,
                receipt.artifact_ref,
                receipt.artifact_fingerprint,
                receipt.schematic_revision,
                schematic_plan=checked_plan,
                circuit_intent=circuit_snapshot,
            )
        )
        evidence = self._active_projection(
            EvidenceObject.create(
                object_id=evidence_id,
                project_id=checked_request.project_id,
                source=receipt.source,
                tool=receipt.tool,
                result=receipt.execution_result or receipt.result_summary,
                timestamp=receipt.occurred_at,
                scope=receipt.scope,
                canonical_evidence_ref=receipt.receipt_id,
                qualification_status=EvidenceQualificationStatus.QUALIFIED,
                subject_object_id=schematic.object_id,
                subject_revision=schematic.revision,
                subject_fingerprint=schematic.fingerprint,
                subject_artifact_ref=schematic.artifact_ref,
                subject_artifact_fingerprint=schematic.artifact_fingerprint,
                dependency_fingerprint=receipt.artifact_fingerprint,
                provenance=Provenance.create(
                    source="APPLICATION_EVIDENCE_REFERENCE",
                    source_ref=f"{checked_request.project_id}:receipt:{receipt.receipt_id}",
                    created_by=checked_request.requester,
                    created_at=checked_request.requested_at,
                    source_fingerprint=receipt.artifact_fingerprint,
                    derived_from=(checked_request.fingerprint,),
                ),
            )
        )
        self._runtime.add_relation(
            requirement, RelationType.SATISFIED_BY, hardware, (evidence.object_id,)
        )
        self._runtime.add_relation(
            schematic, RelationType.VALIDATED_BY, evidence, (evidence.object_id,)
        )
        context_snapshot = self._runtime.resolve_context(
            ContextRequest.create(
                project_id=checked_request.project_id,
                target_object_type=ObjectType.CIRCUIT_INTENT,
                required_object_ids=(
                    requirement.object_id,
                    hardware.object_id,
                    schematic.object_id,
                ),
                evidence_ids=(evidence.object_id,),
                constraints=checked_request.constraints,
            )
        )
        proposal = self._runtime.create_proposal(
            circuit,
            context_fingerprint=context_snapshot.fingerprint,
            proposal_id=_short_id(
                "proposal", checked_request.project_id, circuit.object_id, context_snapshot.fingerprint
            ),
            proposer=checked_request.requester,
        )
        projections = (
            EngineeringObjectProjection(
                object_id=requirement.object_id,
                object_type=requirement.object_type,
                origin=ObjectProjectionOrigin.PROJECTION_REFERENCE,
                source_reference=requirement.provenance.source_ref,
                source_fingerprint=requirement.provenance.source_fingerprint or requirement.fingerprint,
            ),
            EngineeringObjectProjection(
                object_id=hardware.object_id,
                object_type=hardware.object_type,
                origin=ObjectProjectionOrigin.PROJECTION_REFERENCE,
                source_reference=hardware.provenance.source_ref,
                source_fingerprint=hardware.provenance.source_fingerprint or hardware.fingerprint,
            ),
            EngineeringObjectProjection(
                object_id=circuit.object_id,
                object_type=circuit.object_type,
                origin=ObjectProjectionOrigin.NEW_PROPOSAL,
                source_reference=circuit.provenance.source_ref,
                source_fingerprint=circuit.provenance.source_fingerprint or circuit.fingerprint,
            ),
            EngineeringObjectProjection(
                object_id=schematic.object_id,
                object_type=schematic.object_type,
                origin=ObjectProjectionOrigin.PROJECTION_REFERENCE,
                source_reference=schematic.provenance.source_ref,
                source_fingerprint=schematic.provenance.source_fingerprint or schematic.fingerprint,
            ),
            EngineeringObjectProjection(
                object_id=evidence.object_id,
                object_type=evidence.object_type,
                origin=ObjectProjectionOrigin.PROJECTION_REFERENCE,
                source_reference=evidence.provenance.source_ref,
                source_fingerprint=evidence.provenance.source_fingerprint or evidence.fingerprint,
            ),
        )
        return EngineeringObjectFlowResult(
            request_id=checked_request.request_id,
            project_id=checked_request.project_id,
            tenant_id=checked_request.tenant_id,
            route=route.route,
            runtime_context_snapshot=context_snapshot,
            requirement=requirement,
            hardware_architecture=hardware,
            circuit_intent=circuit,
            schematic_design=schematic,
            evidence=evidence,
            proposal=proposal,
            projections=projections,
            persistence_status=PersistenceStatus.PROJECTION_ONLY,
            evidence_status=receipt.verification_status,
            application_context_snapshot=application_context,
        )

    @staticmethod
    def _checked_result(result: EngineeringObjectFlowResult) -> EngineeringObjectFlowResult:
        if type(result) is not EngineeringObjectFlowResult:
            raise TypeError("engineering object flow result is invalid")
        try:
            return EngineeringObjectFlowResult.model_validate(
                copy.deepcopy(result.model_dump(mode="python")), strict=False
            )
        except Exception as error:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROPOSAL_TAMPERED,
                "engineering object flow result or proposal fingerprint is invalid",
            ) from error

    def review(
        self, result: EngineeringObjectFlowResult, *, reviewer: str
    ) -> EngineeringObjectFlowResult:
        checked = self._checked_result(result)
        if checked.approval is not None:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.APPROVAL_REQUIRED,
                "proposal has already been reviewed",
            )
        try:
            approval = self._runtime.approve_proposal(
                checked.proposal.proposal_id,
                approval_id=_short_id("approval", checked.proposal.proposal_id, reviewer),
                reviewer=validate_identifier(reviewer, field="reviewer"),
            )
        except EngineeringObjectFlowError:
            raise
        except Exception as error:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROPOSAL_TAMPERED,
                "proposal could not be approved",
            ) from error
        return checked.model_copy(
            update={
                "proposal": checked.proposal.model_copy(
                    update={"status": ProposalStatus.APPROVED}
                ),
                "approval": approval,
            }
        )

    def commit(self, result: EngineeringObjectFlowResult) -> EngineeringObjectFlowResult:
        checked = self._checked_result(result)
        if checked.approval is None or _text_value(checked.proposal.status) != "APPROVED":
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.APPROVAL_REQUIRED,
                "proposal requires an approval before apply",
            )
        if checked.approval.proposal_fingerprint != checked.proposal.fingerprint:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROPOSAL_TAMPERED,
                "approval does not match proposal fingerprint",
            )
        try:
            receipt = self._runtime.apply_proposal(checked.proposal.proposal_id)
            circuit = self._runtime.get_active_revision(checked.circuit_intent.object_id)
            if not isinstance(circuit, CircuitIntentObject):
                raise TypeError("applied object is not CircuitIntent")
            self._runtime.add_relation(
                checked.hardware_architecture,
                RelationType.IMPLEMENTED_BY,
                circuit,
                (checked.evidence.object_id,),
            )
            self._runtime.add_relation(
                circuit,
                RelationType.EXPRESSED_BY,
                checked.schematic_design,
                (checked.evidence.object_id,),
            )
            snapshot = self._runtime.resolve_context(
                ContextRequest.create(
                    project_id=checked.project_id,
                    target_object_type=ObjectType.CIRCUIT_INTENT,
                    required_object_ids=(
                        checked.requirement.object_id,
                        checked.hardware_architecture.object_id,
                        circuit.object_id,
                        checked.schematic_design.object_id,
                    ),
                    evidence_ids=(checked.evidence.object_id,),
                )
            )
        except EngineeringObjectFlowError:
            raise
        except Exception as error:
            raise EngineeringObjectFlowError(
                EngineeringObjectFlowErrorCode.PROPOSAL_TAMPERED,
                "proposal apply failed at the existing runtime boundary",
            ) from error
        return checked.model_copy(
            update={
                "circuit_intent": circuit,
                "proposal": checked.proposal.model_copy(update={"status": ProposalStatus.APPLIED}),
                "apply_receipt": receipt,
                "runtime_context_snapshot": snapshot,
            }
        )


__all__ = (
    "CircuitIntentSpec",
    "EngineeringObjectFlowError",
    "EngineeringObjectFlowErrorCode",
    "EngineeringObjectFlowResult",
    "EngineeringObjectIntegrationPort",
    "EngineeringObjectIntegrationService",
    "EngineeringObjectProjection",
    "EvidenceReceiptReference",
    "EvidenceReceiptReferencePort",
    "EvidenceReferenceStatus",
    "ObjectProjectionOrigin",
    "OperationReceiptEvidenceResolver",
    "PersistenceStatus",
)
