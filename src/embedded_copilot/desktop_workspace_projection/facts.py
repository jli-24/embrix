"""Read-only Owner references for the Desktop workspace projection."""

from __future__ import annotations

from typing import Iterable

from .models import DesktopEvidencePage, DesktopFactReference


_CONVERSATION_DOMAINS = (
    "REQUIREMENT",
    "ARCHITECTURE",
    "SCHEMATIC",
    "PCB",
    "FIRMWARE",
)


def _value(value: object) -> str:
    return str(getattr(value, "value", value)) if value is not None else ""


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _missing(domain: str, project_id: str, conversation_id: str | None, *, unknown: bool = False) -> DesktopFactReference:
    return DesktopFactReference(
        domain=domain,
        owner="ProjectCatalog",
        project_id=project_id,
        conversation_id=conversation_id,
        currentness="UNKNOWN" if unknown else "UNAVAILABLE",
        reason_code="PAGE_NOT_SCANNED" if unknown else "OWNER_RECORD_MISSING",
    )


def _reference(
    domain: str,
    project_id: str,
    conversation_id: str | None,
    value: object,
    *,
    object_id: str | None,
    revision: str | None,
    currentness: str,
    reason_code: str,
    owner: str = "ProjectCatalog",
) -> DesktopFactReference:
    return DesktopFactReference(
        domain=domain,
        owner=owner,
        project_id=project_id,
        conversation_id=conversation_id,
        object_id=object_id,
        revision=revision,
        fingerprint=_text(getattr(value, "fingerprint", None)),
        source_reference=f"conversation:{conversation_id}" if conversation_id else None,
        source_fingerprint=_text(getattr(value, "fingerprint", None)),
        currentness=currentness,
        reason_code=reason_code,
    )


def _circuit_binding_status(value: object, circuits: dict[str, object] | None, source_currentness: dict[str, bool] | None) -> tuple[str, str]:
    circuit_id = _text(getattr(value, "circuit_intent_id", None))
    if circuit_id is None:
        return "CURRENT", "SOURCE_BINDING_CURRENT"
    if circuits is None:
        return "UNKNOWN", "CIRCUIT_OWNER_UNAVAILABLE"
    current = circuits.get(circuit_id)
    if current is None:
        return "UNAVAILABLE", "CIRCUIT_OWNER_MISSING"
    if (
        _text(getattr(value, "circuit_intent_revision", None)),
        _text(getattr(value, "circuit_intent_fingerprint", None)),
    ) != (
        _text(getattr(current, "revision", None)),
        _text(getattr(current, "fingerprint", None)),
    ):
        return "STALE", "CIRCUIT_REVISION_CHANGED"
    if source_currentness is not None and not source_currentness.get(circuit_id, False):
        return "STALE", "CIRCUIT_SOURCE_CHANGED"
    return "CURRENT", "SOURCE_BINDING_CURRENT"


def _conversation_facts(detail: object, project_id: str, circuits: dict[str, object] | None, source_currentness: dict[str, bool] | None) -> tuple[DesktopFactReference, ...]:
    conversation_id = _text(getattr(detail, "conversation_id", None))
    if conversation_id is None or getattr(detail, "project_id", None) != project_id:
        raise ValueError("conversation Owner identity is invalid")
    result: list[DesktopFactReference] = []

    requirement = getattr(detail, "requirement_context", None)
    if requirement is None:
        result.append(_missing("REQUIREMENT", project_id, conversation_id))
    else:
        ready = _value(getattr(requirement, "effective_status", getattr(requirement, "status", None))) == "CONFIRMED"
        result.append(_reference("REQUIREMENT", project_id, conversation_id, requirement, object_id=None, revision=None, currentness="CURRENT" if ready else "BLOCKED", reason_code="CONFIRMED_SOURCE" if ready else "REQUIREMENT_NOT_CONFIRMED"))

    architecture = getattr(detail, "architecture_design", None)
    if architecture is None:
        result.append(_missing("ARCHITECTURE", project_id, conversation_id))
    else:
        ready = _value(getattr(architecture, "status", None)) in {"APPROVED", "COMPLETED"}
        result.append(_reference("ARCHITECTURE", project_id, conversation_id, architecture, object_id=_text(getattr(architecture, "architecture_id", None)), revision=None, currentness="CURRENT" if ready else "BLOCKED", reason_code="APPROVED_SOURCE" if ready else "ARCHITECTURE_NOT_APPROVED"))

    planning = getattr(detail, "schematic_planning", None)
    plan = None if planning is None else (getattr(planning, "approved_plan", None) or getattr(planning, "proposal", None))
    if plan is None:
        result.append(_missing("SCHEMATIC", project_id, conversation_id))
    else:
        ready = _value(getattr(planning, "status", None)) == "APPROVED"
        status, reason = _circuit_binding_status(plan, circuits, source_currentness) if ready else ("BLOCKED", "SCHEMATIC_NOT_APPROVED")
        result.append(_reference("SCHEMATIC", project_id, conversation_id, plan, object_id=_text(getattr(plan, "plan_id", None)), revision=None, currentness=status, reason_code=reason))

    pcb = getattr(detail, "pcb_engineering_state", None)
    if pcb is None:
        result.append(_missing("PCB", project_id, conversation_id))
    else:
        ready = _value(getattr(pcb, "status", None)) == "CURRENT"
        binding = getattr(pcb, "intent_source_binding", None)
        status, reason = _circuit_binding_status(binding, circuits, source_currentness) if ready and binding is not None else (("CURRENT", "SOURCE_BINDING_CURRENT") if ready else ("STALE", "PCB_STATE_STALE"))
        result.append(_reference("PCB", project_id, conversation_id, pcb, object_id=None, revision=_text(getattr(pcb, "revision", None)), currentness=status, reason_code=reason))

    firmware = getattr(detail, "firmware_spec", None)
    if firmware is None:
        result.append(_missing("FIRMWARE", project_id, conversation_id))
    else:
        hardware = getattr(detail, "hardware_design_spec", None)
        workflow = getattr(detail, "engineering_workflow", None)
        ready = hardware is not None and workflow is not None and getattr(firmware, "hardware_design_fingerprint", None) == getattr(hardware, "fingerprint", None) and getattr(firmware, "workflow_fingerprint", None) == getattr(workflow, "fingerprint", None)
        result.append(_reference("FIRMWARE", project_id, conversation_id, firmware, object_id=_text(getattr(firmware, "firmware_spec_id", None)), revision=_text(getattr(firmware, "design_revision", None)), currentness="CURRENT" if ready else "STALE", reason_code="SOURCE_BINDING_CURRENT" if ready else "FIRMWARE_SOURCE_CHANGED"))
    return tuple(result)


def authoritative_facts(
    *,
    project_id: str,
    conversations: Iterable[object],
    circuits: Iterable[object] | None,
    source_currentness: dict[str, bool] | None = None,
    evidence: DesktopEvidencePage,
    page_incomplete: bool,
) -> tuple[DesktopFactReference, ...]:
    current_circuits = None if circuits is None else {item.object_id: item for item in circuits}
    details = tuple(conversations)
    facts: list[DesktopFactReference] = []
    for detail in details:
        facts.extend(_conversation_facts(detail, project_id, current_circuits, source_currentness))
    if not details:
        facts.extend(_missing(domain, project_id, None, unknown=page_incomplete) for domain in _CONVERSATION_DOMAINS)
    elif page_incomplete:
        facts.extend(_missing(domain, project_id, None, unknown=True) for domain in _CONVERSATION_DOMAINS)

    if current_circuits is None:
        facts.append(DesktopFactReference(domain="CIRCUIT", owner="ProjectCatalog", project_id=project_id, currentness="UNKNOWN", reason_code="CIRCUIT_OWNER_UNAVAILABLE"))
    elif not current_circuits:
        facts.append(_missing("CIRCUIT", project_id, None))
    else:
        for item in current_circuits.values():
            facts.append(DesktopFactReference(
                domain="CIRCUIT", owner="ProjectCatalog", project_id=project_id,
                object_id=item.object_id, revision=item.revision, fingerprint=item.fingerprint,
                source_reference=_text(getattr(getattr(item, "provenance", None), "source_ref", None)) or f"circuit:{item.object_id}",
                source_fingerprint=_text(getattr(getattr(item, "provenance", None), "source_fingerprint", None)),
                currentness=("CURRENT" if source_currentness is None or source_currentness.get(item.object_id, False) else "STALE") if _value(getattr(item, "status", None)) == "ACTIVE" else "STALE",
                reason_code=("ACTIVE_REVISION" if source_currentness is None or source_currentness.get(item.object_id, False) else "CIRCUIT_SOURCE_CHANGED") if _value(getattr(item, "status", None)) == "ACTIVE" else "CIRCUIT_NOT_ACTIVE",
            ))

    if not evidence.items:
        facts.append(_missing("EVIDENCE", project_id, None))
    else:
        for item in evidence.items:
            facts.append(DesktopFactReference(
                domain="EVIDENCE", owner="ProjectCatalog", project_id=project_id,
                object_id=item.evidence_id, fingerprint=item.fingerprint,
                source_reference=item.source_reference,
                source_fingerprint=item.fingerprint,
                currentness="UNKNOWN", reason_code="VERIFICATION_NOT_ESTABLISHED",
            ))
    return tuple(facts)
