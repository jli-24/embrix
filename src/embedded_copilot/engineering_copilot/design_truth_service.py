from __future__ import annotations

import hashlib

from .common import validate_copy
from .contracts import (
    ArchitectureConfidence,
    ArchitectureDesign,
    ArchitectureDesignStatus,
    DesignComponent,
    DesignEvidenceBinding,
    DesignInterface,
    DesignPin,
    DesignSignal,
    DesignSource,
    DesignTruth,
    DesignTruthOrigin,
    EngineeringEvidence,
    EngineeringEvidenceBundle,
    EngineeringEvidenceStatus,
    EvidenceType,
    FirmwareBinding,
    NetBinding,
    PeripheralBinding,
    PlanProvenance,
    TruthLevel,
)

_CONFIDENCE = {
    ArchitectureConfidence.EXPLICIT: 1.0,
    ArchitectureConfidence.INFERRED: 0.5,
    ArchitectureConfidence.UNKNOWN: 0.0,
}
_TRUTH_LEVEL = {
    ArchitectureConfidence.EXPLICIT: TruthLevel.EXPLICIT,
    ArchitectureConfidence.INFERRED: TruthLevel.INFERRED,
    ArchitectureConfidence.UNKNOWN: TruthLevel.UNKNOWN,
}
_COMPONENT_TYPES = {
    "SYSTEM": "system",
    "CONTROLLER": "controller",
    "POWER": "power",
    "SENSOR_INPUT": "sensor",
    "ACTUATOR": "actuator",
    "COMMUNICATION": "communication",
    "DRIVER": "driver",
    "CONTROL": "control",
    "APPLICATION": "application",
}


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _build_from_architecture(
    architecture_design: ArchitectureDesign,
    *,
    tenant_id: str = "default-tenant",
    conversation_id: str | None = None,
) -> DesignTruth:
    """Project an approved architecture into a read-only design fact snapshot."""

    checked = validate_copy(architecture_design, ArchitectureDesign)
    if checked.status is not ArchitectureDesignStatus.APPROVED:
        raise ValueError("design truth requires approved architecture")

    truth_id = _id(
        "design-truth",
        checked.project_id,
        checked.architecture_id,
        checked.fingerprint,
    )
    provenance = PlanProvenance.create(
        project_id=checked.project_id,
        source_type="ARCHITECTURE",
        source_id=checked.architecture_id,
        source_fingerprint=checked.fingerprint,
    )
    bindings: list[DesignEvidenceBinding] = [
        DesignEvidenceBinding.create(
            binding_id=_id("design-evidence", truth_id, "truth"),
            entity_id=truth_id,
            project_id=checked.project_id,
            source=DesignSource.ARCHITECTURE,
            source_id=checked.architecture_id,
            artifact_ref=checked.architecture_id,
            locator="architecture",
            source_fingerprint=checked.fingerprint,
            relationship="derived from approved architecture design",
            truth_level=TruthLevel.EXPLICIT,
        )
    ]

    component_map: dict[str, str] = {}
    components: list[DesignComponent] = []
    for item in checked.system_components:
        component_id = _id("design-component", truth_id, item.component_id)
        component_map[item.component_id] = component_id
        binding_id = _id("design-evidence", truth_id, "component", item.component_id)
        bindings.append(
            DesignEvidenceBinding.create(
                binding_id=binding_id,
                entity_id=component_id,
                project_id=checked.project_id,
                source=DesignSource.ARCHITECTURE,
                source_id=item.component_id,
                artifact_ref=checked.architecture_id,
                locator=f"component:{item.component_id}",
                source_fingerprint=item.fingerprint,
                relationship="projects architecture component",
                truth_level=_TRUTH_LEVEL[item.confidence],
            )
        )
        components.append(
            DesignComponent.create(
                component_id=component_id,
                name=item.name,
                type=_COMPONENT_TYPES.get(item.category, item.category.lower()),
                category=item.category,
                description=item.responsibility,
                confidence=_CONFIDENCE[item.confidence],
                truth_level=_TRUTH_LEVEL[item.confidence],
                evidence_refs=(binding_id,),
            )
        )

    interfaces: list[DesignInterface] = []
    for item in checked.interfaces:
        source = component_map[item.source_component_id]
        target = component_map[item.target_component_id]
        binding_id = _id("design-evidence", truth_id, "interface", item.interface_id)
        bindings.append(
            DesignEvidenceBinding.create(
                binding_id=binding_id,
                entity_id=_id("design-interface", truth_id, item.interface_id),
                project_id=checked.project_id,
                source=DesignSource.ARCHITECTURE,
                source_id=item.interface_id,
                artifact_ref=checked.architecture_id,
                locator=f"interface:{item.interface_id}",
                source_fingerprint=item.fingerprint,
                relationship="projects architecture interface",
                truth_level=_TRUTH_LEVEL[item.confidence],
            )
        )
        interfaces.append(
            DesignInterface.create(
                interface_id=_id("design-interface", truth_id, item.interface_id),
                source=source,
                target=target,
                protocol=item.protocol,
                direction="SOURCE_TO_TARGET",
                confidence=_CONFIDENCE[item.confidence],
                truth_level=_TRUTH_LEVEL[item.confidence],
                evidence_refs=(binding_id,),
            )
        )

    return DesignTruth.create(
        truth_id=truth_id,
        tenant_id=tenant_id,
        project_id=checked.project_id,
        design_revision="v1",
        conversation_id=conversation_id or "legacy-conversation",
        origin=DesignTruthOrigin.CREATE,
        architecture_id=checked.architecture_id,
        components=tuple(components),
        interfaces=tuple(interfaces),
        evidence_bindings=tuple(bindings),
        provenance=(provenance,),
    )


def _attributes(evidence: EngineeringEvidence) -> dict[str, str]:
    return dict(evidence.attributes)


def _attr(evidence: EngineeringEvidence, name: str, default: str = "") -> str:
    return _attributes(evidence).get(name, default)


def _safe_entity_id(prefix: str, truth_id: str, evidence: EngineeringEvidence) -> str:
    return _id(prefix, truth_id, evidence.evidence_id)


def _reverse_truth_id(bundle: EngineeringEvidenceBundle) -> str:
    return _id(
        "design-truth",
        bundle.project_id,
        bundle.conversation_id,
        bundle.bundle_fingerprint,
    )


def _reverse_binding(
    *,
    truth_id: str,
    entity_id: str,
    evidence: EngineeringEvidence,
    tenant_id: str,
) -> DesignEvidenceBinding:
    artifact_ref = evidence.artifact_id or evidence.source.value.lower()
    return DesignEvidenceBinding.create(
        binding_id=_id("design-evidence", truth_id, evidence.evidence_id, entity_id),
        entity_id=entity_id,
        project_id=evidence.project_id,
        source=evidence.source,
        source_id=evidence.artifact_id or evidence.evidence_id,
        artifact_ref=artifact_ref,
        locator=evidence.locator,
        artifact_revision=evidence.artifact_version,
        evidence_id=evidence.evidence_id,
        artifact_content_fingerprint=evidence.content_fingerprint,
        source_fingerprint=evidence.content_fingerprint,
        relationship="derived from approved engineering evidence",
        truth_level=evidence.truth_level,
    )


def _component_type(category: str, name: str) -> str:
    normalized = category.upper()
    if normalized == "MCU" or "STM32" in name.upper() or "ESP32" in name.upper():
        return "controller"
    return _COMPONENT_TYPES.get(normalized, normalized.casefold() or "component")


def _build_from_evidence(
    bundle: EngineeringEvidenceBundle,
    *,
    tenant_id: str | None = None,
) -> DesignTruth:
    if type(bundle) is not EngineeringEvidenceBundle:
        raise TypeError("engineering evidence bundle is invalid")
    checked = EngineeringEvidenceBundle.model_validate(
        bundle.model_dump(mode="python")
    )
    if checked.status is not EngineeringEvidenceStatus.APPROVED:
        raise ValueError("design truth requires approved engineering evidence")
    if any(
        item.verification_status is not EngineeringEvidenceStatus.APPROVED
        for item in checked.evidence
    ):
        raise ValueError("design truth evidence is not approved")
    active_tenant = checked.tenant_id if tenant_id is None else tenant_id
    if active_tenant != checked.tenant_id:
        raise ValueError("engineering evidence tenant mismatch")

    truth_id = _reverse_truth_id(checked)
    bindings: list[DesignEvidenceBinding] = []
    components: list[DesignComponent] = []
    component_by_reference: dict[str, str] = {}
    component_by_name: dict[str, str] = {}

    for evidence in checked.evidence:
        if evidence.evidence_type not in {
            EvidenceType.SCHEMATIC_COMPONENT,
            EvidenceType.BOM_COMPONENT,
        }:
            continue
        reference = _attr(evidence, "reference") or evidence.claim.split(" ", 1)[0]
        name = _attr(evidence, "name") or _attr(evidence, "value")
        if not name:
            parts = evidence.claim.split(" ", 1)
            name = parts[1] if len(parts) == 2 else evidence.claim
        category = _attr(evidence, "category", "COMPONENT")
        component_id = _safe_entity_id("design-component", truth_id, evidence)
        if reference in component_by_reference or name in component_by_name:
            continue
        component_by_reference[reference] = component_id
        component_by_name[name] = component_id
        binding = _reverse_binding(
            truth_id=truth_id,
            entity_id=component_id,
            evidence=evidence,
            tenant_id=active_tenant,
        )
        bindings.append(binding)
        components.append(
            DesignComponent.create(
                component_id=component_id,
                name=name,
                type=_component_type(category, name),
                category=category,
                description=_attr(evidence, "description", "Observed component"),
                confidence=evidence.confidence,
                truth_level=evidence.truth_level,
                evidence_refs=(binding.binding_id,),
            )
        )

    signals: list[DesignSignal] = []
    nets: list[NetBinding] = []
    signal_by_name: dict[str, str] = {}
    for evidence in checked.evidence:
        if evidence.evidence_type is not EvidenceType.SCHEMATIC_NET:
            continue
        name = _attr(evidence, "name") or _attr(evidence, "signal") or evidence.claim
        source_ref = _attr(evidence, "source")
        target_refs = tuple(
            item for item in _attr(evidence, "targets").split(",") if item
        )
        source_id = component_by_reference.get(source_ref, source_ref)
        targets = tuple(component_by_reference.get(item, item) for item in target_refs)
        if not source_id or not targets:
            continue
        if source_id not in set(component_by_reference.values()) or any(
            item not in set(component_by_reference.values()) for item in targets
        ):
            continue
        signal_id = _safe_entity_id("design-signal", truth_id, evidence)
        if name in signal_by_name:
            continue
        signal_by_name[name] = signal_id
        binding = _reverse_binding(
            truth_id=truth_id, entity_id=signal_id, evidence=evidence, tenant_id=active_tenant
        )
        bindings.append(binding)
        signals.append(
            DesignSignal.create(
                signal_id=signal_id,
                name=name,
                type=_attr(evidence, "signal_type", "ELECTRICAL"),
                source=source_id,
                targets=targets,
                protocol=_attr(evidence, "protocol", "UNKNOWN"),
                direction=_attr(evidence, "direction", "SOURCE_TO_TARGET"),
                confidence=evidence.confidence,
                truth_level=evidence.truth_level,
                evidence_refs=(binding.binding_id,),
            )
        )
        net_id = _safe_entity_id("design-net", truth_id, evidence)
        net_binding = _reverse_binding(
            truth_id=truth_id,
            entity_id=net_id,
            evidence=evidence,
            tenant_id=active_tenant,
        )
        bindings.append(net_binding)
        nets.append(
            NetBinding.create(
                binding_id=net_id,
                net_name=name,
                nodes=(source_id, *targets),
                signal=signal_id,
                purpose=_attr(evidence, "purpose", "explicit schematic net"),
                confidence=evidence.confidence,
                truth_level=evidence.truth_level,
                evidence_refs=(net_binding.binding_id,),
            )
        )

    pins: list[DesignPin] = []
    peripherals: list[PeripheralBinding] = []
    for evidence in checked.evidence:
        if evidence.evidence_type is not EvidenceType.SCHEMATIC_PIN:
            continue
        attrs = _attributes(evidence)
        component_ref = attrs.get("component_ref", "")
        component_id = component_by_reference.get(component_ref)
        if component_id is None:
            # A pin without a confirmed component is deliberately not promoted.
            continue
        pin_name = attrs.get("pin_name") or evidence.claim.split(" ", 1)[0]
        pin_number = attrs.get("pin_number", pin_name)
        function = attrs.get("function", "UNKNOWN")
        signal_id = signal_by_name.get(attrs.get("signal", ""))
        pin_id = _safe_entity_id("design-pin", truth_id, evidence)
        binding = _reverse_binding(
            truth_id=truth_id, entity_id=pin_id, evidence=evidence, tenant_id=active_tenant
        )
        bindings.append(binding)
        pins.append(
            DesignPin.create(
                pin_id=pin_id,
                component_id=component_id,
                pin_name=pin_name,
                pin_number=pin_number,
                function=function,
                signal_id=signal_id,
                confidence=evidence.confidence,
                truth_level=evidence.truth_level,
                evidence_refs=(binding.binding_id,),
            )
        )
        if function != "UNKNOWN" and ("TIM" in function.upper() or "PWM" in function.upper()):
            peripheral_id = _safe_entity_id("peripheral", truth_id, evidence)
            peripheral_binding = _reverse_binding(
                truth_id=truth_id,
                entity_id=peripheral_id,
                evidence=evidence,
                tenant_id=active_tenant,
            )
            bindings.append(peripheral_binding)
            peripherals.append(
                PeripheralBinding.create(
                    binding_id=peripheral_id,
                    peripheral=function.split(" ", 1)[0],
                    component=component_id,
                    pin_refs=(pin_id,),
                    function="PWM Output" if "PWM" in function.upper() or "TIM" in function.upper() else function,
                    confidence=evidence.confidence,
                    truth_level=evidence.truth_level,
                    evidence_refs=(peripheral_binding.binding_id,),
                )
            )

    firmware_bindings: list[FirmwareBinding] = []
    for evidence in checked.evidence:
        if evidence.evidence_type is not EvidenceType.FIRMWARE_SYMBOL:
            continue
        attrs = _attributes(evidence)
        hardware_refs = tuple(
            item
            for key in ("pin_id", "peripheral_id", "net_id")
            for item in (attrs.get(key, ""),)
            if item
        )
        # Firmware evidence alone can only be promoted when it names an
        # already confirmed hardware entity.
        known_hardware = {item.pin_id for item in pins} | {
            item.binding_id for item in peripherals
        }
        if not hardware_refs or not set(hardware_refs) <= known_hardware:
            continue
        binding_id = _safe_entity_id("firmware-binding", truth_id, evidence)
        binding = _reverse_binding(
            truth_id=truth_id, entity_id=binding_id, evidence=evidence, tenant_id=active_tenant
        )
        bindings.append(binding)
        firmware_bindings.append(
            FirmwareBinding.create(
                binding_id=binding_id,
                firmware_symbol=attrs.get("symbol", evidence.claim),
                source_file=attrs.get("source_file", evidence.locator),
                hardware_refs=hardware_refs,
                function=attrs.get("function", "firmware hardware binding"),
                truth_level=evidence.truth_level,
                evidence_refs=(binding.binding_id,),
            )
        )

    if not bindings:
        raise ValueError("approved engineering evidence contains no promotable fact")
    provenance = tuple(
        PlanProvenance.create(
            project_id=checked.project_id,
            source_type=evidence.source.value,
            source_id=evidence.artifact_id or evidence.evidence_id,
            source_fingerprint=evidence.content_fingerprint,
        )
        for evidence in checked.evidence
    )
    return DesignTruth.create(
        truth_id=truth_id,
        tenant_id=active_tenant,
        project_id=checked.project_id,
        conversation_id=checked.conversation_id,
        design_revision="v1",
        origin=DesignTruthOrigin.REVERSE,
        architecture_id=None,
        components=tuple(components),
        signals=tuple(signals),
        pins=tuple(pins),
        peripherals=tuple(peripherals),
        nets=tuple(nets),
        firmware_bindings=tuple(firmware_bindings),
        evidence_bindings=tuple(bindings),
        provenance=provenance,
    )


class DesignTruthBuilder:
    """Build DesignTruth only from an approved architecture or evidence bundle."""

    def build_from_approved_architecture(
        self,
        architecture_design: ArchitectureDesign,
        *,
        tenant_id: str = "default-tenant",
        conversation_id: str | None = None,
        architecture_review: object | None = None,
    ) -> DesignTruth:
        checked = validate_copy(architecture_design, ArchitectureDesign)
        if architecture_review is None:
            raise ValueError("design truth requires architecture review")
        if architecture_review is not None:
            status = getattr(architecture_review, "status", None)
            proposal_fingerprint = getattr(architecture_review, "proposal_fingerprint", None)
            if getattr(status, "value", status) != "APPROVED" or proposal_fingerprint != checked.content_fingerprint:
                raise ValueError("architecture review fingerprint mismatch")
        return _build_from_architecture(
            checked, tenant_id=tenant_id, conversation_id=conversation_id
        )

    def build_from_approved_evidence(
        self, evidence_bundle: EngineeringEvidenceBundle
    ) -> DesignTruth:
        return _build_from_evidence(evidence_bundle)

    @staticmethod
    def merge(existing: DesignTruth, incoming: DesignTruth) -> DesignTruth:
        current = validate_copy(existing, DesignTruth)
        candidate = validate_copy(incoming, DesignTruth)
        if current.project_id != candidate.project_id or current.tenant_id != candidate.tenant_id:
            raise ValueError("design truth identity conflict")
        if current.origin is not candidate.origin:
            raise ValueError("design truth origin conflict")
        if current.fingerprint == candidate.fingerprint:
            return current
        current_bindings = {item.entity_id: item for item in current.evidence_bindings}
        candidate_bindings = {item.entity_id: item for item in candidate.evidence_bindings}
        for entity_id, previous in current_bindings.items():
            incoming = candidate_bindings.get(entity_id)
            if previous.truth_level is TruthLevel.INVALIDATED:
                if incoming != previous:
                    raise ValueError("invalidated design truth cannot be restored")
                continue
            if incoming is None:
                continue
            if (
                previous.truth_level is TruthLevel.EXPLICIT
                and incoming.truth_level is TruthLevel.EXPLICIT
                and previous.source_fingerprint != incoming.source_fingerprint
            ):
                raise ValueError("design truth explicit evidence conflict")
            if (
                previous.truth_level is TruthLevel.INFERRED
                and incoming.truth_level is TruthLevel.EXPLICIT
            ):
                raise ValueError("inferred design truth cannot be promoted without basis")
        return candidate


def transition_engineering_evidence(
    bundle: EngineeringEvidenceBundle,
    status: EngineeringEvidenceStatus,
) -> EngineeringEvidenceBundle:
    checked = EngineeringEvidenceBundle.model_validate(bundle.model_dump(mode="python"))
    target = EngineeringEvidenceStatus(status)
    allowed = {
        EngineeringEvidenceStatus.CANDIDATE: {EngineeringEvidenceStatus.REVIEWING, EngineeringEvidenceStatus.REJECTED},
        EngineeringEvidenceStatus.REVIEWING: {EngineeringEvidenceStatus.APPROVED, EngineeringEvidenceStatus.REJECTED},
        EngineeringEvidenceStatus.APPROVED: {EngineeringEvidenceStatus.SUPERSEDED, EngineeringEvidenceStatus.INVALIDATED},
    }
    if target not in allowed.get(checked.status, set()):
        raise ValueError("invalid engineering evidence status transition")
    values = checked.model_dump(mode="python")
    values["status"] = target
    values["evidence"] = tuple(
        item.model_copy(
            update={
                "verification_status": target
                if target
                in {
                    EngineeringEvidenceStatus.REVIEWING,
                    EngineeringEvidenceStatus.APPROVED,
                    EngineeringEvidenceStatus.REJECTED,
                    EngineeringEvidenceStatus.SUPERSEDED,
                    EngineeringEvidenceStatus.INVALIDATED,
                }
                else item.verification_status,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        for item in checked.evidence
    )
    values["fingerprint"] = "sha256:" + "0" * 64
    values["bundle_fingerprint"] = checked.bundle_fingerprint
    return EngineeringEvidenceBundle.model_validate(values)


def generate_design_truth(
    architecture_design: ArchitectureDesign,
    *,
    conversation_id: str | None = None,
) -> DesignTruth:
    """Compatibility entry point for the approved Create-mode architecture path."""

    # Keep the historical helper available to existing application callers.
    # New Create-mode orchestration must use the builder with its review
    # decision so the approval fingerprint is checked at the boundary.
    return _build_from_architecture(
        architecture_design, conversation_id=conversation_id
    )


__all__ = (
    "DesignTruthBuilder",
    "generate_design_truth",
    "transition_engineering_evidence",
)
