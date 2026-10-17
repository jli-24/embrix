from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from embedded_copilot.engineering_consistency import (
    ConsistencyInput,
    ConsistencyOverallStatus,
    HardwareFirmwareConsistencyResult,
    VerificationLevel,
)
from embedded_copilot.engineering_pcb.models import (
    PCBBoardArtifact,
    PCBBOMArtifact,
    PCBBOMItem,
    PCBComponent,
    PCBPopulation,
    PCBSchematicArtifact,
)

from .contracts import (
    AssemblyCoverage,
    AssemblyEvidence,
    AssemblyStatus,
    BindingStatus,
    ComponentBinding,
    ElectricalModelBinding,
    FootprintClass,
    GapSeverity,
    GateStatus,
    MechanicalModelBinding,
    ModelAvailability,
    ModelType,
    PinPadBinding,
    PinPadStatus,
    PowerDomainBinding,
    SimulationGap,
    VirtualAssemblyInterface,
    VirtualBoardAssembly,
)


class ArtifactReaderPort(Protocol):
    def read(self, relative_path: str) -> bytes: ...


class ModelCatalogPort(Protocol):
    def electrical_model(
        self, item: PCBBOMItem, footprint_class: FootprintClass
    ) -> ElectricalModelBinding: ...


@dataclass(frozen=True, slots=True)
class VirtualAssemblySource:
    """Existing canonical projections plus parsed KiCad artifacts.

    This is an in-process composition object. It is never persisted; only the
    resulting VirtualBoardAssembly is persisted.
    """

    consistency_input: ConsistencyInput
    consistency_result: HardwareFirmwareConsistencyResult
    schematic: PCBSchematicArtifact
    board: PCBBoardArtifact
    bom: PCBBOMArtifact
    artifact_reader: ArtifactReaderPort
    mechanical_models: tuple[MechanicalModelBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class InMemoryArtifactReader:
    contents: dict[str, bytes]

    def read(self, relative_path: str) -> bytes:
        try:
            return bytes(self.contents[relative_path])
        except KeyError as exc:
            raise FileNotFoundError("artifact is unavailable") from exc


class RuleBasedModelCatalog:
    """Small explicit capability catalog; it does not execute any backend."""

    def electrical_model(
        self, item: PCBBOMItem, footprint_class: FootprintClass
    ) -> ElectricalModelBinding:
        value = item.value.casefold()
        reference = item.references[0]
        evidence = (f"bom-{reference}",)
        if reference.upper().startswith("U") and "stm32" in value:
            return ElectricalModelBinding.create(
                component_reference=reference,
                model_type=ModelType.UNAVAILABLE,
                availability=ModelAvailability.UNAVAILABLE,
                model_id=None,
                version="catalog-v1",
                source="Embrix model catalog",
                provenance="No installed or registered STM32F103C8T6 execution model",
                confidence=1.0,
                evidence_refs=evidence,
            )
        if "button" in item.description.casefold() or reference.upper().startswith("SW"):
            model_type = ModelType.BEHAVIORAL
            model_id = "embrix-behavioral-switch-v1"
            provenance = "Approved engineering approximation"
        elif value.endswith(("nf", "uf")) or "capacitor" in value:
            model_type = ModelType.IDEAL
            model_id = "ideal-capacitor-v1"
            provenance = "Approved engineering approximation"
        elif value.endswith(("k", "r")) or "resistor" in value:
            model_type = ModelType.IDEAL
            model_id = "ideal-resistor-v1"
            provenance = "Approved engineering approximation"
        elif value == "led" or reference.upper().startswith("D"):
            model_type = ModelType.BEHAVIORAL
            model_id = "embrix-led-behavior-v1"
            provenance = "Approved engineering approximation; not vendor-accurate"
        elif reference.upper().startswith("J"):
            model_type = ModelType.IDEAL
            model_id = "ideal-connector-v1"
            provenance = "Approved engineering approximation"
        else:
            model_type = ModelType.NONE
            model_id = None
            provenance = "No registered electrical model"
        availability = (
            ModelAvailability.AVAILABLE
            if model_type not in {ModelType.NONE, ModelType.UNAVAILABLE}
            else ModelAvailability.UNAVAILABLE
        )
        return ElectricalModelBinding.create(
            component_reference=reference,
            model_type=model_type,
            availability=availability,
            model_id=model_id,
            version="catalog-v1",
            source="Embrix model catalog",
            provenance=provenance,
            confidence=1.0 if model_type is not ModelType.BEHAVIORAL else 0.75,
            evidence_refs=evidence,
        )


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _normalized_net(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    aliases = {
        "3V3": "3V3_MCU",
        "GND": "GND_MCU",
        "LED_PA8": "LED",
        "BUTTON_PB0": "BUTTON",
        "VDDA": "3V3_MCU",
        "VDD_1": "3V3_MCU",
        "VDD_2": "3V3_MCU",
        "VDD_3": "3V3_MCU",
        "VSSA": "GND_MCU",
        "VSS_1": "GND_MCU",
        "VSS_2": "GND_MCU",
        "VSS_3": "GND_MCU",
    }
    return aliases.get(normalized, normalized)


def _coverage(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 4) if denominator else 100.0


def _component_role(item: PCBBOMItem) -> str:
    if item.references[0].upper().startswith("U"):
        return "MCU"
    if item.references[0].upper().startswith("J"):
        return "CONNECTOR"
    if item.references[0].upper().startswith("D"):
        return "INDICATOR"
    if item.references[0].upper().startswith("SW"):
        return "INPUT"
    return "PASSIVE"


def _is_dnp(item: PCBBOMItem) -> bool:
    value = f"{item.value} {item.description or ''}".casefold()
    return item.population is PCBPopulation.DNP or any(
        marker in value for marker in ("dnp", "do not populate", "not fitted")
    )


def classify_footprint(component: PCBComponent, bom_refs: set[str]) -> FootprintClass:
    reference = component.reference.upper()
    footprint = (component.footprint or "").casefold()
    if reference.startswith("MH") or "mountinghole" in footprint or "mounting_hole" in footprint:
        return FootprintClass.MOUNTING_HOLE
    if reference.startswith("FID") or "fiducial" in footprint:
        return FootprintClass.FIDUCIAL
    if reference.startswith("TP") or "testpoint" in footprint or "test_point" in footprint:
        return FootprintClass.TEST_POINT
    if reference not in bom_refs:
        return FootprintClass.UNKNOWN
    return FootprintClass.ELECTRICAL_COMPONENT


def _gate_for_presence(present: bool, conflict: bool = False) -> GateStatus:
    if conflict:
        return GateStatus.CONFLICT
    return GateStatus.PASS if present else GateStatus.INCOMPLETE


class VirtualAssemblyService:
    """Deterministically projects canonical hardware and firmware into assembly state."""

    def __init__(
        self,
        *,
        model_catalog: ModelCatalogPort | None = None,
    ) -> None:
        self._models = model_catalog or RuleBasedModelCatalog()

    def assemble(self, source: VirtualAssemblySource) -> VirtualBoardAssembly:
        checked = self._copy_source(source)
        current_integrity, stale_inputs = self._artifact_integrity(checked)
        truth = checked.consistency_input.truth
        hardware = checked.consistency_input.hardware
        build = checked.consistency_input.build
        bom_items = tuple(sorted(checked.bom.items, key=lambda item: item.references[0]))
        schematic_components = {item.reference: item for item in checked.schematic.components}
        board_components = {item.reference: item for item in checked.board.components}
        bom_by_ref = {reference: item for item in bom_items for reference in item.references}
        bom_refs = set(bom_by_ref)

        component_bindings: list[ComponentBinding] = []
        mechanical_bindings: list[MechanicalModelBinding] = []
        electrical_bindings: list[ElectricalModelBinding] = []
        gaps: list[SimulationGap] = []
        component_conflict = False
        component_incomplete = False
        footprint_conflict = False

        provided_mechanical = {item.component_reference: item for item in checked.mechanical_models}
        for reference in sorted(bom_refs):
            item = bom_by_ref[reference]
            schematic = schematic_components.get(reference)
            board = board_components.get(reference)
            dnp = _is_dnp(item)
            value_conflict = bool(
                schematic is not None
                and schematic.value is not None
                and schematic.value.casefold() != item.value.casefold()
            )
            footprint_conflict_for_component = bool(
                schematic is not None
                and board is not None
                and schematic.footprint
                and board.footprint
                and schematic.footprint.rsplit(":", 1)[-1].casefold()
                != board.footprint.rsplit(":", 1)[-1].casefold()
            )
            if value_conflict:
                component_conflict = True
            if footprint_conflict_for_component:
                footprint_conflict = True
            if not dnp and (schematic is None or board is None):
                component_incomplete = True
            footprint_class = (
                FootprintClass.DNP if dnp and board is None else
                classify_footprint(board, bom_refs) if board is not None else
                FootprintClass.ELECTRICAL_COMPONENT
            )
            electrical = self._models.electrical_model(item, footprint_class)
            electrical_bindings.append(electrical)
            if electrical.availability is not ModelAvailability.AVAILABLE:
                if reference.upper().startswith("U"):
                    gaps.append(
                        SimulationGap.create(
                            gap_id=f"simulation-gap-{reference.lower()}-execution-model",
                            component_reference=reference,
                            required_behavior="Execute the current firmware ELF against the exact MCU target",
                            missing_model="STM32F103C8T6 firmware execution backend",
                            impact="Firmware functional simulation cannot run without an MCU model",
                            severity=GapSeverity.BLOCKING,
                            blocking=True,
                            recommended_resolution="Select an approved backend that provides a verified STM32F103C8T6 execution model",
                            evidence_refs=("model-catalog",),
                        )
                    )
                elif footprint_class is FootprintClass.ELECTRICAL_COMPONENT:
                    gaps.append(
                        SimulationGap.create(
                            gap_id=f"simulation-gap-{reference.lower()}-electrical-model",
                            component_reference=reference,
                            required_behavior="Represent the component in a future simulation scenario",
                            missing_model="Registered electrical model",
                            impact="This component requires an explicit model decision before scenario execution",
                            severity=GapSeverity.NON_BLOCKING,
                            blocking=False,
                            recommended_resolution="Register a bounded SPICE, ideal, or behavioral model",
                            evidence_refs=("model-catalog",),
                        )
                    )
            if reference in provided_mechanical:
                mechanical = provided_mechanical[reference]
            else:
                mechanical = MechanicalModelBinding.create(
                    component_reference=reference,
                    status=ModelAvailability.MISSING,
                    model_reference=None,
                    model_id=None,
                    model_type=ModelType.NONE,
                    version=None,
                    provenance="KiCad footprint contained no observed 3D model binding",
                    evidence_refs=("parser-pcb",),
                )
            mechanical_bindings.append(mechanical)
            if (
                not dnp
                and mechanical.status is ModelAvailability.MISSING
            ):
                gaps.append(
                    SimulationGap.create(
                        gap_id=f"simulation-gap-{reference.lower()}-mechanical-model",
                        component_reference=reference,
                        required_behavior="Mechanical assembly visualization and enclosure-fit review",
                        missing_model="Ordinary 3D mechanical model",
                        impact="Mechanical-only review remains incomplete; firmware execution is unaffected",
                        severity=GapSeverity.NON_BLOCKING,
                        blocking=False,
                        recommended_resolution="Register an approved STEP or WRL model when mechanical review requires it",
                        evidence_refs=("parser-pcb",),
                    )
                )
            if electrical.model_type is ModelType.BEHAVIORAL and "not vendor-accurate" in electrical.provenance:
                gaps.append(
                    SimulationGap.create(
                        gap_id=f"simulation-gap-{reference.lower()}-vendor-model",
                        component_reference=reference,
                        required_behavior="Represent the component response without vendor-specific parameters",
                        missing_model="Vendor-accurate model",
                        impact="The approximation is suitable only for bounded future scenarios",
                        severity=GapSeverity.NON_BLOCKING,
                        blocking=False,
                        recommended_resolution="Replace the approximation with an approved vendor or measured model when required",
                        evidence_refs=("model-catalog",),
                    )
                )
            if dnp:
                binding_status = BindingStatus.NOT_APPLICABLE
            elif value_conflict:
                binding_status = BindingStatus.PARTIAL
            elif schematic is None or board is None or not board.footprint:
                binding_status = BindingStatus.UNBOUND if board is None else BindingStatus.PARTIAL
            else:
                binding_status = BindingStatus.BOUND
            component_bindings.append(
                ComponentBinding.create(
                    reference=reference,
                    component_id=f"component-{reference}",
                    value=item.value,
                    part_number=item.manufacturer_part_number,
                    role=_component_role(item),
                    symbol_library_id=(item.symbol.split(":", 1)[0] if item.symbol and ":" in item.symbol else item.symbol),
                    symbol_reference=(item.symbol.split(":", 1)[1] if item.symbol and ":" in item.symbol else item.symbol),
                    footprint_library_id="KiCad" if board is not None and board.footprint else None,
                    footprint_reference=board.footprint if board is not None else None,
                    pcb_instance_reference=board.reference if board is not None else None,
                    footprint_class=footprint_class,
                    population_status="NOT_POPULATED" if dnp else "POPULATED",
                    binding_status=binding_status,
                    binding_evidence=("artifact-bom", "parser-schematic", "parser-pcb"),
                    mechanical_model_status=mechanical.status,
                    mechanical_model_reference=mechanical.model_reference,
                    electrical_model=electrical.model_type,
                    simulation_capability=electrical.model_type,
                    simulation_gap_ids=tuple(
                        gap.gap_id for gap in gaps if gap.component_reference == reference
                    ),
                )
            )

        pin_pad_bindings = self._pin_pad_bindings(checked)
        pin_statuses = tuple(item.status for item in pin_pad_bindings)
        pin_pad_status = (
            GateStatus.CONFLICT if PinPadStatus.CONFLICT in pin_statuses else
            GateStatus.PASS if pin_statuses and all(item is PinPadStatus.PASS for item in pin_statuses) else
            GateStatus.INCOMPLETE
        )
        schematic_refs = set(schematic_components)
        board_refs = set(board_components)
        populated_bom_refs = {ref for ref, item in bom_by_ref.items() if not _is_dnp(item)}
        schematic_conflict = component_conflict
        schematic_gate = _gate_for_presence(populated_bom_refs.issubset(schematic_refs), schematic_conflict)
        pcb_gate = _gate_for_presence(populated_bom_refs.issubset(board_refs))
        bom_gate = _gate_for_presence(
            schematic_refs.intersection(bom_refs) == schematic_refs,
            schematic_conflict,
        )
        component_gate = (
            GateStatus.CONFLICT if component_conflict else
            GateStatus.INCOMPLETE if component_incomplete else GateStatus.PASS
        )
        footprint_gate = (
            GateStatus.CONFLICT
            if footprint_conflict
            else
            GateStatus.INCOMPLETE
            if any(item.binding_status is BindingStatus.UNBOUND for item in component_bindings if item.population_status == "POPULATED")
            else GateStatus.PASS
        )
        truth_gate = self._truth_binding(checked)
        firmware_gate = self._firmware_binding(checked)
        consistency_gate = self._consistency_binding(checked.consistency_result)
        interfaces = self._interfaces(checked, pin_pad_bindings)
        power_domains = self._power_domains(checked)
        coverage = self._coverage(component_bindings, board_components, bom_by_ref, mechanical_bindings, electrical_bindings)
        evidence = self._evidence(checked, coverage, pin_pad_bindings)
        blocking_count = sum(item.blocking for item in gaps)
        non_blocking_count = len(gaps) - blocking_count
        status = self._status(
            artifact_integrity=current_integrity,
            truth=truth_gate,
            schematic=schematic_gate,
            pcb=pcb_gate,
            bom=bom_gate,
            component=component_gate,
            footprint=footprint_gate,
            pin_pad=pin_pad_status,
            firmware=firmware_gate,
            consistency=consistency_gate,
            gaps=tuple(gaps),
            stale_inputs=stale_inputs,
        )
        source_fp = self._source_fingerprint(checked)
        assembly_id = "assembly-" + source_fp.removeprefix("sha256:")[:32]
        return VirtualBoardAssembly.create(
            assembly_id=assembly_id,
            project_id=checked.consistency_input.project_id,
            conversation_id=checked.consistency_input.conversation_id,
            design_truth_id=truth.truth_id,
            design_truth_fingerprint=truth.design_truth_fingerprint,
            hardware_design_id=(
                checked.consistency_input.firmware_specification.hardware_design_id
                if checked.consistency_input.firmware_specification is not None
                else None
            ),
            schematic_artifact_id=hardware.schematic.artifact_id,
            schematic_sha256=hardware.schematic.content_fingerprint,
            pcb_artifact_id=hardware.pcb.artifact_id,
            pcb_sha256=hardware.pcb.content_fingerprint,
            bom_artifact_id=hardware.bom.artifact_id,
            bom_sha256=hardware.bom.content_fingerprint,
            firmware_specification_id=checked.consistency_input.firmware_specification.firmware_spec_id if checked.consistency_input.firmware_specification else None,
            firmware_specification_fingerprint=checked.consistency_input.firmware_specification.fingerprint if checked.consistency_input.firmware_specification else None,
            build_configuration_id=build.build_configuration_id,
            build_configuration_fingerprint=build.build_configuration_fingerprint,
            elf_artifact_id=build.elf.artifact_id,
            elf_sha256=build.elf.content_fingerprint,
            bin_artifact_id=build.binary.artifact_id,
            bin_sha256=build.binary.content_fingerprint,
            hw_fw_consistency_result_id=checked.consistency_result.result_id,
            components=tuple(component_bindings),
            nets=tuple(power_domains),
            power_domains=tuple(power_domains),
            interfaces=tuple(interfaces),
            component_bindings=tuple(component_bindings),
            pin_pad_bindings=tuple(pin_pad_bindings),
            mechanical_model_bindings=tuple(mechanical_bindings),
            electrical_model_bindings=tuple(electrical_bindings),
            simulation_gaps=tuple(gaps),
            evidence=tuple(evidence),
            coverage=coverage,
            design_truth_binding=truth_gate,
            schematic_binding=schematic_gate,
            pcb_binding=pcb_gate,
            bom_binding=bom_gate,
            component_binding=component_gate,
            footprint_binding=footprint_gate,
            pin_pad_mapping=pin_pad_status,
            firmware_binding=firmware_gate,
            hw_fw_consistency_binding=consistency_gate,
            artifact_integrity=current_integrity,
            assembly_status=status,
            stale_inputs=tuple(stale_inputs),
            blocking_simulation_gaps=blocking_count,
            non_blocking_simulation_gaps=non_blocking_count,
        )

    def mark_stale(
        self, assembly: VirtualBoardAssembly, stale_inputs: tuple[str, ...]
    ) -> VirtualBoardAssembly:
        if not stale_inputs:
            return assembly
        payload = assembly.model_dump(mode="python")
        payload.update(
            {
                "assembly_status": AssemblyStatus.STALE,
                "stale_inputs": tuple(sorted(set(stale_inputs))),
                "updated_at": assembly.updated_at,
                "assembly_fingerprint": "sha256:" + "0" * 64,
            }
        )
        return VirtualBoardAssembly.model_validate(payload)

    @staticmethod
    def _copy_source(source: VirtualAssemblySource) -> VirtualAssemblySource:
        if not isinstance(source, VirtualAssemblySource):
            raise TypeError("virtual assembly source is invalid")
        return copy.deepcopy(source)

    @staticmethod
    def _artifact_integrity(
        source: VirtualAssemblySource,
    ) -> tuple[GateStatus, tuple[str, ...]]:
        hardware = source.consistency_input.hardware
        build = source.consistency_input.build
        expected = [hardware.schematic, hardware.pcb, hardware.bom, *build.source_artifacts, build.elf, build.binary]
        stale: list[str] = []
        parsed = (
            (source.schematic.artifact.artifact_id, source.schematic.artifact.content_fingerprint, hardware.schematic),
            (source.board.artifact.artifact_id, source.board.artifact.content_fingerprint, hardware.pcb),
            (source.bom.artifact.artifact_id, source.bom.artifact.content_fingerprint, hardware.bom),
        )
        for artifact_id, fingerprint, expected_artifact in parsed:
            if artifact_id != expected_artifact.artifact_id or fingerprint != expected_artifact.content_fingerprint:
                stale.append(expected_artifact.relative_path)
        for artifact in expected:
            try:
                content = source.artifact_reader.read(artifact.relative_path)
            except (FileNotFoundError, OSError, ValueError):
                stale.append(artifact.relative_path)
                continue
            if len(content) != artifact.size_bytes or _sha256(content) != artifact.content_fingerprint:
                stale.append(artifact.relative_path)
        return (GateStatus.PASS if not stale else GateStatus.FAIL, tuple(sorted(set(stale))))

    @staticmethod
    def _truth_binding(source: VirtualAssemblySource) -> GateStatus:
        truth = source.consistency_input.truth
        hardware = source.consistency_input.hardware
        if any(
            value is None
            for value in (truth.schematic_fingerprint, truth.pcb_fingerprint, truth.bom_fingerprint)
        ):
            return GateStatus.INCOMPLETE
        if (
            truth.project_id != source.consistency_input.project_id
            or truth.schematic_fingerprint != hardware.schematic.content_fingerprint
            or truth.pcb_fingerprint != hardware.pcb.content_fingerprint
            or truth.bom_fingerprint != hardware.bom.content_fingerprint
        ):
            return GateStatus.CONFLICT
        return GateStatus.PASS

    @staticmethod
    def _firmware_binding(source: VirtualAssemblySource) -> GateStatus:
        value = source.consistency_input
        build = value.build
        if value.elf_inspection is None or not build.elf or not build.binary:
            return GateStatus.INCOMPLETE
        spec = value.firmware_specification
        if (
            build.build_result_status != "BUILD_PASS"
            or build.target_kind != value.truth.mcu_exact_part
            or value.firmware.mcu_exact_part != value.truth.mcu_exact_part
            or spec is None
            or spec.mcu_exact_part != value.truth.mcu_exact_part
            or value.elf_inspection.artifact_fingerprint != build.elf.content_fingerprint
            or value.elf_inspection.binary_fingerprint != build.binary.content_fingerprint
        ):
            return GateStatus.CONFLICT
        return GateStatus.PASS

    @staticmethod
    def _consistency_binding(result: HardwareFirmwareConsistencyResult) -> GateStatus:
        if result.overall_status is ConsistencyOverallStatus.CONSISTENT and result.verification_level is VerificationLevel.L3_ENGINEERING_VERIFIED:
            return GateStatus.PASS
        if result.overall_status is ConsistencyOverallStatus.CONFLICT:
            return GateStatus.CONFLICT
        return GateStatus.INCOMPLETE

    def _pin_pad_bindings(self, source: VirtualAssemblySource) -> tuple[PinPadBinding, ...]:
        schematic = {item.reference: item for item in source.schematic.components}
        board = {item.reference: item for item in source.board.components}
        schematic_connections = {
            (item.component_reference, item.pin_number): item.net_id
            for item in source.schematic.connections
        }
        board_connections = {
            (item.component_reference, item.pin_number): item.net_id
            for item in source.board.connections
        }
        truth = source.consistency_input.truth
        required: list[tuple[str, str, str | None]] = [
            (item.signal, item.mcu_pin, None) for item in truth.pins
        ]
        mcu = schematic.get("U1")
        board_power_aliases = (
            ("3V3", frozenset({"3V3", "3V3_MCU", "VDD", "VDDA"})),
            ("GND", frozenset({"GND", "GND_MCU", "VSS", "VSSA"})),
        )
        for logical_pin, aliases in board_power_aliases:
            board_connection = next(
                (
                    item
                    for item in source.board.connections
                    if item.component_reference == "U1"
                    and _normalized_net(item.net_id) in aliases
                ),
                None,
            )
            if board_connection is None:
                continue
            symbol_pin = next(
                (
                    item
                    for item in (mcu.pins if mcu is not None else ())
                    if item.pin_number == board_connection.pin_number
                ),
                None,
            )
            required.append(
                (
                    logical_pin,
                    board_connection.pin_number,
                    symbol_pin.pin_name if symbol_pin is not None else None,
                )
            )
        result: list[PinPadBinding] = []
        for logical_pin, pin_number, expected_name in required:
            symbol_pin = next(
                (pin for pin in (mcu.pins if mcu is not None else ()) if pin.pin_number == pin_number or (expected_name and pin.pin_name == expected_name)),
                None,
            )
            schematic_net = schematic_connections.get(("U1", pin_number))
            pcb_net = board_connections.get(("U1", pin_number))
            status = PinPadStatus.UNKNOWN
            reason = "symbol, schematic net, and PCB pad evidence are required"
            board_mcu = board.get("U1")
            board_pin_numbers = {pin.pin_number for pin in board_mcu.pins} if board_mcu is not None else set()
            if symbol_pin is None or pin_number not in board_pin_numbers:
                status = PinPadStatus.UNKNOWN
                reason = "symbol pin or PCB pad is missing"
            elif schematic_net is None or pcb_net is None:
                wrong_pin = next(
                    (
                        (candidate_pin, candidate_net)
                        for (candidate_reference, candidate_pin), candidate_net in schematic_connections.items()
                        if candidate_reference == "U1"
                        and candidate_pin != pin_number
                        and _normalized_net(candidate_net) == _normalized_net(pcb_net)
                    ),
                    None,
                ) if pcb_net is not None else None
                if wrong_pin is not None:
                    status = PinPadStatus.CONFLICT
                    reason = f"schematic net is bound to U1 pin {wrong_pin[0]} instead of the expected symbol pin"
                else:
                    status = PinPadStatus.UNKNOWN
                    reason = "schematic net or PCB net is not exposed by the parser"
            elif _normalized_net(schematic_net) != _normalized_net(pcb_net):
                status = PinPadStatus.CONFLICT
                reason = "schematic net and PCB net disagree"
            else:
                status = PinPadStatus.PASS
                reason = "symbol pin, schematic net, footprint pad, and PCB net agree"
            result.append(
                PinPadBinding.create(
                    component_reference="U1",
                    logical_pin=logical_pin,
                    symbol_pin_number=symbol_pin.pin_number if symbol_pin else pin_number,
                    symbol_pin_name=symbol_pin.pin_name if symbol_pin else expected_name,
                    schematic_net=schematic_net,
                    footprint_pad_number=pin_number if pcb_net is not None else None,
                    pcb_net=pcb_net,
                    status=status,
                    reason=reason,
                    evidence_refs=("parser-schematic", "parser-pcb"),
                )
            )
        return tuple(result)

    @staticmethod
    def _power_domains(source: VirtualAssemblySource) -> tuple[PowerDomainBinding, ...]:
        board = source.board
        names = {connection.net_id for connection in board.connections}
        domains: list[PowerDomainBinding] = []
        for domain_id, aliases in (("power-3v3", ("3V3_MCU", "3V3")), ("power-ground", ("GND_MCU", "GND"))):
            matched = tuple(sorted(name for name in names if name.upper() in aliases))
            refs = tuple(sorted({item.component_reference for item in board.connections if item.net_id in matched}))
            if matched:
                domains.append(
                    PowerDomainBinding.create(
                        domain_id=domain_id,
                        net_names=matched,
                        component_references=refs,
                        supply_pin_references=tuple(sorted(f"{ref}:{pin}" for ref, pin, net in ((item.component_reference, item.pin_number, item.net_id) for item in board.connections) if net in matched)),
                        evidence_refs=("parser-pcb",),
                    )
                )
        return tuple(domains)

    @staticmethod
    def _interfaces(
        source: VirtualAssemblySource, pins: tuple[PinPadBinding, ...]
    ) -> tuple[VirtualAssemblyInterface, ...]:
        by_logical = {item.logical_pin: item for item in pins}
        definitions = (
            ("interface-swd", "SWD", "DEBUG", ("PA13-SWDIO", "PA14-SWCLK", "NRST")),
            ("interface-led", "LED", "OUTPUT", ("PA8",)),
            ("interface-button", "Button", "INPUT", ("PB0",)),
        )
        interfaces: list[VirtualAssemblyInterface] = []
        for interface_id, name, kind, signals in definitions:
            nets = tuple(
                item.pcb_net for signal in signals if (item := by_logical.get(signal)) is not None and item.pcb_net
            )
            interfaces.append(
                VirtualAssemblyInterface.create(
                    interface_id=interface_id,
                    name=name,
                    interface_type=kind,
                    logical_signals=signals,
                    net_names=nets,
                    description="Derived interface binding; execution is outside Virtual Assembly v1",
                    evidence_refs=("parser-pcb", "design-truth"),
                )
            )
        return tuple(interfaces)

    @staticmethod
    def _coverage(
        components: list[ComponentBinding],
        board: dict[str, PCBComponent],
        bom: dict[str, PCBBOMItem],
        mechanical: list[MechanicalModelBinding],
        electrical: list[ElectricalModelBinding],
    ) -> AssemblyCoverage:
        populated = [item for item in components if item.population_status == "POPULATED"]
        bound = [item for item in populated if item.binding_status is BindingStatus.BOUND]
        classes = [classify_footprint(component, set(bom)) for component in board.values()]
        available_mechanical = [item for item in mechanical if item.status is ModelAvailability.AVAILABLE]
        available_electrical = [item for item in electrical if item.availability is ModelAvailability.AVAILABLE]
        firmware_total = sum(item.reference.upper().startswith("U") for item in components)
        firmware_available = sum(
            item.reference.upper().startswith("U") and item.simulation_capability in {ModelType.RENODE, ModelType.WOKWI}
            for item in components
        )
        return AssemblyCoverage.create(
            total_bom_components=len(components),
            bound_components=len(bound),
            unbound_components=len(populated) - len(bound),
            total_pcb_footprints=len(board),
            bound_footprints=sum(item.pcb_instance_reference in board for item in components if item.population_status == "POPULATED"),
            electrical_footprints=classes.count(FootprintClass.ELECTRICAL_COMPONENT),
            mechanical_footprints=sum(item in {FootprintClass.MECHANICAL, FootprintClass.MOUNTING_HOLE} for item in classes),
            dnp_footprints=classes.count(FootprintClass.DNP),
            unknown_footprints=classes.count(FootprintClass.UNKNOWN),
            component_binding_coverage=_coverage(len(bound), len(populated)),
            footprint_binding_coverage=_coverage(sum(item.pcb_instance_reference in board for item in components if item.population_status == "POPULATED"), len(populated)),
            total_physical_components=len(populated),
            components_with_3d_model=len(available_mechanical),
            missing_3d_models=len(populated) - len(available_mechanical),
            mechanical_model_coverage=_coverage(len(available_mechanical), len(populated)),
            electrical_model_total=len(electrical),
            electrical_model_available=len(available_electrical),
            electrical_model_missing=len(electrical) - len(available_electrical),
            electrical_model_coverage=_coverage(len(available_electrical), len(electrical)),
            firmware_execution_model_total_required=firmware_total,
            firmware_execution_model_available=firmware_available,
            firmware_execution_model_missing=firmware_total - firmware_available,
            firmware_execution_model_coverage=_coverage(firmware_available, firmware_total),
            simulation_model_coverage=_coverage(len(available_electrical), len(electrical)),
        )

    @staticmethod
    def _evidence(
        source: VirtualAssemblySource,
        coverage: AssemblyCoverage,
        pins: tuple[PinPadBinding, ...],
    ) -> tuple[AssemblyEvidence, ...]:
        hardware = source.consistency_input.hardware
        build = source.consistency_input.build
        return (
            AssemblyEvidence.create(
                evidence_id="design-truth",
                source_kind="DETERMINISTIC_BINDING",
                source_id=source.consistency_input.truth.truth_id,
                claim="DesignTruth identity and artifact fingerprints were checked against the current canonical input",
                references=(source.consistency_input.truth.design_truth_fingerprint,),
            ),
            AssemblyEvidence.create(
                evidence_id="parser-schematic",
                source_kind="PARSER",
                source_id=hardware.schematic.artifact_id,
                claim="Schematic component and pin observations came from the existing KiCad schematic parser",
                references=(hardware.schematic.content_fingerprint,),
            ),
            AssemblyEvidence.create(
                evidence_id="parser-pcb",
                source_kind="PARSER",
                source_id=hardware.pcb.artifact_id,
                claim="PCB footprint, pad, and net observations came from the existing KiCad PCB parser",
                references=(hardware.pcb.content_fingerprint,),
            ),
            AssemblyEvidence.create(
                evidence_id="artifact-bom",
                source_kind="ARTIFACT_STORE",
                source_id=hardware.bom.artifact_id,
                claim="BOM identity and values were checked against the immutable artifact fingerprint",
                references=(hardware.bom.content_fingerprint,),
            ),
            AssemblyEvidence.create(
                evidence_id="firmware-build",
                source_kind="TOOL_OBSERVATION",
                source_id=build.elf.artifact_id,
                claim="ELF and BIN bindings were taken from the existing BuildSnapshot and consistency result",
                references=(build.elf.content_fingerprint, build.binary.content_fingerprint),
            ),
            AssemblyEvidence.create(
                evidence_id="model-catalog",
                source_kind="MODEL_CATALOG",
                source_id="model-catalog-v1",
                claim=f"Model coverage was derived from the explicit catalog: electrical={coverage.electrical_model_coverage:.4f}% mechanical={coverage.mechanical_model_coverage:.4f}% firmware={coverage.firmware_execution_model_coverage:.4f}%",
                references=tuple(item.logical_pin for item in pins),
            ),
        )

    @staticmethod
    def _source_fingerprint(source: VirtualAssemblySource) -> str:
        payload = {
            "input": source.consistency_input.fingerprint,
            "consistency": source.consistency_result.fingerprint,
            "schematic": source.schematic.fingerprint,
            "board": source.board.fingerprint,
            "bom": source.bom.fingerprint,
            "mechanical": tuple(item.fingerprint for item in source.mechanical_models),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return _sha256(encoded)

    @staticmethod
    def _status(
        *,
        artifact_integrity: GateStatus,
        truth: GateStatus,
        schematic: GateStatus,
        pcb: GateStatus,
        bom: GateStatus,
        component: GateStatus,
        footprint: GateStatus,
        pin_pad: GateStatus,
        firmware: GateStatus,
        consistency: GateStatus,
        gaps: tuple[SimulationGap, ...],
        stale_inputs: tuple[str, ...],
    ) -> AssemblyStatus:
        if stale_inputs:
            return AssemblyStatus.STALE
        statuses = (artifact_integrity, truth, schematic, pcb, bom, component, footprint, pin_pad, firmware, consistency)
        if any(item in {GateStatus.CONFLICT, GateStatus.FAIL} for item in statuses) or any(item.blocking for item in gaps):
            return AssemblyStatus.BLOCKED
        if all(item is GateStatus.PASS for item in statuses) and not any(item.blocking for item in gaps):
            return AssemblyStatus.READY_FOR_SIMULATION
        return AssemblyStatus.INCOMPLETE


__all__ = (
    "ArtifactReaderPort",
    "InMemoryArtifactReader",
    "ModelCatalogPort",
    "RuleBasedModelCatalog",
    "VirtualAssemblyService",
    "VirtualAssemblySource",
    "classify_footprint",
)
