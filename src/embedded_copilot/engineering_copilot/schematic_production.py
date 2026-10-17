from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime

from .common import ZERO, ident, text, validate_copy
from .contracts import (
    AuthorizationDecisionStatus,
    DesignTruth,
    DesignTruthOrigin,
    DesignTruthStatus,
    ExecutionAuthorization,
    ExecutionIntent,
    ExecutionRequestStatus,
    HardwareDesignSpecification,
    ImplementationArtifactType,
    ImplementationSpecification,
    SchematicComponentProposal,
    SchematicErcIssueClassification,
    SchematicErcResult,
    SchematicErcStatus,
    SchematicNetProposal,
    SchematicPinProposal,
    SchematicPowerProposal,
    SchematicProductionRequest,
    SchematicProductionState,
    SchematicProductionStatus,
    SchematicProposal,
    SchematicProposalDecision,
    SchematicProposalDecisionStatus,
    SchematicReadinessResult,
    SchematicReadinessStatus,
    SchematicRepairAssessment,
    SchematicRepairAssessmentKind,
    SchematicRepairProposal,
)
from .hardware_selection import HardwareSelectionState, HardwareSelectionStatus
from .schematic_planning import SchematicPlan, SchematicPlanningStatus, _known


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _refs(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    result = tuple(ident(value, field.removesuffix("s")) for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must be unique")
    return result


def _schematic_task(implementation: ImplementationSpecification):
    artifacts = {item.artifact_id: item for item in implementation.expected_artifacts}
    candidates = []
    for task in implementation.tasks:
        if any(
            artifact_id == "SCHEMATIC"
            or artifacts.get(artifact_id, None) is not None
            and artifacts[artifact_id].artifact_type is ImplementationArtifactType.SCHEMATIC
            for artifact_id in task.expected_artifacts
        ):
            candidates.append(task)
    return tuple(candidates)


def _schematic_target_refs(implementation: ImplementationSpecification) -> tuple[str, ...]:
    """Return explicit schematic artifact references without choosing a target."""

    artifacts = {item.artifact_id: item for item in implementation.expected_artifacts}
    refs: list[str] = []
    for task in implementation.tasks:
        for artifact_ref in task.expected_artifacts:
            artifact = artifacts.get(artifact_ref)
            if artifact_ref == "SCHEMATIC" or (
                artifact is not None and artifact.artifact_type is ImplementationArtifactType.SCHEMATIC
            ):
                refs.append(artifact_ref)
    return tuple(dict.fromkeys(refs))


def _approved_schematic_plan(value: object | None):
    """Validate the rich G2 plan without making the production contract own it."""

    if value is None:
        return None
    from .schematic_planning import SchematicPlan

    if not isinstance(value, SchematicPlan):
        raise TypeError("approved schematic plan is invalid")
    return SchematicPlan.model_validate(value.model_dump(mode="python"), strict=False)


def evaluate_schematic_readiness(
    design_truth: DesignTruth | None,
    hardware_design: HardwareDesignSpecification | None,
    implementation: ImplementationSpecification | None,
    approved_schematic_plan: object | None = None,
) -> SchematicReadinessResult:
    """Check only confirmed electrical facts; never infer missing values."""

    truth_fp = ZERO if design_truth is None else design_truth.fingerprint
    missing: list[str] = []
    affected: list[str] = []
    status = SchematicReadinessStatus.READY_FOR_GENERATION
    ambiguous_target = False
    plan = None
    if approved_schematic_plan is not None:
        try:
            plan = _approved_schematic_plan(approved_schematic_plan)
        except (TypeError, ValueError):
            status = SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
            missing.append("approved_schematic_plan")
    if plan is not None:
        plan_id = plan.plan_id
        plan_fp = plan.fingerprint
        if (
            design_truth is None
            or hardware_design is None
            or plan.project_id != design_truth.project_id
            or plan.project_id != hardware_design.project_id
            or not any(
                item.source_type == "SCHEMATIC_PLAN"
                and item.source_id == plan.plan_id
                and item.source_fingerprint == plan.fingerprint
                for item in design_truth.provenance
            )
        ):
            status = SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
            missing.append("schematic_plan_design_truth_binding")
    else:
        plan_id = None
        plan_fp = None
    if design_truth is None or design_truth.origin is DesignTruthOrigin.REVERSE or design_truth.status is not DesignTruthStatus.APPROVED:
        status = SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
        missing.append("approved_create_design_truth")
    elif not design_truth.components:
        status = SchematicReadinessStatus.BLOCKED_MISSING_COMPONENT
        missing.append("components")
    else:
        component_ids = tuple(item.component_id for item in design_truth.components)
        if len(component_ids) != len(set(component_ids)):
            status = SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
            missing.append("unique_component_identity")
        elif any(not item.symbol_binding for item in design_truth.components):
            status = SchematicReadinessStatus.BLOCKED_MISSING_SYMBOL
            missing.extend(item.component_id for item in design_truth.components if not item.symbol_binding)
        elif any(
            item.symbol_binding is None or not re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+", item.symbol_binding)
            for item in design_truth.components
        ):
            status = SchematicReadinessStatus.BLOCKED_UNSUPPORTED_KICAD_MAPPING
            missing.extend(item.component_id for item in design_truth.components if item.symbol_binding)
        pins = tuple(item for item in design_truth.pins if item.component_id in component_ids)
        pin_components = {item.component_id for item in pins}
        pin_refs = tuple((item.component_id, item.pin_number) for item in pins)
        if status is SchematicReadinessStatus.READY_FOR_GENERATION and (
            not pins
            or pin_components != set(component_ids)
            or len(pin_refs) != len(set(pin_refs))
            or any(item.component_id not in component_ids or not item.pin_number or not item.pin_name for item in design_truth.pins)
        ):
            status = SchematicReadinessStatus.BLOCKED_MISSING_PIN
            missing.append("pin_identity")
        signals = {item.signal_id for item in design_truth.signals}
        nets = tuple(design_truth.nets)
        required_pins = tuple(item for item in pins if not getattr(item, "intentionally_unconnected", False))
        pin_by_ref = {
            f"{item.component_id}.{item.pin_number}": item
            for item in pins
        }
        if status is SchematicReadinessStatus.READY_FOR_GENERATION and (
            any(item.signal_id is None or item.signal_id not in signals for item in required_pins)
        ):
            status = SchematicReadinessStatus.BLOCKED_MISSING_NET
            missing.append("pin_signal_binding")
        if status is SchematicReadinessStatus.READY_FOR_GENERATION and (
            not nets
            or any(
                item.signal not in signals
                or len(item.nodes) < 2
                or any(
                    node not in pin_by_ref
                    or pin_by_ref[node].signal_id != item.signal
                    for node in item.nodes
                )
                for item in nets
            )
        ):
            status = SchematicReadinessStatus.BLOCKED_MISSING_NET
            missing.append("net_connectivity")
        if status is SchematicReadinessStatus.READY_FOR_GENERATION and (
            not design_truth.power_domains
            or any(
                not item.voltage
                or not item.ground_net
                or item.ground_net not in {net.net_name for net in nets}
                or any(component_ref not in component_ids for component_ref in item.component_refs)
                for item in design_truth.power_domains
            )
        ):
            status = SchematicReadinessStatus.BLOCKED_MISSING_POWER_FACT
            missing.extend(
                item.power_domain_id
                for item in design_truth.power_domains
                if (
                    not item.voltage
                    or not item.ground_net
                    or item.ground_net not in {net.net_name for net in nets}
                    or any(component_ref not in component_ids for component_ref in item.component_refs)
                )
            )
        affected.extend(item.component_id for item in design_truth.components)
    if hardware_design is None or implementation is None:
        status = SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
        missing.append("hardware_and_implementation_binding")
    elif design_truth is not None and (
        hardware_design.project_id != design_truth.project_id
        or implementation.project_id != design_truth.project_id
        or implementation.hardware_design_id != hardware_design.hardware_spec_id
    ):
        status = SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
        missing.append("project_identity_binding")
    if hardware_design is not None and hardware_design.schematic_plan is None:
        status = SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
        missing.append("schematic_design_plan")
    if implementation is not None and (
        len(_schematic_task(implementation)) != 1
        or len(_schematic_target_refs(implementation)) != 1
    ):
        status = SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
        missing.append("one_schematic_target")
        ambiguous_target = True
    next_action = (
        "SCHEMATIC_TARGET_SELECTION_REQUIRED"
        if ambiguous_target
        else "Collect or approve the missing structured design facts before generation."
    )
    if status is SchematicReadinessStatus.READY_FOR_GENERATION:
        return SchematicReadinessResult.create(
            status=status,
            missing_facts=(),
            affected_source_refs=_refs(tuple(affected), "affected_source_refs"),
            required_next_action="Proceed to structured schematic proposal.",
            design_truth_fingerprint=truth_fp,
            schematic_plan_id=plan_id,
            schematic_plan_fingerprint=plan_fp,
        )
    return SchematicReadinessResult.create(
        status=status,
        missing_facts=tuple(dict.fromkeys(missing)),
        affected_source_refs=_refs(tuple(dict.fromkeys(affected or missing)), "affected_source_refs"),
        required_next_action=next_action,
        design_truth_fingerprint=truth_fp,
        schematic_plan_id=plan_id,
        schematic_plan_fingerprint=plan_fp,
    )


def _approved_request(
    authorization: ExecutionAuthorization,
    *,
    intent_id: str,
) -> tuple:
    matches = tuple(
        (request, decision)
        for request, decision in zip(authorization.requests, authorization.decisions, strict=True)
        if request.capability_id == "ARTIFACT_CREATION"
        and request.intent_id == intent_id
        and request.status is ExecutionRequestStatus.AUTHORIZED
        and decision.decision is AuthorizationDecisionStatus.APPROVED
    )
    if len(matches) != 1:
        raise ValueError("schematic production requires one approved artifact authorization")
    return matches[0]


def generate_schematic_production_request(
    *,
    design_truth: DesignTruth,
    hardware_design: HardwareDesignSpecification,
    implementation: ImplementationSpecification,
    authorization: ExecutionAuthorization,
    readiness: SchematicReadinessResult,
    execution_intents: tuple[ExecutionIntent, ...],
    conversation_id: str,
    requester: str,
    source_request_id: str | None = None,
    trace_id: str | None = None,
    target_relative_path: str = "hardware/main.kicad_sch",
    workspace_identity: str = "project-root",
    approved_schematic_plan: object | None = None,
) -> SchematicProductionRequest:
    truth = validate_copy(design_truth, DesignTruth)
    hardware = validate_copy(hardware_design, HardwareDesignSpecification)
    implementation_copy = validate_copy(implementation, ImplementationSpecification)
    auth = validate_copy(authorization, ExecutionAuthorization)
    ready = validate_copy(readiness, SchematicReadinessResult)
    plan = _approved_schematic_plan(approved_schematic_plan)
    if ready.status is not SchematicReadinessStatus.READY:
        raise ValueError("schematic production requires READY readiness")
    if ready.design_truth_fingerprint != truth.fingerprint:
        raise ValueError("schematic readiness design truth binding mismatch")
    current_readiness = evaluate_schematic_readiness(
        truth, hardware, implementation_copy, approved_schematic_plan=plan
    )
    if current_readiness.status is not SchematicReadinessStatus.READY or current_readiness.fingerprint != ready.fingerprint:
        raise ValueError("schematic readiness is stale")
    if truth.project_id != hardware.project_id or hardware.project_id != implementation_copy.project_id or conversation_id != implementation_copy.conversation_id:
        raise ValueError("schematic production identity mismatch")
    if plan is not None and (
        plan.project_id != implementation_copy.project_id
        or plan.conversation_id != conversation_id
        or plan.architecture_id != hardware.architecture_id
        or plan.target.relative_path != target_relative_path
        or ready.schematic_plan_id != plan.plan_id
        or ready.schematic_plan_fingerprint != plan.fingerprint
    ):
        raise ValueError("schematic plan target binding mismatch")
    if plan is None and (ready.schematic_plan_id is not None or ready.schematic_plan_fingerprint is not None):
        raise ValueError("schematic plan readiness binding is unavailable")
    tasks = _schematic_task(implementation_copy)
    if (
        len(tasks) != 1
        or len(_schematic_target_refs(implementation_copy)) != 1
        or auth.project_id != implementation_copy.project_id
        or auth.conversation_id != conversation_id
    ):
        raise ValueError("schematic implementation or authorization binding mismatch")
    task = tasks[0]
    intents = tuple(validate_copy(item, ExecutionIntent) for item in execution_intents)
    matching_intents = tuple(item for item in intents if item.source_task_refs == (task.task_id,))
    if len(matching_intents) != 1:
        raise ValueError("schematic task intent binding mismatch")
    request, decision = _approved_request(auth, intent_id=matching_intents[0].intent_id)
    if (
        request.intent_id != matching_intents[0].intent_id
        or "ARTIFACT_CREATION" not in matching_intents[0].required_capabilities
    ):
        raise ValueError("schematic task intent binding mismatch")
    requester_checked = ident(requester, "requester")
    source_request_id = (
        None if source_request_id is None else ident(source_request_id, "source_request_id")
    )
    trace_id = None if trace_id is None else ident(trace_id, "trace_id")
    if request.requested_by != requester_checked:
        raise ValueError("schematic requester binding mismatch")
    source_refs = _refs(
        (
            truth.truth_id,
            hardware.hardware_spec_id,
            implementation_copy.implementation_id,
            task.task_id,
            request.request_id,
            decision.decision_id,
            *((plan.plan_id,) if plan is not None else ()),
        ),
        "source_refs",
    )
    return SchematicProductionRequest.create(
        request_id=_id("schematic-production-request", truth.fingerprint, hardware.fingerprint, implementation_copy.fingerprint, ready.fingerprint, request.fingerprint, decision.fingerprint, requester_checked, target_relative_path),
        source_request_id=source_request_id,
        trace_id=trace_id,
        project_id=implementation_copy.project_id,
        conversation_id=conversation_id,
        requester=requester_checked,
        architecture_id=hardware.architecture_id,
        design_truth_id=truth.truth_id,
        hardware_design_id=hardware.hardware_spec_id,
        implementation_id=implementation_copy.implementation_id,
        implementation_task_id=task.task_id,
        authorization_id=auth.authorization_id,
        authorization_request_id=request.request_id,
        design_truth_fingerprint=truth.fingerprint,
        hardware_design_fingerprint=hardware.fingerprint,
        implementation_fingerprint=implementation_copy.fingerprint,
        authorization_fingerprint=auth.fingerprint,
        workspace_identity=workspace_identity,
        target_relative_path=target_relative_path,
        source_refs=source_refs,
        readiness_fingerprint=ready.fingerprint,
        schematic_plan_id=None if plan is None else plan.plan_id,
        schematic_plan_fingerprint=None if plan is None else plan.fingerprint,
    )


def generate_schematic_proposal(
    request: SchematicProductionRequest,
    design_truth: DesignTruth,
) -> SchematicProposal:
    production_request = validate_copy(request, SchematicProductionRequest)
    truth = validate_copy(design_truth, DesignTruth)
    if truth.truth_id != production_request.design_truth_id or truth.fingerprint != production_request.design_truth_fingerprint:
        raise ValueError("schematic proposal design truth binding mismatch")
    components = tuple(
        SchematicComponentProposal.create(
            component_id=item.component_id,
            reference=item.component_id,
            symbol_binding=item.symbol_binding,
            value=item.name,
            source_refs=(truth.truth_id, item.component_id, *item.evidence_refs),
        )
        for item in truth.components
    )
    pins = tuple(
        SchematicPinProposal.create(
            pin_id=item.pin_id,
            component_id=item.component_id,
            pin_number=item.pin_number,
            pin_name=item.pin_name,
            signal_id=item.signal_id,
            source_refs=(truth.truth_id, item.pin_id, *item.evidence_refs),
        )
        for item in truth.pins
    )
    nets = tuple(
        SchematicNetProposal.create(
            net_id=item.binding_id,
            net_name=item.net_name,
            signal_id=item.signal,
            nodes=item.nodes,
            source_refs=(truth.truth_id, item.binding_id, *item.evidence_refs),
        )
        for item in truth.nets
    )
    power = tuple(
        SchematicPowerProposal.create(
            power_domain_id=item.power_domain_id,
            name=item.name,
            voltage=item.voltage,
            ground_net=item.ground_net,
            component_refs=item.component_refs,
            source_refs=(truth.truth_id, item.power_domain_id, *item.evidence_refs),
        )
        for item in truth.power_domains
    )
    connectivity = "sha256:" + hashlib.sha256("|".join((*[item.fingerprint for item in pins], *[item.fingerprint for item in nets], *[item.fingerprint for item in power])).encode("utf-8")).hexdigest()
    return SchematicProposal.create(
        proposal_id=_id("schematic-proposal", production_request.request_id, production_request.fingerprint, truth.fingerprint, connectivity),
        request_id=production_request.request_id,
        project_id=production_request.project_id,
        conversation_id=production_request.conversation_id,
        target_relative_path=production_request.target_relative_path,
        components=components,
        pins=pins,
        nets=nets,
        power_symbols=power,
        verification_refs=(),
        source_refs=(production_request.request_id, truth.truth_id),
        design_truth_fingerprint=truth.fingerprint,
        connectivity_fingerprint=connectivity,
        schematic_plan_id=production_request.schematic_plan_id,
        schematic_plan_fingerprint=production_request.schematic_plan_fingerprint,
    )


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _uuid(value: str) -> str:
    """Return a deterministic RFC 4122-shaped UUID token for KiCad S-expr."""

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def _pin_geometry(pin_index: int, pin_count: int) -> tuple[float, float, int]:
    """Return deterministic relative pin geometry for a single-unit symbol."""

    if pin_count <= 0:
        raise ValueError("pin count must be positive")
    row = pin_index // 2
    side = pin_index % 2
    span = max(2, (pin_count + 1) // 2)
    y = (row - (span - 1) / 2) * 2.54
    # Alternating sides keeps a generated symbol readable while preserving the
    # explicit pin identity supplied by DesignTruth.
    return (-7.62 if side == 0 else 7.62, y, 0 if side == 0 else 180)


def _fmt(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _balanced_sexpr_end(source: str, start: int) -> int | None:
    depth = 0
    in_quote = False
    escaped = False
    for index in range(start, len(source)):
        character = source[index]
        if in_quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_quote = False
            continue
        if character == '"':
            in_quote = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                return None
    return None


def _authoritative_pin_geometry(definition: str) -> dict[str, tuple[float, float, float]]:
    """Read pin anchors from an authoritative symbol definition."""

    result: dict[str, tuple[float, float, float]] = {}
    for match in re.finditer(r"\(pin\s+", definition):
        end = _balanced_sexpr_end(definition, match.start())
        if end is None:
            raise ValueError("authoritative KiCad pin definition is invalid")
        block = definition[match.start():end]
        at = re.search(r"\(at\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\)", block)
        number = re.search(r"\(number\s+\"([^\"\\]+)\"", block)
        if at is None or number is None:
            continue
        result[number.group(1)] = (float(at.group(1)), float(at.group(2)), float(at.group(3)))
    return result


def _authoritative_pin_types(definition: str) -> dict[str, str]:
    """Read electrical pin types from an authoritative symbol definition."""

    result: dict[str, str] = {}
    for match in re.finditer(r"\(pin\s+", definition):
        end = _balanced_sexpr_end(definition, match.start())
        if end is None:
            raise ValueError("authoritative KiCad pin definition is invalid")
        block = definition[match.start():end]
        pin_type = re.match(r"\(pin\s+([^\s()]+)", block)
        number = re.search(r"\(number\s+\"([^\"\\]+)\"", block)
        if pin_type is None or number is None:
            continue
        result[number.group(1)] = pin_type.group(1)
    return result


def _route_authoritative_nets(
    lines: list[str],
    *,
    nets,
    endpoint_coordinates: dict[str, tuple[float, float]],
    extra_net_nodes: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Project authoritative nets using native KiCad pin-attached labels."""

    # A net label attached directly to an authoritative pin anchor is a
    # native KiCad connectivity primitive.  It avoids introducing arbitrary
    # crossing geometry into a generated fixture while remaining visible to
    # the real ERC engine.
    for net in nets:
        nodes = (*net.nodes, *((extra_net_nodes or {}).get(net.net_name, ())))
        for segment_index, node in enumerate(nodes, start=1):
            point = endpoint_coordinates.get(node)
            if point is None:
                continue
            lines.extend(
                [
                    f"  (label {_quote(net.net_name)} (at {_fmt(point[0])} {_fmt(point[1])} 0)",
                    "    (effects (font (size 1.27 1.27)) (justify left bottom))",
                    f"    (uuid {_uuid(f'label:{net.net_id}:{segment_index}')})",
                    "  )",
                ]
            )
    return


def _effect(indent: str = "      ", *, hidden: bool = False) -> list[str]:
    suffix = " hide" if hidden else ""
    return [
        f"{indent}(effects (font (size 1.27 1.27)){suffix})",
    ]


def _library_symbol(
    component: SchematicComponentProposal,
    pins: tuple[SchematicPinProposal, ...],
    *,
    authoritative_definition: str | None = None,
) -> list[str]:
    """Render one self-contained KiCad library symbol definition."""

    if authoritative_definition is not None:
        definition = authoritative_definition.strip()
        if not definition.startswith('(symbol "') or not definition.endswith(")"):
            raise ValueError("authoritative KiCad symbol definition is invalid")
        return [f"    {line}" for line in definition.splitlines()]

    symbol_name = component.symbol_binding.rsplit(":", 1)[-1]
    reference_match = re.match(r"[A-Za-z#]+", component.reference)
    reference_prefix = reference_match.group(0) if reference_match else "U"
    pin_count = len(pins)
    lines = [
        f'    (symbol {_quote(component.symbol_binding)}',
        "      (pin_numbers (hide no))",
        "      (pin_names (offset 0))",
        "      (exclude_from_sim no)",
        "      (in_bom yes)",
        "      (on_board yes)",
        "      (in_pos_files yes)",
        "      (duplicate_pin_numbers_are_jumpers no)",
        f'      (property "Reference" {_quote(reference_prefix)} (at 0 6.35 0)',
        *_effect("        "),
        "      )",
        f'      (property "Value" {_quote(component.value)} (at 0 -6.35 0)',
        *_effect("        "),
        "      )",
        '      (property "Footprint" "" (at 0 0 0)',
        *_effect("        ", hidden=True),
        "      )",
        '      (property "Datasheet" "~" (at 0 0 0)',
        *_effect("        ", hidden=True),
        "      )",
        f'      (symbol {_quote(symbol_name + "_0_1")}',
        "        (rectangle (start -5.08 5.08) (end 5.08 -5.08)",
        "          (stroke (width 0) (type default) (color 0 0 0 0))",
        "          (fill (type background))",
        "        )",
        "      )",
        f'      (symbol {_quote(symbol_name + "_1_1")}',
    ]
    for index, pin in enumerate(pins):
        x, y, angle = _pin_geometry(index, pin_count)
        lines.extend(
            [
                f'        (pin bidirectional line (at {_fmt(x)} {_fmt(y)} {angle}) (length 2.54)',
                f'          (name {_quote(pin.pin_name)} (effects (font (size 1.27 1.27))))',
                f'          (number {_quote(pin.pin_number)} (effects (font (size 1.27 1.27))))',
                "        )",
            ]
        )
    lines.extend(["      )", "      (embedded_fonts no)", "    )"])
    return lines


def _approved_part_properties(
    proposal: SchematicProposal,
    plan: SchematicPlan | None,
    selection: HardwareSelectionState | None,
) -> dict[str, tuple[str, str, str, str]]:
    """Bind placed part properties only to the approved plan and source pair."""

    if plan is None and selection is None:
        if proposal.schematic_plan_id is not None or proposal.schematic_plan_fingerprint is not None:
            raise ValueError("plan-bound schematic requires approved part property inputs")
        return {}
    if plan is None or selection is None:
        raise ValueError("approved schematic plan and hardware selection are required together")
    checked_plan = validate_copy(plan, SchematicPlan)
    checked_selection = validate_copy(selection, HardwareSelectionState)
    if (
        checked_plan.status is not SchematicPlanningStatus.APPROVED
        or checked_selection.status is not HardwareSelectionStatus.APPROVED
        or checked_selection.hardware_truth is None
        or checked_selection.bom is None
    ):
        raise ValueError("schematic part properties require approved inputs")
    hardware_truth = checked_selection.hardware_truth
    bom = checked_selection.bom
    if (
        checked_plan.project_id != proposal.project_id
        or checked_plan.conversation_id != proposal.conversation_id
        or checked_selection.proposal.project_id != proposal.project_id
        or checked_selection.proposal.conversation_id != proposal.conversation_id
        or checked_plan.plan_id != proposal.schematic_plan_id
        or checked_plan.fingerprint != proposal.schematic_plan_fingerprint
        or checked_plan.target.project_id != proposal.project_id
        or checked_plan.target.relative_path != proposal.target_relative_path
        or checked_plan.hardware_truth_id != hardware_truth.truth_id
        or checked_plan.hardware_truth_fingerprint != hardware_truth.fingerprint
        or checked_plan.bom_id != bom.bom_id
        or checked_plan.bom_fingerprint != bom.fingerprint
        or checked_plan.architecture_id != checked_selection.proposal.architecture_id
        or checked_plan.architecture_fingerprint != checked_selection.proposal.architecture_fingerprint
        or checked_plan.requirements_fingerprint != checked_selection.proposal.requirements_fingerprint
    ):
        raise ValueError("schematic part property approval binding mismatch")
    components = {item.component_id: item for item in proposal.components}
    instances = {item.instance_id: item for item in checked_plan.component_instances}
    selections = {item.slot_id: item for item in checked_selection.proposal.selected_components}
    truth_components = {item.slot_id: item for item in hardware_truth.components}
    bom_items = {item.slot_id: item for item in bom.items}
    instance_slots = {item.hardware_slot_id for item in checked_plan.component_instances}
    if (
        len(components) != len(proposal.components)
        or len({item.reference for item in proposal.components}) != len(proposal.components)
        or set(components) != set(instances)
        or {item.design_truth_component_id for item in instances.values()} != set(components)
        or len(instance_slots) != len(instances)
        or instance_slots != set(selections)
        or instance_slots != set(truth_components)
        or instance_slots != set(bom_items)
        or len(bom_items) != len(bom.items)
    ):
        raise ValueError("schematic part property component bijection mismatch")
    properties: dict[str, tuple[str, str, str, str]] = {}
    for component_id, instance in instances.items():
        component = components[component_id]
        selected = selections[instance.hardware_slot_id]
        source = selected.source
        truth_component = truth_components[instance.hardware_slot_id]
        bom_item = bom_items[instance.hardware_slot_id]
        identity = (source.manufacturer, source.mpn, source.symbol, source.footprint)
        if (
            instance.design_truth_component_id != component_id
            or selected.architecture_component_id != instance.architecture_component_id
            or instance.quantity != 1 or selected.quantity != 1
            or truth_component.quantity != 1 or bom_item.quantity != 1
            or identity != (instance.manufacturer, instance.mpn, instance.symbol, instance.footprint)
            or identity != (truth_component.manufacturer, truth_component.mpn, truth_component.symbol, truth_component.footprint)
            or identity != (bom_item.manufacturer, bom_item.mpn, bom_item.symbol, bom_item.footprint)
            or source.symbol != component.symbol_binding
            or source.source_id not in instance.source_refs
            or source.fingerprint not in instance.evidence_fingerprints
            or truth_component.source_id != source.source_id
            or truth_component.source_fingerprint != source.fingerprint
            or bom_item.component_source_id != source.source_id
            or source.fingerprint not in bom_item.evidence_fingerprints
        ):
            raise ValueError("schematic part property source binding mismatch")
        values = (source.footprint, source.manufacturer, source.mpn, source.package)
        for name, value in zip(("Footprint", "Manufacturer", "MPN", "Package"), values, strict=True):
            _known(value, name, 1024)
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("schematic part property contains unsafe control characters")
        properties[component_id] = values
    return properties


def serialize_kicad_schematic(
    proposal: SchematicProposal,
    *,
    symbol_definitions: Mapping[str, str] | None = None,
    approved_schematic_plan: SchematicPlan | None = None,
    hardware_selection: HardwareSelectionState | None = None,
) -> bytes:
    """Serialize a deterministic, self-contained KiCad 8 schematic subset.

    The default serializer embeds deterministic offline definitions. Production
    composition may inject definitions read from the currently bound KiCad
    library; the serializer never discovers or guesses those definitions.
    """

    checked = validate_copy(proposal, SchematicProposal)
    part_properties = _approved_part_properties(checked, approved_schematic_plan, hardware_selection)
    by_component: dict[str, tuple[SchematicPinProposal, ...]] = {
        component.component_id: tuple(
            pin for pin in checked.pins if pin.component_id == component.component_id
        )
        for component in checked.components
    }
    locations: dict[str, tuple[float, float]] = {}
    lines = [
        '(kicad_sch (version 20231120) (generator "eeschema")',
        '  (generator_version "8.0")',
        f"  (uuid {_uuid(checked.proposal_id)})",
        "  (paper \"A4\")",
        "  (lib_symbols",
    ]
    emitted_symbols: set[str] = set()
    authoritative_pin_geometry: dict[str, dict[str, tuple[float, float, float]]] = {}
    authoritative_pin_types: dict[str, dict[str, str]] = {}
    for component in checked.components:
        if component.symbol_binding in emitted_symbols:
            continue
        emitted_symbols.add(component.symbol_binding)
        authoritative_definition = None if symbol_definitions is None else symbol_definitions.get(component.symbol_binding)
        if symbol_definitions is not None and authoritative_definition is None:
            raise ValueError("authoritative KiCad symbol definition is unavailable")
        if authoritative_definition is not None:
            authoritative_pin_geometry[component.symbol_binding] = _authoritative_pin_geometry(authoritative_definition)
            authoritative_pin_types[component.symbol_binding] = _authoritative_pin_types(authoritative_definition)
        lines.extend(
            _library_symbol(
                component,
                by_component[component.component_id],
                authoritative_definition=authoritative_definition,
            )
        )
    power_flag_definition = None
    power_flag_net_names = ("3V3", "GND")
    if symbol_definitions is not None and "power:PWR_FLAG" in symbol_definitions:
        power_flag_definition = symbol_definitions["power:PWR_FLAG"].strip()
        if not power_flag_definition.startswith('(symbol "') or not power_flag_definition.endswith(")"):
            raise ValueError("authoritative KiCad power flag definition is invalid")
        emitted_symbols.add("power:PWR_FLAG")
        lines.extend(f"    {line}" for line in power_flag_definition.splitlines())
        component_by_id = {item.component_id: item for item in checked.components}
        pin_by_node = {
            f"{item.component_id}.{item.pin_number}": item
            for item in checked.pins
        }
        power_output_nets = {
            net.net_name
            for net in checked.nets
            if any(
                authoritative_pin_types.get(
                    component_by_id[pin_by_node[node].component_id].symbol_binding,
                    {},
                ).get(pin_by_node[node].pin_number)
                == "power_out"
                for node in net.nodes
                if node in pin_by_node
            )
        }
        power_flag_net_names = tuple(
            net_name for net_name in power_flag_net_names
            if net_name not in power_output_nets
        )
    lines.append("  )")
    for index, component in enumerate(checked.components, start=1):
        uuid_value = _uuid(f"placed:{component.component_id}")
        # KiCad's default 50-mil grid is 1.27 mm.  Keep both symbol origins
        # and generated pin endpoints on that grid so the real ERC engine does
        # not turn serializer rounding into electrical diagnostics.
        x = 80.01 + ((index - 1) % 4) * 45.72
        y = 60.96 + ((index - 1) // 4) * 35.56
        locations[component.component_id] = (x, y)
        properties = part_properties.get(component.component_id)
        footprint = "" if properties is None else properties[0]
        lines.extend([
            f"  (symbol (lib_id {_quote(component.symbol_binding)}) (at {_fmt(x)} {_fmt(y)} 0) (unit 1)",
            "    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced)",
            f"    (uuid {uuid_value})",
            f"    (property \"Reference\" {_quote(component.reference)} (at {_fmt(x + 2.54)} {_fmt(y - 1.27)} 0)",
            "      (effects (font (size 1.27 1.27)) (justify left))",
            "    )",
            f"    (property \"Value\" {_quote(component.value)} (at {_fmt(x + 2.54)} {_fmt(y + 1.27)} 0)",
            "      (effects (font (size 1.27 1.27)) (justify left))",
            "    )",
            f"    (property \"Footprint\" {_quote(footprint)} (at {_fmt(x)} {_fmt(y)} 0)",
            "      (effects (font (size 1.27 1.27)) hide)",
            "    )",
            f"    (property \"Datasheet\" \"~\" (at {_fmt(x)} {_fmt(y)} 0)",
            "      (effects (font (size 1.27 1.27)) hide)",
            "    )",
        ])
        if properties is not None:
            for name, value in zip(("Manufacturer", "MPN", "Package"), properties[1:], strict=True):
                lines.extend([
                    f"    (property {_quote(name)} {_quote(value)} (at {_fmt(x)} {_fmt(y)} 0)",
                    "      (effects (font (size 1.27 1.27)) hide)",
                    "    )",
                ])
        for pin in by_component[component.component_id]:
            pin_index = by_component[component.component_id].index(pin)
            lines.append(
                f'    (pin {_quote(pin.pin_number)} (uuid {_uuid(f"instance-pin:{component.component_id}:{pin.pin_number}:{pin_index}")}))'
            )
        lines.extend(
            [
                "    (instances",
                '      (project "embrix"',
                f'        (path "/" (reference {_quote(component.reference)}) (unit 1))',
                "      )",
                "    )",
                "  )",
            ]
        )
    power_flag_locations: dict[str, tuple[float, float]] = {}
    if power_flag_definition is not None:
        # These are explicit supply-source markers for the canonical input
        # connector.  They are part of the generated design, not an ERC
        # severity override or a post-processing suppression.
        for index, (net_name, x, y) in enumerate(
            tuple(
                (net_name, x, y)
                for net_name, x, y in (("3V3", 80.01, 25.40), ("GND", 170.18, 25.40))
                if net_name in power_flag_net_names
            ),
            start=1,
        ):
            power_flag_locations[net_name] = (x, y)
            lines.extend(
                [
                    f'  (symbol (lib_id "power:PWR_FLAG") (at {_fmt(x)} {_fmt(y)} 0) (unit 1)',
                    "    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced)",
                    f"    (uuid {_uuid(f'placed:power-flag:{net_name}')})",
                    f'    (property "Reference" {_quote(f"#FLG{index:02d}")} (at {_fmt(x + 2.54)} {_fmt(y - 1.27)} 0)',
                    "      (effects (font (size 1.27 1.27)) (justify left))",
                    "    )",
                    f'    (property "Value" "PWR_FLAG" (at {_fmt(x + 2.54)} {_fmt(y + 1.27)} 0)',
                    "      (effects (font (size 1.27 1.27)) (justify left))",
                    "    )",
                    f'    (property "Footprint" "" (at {_fmt(x)} {_fmt(y)} 0)',
                    "      (effects (font (size 1.27 1.27)) hide)",
                    "    )",
                    f'    (property "Datasheet" "~" (at {_fmt(x)} {_fmt(y)} 0)',
                    "      (effects (font (size 1.27 1.27)) hide)",
                    "    )",
                    f'    (pin "1" (uuid {_uuid(f"instance-pin:power-flag:{net_name}")}))',
                    "    (instances",
                    '      (project "embrix"',
                    f'        (path "/" (reference {_quote(f"#FLG{index:02d}")}) (unit 1))',
                    "      )",
                    "    )",
                    "  )",
                ]
            )
    endpoint_coordinates: dict[str, tuple[float, float]] = {}
    endpoint_directions: dict[str, tuple[int, int]] = {}
    for component in checked.components:
        x, y = locations[component.component_id]
        for index, pin in enumerate(by_component[component.component_id]):
            authoritative = authoritative_pin_geometry.get(component.symbol_binding, {}).get(pin.pin_number)
            if authoritative is None:
                rel_x, rel_y, _ = _pin_geometry(index, len(by_component[component.component_id]))
                endpoint = (x + rel_x, y - rel_y)
                direction = (-1 if rel_x < 0 else 1, 0)
            else:
                rel_x, rel_y, _ = authoritative
                # KiCad's schematic coordinate system mirrors the symbol
                # definition's local Y axis when it is placed on a sheet.
                endpoint = (x + rel_x, y - rel_y)
                direction = (
                    (1 if rel_x > 0 else -1, 0)
                    if abs(rel_x) >= abs(rel_y)
                    else (0, 1 if rel_y > 0 else -1)
                )
            endpoint_coordinates[f"{component.component_id}.{pin.pin_number}"] = endpoint
            endpoint_directions[f"{component.component_id}.{pin.pin_number}"] = direction
    for net_name, location in power_flag_locations.items():
        node = f"__power_flag_{net_name}.1"
        endpoint_coordinates[node] = location
        endpoint_directions[node] = (0, 1)

    if symbol_definitions is None:
        for net in checked.nets:
            endpoints = [
                (node, endpoint_coordinates[node])
                for node in net.nodes
                if node in endpoint_coordinates
            ]
            if len(endpoints) < 2:
                continue
            # Offline mode keeps the compact labelled representation used by
            # unit fixtures.  Production mode below uses physical connectivity
            # so the real KiCad ERC engine can verify every net without
            # isolated-label or dangling-wire warnings.
            for segment_index, (node, (pin_x, pin_y)) in enumerate(endpoints, start=1):
                direction_x, direction_y = endpoint_directions[node]
                stub_x = pin_x + direction_x * 2.54
                stub_y = pin_y + direction_y * 2.54
                lines.extend(
                    [
                        f"  (wire (pts (xy {_fmt(pin_x)} {_fmt(pin_y)}) (xy {_fmt(stub_x)} {_fmt(stub_y)}))",
                        "    (stroke (width 0) (type default))",
                        f"    (uuid {_uuid(f'wire:{net.net_id}:{segment_index}')})",
                        "  )",
                    ]
                )
                lines.extend(
                    [
                        f"  (label {_quote(net.net_name)} (at {_fmt(stub_x)} {_fmt(stub_y)} 0)",
                        "    (effects (font (size 1.27 1.27)) (justify left bottom))",
                        f"    (uuid {_uuid(f'label:{net.net_id}:{segment_index}')})",
                        "  )",
                    ]
                )
    else:
        unused_pin_numbers = {
            component.component_id: set(authoritative_pin_geometry.get(component.symbol_binding, {}))
            - {pin.pin_number for pin in by_component[component.component_id]}
            for component in checked.components
        }
        for component in checked.components:
            x, y = locations[component.component_id]
            for pin_number in sorted(unused_pin_numbers[component.component_id]):
                rel_x, rel_y, _ = authoritative_pin_geometry[component.symbol_binding][pin_number]
                lines.append(
                    f"  (no_connect (at {_fmt(x + rel_x)} {_fmt(y - rel_y)}) (uuid {_uuid(f'no-connect:{component.component_id}:{pin_number}')}))"
                )
        _route_authoritative_nets(
            lines,
            nets=checked.nets,
            endpoint_coordinates=endpoint_coordinates,
            extra_net_nodes={
                net_name: (f"__power_flag_{net_name}.1",)
                for net_name in power_flag_net_names
            },
        )
    lines.extend([
        '  (sheet_instances (path "/" (page "1")))',
        "  (embedded_fonts no)",
        ")",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def pending_schematic_proposal_decision(proposal: SchematicProposal) -> SchematicProposalDecision:
    checked = validate_copy(proposal, SchematicProposal)
    return SchematicProposalDecision.create(
        decision_id=_id("schematic-decision", checked.proposal_id, checked.fingerprint),
        proposal_id=checked.proposal_id,
        proposal_fingerprint=checked.fingerprint,
        decision=SchematicProposalDecisionStatus.PENDING,
        reason="Awaiting explicit schematic proposal approval.",
    )


def decide_schematic_proposal(
    proposal: SchematicProposal,
    *,
    decision: SchematicProposalDecisionStatus,
    reviewer: str,
    decided_at: datetime,
) -> SchematicProposalDecision:
    checked = validate_copy(proposal, SchematicProposal)
    if not isinstance(decision, SchematicProposalDecisionStatus):
        decision = SchematicProposalDecisionStatus(decision)
    if decision is SchematicProposalDecisionStatus.PENDING:
        raise ValueError("terminal schematic proposal decision is required")
    if not isinstance(decided_at, datetime) or decided_at.tzinfo is None:
        raise ValueError("decided_at must be timezone-aware")
    return SchematicProposalDecision.create(
        decision_id=_id("schematic-decision", checked.proposal_id, checked.fingerprint),
        proposal_id=checked.proposal_id,
        proposal_fingerprint=checked.fingerprint,
        decision=decision,
        reviewer=ident(reviewer, "reviewer"),
        decided_at=decided_at.astimezone(UTC),
        reason="Schematic proposal explicitly approved." if decision is SchematicProposalDecisionStatus.APPROVED else "Schematic proposal explicitly rejected.",
    )


def create_schematic_production_state(request: SchematicProductionRequest, proposal: SchematicProposal) -> SchematicProductionState:
    checked_request = validate_copy(request, SchematicProductionRequest)
    checked_proposal = validate_copy(proposal, SchematicProposal)
    if checked_proposal.request_id != checked_request.request_id:
        raise ValueError("schematic request/proposal mismatch")
    return SchematicProductionState.create(
        production_id=_id("schematic-production", checked_request.request_id, checked_proposal.fingerprint),
        project_id=checked_request.project_id,
        conversation_id=checked_request.conversation_id,
        request=checked_request,
        proposal=checked_proposal,
        decision=pending_schematic_proposal_decision(checked_proposal),
        status=SchematicProductionStatus.AWAITING_APPROVAL,
    )


def assess_schematic_repair(erc_result: SchematicErcResult) -> SchematicRepairAssessment:
    result = validate_copy(erc_result, SchematicErcResult)
    issues = tuple(result.issues)
    issue_refs = tuple(item.issue_id for item in issues)
    correlations = tuple(result.correlations)
    if result.status is SchematicErcStatus.ERC_FAIL:
        # Only defects attributable to our deterministic serializer may be
        # proposed for repair. Electrical/design conflicts remain blocked.
        rendering_rules = {"FORMAT", "RENDER", "SERIALIZER"}
        is_rendering_defect = (
            bool(issues)
            and all(
                issue.classification is SchematicErcIssueClassification.ARTIFACT_RENDERING_DEFECT
                or any(marker in issue.rule_id.upper() for marker in rendering_rules)
                for issue in issues
            )
        ) or (
            not issues
            and bool(result.violations)
            and all(any(marker in violation.rule.upper() for marker in rendering_rules) for violation in result.violations)
        )
        classification = (
            SchematicErcIssueClassification.ARTIFACT_RENDERING_DEFECT
            if is_rendering_defect
            else SchematicErcIssueClassification.PLAN_OR_TRUTH_DEFECT
        )
        kind = (
            SchematicRepairAssessmentKind.DETERMINISTIC_RENDERING_DEFECT
            if is_rendering_defect
            else SchematicRepairAssessmentKind.DESIGN_TRUTH_CONFLICT
        )
        allowed = is_rendering_defect
    elif result.status is SchematicErcStatus.ERC_WARNINGS:
        classification = (
            SchematicErcIssueClassification.LIBRARY_OR_EDA_MAPPING_DEFECT
            if any(item.classification is SchematicErcIssueClassification.LIBRARY_OR_EDA_MAPPING_DEFECT for item in issues)
            else SchematicErcIssueClassification.NON_BLOCKING_WARNING
        )
        kind = (
            SchematicRepairAssessmentKind.LIBRARY_OR_EDA_MAPPING_DEFECT
            if classification is SchematicErcIssueClassification.LIBRARY_OR_EDA_MAPPING_DEFECT
            else SchematicRepairAssessmentKind.NON_BLOCKING_WARNING
        )
        allowed = False
    elif result.status in {SchematicErcStatus.ERC_BLOCKED, SchematicErcStatus.ERC_UNAVAILABLE}:
        kind = SchematicRepairAssessmentKind.UNSUPPORTED
        classification = SchematicErcIssueClassification.TOOLCHAIN_OR_ENVIRONMENT
        allowed = False
    else:
        kind = SchematicRepairAssessmentKind.MISSING_DESIGN_FACT
        classification = SchematicErcIssueClassification.UNCLASSIFIED
        allowed = False
    return SchematicRepairAssessment.create(
        assessment_id=_id("schematic-repair-assessment", result.operation_id, result.fingerprint),
        erc_result_fingerprint=result.fingerprint,
        kind=kind,
        rationale=(
            "No automatic electrical repair is permitted without preserving confirmed DesignTruth."
            if not allowed
            else "The issue is attributed to deterministic rendering and requires a reviewed serializer/G3 change."
        ),
        source_refs=(result.operation_id, result.receipt_ref),
        proposal_allowed=allowed,
        issue_refs=issue_refs,
        classification=classification,
        correlations=correlations,
    )


def generate_schematic_repair_proposal(
    assessment: SchematicRepairAssessment,
    base_proposal: SchematicProposal,
    corrected_content: str,
) -> SchematicRepairProposal:
    """Create a reviewable repair proposal without applying or queueing it."""

    checked_assessment = validate_copy(assessment, SchematicRepairAssessment)
    checked_base = validate_copy(base_proposal, SchematicProposal)
    if not checked_assessment.proposal_allowed or checked_assessment.kind is not SchematicRepairAssessmentKind.DETERMINISTIC_RENDERING_DEFECT:
        raise ValueError("repair proposal is not allowed for this ERC assessment")
    if not isinstance(corrected_content, str):
        raise TypeError("corrected_content is invalid")
    # Encode explicitly so malformed surrogate input cannot cross the boundary.
    try:
        corrected_content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("corrected_content must be valid UTF-8") from exc
    normalized = text(corrected_content, "corrected_content", 16 * 1024 * 1024)
    content_fp = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return SchematicRepairProposal.create(
        repair_id=_id(
            "schematic-repair",
            checked_assessment.assessment_id,
            checked_assessment.fingerprint,
            checked_base.proposal_id,
            checked_base.fingerprint,
            content_fp,
        ),
        erc_result_fingerprint=checked_assessment.erc_result_fingerprint,
        base_proposal_id=checked_base.proposal_id,
        target_relative_path=checked_base.target_relative_path,
        corrected_content=normalized,
        corrected_content_fingerprint=content_fp,
        source_refs=(
            checked_assessment.assessment_id,
            checked_base.proposal_id,
        ),
        issue_refs=checked_assessment.issue_refs,
        classification=checked_assessment.classification,
        root_cause=checked_assessment.rationale,
        proposed_change="Regenerate the schematic through the approved G3 path after the rendering correction.",
        target_authority="Schematic serializer and existing G3 generation authority",
        affected_truth_refs=(),
        affected_plan_refs=(),
        supporting_evidence=checked_assessment.source_refs,
        expected_effect="The same approved plan produces an ERC-clean artifact without changing Engineering Truth.",
        risk="Serializer regression or artifact fingerprint change; G3-V parse and ERC rerun are required.",
        source_fingerprint=checked_assessment.erc_result_fingerprint,
        status=SchematicProductionStatus.AWAITING_APPROVAL,
    )


def is_schematic_generation_request(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[\s，。！？、,.!?]+", "", value).casefold()
    return normalized in {
        "开始画原理图", "生成原理图", "把这个硬件设计画成kicad原理图",
        "generateschematic", "drawschematic",
    }


__all__ = (
    "assess_schematic_repair",
    "create_schematic_production_state",
    "decide_schematic_proposal",
    "evaluate_schematic_readiness",
    "generate_schematic_production_request",
    "generate_schematic_proposal",
    "generate_schematic_repair_proposal",
    "is_schematic_generation_request",
    "pending_schematic_proposal_decision",
    "serialize_kicad_schematic",
)
