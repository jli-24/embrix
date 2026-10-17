from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from embedded_copilot.engineering_pcb import (
    ApprovalDecision,
    ReadyForFabStatus,
    build_ready_for_fab_approval_target,
)
from embedded_copilot.verification_loop import VerificationStatus


class ManufacturingEntryGateRejected(RuntimeError):
    """Raised before any manufacturing operation can be created."""

    code = "G8_ENTRY_GATE_REJECTED"


@dataclass(frozen=True, slots=True)
class ManufacturingEntryGateReceipt:
    project_id: str
    conversation_id: str
    pcb_artifact_fingerprint: str
    pcb_state_fingerprint: str
    summary_fingerprint: str
    dfm_fingerprint: str
    ready_for_fab_target_fingerprint: str
    ready_for_fab_fingerprint: str
    approval_fingerprint: str
    manufacturer_profile_fingerprint: str


class ManufacturingEntryGatePort(Protocol):
    def assert_current(
        self,
        *,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        requester: str,
        pcb_artifact_fingerprint: str,
    ) -> ManufacturingEntryGateReceipt: ...


class CanonicalManufacturingEntryGate:
    """Read the current ProjectCatalog authority immediately before export."""

    def __init__(self, project_catalog: object | None) -> None:
        self._project_catalog = project_catalog

    def assert_current(
        self,
        *,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        requester: str,
        pcb_artifact_fingerprint: str,
    ) -> ManufacturingEntryGateReceipt:
        catalog = self._project_catalog
        if catalog is None:
            raise ManufacturingEntryGateRejected("canonical ProjectCatalog is unavailable")
        try:
            detail = catalog.conversation(
                project_id,
                conversation_id,
                tenant_id=tenant_id,
                requester=requester,
            )
        except Exception as error:
            raise ManufacturingEntryGateRejected("current ProjectCatalog authority could not be read") from error

        state = getattr(detail, "pcb_engineering_state", None)
        summary = getattr(detail, "pcb_engineering_verification_summary", None)
        ready = getattr(detail, "pcb_ready_for_fab", None)
        approval = getattr(detail, "pcb_ready_for_fab_approval", None)
        profile = getattr(detail, "pcb_manufacturer_profile", None)
        measurements = getattr(detail, "pcb_manufacturing_measurements", None)
        if state is None or summary is None or ready is None or approval is None or profile is None:
            raise ManufacturingEntryGateRejected("current G7 state, summary, profile, gate, and approval are required")
        if measurements is None:
            raise ManufacturingEntryGateRejected("current PCB manufacturing measurements are required")
        if getattr(detail, "project_id", project_id) != project_id:
            raise ManufacturingEntryGateRejected("ProjectCatalog project identity mismatch")
        for value, label in (
            (state, "PCB engineering state"),
            (getattr(detail, "pcb_constraint_set", None), "PCB constraint set"),
            (getattr(detail, "pcb_verification_plan", None), "PCB verification plan"),
        ):
            if value is None or (
                getattr(value, "status", None) is not None
                and getattr(value.status, "value", value.status) == "STALE"
            ):
                raise ManufacturingEntryGateRejected(f"current {label} is stale or unavailable")
        source_binding = getattr(state, "intent_source_binding", None)
        production = getattr(detail, "pcb_production", None)
        request = None if production is None else getattr(production, "request", None)
        generation = None if production is None else getattr(production, "generation_receipt", None)
        current_artifact = getattr(state, "pcb_artifact_fingerprint", None)
        if current_artifact is None:
            current_artifact = getattr(state, "source_artifact_fingerprint", None)
        if request is None or generation is None:
            raise ManufacturingEntryGateRejected("current PCB artifact binding is unavailable")
        if (
            getattr(generation, "artifact_fingerprint", None) != current_artifact
            or getattr(generation, "schematic_fingerprint", None)
            != getattr(request, "schematic_artifact_fingerprint", None)
        ):
            raise ManufacturingEntryGateRejected("current PCB artifact binding is stale")
        if source_binding is not None:
            if getattr(source_binding.status, "value", source_binding.status) != "BOUND":
                raise ManufacturingEntryGateRejected("PCB source lineage is not BOUND")
            if (
                getattr(source_binding, "project_id", None) != project_id
                or getattr(source_binding, "conversation_id", None) != conversation_id
                or getattr(source_binding, "schematic_artifact_fingerprint", None)
                != getattr(request, "schematic_artifact_fingerprint", None)
            ):
                raise ManufacturingEntryGateRejected("PCB source lineage is not current")
            element_bindings = tuple(getattr(state, "element_bindings", ()))
            if not element_bindings or any(
                getattr(item.status, "value", item.status) != "BOUND" for item in element_bindings
            ):
                raise ManufacturingEntryGateRejected("PCB element lineage is not fully BOUND")
        if getattr(profile, "manufacturer_id", None) != "JLCPCB":
            raise ManufacturingEntryGateRejected("current manufacturer profile is not JLCPCB")
        if getattr(ready.status, "value", ready.status) != ReadyForFabStatus.PASS.value:
            raise ManufacturingEntryGateRejected("READY_FOR_FAB is not PASS")
        if getattr(ready, "approval_valid", False) is not True:
            raise ManufacturingEntryGateRejected("READY_FOR_FAB approval is not current")
        if getattr(approval.decision, "value", approval.decision) != ApprovalDecision.APPROVED.value:
            raise ManufacturingEntryGateRejected("human approval is not APPROVED")
        if getattr(summary, "pcb_state_fingerprint", None) != state.fingerprint:
            raise ManufacturingEntryGateRejected("G7 summary is not bound to the current PCB state")
        plan = getattr(detail, "pcb_verification_plan", None)
        if plan is None or getattr(summary, "verification_plan_fingerprint", None) != plan.fingerprint:
            raise ManufacturingEntryGateRejected("G7 summary is not bound to the current verification plan")
        if ready.summary_fingerprint != summary.fingerprint:
            raise ManufacturingEntryGateRejected("READY_FOR_FAB is not bound to the current G7 summary")
        if (
            pcb_artifact_fingerprint != current_artifact
            or ready.artifact_fingerprint != current_artifact
            or approval.artifact_fingerprint != current_artifact
            or measurements.artifact_fingerprint != current_artifact
        ):
            raise ManufacturingEntryGateRejected("PCB artifact fingerprint is not current")
        if approval.summary_fingerprint != summary.fingerprint:
            raise ManufacturingEntryGateRejected("human approval is not bound to the current G7 summary")
        if approval.state_fingerprint != state.fingerprint:
            raise ManufacturingEntryGateRejected("human approval is not bound to the current PCB state")
        if approval.manufacturer_profile_fingerprint != profile.fingerprint:
            raise ManufacturingEntryGateRejected("human approval is not bound to the current manufacturer profile")
        dfm = next(
            (item for item in (*summary.required_results, *summary.optional_results) if item.verifier in {"DFM", "DFMVerifier"}),
            None,
        )
        if dfm is None or dfm.status is not VerificationStatus.PASS:
            raise ManufacturingEntryGateRejected("current DFM verification is not PASS")
        if profile.fingerprint not in dfm.compared_fingerprints:
            raise ManufacturingEntryGateRejected("current DFM result is not bound to the manufacturer profile")
        target = build_ready_for_fab_approval_target(summary, current_artifact)
        if approval.project_id != project_id:
            raise ManufacturingEntryGateRejected("human approval project identity mismatch")
        if approval.conversation_id != conversation_id:
            raise ManufacturingEntryGateRejected("human approval conversation identity mismatch")
        if approval.dfm_fingerprint != dfm.fingerprint:
            raise ManufacturingEntryGateRejected("human approval DFM fingerprint mismatch")
        if approval.ready_for_fab_target_fingerprint != target.fingerprint:
            raise ManufacturingEntryGateRejected("human approval target fingerprint mismatch")
        if approval.selected_by != "USER":
            raise ManufacturingEntryGateRejected("human approval was not selected by USER")
        if approval.provenance != "EXPLICIT_USER_APPROVAL":
            raise ManufacturingEntryGateRejected("human approval provenance is not explicit")
        return ManufacturingEntryGateReceipt(
            project_id=project_id,
            conversation_id=conversation_id,
            pcb_artifact_fingerprint=current_artifact,
            pcb_state_fingerprint=state.fingerprint,
            summary_fingerprint=summary.fingerprint,
            dfm_fingerprint=dfm.fingerprint,
            ready_for_fab_target_fingerprint=target.fingerprint,
            ready_for_fab_fingerprint=ready.fingerprint,
            approval_fingerprint=approval.fingerprint,
            manufacturer_profile_fingerprint=profile.fingerprint,
        )


__all__ = (
    "CanonicalManufacturingEntryGate",
    "ManufacturingEntryGatePort",
    "ManufacturingEntryGateReceipt",
    "ManufacturingEntryGateRejected",
)
