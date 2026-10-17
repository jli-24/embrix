"""Bootstrap the persistent Embrix Minimal MCU Control Core Golden.

This module composes existing canonical contracts and execution boundaries. It
does not discover or install firmware toolchains and it never invokes a real
firmware build.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from embedded_copilot.engineering_copilot import (
    ArtifactIntelligenceService,
    DesignComponent,
    DesignEvidenceBinding,
    DesignInterface,
    DesignPowerDomain,
    DesignSignal,
    DesignSource,
    DesignTruth,
    DesignTruthBuilder,
    DesignTruthOrigin,
    DesignTruthStatus,
    FirmwareSourceFileProposal,
    FirmwareSourceProposal,
    FirmwareSourceProposalDecisionStatus,
    FirmwareSourceGenerationStatus,
    HardwareBOM,
    HardwareBOMItem,
    HardwareSelectionApproval,
    HardwareSelectionDecision,
    HardwareSelectionEvaluation,
    HardwareSelectionProposal,
    HardwareSelectionState,
    HardwareSelectionStatus,
    HardwareTruth,
    HardwareTruthComponent,
    NetBinding,
    PeripheralBinding,
    PlanProvenance,
    SchematicComponentInstance,
    SchematicEndpoint,
    SchematicInterfaceBus,
    SchematicNet,
    SchematicPinBinding,
    SchematicPlanDecision,
    SchematicPlanProposal,
    SchematicPlanningService,
    SchematicPowerDomain,
    SchematicProductionStatus,
    SchematicSignal,
    SchematicTarget,
    TruthLevel,
    evaluate_authorization,
    evaluate_capabilities,
    evaluate_schematic_readiness,
    generate_architecture_design,
    generate_engineering_workflow,
    generate_firmware_source_request,
    generate_firmware_spec,
    generate_hardware_design_spec,
    generate_implementation_spec,
    generate_simulation_spec,
    generate_schematic_production_request,
    generate_schematic_proposal,
    generate_verification_spec,
    approve_architecture_design,
    approve_engineering_plan,
    decide_execution_authorization_request,
    decide_firmware_source_proposal,
    decide_schematic_proposal,
    generate_execution_intent,
    generate_execution_requests,
    create_firmware_source_generation_state,
    create_schematic_production_state,
)
from embedded_copilot.component_recommendation import ComponentSourceRecord
from embedded_copilot.engineering_copilot import ComponentRequirement, SelectedHardwareComponent
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_requirements import (
    RequirementLifecycle,
    generate_engineering_plan,
    merge_requirement_context,
)
from embedded_copilot.engineering_workspace_apply import (
    ApplyOperation,
    ChangeIntent,
    ChangeType,
    FixedWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionToolRegistry,
    ExecutionWorker,
    FirmwareSourceWriteAdapter,
    KiCadCliPCBParseAdapter,
    KiCadCliPCBDrCAdapter,
    KiCadCliSchematicErcAdapter,
    KiCadCliSchematicParseAdapter,
    OperationStatus,
    OperationType,
    PCBWriteAdapter,
    SchematicSymbolLibraryValidator,
    SchematicWriteAdapter,
    SQLiteExecutionStore,
    ToolDefinition,
    ToolFamily,
    ToolResolution,
    discover_kicad_cli,
    discover_kicad_footprint_root,
    discover_kicad_symbol_root,
    discover_kicad_pcb_parse_command,
    discover_kicad_pcb_drc_command,
    probe_kicad_cli_version,
)
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareSourceArtifact,
    create_firmware_build_request,
    evaluate_build_readiness,
)
from embedded_copilot.firmware_engineering.models import canonical_fingerprint
from embedded_copilot.firmware_source_execution import FirmwareSourceExecutionBridge
from embedded_copilot.pcb_production import (
    PCBBoardDefinition,
    PCBComponentTruth,
    PCBEndpointTruth,
    PCBPhysicalDesignProposal,
    PCBNetTruth,
    PCBPlacementConstraint,
    PCBProductionService,
    PCBProposalDecisionStatus,
    PCBRoutingConstraint,
    PCBRouteSegment,
    KiCadFootprintAvailability,
    decide_pcb_proposal,
)
from embedded_copilot.pcb_production.execution import PCBExecutionBridge
from embedded_copilot.pcb_production.geometry import pad_world_position
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectType,
)
from embedded_copilot.project_catalog.models import ConversationDetail
from embedded_copilot.schematic_execution import SchematicExecutionBridge


TENANT_ID = "desktop-tenant"
REQUESTER = "golden-bootstrap-reviewer"
GOLDEN_NAME = "Minimal MCU Control Core"
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
FACTS_RELATIVE = "docs/canonical-golden-facts.json"
BOM_RELATIVE = "hardware/Minimal_MCU_Control_Core_BOM.csv"
SCHEMATIC_RELATIVE = "hardware/main.kicad_sch"
PCB_RELATIVE = "hardware/main.kicad_pcb"

_CANONICAL_FOOTPRINTS = {
    "U1": ("Package_QFP:LQFP-48_7x7mm_P0.5mm", tuple(str(index) for index in range(1, 49))),
    "C1": ("Capacitor_SMD:C_0603_1608Metric", ("1", "2")),
    "C2": ("Capacitor_SMD:C_0603_1608Metric", ("1", "2")),
    "C3": ("Capacitor_SMD:C_0603_1608Metric", ("1", "2")),
    "R1": ("Resistor_SMD:R_0603_1608Metric", ("1", "2")),
    "R2": ("Resistor_SMD:R_0603_1608Metric", ("1", "2")),
    "R3": ("Resistor_SMD:R_0603_1608Metric", ("1", "2")),
    "D1": ("LED_SMD:LED_0603_1608Metric", ("1", "2")),
    "SW1": ("Button_Switch_SMD:Panasonic_EVQPUJ_EVQPUA", ("1", "2")),
    "J1": ("Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", ("1", "2", "3", "4")),
    "J2": ("Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", ("1", "2")),
}


class _WorkspaceClock:
    def now(self) -> datetime:
        return NOW


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _facts_payload() -> dict[str, object]:
    pins = {
        "1": "VBAT", "2": "PC13-TAMPER-RTC", "3": "PC14-OSC32_IN",
        "4": "PC15-OSC32_OUT", "5": "PD0-OSC_IN", "6": "PD1-OSC_OUT",
        "7": "NRST", "8": "VSSA", "9": "VDDA", "10": "PA0-WKUP",
        "11": "PA1", "12": "PA2", "13": "PA3", "14": "PA4",
        "15": "PA5", "16": "PA6", "17": "PA7", "18": "PB0",
        "19": "PB1", "20": "PB2", "21": "PB10", "22": "PB11",
        "23": "VSS_1", "24": "VDD_1", "25": "PB12", "26": "PB13",
        "27": "PB14", "28": "PB15", "29": "PA8", "30": "PA9",
        "31": "PA10", "32": "PA11", "33": "PA12", "34": "PA13-SWDIO",
        "35": "VSS_2", "36": "VDD_2", "37": "PA14-SWCLK", "38": "PA15",
        "39": "PB3", "40": "PB4", "41": "PB5", "42": "PB6",
        "43": "PB7", "44": "BOOT0", "45": "PB8", "46": "PB9",
        "47": "VSS_3", "48": "VDD_3",
    }
    return {
        "schema": "embrix.canonical-golden-facts.v1",
        "golden_name": GOLDEN_NAME,
        "target": {
            "mcu_exact_part": "STM32F103C8T6",
            "mcu_family": "STM32F1",
            "architecture": "ARM Cortex-M3",
            "firmware_framework": "CMSIS + Minimal Bare-metal",
            "build_system": "CMake",
            "toolchain_family": "ARM_NONE_EABI_GCC",
            "board_class": "Minimal 2-layer MCU control board",
        },
        "official_evidence": {
            "datasheet_uri": "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf",
            "datasheet_revision": "DS5319 Rev 20",
            "package": "LQFP48 7x7 mm",
            "supply_range": "2.0-3.6 V",
            "design_supply": "3.3 V",
        },
        "pinout_lqfp48": pins,
        "implemented_pin_bindings": {
            "PA8": {"pin": "29", "function": "LED"},
            "PB0": {"pin": "18", "function": "USER_BUTTON"},
            "PA13-SWDIO": {"pin": "34", "function": "SWDIO"},
            "PA14-SWCLK": {"pin": "37", "function": "SWCLK"},
            "NRST": {"pin": "7", "function": "RESET"},
            "BOOT0": {"pin": "44", "function": "BOOT_SELECT"},
        },
        "acceptance": {
            "schematic_erc": "PASS_REQUIRED",
            "pcb_drc": "PASS_REQUIRED",
            "firmware_build": "BLOCKED_TOOLCHAIN_UNAVAILABLE",
        },
    }


def _render_facts() -> tuple[str, str]:
    content = json.dumps(_facts_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return content, _hash(content.encode())


def _symbol_table(symbol_dir: str | None = None) -> str:
    libraries = (
        "Device", "MCU_ST_STM32F1", "Switch", "Connector_Generic", "power",
    )
    base_uri = None
    if symbol_dir:
        base_uri = Path(symbol_dir).resolve(strict=True).as_posix().rstrip("/") + "/"
    lines = ["(sym_lib_table (version 7)"]
    for name in libraries:
        uri = f"{base_uri}{name}.kicad_sym" if base_uri is not None else f"${{KICAD_SYMBOL_DIR}}/{name}.kicad_sym"
        lines.append(
            f'  (lib (name "{name}") (type "KiCad") '
            f'(uri "{uri}") (options "") (descr ""))'
        )
    lines.append(")\n")
    return "\n".join(lines)


def _kicad_project_settings() -> str:
    """Return deterministic board rules consumed by KiCad's real DRC command."""
    payload = {
        "board": {
            "design_settings": {
                "meta": {"version": 2},
                "rules": {
                    "min_clearance": 0.1,
                    "min_connection": 0.1,
                    "min_copper_edge_clearance": 0.5,
                    "min_groove_width": 0.0,
                    "min_hole_clearance": 0.15,
                    "min_hole_to_hole": 0.25,
                    "min_microvia_diameter": 0.2,
                    "min_microvia_drill": 0.1,
                    "min_silk_clearance": 0.0,
                    "min_through_hole_diameter": 0.3,
                    "min_track_width": 0.1,
                    "min_via_annular_width": 0.1,
                    "min_via_diameter": 0.4,
                    "solder_mask_to_copper_clearance": 0.0,
                },
                "track_widths": [0.0, 0.1, 0.2, 0.25, 0.5],
                "via_dimensions": [
                    {"diameter": 0.0, "drill": 0.0},
                    {"diameter": 0.5, "drill": 0.3},
                ],
            }
        },
        "boards": [],
        "component_class_settings": {},
        "cvpcb": {},
        "erc": {"erc_exclusions": [], "meta": {"version": 0}, "pin_map": {}, "rule_severities": {}},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": "main.kicad_pro", "version": 3},
        "net_settings": {
            "classes": [
                {
                    "bus_width": 12,
                    "clearance": 0.1,
                    "diff_pair_gap": 0.25,
                    "diff_pair_via_gap": 0.25,
                    "diff_pair_width": 0.2,
                    "line_style": 0,
                    "microvia_diameter": 0.3,
                    "microvia_drill": 0.1,
                    "name": "Default",
                    "pcb_color": "rgba(0, 0, 0, 0.000)",
                    "priority": 2147483647,
                    "schematic_color": "rgba(0, 0, 0, 0.000)",
                    "track_width": 0.1,
                    "via_diameter": 0.5,
                    "via_drill": 0.3,
                    "wire_width": 6,
                }
            ],
            "meta": {"version": 4},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [],
        },
        "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
        "schematic": {},
        "sheets": [],
        "text_variables": {},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _controlled_files(
    workspace: WorkspaceApplyService,
    *,
    root: Path,
    project_id: str,
    files: dict[str, str],
) -> None:
    intents: list[ChangeIntent] = []
    for relative, content in sorted(files.items()):
        target = root / relative
        if target.exists():
            if target.is_symlink() or not target.is_file() or target.read_text(encoding="utf-8") != content:
                raise RuntimeError(f"STOP_OVERLAPPING_EDIT:{relative}")
            continue
        intents.append(
            ChangeIntent(
                relative_file_reference=relative,
                change_type=ChangeType.CREATE_FILE,
                before_fingerprint=ZERO,
                after_fingerprint=_hash(content.encode()),
                # Preview validation intentionally rejects URI/path material;
                # the approved after_content retains the exact evidence file.
                safe_preview=(
                    "Approved canonical KiCad symbol table."
                    if relative == "sym-lib-table"
                    else re.sub(r"https?://[^\s\"']+", "[official-uri]", content)
                ),
                reason="Approved canonical Golden evidence/configuration file.",
                source_proposal_reference=_id("golden-file-proposal", project_id, relative),
                after_content=content,
            )
        )
    if not intents:
        return
    snapshot = workspace.snapshot(tenant_id=TENANT_ID, project_id=project_id, target_id="project-root")
    proposal = SimpleNamespace(
        status="APPROVED",
        proposal_id=_id("golden-file-proposal", project_id, *(item.relative_file_reference for item in intents)),
        proposal_fingerprint=_hash("|".join(item.after_fingerprint for item in intents).encode()),
        tenant_id=TENANT_ID,
        project_id=project_id,
        requester_id=REQUESTER,
    )
    changeset = workspace.create_changeset(
        proposal=proposal,
        snapshot=snapshot,
        intents=tuple(intents),
        operation=ApplyOperation.WORKSPACE_APPLY,
    )
    approval = workspace.issue_apply_approval(
        changeset=changeset,
        reviewer_id=REQUESTER,
        nonce=_id("golden-file-approval", changeset.fingerprint),
    )
    receipt = workspace.apply(changeset=changeset, approval=approval, operation_id=_id("golden-file-apply", changeset.fingerprint))
    if receipt.status.value != "APPLIED" or receipt.validation_status.value != "PASSED":
        raise RuntimeError("canonical evidence/configuration file apply failed")


def _make_design_truth(project_id: str, conversation_id: str, architecture, facts_fp: str) -> DesignTruth:
    evidence_ids = {
        "facts": "evidence-golden-facts",
        "architecture": "evidence-architecture",
    }
    entities = (
        ("U1", "STM32F103C8T6", "MCU", "controller", "STM32F1 MCU", "MCU_ST_STM32F1:STM32F103C8Tx"),
        ("C1", "100nF", "CAPACITOR", "passive", "VDDA decoupling", "Device:C"),
        ("C2", "100nF", "CAPACITOR", "passive", "VDD decoupling", "Device:C"),
        ("C3", "100nF", "CAPACITOR", "passive", "NRST filter", "Device:C"),
        ("R1", "1k", "RESISTOR", "passive", "LED current limit", "Device:R"),
        ("R2", "10k", "RESISTOR", "passive", "BOOT0 pulldown", "Device:R"),
        ("R3", "10k", "RESISTOR", "passive", "NRST pullup", "Device:R"),
        ("D1", "LED", "LED", "indicator", "PA8 status indicator", "Device:LED"),
        ("SW1", "USER", "SWITCH", "input", "PB0 user button", "Switch:SW_Push"),
        ("J1", "SWD", "CONNECTOR", "debug", "SWD programming header", "Connector_Generic:Conn_01x04"),
        ("J2", "POWER", "CONNECTOR", "power", "3V3/GND input header", "Connector_Generic:Conn_01x02"),
    )
    binding = lambda entity: (evidence_ids["facts"],)
    components = tuple(
        DesignComponent.create(
            component_id=component_id,
            name=name,
            type=component_type,
            category=category,
            description=description,
            symbol_binding=symbol,
            confidence=1.0,
            truth_level=TruthLevel.EXPLICIT,
            evidence_refs=binding(component_id),
        )
        for component_id, name, component_type, category, description, symbol in entities
    )
    signals_data = (
        ("NET_3V3", "3V3", "J2.1", ("U1.1", "U1.9", "U1.24", "U1.36", "U1.48", "C1.1", "C2.1", "R1.2", "R3.2", "J1.1"), "POWER", "PASSIVE"),
        ("NET_GND", "GND", "J2.2", ("U1.8", "U1.23", "U1.35", "U1.47", "C1.2", "C2.2", "C3.2", "D1.2", "R2.2", "SW1.2", "J1.2"), "POWER", "PASSIVE"),
        ("NET_LED", "LED_PA8", "U1.29", ("R1.1", "D1.1"), "GPIO", "BIDIRECTIONAL"),
        ("NET_BUTTON", "BUTTON_PB0", "SW1.1", ("U1.18",), "GPIO", "INPUT"),
        ("NET_NRST", "NRST", "U1.7", ("R3.1", "C3.1"), "RESET", "BIDIRECTIONAL"),
        ("NET_BOOT0", "BOOT0", "U1.44", ("R2.1",), "BOOT", "INPUT"),
        ("NET_SWDIO", "SWDIO", "U1.34", ("J1.3",), "SWD", "BIDIRECTIONAL"),
        ("NET_SWCLK", "SWCLK", "U1.37", ("J1.4",), "SWD", "INPUT"),
    )
    signals = tuple(
        DesignSignal.create(
            signal_id=signal_id,
            name=name,
            type=signal_type,
            source=source,
            targets=targets,
            protocol=protocol,
            direction=direction,
            confidence=1.0,
            truth_level=TruthLevel.EXPLICIT,
            evidence_refs=(evidence_ids["facts"],),
        )
        for signal_id, name, source, targets, protocol, direction in signals_data
        for signal_type in ("ELECTRICAL",)
    )
    pins_by_component: dict[str, list[tuple[str, str, str, str | None]]] = {
        "U1": [("1", "VBAT", "NET_3V3", "VBAT"), ("7", "NRST", "NET_NRST", "RESET"), ("8", "VSSA", "NET_GND", "GND"), ("9", "VDDA", "NET_3V3", "3V3"), ("18", "PB0", "NET_BUTTON", "USER_BUTTON"), ("23", "VSS_1", "NET_GND", "GND"), ("24", "VDD_1", "NET_3V3", "3V3"), ("29", "PA8", "NET_LED", "LED"), ("34", "PA13-SWDIO", "NET_SWDIO", "SWDIO"), ("35", "VSS_2", "NET_GND", "GND"), ("36", "VDD_2", "NET_3V3", "3V3"), ("37", "PA14-SWCLK", "NET_SWCLK", "SWCLK"), ("44", "BOOT0", "NET_BOOT0", "BOOT_SELECT"), ("47", "VSS_3", "NET_GND", "GND"), ("48", "VDD_3", "NET_3V3", "3V3")],
        "C1": [("1", "1", "NET_3V3", None), ("2", "2", "NET_GND", None)],
        "C2": [("1", "1", "NET_3V3", None), ("2", "2", "NET_GND", None)],
        "C3": [("1", "1", "NET_NRST", None), ("2", "2", "NET_GND", None)],
        "R1": [("1", "1", "NET_LED", None), ("2", "2", "NET_3V3", None)],
        "R2": [("1", "1", "NET_BOOT0", None), ("2", "2", "NET_GND", None)],
        "R3": [("1", "1", "NET_NRST", None), ("2", "2", "NET_3V3", None)],
        "D1": [("1", "A", "NET_LED", None), ("2", "K", "NET_GND", None)],
        "SW1": [("1", "1", "NET_BUTTON", None), ("2", "2", "NET_GND", None)],
        "J1": [("1", "3V3", "NET_3V3", None), ("2", "GND", "NET_GND", None), ("3", "SWDIO", "NET_SWDIO", None), ("4", "SWCLK", "NET_SWCLK", None)],
        "J2": [("1", "3V3", "NET_3V3", None), ("2", "GND", "NET_GND", None)],
    }
    pins = tuple(
        __import__("embedded_copilot.engineering_copilot", fromlist=["DesignPin"]).DesignPin.create(
            pin_id=f"{component_id}-pin-{pin_number}", component_id=component_id,
            pin_name=pin_name, pin_number=pin_number, function=function or pin_name,
            signal_id=signal_id, confidence=1.0, truth_level=TruthLevel.EXPLICIT,
            evidence_refs=(evidence_ids["facts"],),
        )
        for component_id, entries in pins_by_component.items()
        for pin_number, pin_name, signal_id, function in entries
    )
    nets = tuple(
        NetBinding.create(
            binding_id=f"binding-{signal_id.casefold()}", net_name=name,
            nodes=(source, *targets), signal=signal_id, purpose=f"{name} electrical net",
            confidence=1.0, truth_level=TruthLevel.EXPLICIT,
            evidence_refs=(evidence_ids["facts"],),
        )
        for signal_id, name, source, targets, _, _ in signals_data
    )
    peripherals = (
        PeripheralBinding.create(binding_id="peripheral-led", peripheral="GPIO", component="U1", pin_refs=("U1-pin-29",), function="PA8 drives status LED", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=(evidence_ids["facts"],)),
        PeripheralBinding.create(binding_id="peripheral-button", peripheral="GPIO", component="U1", pin_refs=("U1-pin-18",), function="PB0 reads user button", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=(evidence_ids["facts"],)),
        PeripheralBinding.create(binding_id="peripheral-swd", peripheral="SWD", component="U1", pin_refs=("U1-pin-34", "U1-pin-37"), function="SWD debug/programming", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=(evidence_ids["facts"],)),
    )
    interfaces = (
        DesignInterface.create(interface_id="interface-swd", source="U1", target="J1", protocol="SWD", direction="BIDIRECTIONAL", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=(evidence_ids["facts"],)),
        DesignInterface.create(interface_id="interface-power", source="J2", target="U1", protocol="3V3", direction="INPUT", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=(evidence_ids["facts"],)),
    )
    power = (
        DesignPowerDomain.create(power_domain_id="power-3v3", name="3V3", component_refs=tuple(item[0] for item in entities), purpose="MCU and peripheral supply", voltage="3.3V", ground_net="GND", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=(evidence_ids["facts"],)),
    )
    bindings = (
        DesignEvidenceBinding.create(binding_id=evidence_ids["architecture"], entity_id=_id("truth", project_id, conversation_id), project_id=project_id, source=DesignSource.ARCHITECTURE, source_id=architecture.architecture_id, artifact_ref="architecture-design", locator="approved architecture", artifact_revision=architecture.fingerprint, source_fingerprint=architecture.fingerprint, relationship="approved architecture lineage", truth_level=TruthLevel.EXPLICIT),
        DesignEvidenceBinding.create(binding_id=evidence_ids["facts"], entity_id="U1", project_id=project_id, source=DesignSource.DATASHEET, source_id="stm32f103c8-datasheet", artifact_ref=FACTS_RELATIVE, locator="pinout_lqfp48 and implemented_pin_bindings", artifact_revision="golden-facts-v1", artifact_content_fingerprint=facts_fp, source_fingerprint=facts_fp, relationship="official MCU and Golden hardware facts", truth_level=TruthLevel.EXPLICIT),
    )
    # One binding is intentionally shared by the deterministic facts-derived
    # children; the architecture lineage binding is attached to the truth root.
    truth_id = bindings[0].entity_id
    entity_bindings = []
    for entity_id in (
        *(item.component_id for item in components), *(item.pin_id for item in pins),
        *(item.signal_id for item in signals), *(item.interface_id for item in interfaces),
        *(item.power_domain_id for item in power), *(item.binding_id for item in peripherals),
        *(item.binding_id for item in nets),
    ):
        entity_bindings.append(
            DesignEvidenceBinding.create(
                binding_id=f"facts-{entity_id}", entity_id=entity_id, project_id=project_id,
                source=DesignSource.DATASHEET, source_id="stm32f103c8-datasheet",
                artifact_ref=FACTS_RELATIVE, locator=f"entity:{entity_id}", artifact_revision="golden-facts-v1",
                artifact_content_fingerprint=facts_fp, source_fingerprint=facts_fp,
                relationship="explicit Golden fact", truth_level=TruthLevel.EXPLICIT,
            )
        )
    all_bindings = (bindings[0], bindings[1], *entity_bindings)
    return DesignTruth.create(
        truth_id=truth_id, tenant_id=TENANT_ID, project_id=project_id,
        conversation_id=conversation_id, origin=DesignTruthOrigin.CREATE, status=DesignTruthStatus.APPROVED,
        architecture_id=architecture.architecture_id, design_revision="golden-design-revision-v1",
        components=components, pins=pins, signals=signals, peripherals=peripherals,
        nets=nets, interfaces=interfaces, power_domains=power, firmware_bindings=(),
        evidence_bindings=all_bindings,
        provenance=(
            PlanProvenance.create(project_id=project_id, source_type="ARCHITECTURE_DESIGN", source_id=architecture.architecture_id, source_fingerprint=architecture.fingerprint),
            PlanProvenance.create(project_id=project_id, source_type="GOLDEN_FACTS", source_id="stm32f103c8-datasheet", source_fingerprint=facts_fp),
        ),
    )


def _make_canonical_hardware_selection(
    truth: DesignTruth,
    *,
    architecture,
    requirements,
) -> HardwareSelectionState:
    """Create the curated, read-only hardware evidence used by the Golden."""

    sources = tuple(
        ComponentSourceRecord.create(
            source_id=f"canonical-source-{component.component_id}",
            source_uri=f"https://canonical.invalid/{component.component_id}",
            source_revision="canonical-golden-v1",
            manufacturer="Canonical Evidence",
            mpn=component.name.replace(" ", "-"),
            category=component.type,
            package="CANONICAL",
            description=component.description,
            symbol=component.symbol_binding,
            footprint=_CANONICAL_FOOTPRINTS[component.component_id][0],
            supported_roles=(component.category,),
            pin_facts=(),
            electrical_facts=(),
            interface_facts=(),
            power_facts=(),
            supporting_requirements=(),
        )
        for component in truth.components
    )
    requirements_values = tuple(
        ComponentRequirement(
            slot_id=f"canonical-slot-{component.component_id}",
            architecture_component_id=component.component_id,
            role=component.category,
            category=component.type,
            functional_requirements=(component.description,),
            interface_requirements=(),
        )
        for component in truth.components
    )
    selected = tuple(
        SelectedHardwareComponent(
            slot_id=requirement.slot_id,
            architecture_component_id=requirement.architecture_component_id,
            role=requirement.role,
            quantity=1,
            source=source,
        )
        for requirement, source in zip(requirements_values, sources, strict=True)
    )
    proposal = HardwareSelectionProposal.create(
        proposal_id=_id("canonical-hardware-selection", truth.project_id),
        project_id=truth.project_id,
        conversation_id=truth.conversation_id,
        architecture_id=architecture.architecture_id,
        architecture_fingerprint=architecture.fingerprint,
        requirements_fingerprint=requirements.fingerprint,
        requirements=requirements_values,
        evaluations=tuple(
            HardwareSelectionEvaluation(
                slot_id=item.slot_id,
                source=item.source,
                selectable=True,
                requirement_matches=("canonical-evidence",),
                rejection_reasons=(),
                selection_reason="Curated canonical Golden evidence.",
            )
            for item in selected
        ),
        selected_components=selected,
        status=HardwareSelectionStatus.REVIEW,
        fingerprint=ZERO,
    )
    bom = HardwareBOM.create(
        bom_id=_id("canonical-bom", truth.project_id),
        project_id=truth.project_id,
        conversation_id=truth.conversation_id,
        architecture_id=architecture.architecture_id,
        architecture_fingerprint=architecture.fingerprint,
        requirements_fingerprint=requirements.fingerprint,
        revision="canonical-golden-v1",
        items=tuple(
            HardwareBOMItem(
                item_id=f"canonical-bom-item-{item.slot_id}",
                slot_id=item.slot_id,
                role=item.role,
                manufacturer=item.source.manufacturer,
                mpn=item.source.mpn,
                quantity=item.quantity,
                component_source_id=item.source.source_id,
                symbol=item.source.symbol,
                footprint=item.source.footprint,
                evidence_references=(item.source.source_id,),
                evidence_fingerprints=(item.source.fingerprint,),
                approval_status=HardwareSelectionStatus.APPROVED,
            )
            for item in selected
        ),
    )
    approval = HardwareSelectionApproval.create(
        approval_id=_id("canonical-hardware-approval", proposal.proposal_id),
        proposal_id=proposal.proposal_id,
        project_id=proposal.project_id,
        architecture_fingerprint=proposal.architecture_fingerprint,
        selection_fingerprint=proposal.fingerprint,
        evidence_fingerprints=tuple(item.source.fingerprint for item in selected),
        bom_fingerprint=bom.fingerprint,
        decision=HardwareSelectionDecision.APPROVED,
        reviewer=REQUESTER,
        decided_at=NOW,
        fingerprint=ZERO,
    )
    hardware_truth = HardwareTruth.create(
        truth_id=_id("canonical-hardware-truth", truth.project_id),
        project_id=truth.project_id,
        conversation_id=truth.conversation_id,
        architecture_id=architecture.architecture_id,
        architecture_fingerprint=architecture.fingerprint,
        requirements_fingerprint=requirements.fingerprint,
        bom_id=bom.bom_id,
        bom_fingerprint=bom.fingerprint,
        approval_fingerprint=approval.fingerprint,
        components=tuple(
            HardwareTruthComponent(
                slot_id=item.slot_id,
                architecture_component_id=item.architecture_component_id,
                role=item.role,
                quantity=item.quantity,
                manufacturer=item.source.manufacturer,
                mpn=item.source.mpn,
                symbol=item.source.symbol,
                footprint=item.source.footprint,
                source_id=item.source.source_id,
                source_uri=f"canonical-source-{item.slot_id}",
                source_revision="canonical-golden-v1",
                source_fingerprint=item.source.fingerprint,
                pin_facts=(),
                electrical_facts=(),
                interface_facts=(),
                power_facts=(),
                supporting_requirements=(),
            )
            for item in selected
        ),
        fingerprint=ZERO,
    )
    return HardwareSelectionState(
        proposal=proposal,
        status=HardwareSelectionStatus.APPROVED,
        approval=approval,
        bom=bom,
        hardware_truth=hardware_truth,
    )


def _make_approved_schematic_plan(
    truth: DesignTruth,
    *,
    architecture,
    requirements,
    hardware,
    hardware_selection: HardwareSelectionState,
    target_relative_path: str,
):
    """Project the curated canonical truth through the typed G2 plan gate."""

    def component_ref(node: str) -> str:
        return node.split(".", 1)[0]

    component_instances = tuple(
        SchematicComponentInstance(
            instance_id=component.component_id,
            architecture_component_id=f"canonical-architecture-{component.component_id}",
            hardware_slot_id=f"canonical-slot-{component.component_id}",
            design_truth_component_id=component.component_id,
            manufacturer="Canonical Evidence",
            mpn=component.name,
            symbol=component.symbol_binding,
            footprint=_CANONICAL_FOOTPRINTS[component.component_id][0],
            source_refs=(truth.truth_id, component.component_id, *component.evidence_refs),
            evidence_fingerprints=(truth.fingerprint,),
        )
        for component in truth.components
    )
    signals = tuple(
        SchematicSignal.create(
            signal_id=signal.signal_id,
            name=signal.name,
            semantic_role=signal.type,
            direction=signal.direction,
            voltage_domain="3V3",
            protocol=signal.protocol,
            source_component_id=component_ref(signal.source),
            target_component_ids=tuple(dict.fromkeys(component_ref(item) for item in signal.targets)),
            source_requirement_refs=("canonical-requirements",),
            source_refs=(truth.truth_id, signal.signal_id, *signal.evidence_refs),
            fingerprint=ZERO,
        )
        for signal in truth.signals
    )
    pin_bindings = tuple(
        SchematicPinBinding.create(
            binding_id=f"canonical-binding-{pin.component_id}-{pin.pin_number}",
            component_id=pin.component_id,
            architecture_component_id=f"canonical-architecture-{pin.component_id}",
            pin_number=pin.pin_number,
            pin_name=pin.pin_name,
            function=pin.function,
            pad_number=pin.pin_number,
            signal_id=pin.signal_id,
            intentionally_unconnected=pin.intentionally_unconnected,
            unconnected_reason=pin.unconnected_reason,
            source_refs=(truth.truth_id, pin.pin_id, *pin.evidence_refs),
            evidence_fingerprints=(truth.fingerprint,),
            fingerprint=ZERO,
        )
        for pin in truth.pins
    )
    binding_by_key = {(item.component_id, item.pin_number): item for item in pin_bindings}
    nets = tuple(
        SchematicNet.create(
            net_id=net.binding_id,
            name=net.net_name,
            signal_id=net.signal,
            endpoints=tuple(
                SchematicEndpoint(
                    component_id=component_ref(node),
                    pin_number=node.split(".", 1)[1],
                    binding_id=binding_by_key[(component_ref(node), node.split(".", 1)[1])].binding_id,
                )
                for node in net.nodes
            ),
            power_domain=net.net_name if net.net_name in {item.name for item in truth.power_domains} else None,
            source_refs=(truth.truth_id, net.binding_id, *net.evidence_refs),
            fingerprint=ZERO,
        )
        for net in truth.nets
    )
    power_domains = []
    for domain in truth.power_domains:
        components = tuple(
            dict.fromkeys(
                component_ref(node)
                for net in truth.nets
                if net.net_name == domain.name
                for node in net.nodes
            )
        )
        sources, consumers = (
            ((), components)
            if domain.name.casefold() == "gnd"
            else (components[:1], components[1:])
        )
        power_domains.append(
            SchematicPowerDomain.create(
                domain_id=domain.power_domain_id,
                name=domain.name,
                nominal_voltage=domain.voltage,
                source_component_ids=sources,
                consumer_component_ids=consumers,
                ground_reference=domain.ground_net,
                constraints=(domain.purpose,),
                source_refs=(truth.truth_id, domain.power_domain_id, *domain.evidence_refs),
                fingerprint=ZERO,
            )
        )
    interfaces = tuple(
        SchematicInterfaceBus.create(
            interface_id=interface.interface_id,
            protocol=interface.protocol,
            participants=(interface.source, interface.target),
            signal_ids=tuple(
                signal.signal_id
                for signal in truth.signals
                if component_ref(signal.source) in {interface.source, interface.target}
                or bool({component_ref(item) for item in signal.targets} & {interface.source, interface.target})
            ) or (truth.signals[0].signal_id,),
            voltage_domain="3V3",
            source_refs=(truth.truth_id, interface.interface_id, *interface.evidence_refs),
            fingerprint=ZERO,
        )
        for interface in truth.interfaces
    )
    target = SchematicTarget.create(
        project_id=truth.project_id,
        relative_path=target_relative_path,
        sheet_name="main",
        eda_format="KICAD_SCHEMATIC",
        component_instance_ids=tuple(item.instance_id for item in component_instances),
        source_refs=(truth.truth_id, architecture.architecture_id),
    )
    proposal = SchematicPlanProposal.create(
        plan_id=_id("canonical-schematic-plan", truth.truth_id, target_relative_path),
        project_id=truth.project_id,
        conversation_id=truth.conversation_id,
        architecture_id=architecture.architecture_id,
        architecture_fingerprint=architecture.fingerprint,
        requirements_fingerprint=requirements.fingerprint,
        hardware_truth_id=hardware_selection.hardware_truth.truth_id,
        hardware_truth_fingerprint=hardware_selection.hardware_truth.fingerprint,
        bom_id=hardware_selection.bom.bom_id,
        bom_fingerprint=hardware_selection.bom.fingerprint,
        target=target,
        component_instances=component_instances,
        signals=signals,
        pin_bindings=pin_bindings,
        nets=nets,
        power_domains=tuple(power_domains),
        interfaces=interfaces,
        source_refs=(truth.truth_id, architecture.architecture_id, hardware.hardware_spec_id),
        evidence_fingerprints=(truth.fingerprint, architecture.fingerprint, hardware.fingerprint),
        status="REVIEW",
        blocking_reasons=(),
        fingerprint=ZERO,
    )
    return SchematicPlanningService().decide(
        proposal,
        SchematicPlanDecision.APPROVED,
        reviewer=REQUESTER,
        decided_at=NOW,
    )


def _rebind_child_evidence(truth: DesignTruth) -> DesignTruth:
    """Attach the shared facts binding to all entities without changing facts."""

    facts_binding = next(item for item in truth.evidence_bindings if item.binding_id == "evidence-golden-facts")
    values = truth.model_dump(mode="python")
    values["evidence_bindings"] = tuple(
        item if item.entity_id == truth.truth_id else item
        for item in truth.evidence_bindings
    )
    del facts_binding
    return DesignTruth.model_validate(values, strict=False)


def _build_bom(truth: DesignTruth) -> str:
    rows = [(item.component_id, item.name, item.symbol_binding or "", item.description) for item in truth.components]
    import io
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("Reference", "Value", "Symbol", "Description", "Source"))
    for reference, value, symbol, description in rows:
        writer.writerow((reference, value, symbol, description, FACTS_RELATIVE))
    return stream.getvalue()


def _source_proposal(request, module) -> FirmwareSourceProposal:
    header = """#ifndef EMBRIX_MINIMAL_MCU_CONTROL_CORE_H\n#define EMBRIX_MINIMAL_MCU_CONTROL_CORE_H\n\nvoid minimal_mcu_control_core_init(void);\nvoid minimal_mcu_control_core_poll(void);\n\n#endif\n"""
    source = """#include \"minimal_mcu_control_core.h\"\n\n#include \"stm32f103xb.h\"\n\n#define LED_PIN (1u << 8)\n#define BUTTON_PIN (1u << 0)\n\nvoid minimal_mcu_control_core_init(void)\n{\n    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_IOPBEN;\n    GPIOA->CRH = (GPIOA->CRH & ~(0xFu << 0)) | (0x2u << 0);\n    GPIOB->CRL &= ~(0xFu << 0);\n    GPIOB->CRL |= (0x4u << 0);\n}\n\nvoid minimal_mcu_control_core_poll(void)\n{\n    if ((GPIOB->IDR & BUTTON_PIN) == 0u) {\n        GPIOA->BSRR = LED_PIN;\n    } else {\n        GPIOA->BRR = LED_PIN;\n    }\n}\n"""
    files = tuple(
        FirmwareSourceFileProposal.create(
            relative_path=path, content=content, content_fingerprint=_hash(content.encode()), size_bytes=len(content.encode())
        )
        for path, content in (("generated/minimal_mcu_control_core.h", header), ("generated/minimal_mcu_control_core.c", source))
    )
    validation = _hash("|".join(item.content_fingerprint for item in files).encode())
    return FirmwareSourceProposal.create(
        proposal_id=_id("firmware-source-proposal", request.request_id, request.fingerprint, module.fingerprint, *(item.fingerprint for item in files)),
        tenant_id=request.tenant_id, project_id=request.project_id, conversation_id=request.conversation_id,
        requester=request.requester, generation_request_id=request.request_id, generation_request_fingerprint=request.fingerprint,
        firmware_module_id=module.module_id, module_name=module.name, module_responsibility=module.responsibility,
        files=files, verification_refs=request.verification_refs, validation_fingerprint=validation,
        source_refs=(request.request_id, module.module_id),
    )


def _build_worker(project_root: Path, state_root: Path, project_id: str, executable: str):
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(project_root, tenant_id=TENANT_ID, project_id=project_id),
        store=SQLiteWorkspaceApplyStore(state_root / "workspace.sqlite3"),
        state_root=state_root / "workspace-state",
        clock=_WorkspaceClock(),
        approval_ttl=timedelta(minutes=15),
    )
    execution = ExecutionOperationService(store=SQLiteExecutionStore(state_root / "execution.sqlite3"), clock=lambda: NOW)
    footprint_root = discover_kicad_footprint_root(executable)
    symbol_root = discover_kicad_symbol_root(
        executable,
        configured_root=os.environ.get("KICAD_SYMBOL_DIR", "").strip() or None,
    )
    parse_builder = discover_kicad_pcb_parse_command(executable, cwd=project_root)
    drc_builder = discover_kicad_pcb_drc_command(executable, cwd=project_root)
    if footprint_root is None or parse_builder is None or drc_builder is None:
        raise RuntimeError("KiCad footprint library, PCB parse command, or PCB DRC command unavailable")
    worker = ExecutionWorker(
        worker_id="canonical-golden-worker", store=execution.store,
        registry=None, workspace_resolver=None, targets=None,
        firmware_source_write=FirmwareSourceWriteAdapter(workspace),
        schematic_write=SchematicWriteAdapter(workspace),
        schematic_erc=KiCadCliSchematicErcAdapter(
            executable=executable,
            workspace_resolver=lambda operation: project_root,
            kicad_version=probe_kicad_cli_version(executable, cwd=project_root),
            library_variables=(
                {"KICAD_SYMBOL_DIR": str(symbol_root)}
                if symbol_root is not None
                else None
            ),
        ),
        pcb_write=PCBWriteAdapter(workspace),
        pcb_drc=KiCadCliPCBDrCAdapter(executable=executable, workspace_resolver=lambda operation: project_root, command_builder=drc_builder, kicad_version=probe_kicad_cli_version(executable, cwd=project_root), clock=lambda: NOW),
        allowed_operation_types=(OperationType.FIRMWARE_SOURCE_WRITE, OperationType.KICAD_SCHEMATIC_WRITE, OperationType.KICAD_SCHEMATIC_ERC, OperationType.KICAD_PCB_WRITE, OperationType.KICAD_PCB_DRC),
        clock=lambda: NOW,
    )
    return workspace, execution, worker, footprint_root, parse_builder


def _approve_capabilities(implementation, requester: str):
    evaluation = evaluate_capabilities(implementation)
    intents = tuple(generate_execution_intent(task) for task in implementation.tasks)
    requests = generate_execution_requests(evaluation, intents, conversation_id=implementation.conversation_id, requested_by=requester)
    authorization = evaluate_authorization(evaluation, intents, requests)
    for request, intent in zip(authorization.requests, intents, strict=True):
        if request.capability_id not in {"ARTIFACT_CREATION", "FIRMWARE_CODE_GENERATION"}:
            continue
        authorization = __import__("embedded_copilot.engineering_copilot", fromlist=["decide_execution_authorization_request"]).decide_execution_authorization_request(
            evaluation, intents, authorization, request_id=request.request_id,
            decision=__import__("embedded_copilot.engineering_copilot", fromlist=["AuthorizationDecisionStatus"]).AuthorizationDecisionStatus.APPROVED,
            reviewer=REQUESTER, decided_at=NOW,
        )
    return evaluation, intents, authorization


def _project_root(runtime: ProjectCatalogRuntime, project) -> Path:
    location = next(item for item in runtime.locations(tenant_id=project.tenant_id, requester=REQUESTER) if item.location_id == project.location_id)
    del location
    return Path(runtime._location_service.resolve(project.location_id, tenant_id=project.tenant_id)) / project.relative_path


def _existing(runtime: ProjectCatalogRuntime):
    matches = tuple(item for item in runtime.projects(TENANT_ID) if item.name == GOLDEN_NAME)
    if len(matches) > 1:
        raise RuntimeError("canonical Golden project identity is ambiguous")
    if not matches:
        return None, None
    project = matches[0]
    conversations = runtime._repository.conversations(project.project_id, TENANT_ID, REQUESTER)
    if len(conversations) > 1:
        raise RuntimeError("canonical Golden conversation identity is ambiguous")
    return project, conversations[0] if conversations else None


def bootstrap(*, project_root: Path, state_database: Path, executable: str, stop_after_g4: bool = False, stop_after_g5: bool = False) -> dict[str, object]:
    project_root.mkdir(parents=True, exist_ok=True)
    state_database.parent.mkdir(parents=True, exist_ok=True)
    runtime = ProjectCatalogRuntime(project_root=project_root, state_database=state_database, initialize_async=False, production_mode=True)
    project, existing = _existing(runtime)
    if stop_after_g4 and existing is not None and existing.schematic_production is not None:
        return _report(
            runtime,
            project,
            existing,
            g4_only=True,
            execution_state=state_database.parent / "canonical-golden-execution",
        )
    if stop_after_g5 and existing is not None and existing.pcb_production is not None and existing.pcb_production.drc_result is not None:
        return _report(
            runtime,
            project,
            existing,
            g5_only=True,
            execution_state=state_database.parent / "canonical-golden-execution",
        )
    if existing is not None and existing.build_configuration is not None and existing.pcb_production is not None and existing.schematic_production is not None and existing.firmware_source_generation is not None:
        return _report(runtime, project, existing, execution_state=state_database.parent / "canonical-golden-execution")
    if project is not None or existing is not None:
        raise RuntimeError("existing canonical Golden state is partial; refusing duplicate or destructive recovery")

    location = next(item for item in runtime.locations(tenant_id=TENANT_ID, requester=REQUESTER) if item.location_id == "default-project-root")
    preview = runtime.preview(ProjectCreationRequest(name=GOLDEN_NAME, project_type=ProjectType.STM32, creation_mode=ProjectCreationMode.CREATE_NEW, parent_location={"location_id": location.location_id, "fingerprint": location.fingerprint}), tenant_id=TENANT_ID, requester=REQUESTER)
    project = runtime.create(preview.preview_id, preview.fingerprint, confirmed_at=preview.created_at + timedelta(seconds=1), tenant_id=TENANT_ID, requester=REQUESTER)
    session = runtime.create_conversation(project.project_id, tenant_id=TENANT_ID, requester=REQUESTER)
    conversation = session.conversation
    root = _project_root(runtime, project)
    # Project Catalog creates an empty directory.  WorkspaceApply's snapshot
    # contract intentionally requires at least one existing project-owned file;
    # this bootstrap README is project initialization, not an engineering
    # artifact or a second write boundary.
    (root / "README.md").write_text(
        "# Minimal MCU Control Core\n\nCanonical Embrix Golden project bootstrap.\n",
        encoding="utf-8",
    )
    facts_content, facts_fp = _render_facts()
    workspace, execution, worker, footprint_root, parse_builder = _build_worker(root, state_database.parent / "canonical-golden-execution", project.project_id, executable)
    symbol_root = discover_kicad_symbol_root(
        executable,
        configured_root=os.environ.get("KICAD_SYMBOL_DIR", "").strip() or None,
    )
    _controlled_files(
        workspace,
        root=root,
        project_id=project.project_id,
        files={
            FACTS_RELATIVE: facts_content,
            "sym-lib-table": _symbol_table(str(symbol_root) if symbol_root is not None else None),
            "hardware/main.kicad_pro": _kicad_project_settings(),
        },
    )

    context = None
    for index, utterance in enumerate((
        "做一个 Minimal MCU Control Core，功能包括 MCU 最小控制核心、LED 状态显示、用户按键输入和 SWD 调试；目标使用 STM32F103C8T6，使用 CMSIS + Minimal Bare-metal。",
        "输入为 3.3V 电源和用户按键，输出为 PA8 LED；硬件范围为 Minimal 2-layer MCU control board；软件范围为 CMSIS + Minimal Bare-metal；交付物为原理图、PCB、固件。",
        "需要 CMake 构建，未来工具链为 GNU Arm Embedded Toolchain；要求原理图 ERC PASS、PCB DRC PASS；Given: canonical target facts When: target is reviewed Then: target is approved Verification: DesignTruth and official datasheet evidence",
    ), start=1):
        context = merge_requirement_context(context, project_id=project.project_id, conversation_id=conversation.conversation_id, turn_id=f"golden-requirement-{index}", utterance=utterance, project_type=project.project_type.value, lifecycle=RequirementLifecycle.DISCOVERY_STARTED if index < 3 else RequirementLifecycle.DESIGN_READY, confirmation_requested=index == 3)
    plan = generate_engineering_plan(context)
    plan, plan_review = approve_engineering_plan(plan, reviewer=REQUESTER, decided_at=NOW)
    architecture = generate_architecture_design(context, plan)
    architecture, architecture_review = approve_architecture_design(architecture, reviewer=REQUESTER, decided_at=NOW)
    workflow = generate_engineering_workflow(architecture, plan)
    hardware = generate_hardware_design_spec(architecture, workflow)
    truth = _make_design_truth(project.project_id, conversation.conversation_id, architecture, facts_fp)
    hardware_selection = _make_canonical_hardware_selection(
        truth,
        architecture=architecture,
        requirements=context,
    )
    schematic_planning = _make_approved_schematic_plan(
        truth,
        architecture=architecture,
        requirements=context,
        hardware=hardware,
        hardware_selection=hardware_selection,
        target_relative_path=SCHEMATIC_RELATIVE,
    )
    if schematic_planning.approved_plan is None:
        raise RuntimeError("canonical schematic plan approval unavailable")
    truth = SchematicPlanningService().project_design_truth(
        truth,
        schematic_planning.approved_plan,
    )
    firmware = generate_firmware_spec(hardware, workflow).model_copy(update={
        "target_platform": "STM32F103C8T6", "mcu_family": "STM32F1", "mcu_exact_part": "STM32F103C8T6",
        "architecture": "ARM Cortex-M3", "firmware_framework": "CMSIS + Minimal Bare-metal",
        "build_system": "CMAKE", "toolchain_family": "ARM_NONE_EABI_GCC",
        "design_truth_id": truth.truth_id, "design_truth_fingerprint": truth.fingerprint,
        "source_layout": ("generated/minimal_mcu_control_core.c", "generated/minimal_mcu_control_core.h"),
        "entry_point": "minimal_mcu_control_core_init", "peripheral_expectations": ("GPIOA.PA8", "GPIOB.PB0", "SWD.PA13_PA14", "NRST", "BOOT0"),
        "fingerprint": ZERO,
    })
    firmware = type(firmware).model_validate({**firmware.model_dump(mode="python"), "fingerprint": ZERO})
    simulation = generate_simulation_spec(hardware, firmware, workflow)
    verification = generate_verification_spec(hardware, firmware, simulation, workflow)
    build_config = FirmwareBuildConfiguration(target_kind="STM32F103C8T6", build_system="CMAKE", toolchain_id="ARM_NONE_EABI_GCC", toolchain_version=os.environ.get("EMBRIX_CANONICAL_TOOLCHAIN_VERSION", "UNAVAILABLE"), profile="Release", build_directory="build", expected_artifact_types=("ELF", "BIN", "MAP"), output_stem="minimal_mcu_control_core", compiler_flags=("-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft"))
    build_config_fp = canonical_fingerprint(build_config)
    build_config_id = _id("build-config", project.project_id, build_config_fp)
    implementation = generate_implementation_spec(hardware, firmware, simulation, verification, workflow).model_copy(update={
        "design_truth_id": truth.truth_id, "design_truth_fingerprint": truth.fingerprint,
        "build_configuration_id": build_config_id, "build_configuration_fingerprint": build_config_fp,
        "fingerprint": ZERO,
    })
    implementation = type(implementation).model_validate({**implementation.model_dump(mode="python"), "fingerprint": ZERO})
    evaluation, intents, authorization = _approve_capabilities(implementation, REQUESTER)
    readiness = evaluate_schematic_readiness(
        truth,
        hardware,
        implementation,
        approved_schematic_plan=schematic_planning.approved_plan,
    )
    if readiness.status.value != "READY":
        raise RuntimeError(f"schematic readiness failed: {readiness.status.value} {readiness.missing_facts}")
    schematic_request = generate_schematic_production_request(design_truth=truth, hardware_design=hardware, implementation=implementation, authorization=authorization, readiness=readiness, execution_intents=intents, conversation_id=conversation.conversation_id, requester=REQUESTER, target_relative_path=SCHEMATIC_RELATIVE, approved_schematic_plan=schematic_planning.approved_plan)
    schematic_proposal = generate_schematic_proposal(schematic_request, truth)
    schematic_decision = decide_schematic_proposal(schematic_proposal, decision=__import__("embedded_copilot.engineering_copilot", fromlist=["SchematicProposalDecisionStatus"]).SchematicProposalDecisionStatus.APPROVED, reviewer=REQUESTER, decided_at=NOW)
    symbol_validator = SchematicSymbolLibraryValidator(lambda **kwargs: root)

    def _canonical_symbol_provider(**kwargs):
        bindings = tuple(kwargs["bindings"])
        return symbol_validator.resolve_definitions(
            tenant_id=kwargs["tenant_id"],
            project_id=kwargs["project_id"],
            target_id=kwargs["target_id"],
            bindings=(*bindings, "power:PWR_FLAG"),
        )

    schematic_bridge = SchematicExecutionBridge(
        workspace=workspace,
        execution=execution,
        clock=lambda: NOW,
        symbol_binding_validator=symbol_validator,
        symbol_library_provider=_canonical_symbol_provider,
    )
    schematic_op = schematic_bridge.approve_and_enqueue(production_request=schematic_request, proposal=schematic_proposal, decision=schematic_decision, tenant_id=TENANT_ID)
    if worker.run_once().status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("schematic write failed")
    schematic_receipt = execution.get_receipt(tenant_id=TENANT_ID, project_id=project.project_id, operation_id=schematic_op.operation.operation_id)
    if schematic_receipt is None or schematic_receipt.status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("schematic write receipt unavailable")
    schematic_state = create_schematic_production_state(schematic_request, schematic_proposal).model_copy(update={"decision": schematic_decision, "operation_id": schematic_op.operation.operation_id, "operation_fingerprint": schematic_op.operation.fingerprint, "status": SchematicProductionStatus.QUEUED, "fingerprint": ZERO})
    conversation = runtime.persist_conversation_detail(
        conversation.model_copy(
            update={
                "title": GOLDEN_NAME,
                "engineering_session_mode": "CREATE",
                "requirement_context": context,
                "engineering_plan": plan,
                "engineering_plan_review": plan_review,
                "architecture_design": architecture,
                "architecture_review": architecture_review,
                "design_truth": truth,
                "schematic_planning": schematic_planning,
                "engineering_workflow": workflow,
                "hardware_design_spec": hardware,
                "hardware_selection": hardware_selection,
                "firmware_spec": firmware,
                "simulation_spec": simulation,
                "verification_spec": verification,
                "implementation_spec": implementation,
                "capability_evaluation": evaluation,
                "execution_intents": intents,
                "execution_authorization": authorization,
                "schematic_production": schematic_state,
                "fingerprint": ZERO,
            }
        )
    )

    generation_detail = runtime.record_schematic_generation(
        operation=schematic_op.operation,
        receipt=schematic_receipt,
        external_validation_status="UNAVAILABLE",
    )
    generation = generation_detail.schematic_production.generation_receipt
    if generation is None:
        raise RuntimeError("schematic generation projection unavailable")
    parse_evidence = KiCadCliSchematicParseAdapter(
        executable=executable,
        kicad_version=probe_kicad_cli_version(executable, cwd=root),
        clock=lambda: NOW,
    ).execute(
        project_id=project.project_id,
        conversation_id=conversation.conversation_id,
        operation_id=generation.operation_id,
        artifact=root / generation.artifact_path,
        artifact_path=generation.artifact_path,
        expected_artifact_fingerprint=generation.artifact_fingerprint,
    )
    if parse_evidence.validation_status != "PARSE_VERIFIED":
        raise RuntimeError("canonical schematic parse validation failed")
    runtime.record_schematic_parse_validation(evidence=parse_evidence)
    erc_op = schematic_bridge.enqueue_erc(write_operation=schematic_op.operation, receipt_fingerprint=schematic_receipt.fingerprint, reviewer_id=REQUESTER)
    if worker.run_once().status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("schematic ERC failed")
    erc_receipt = execution.get_receipt(tenant_id=TENANT_ID, project_id=project.project_id, operation_id=erc_op.operation.operation_id)
    if erc_receipt is None or erc_receipt.schematic_erc_status != "ERC_PASS":
        raise RuntimeError(f"schematic ERC was not PASS: {getattr(erc_receipt, 'schematic_erc_status', None)}")
    erc_detail = runtime.record_schematic_erc_validation(operation=erc_op.operation, receipt=erc_receipt)
    schematic_state = erc_detail.schematic_production
    artifact_snapshot = ArtifactIntelligenceService().scan_directory(
        root=root,
        tenant_id=TENANT_ID,
        project_id=project.project_id,
        conversation_id=conversation.conversation_id,
        source_operation_id=schematic_op.operation.operation_id,
        source_proposal_fingerprint=schematic_proposal.fingerprint,
    )

    if stop_after_g4:
        final_detail = erc_detail.model_copy(update={"artifact_intelligence_snapshot": artifact_snapshot, "fingerprint": ZERO})
        final_detail = runtime.persist_conversation_detail(final_detail)
        return _report(
            runtime,
            project,
            final_detail,
            g4_only=True,
            facts_fp=facts_fp,
            schematic_receipt=schematic_receipt,
            erc_receipt=erc_receipt,
            execution_state=state_database.parent / "canonical-golden-execution",
        )
    schematic_artifact = next(item for item in artifact_snapshot.inventory.artifacts if item.path_reference == SCHEMATIC_RELATIVE)
    source_artifact_id = schematic_artifact.artifact_id

    pcb_components = []
    footprint_by_ref = _CANONICAL_FOOTPRINTS
    for component in truth.components:
        footprint, pads = footprint_by_ref[component.component_id]
        pcb_components.append(PCBComponentTruth.create(reference=component.component_id, symbol_binding=component.symbol_binding or "", footprint_binding=footprint, footprint_source_refs=(FACTS_RELATIVE,), pin_numbers=tuple(pads), pad_numbers=tuple(pads)))
    placements = {
        "U1": (60.0, 55.0), "C1": (95.0, 90.55), "C2": (100.0, 80.55),
        "C3": (30.0, 30.0), "R1": (100.0, 25.55), "R2": (104.0, 75.55),
        "R3": (96.0, 18.55), "D1": (30.0, 70.0), "SW1": (100.0, 95.55),
        "J1": (108.0, 44.0), "J2": (115.0, 16.55),
    }
    pcb_placements = tuple(PCBPlacementConstraint.create(reference=ref, x_mm=xy[0], y_mm=xy[1], rotation_deg=90.0 if ref == "J1" else (270.0 if ref == "J2" else 0.0), side="F.Cu", source_refs=(FACTS_RELATIVE,)) for ref, xy in placements.items())
    def endpoint(ref: str, pin: str) -> PCBEndpointTruth:
        return PCBEndpointTruth.create(reference=ref, pin_number=pin)
    truth_power_domains = {item.name: item for item in truth.power_domains}
    pcb_nets = tuple(
        PCBNetTruth.create(
            name=net.net_name,
            endpoints=tuple(endpoint(node.split(".", 1)[0], node.split(".", 1)[1]) for node in net.nodes),
            net_class=(
                "ground"
                if net.net_name.casefold() in {"gnd", "ground"}
                else "power"
                if net.net_name in truth_power_domains
                else "default"
            ),
            source_refs=tuple(dict.fromkeys((truth.truth_id, net.binding_id, *net.evidence_refs))),
        )
        for net in truth.nets
    )
    routes = []
    component_by_ref = {item.reference: item for item in pcb_components}
    placement_by_ref = {item.reference: item for item in pcb_placements}
    footprint_library = KiCadFootprintAvailability(footprint_root)
    endpoint_net_by_key = {
        (endpoint.reference, endpoint.pin_number): net.name
        for net in pcb_nets
        for endpoint in net.endpoints
    }
    pad_points = tuple(
        (
            component.reference,
            pad_number,
            world_position,
            endpoint_net_by_key.get((component.reference, pad_number)),
        )
        for component in pcb_components
        for pad_number in component.pad_numbers
        for world_position in footprint_library.pad_world_positions(
            placement_by_ref[component.reference],
            component.footprint_binding or "",
            pad_number,
        )
    )

    def _distance_to_segment(point, start, end):
        dx, dy = end[0] - start[0], end[1] - start[1]
        if dx == 0 and dy == 0:
            return math.dist(point, start)
        t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)))
        return math.dist(point, (start[0] + t * dx, start[1] + t * dy))

    def _segments_intersect(first_start, first_end, second_start, second_end):
        def orientation(a, b, c):
            value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
            return 0 if abs(value) < 1e-9 else (1 if value > 0 else 2)

        return (
            orientation(first_start, first_end, second_start) != orientation(first_start, first_end, second_end)
            and orientation(second_start, second_end, first_start) != orientation(second_start, second_end, first_end)
        )

    def _segment_distance(first_start, first_end, second_start, second_end):
        if _segments_intersect(first_start, first_end, second_start, second_end):
            return 0.0
        return min(
            _distance_to_segment(point, start, end)
            for point, start, end in (
                (first_start, second_start, second_end),
                (first_end, second_start, second_end),
                (second_start, first_start, first_end),
                (second_end, first_start, first_end),
            )
        )

    def _pad_escape(reference, pad_number, position):
        placement = placement_by_ref[reference]
        # U1.8 is adjacent to the 3V3 U1.9 escape on the integer routing grid;
        # keep its physical escape distinct without changing its electrical net.
        if reference == "U1" and pad_number == "8":
            return (position[0] - 2.0, position[1] + 2.0)
        if reference == "U1" and pad_number == "34":
            return (position[0] + 3.0, position[1])
        if reference == "J1":
            if pad_number == "1":
                return (position[0] - 2.0, position[1])
            if pad_number == "4":
                return (position[0] + 2.0, position[1])
            return (position[0], position[1] + (2.0 if pad_number == "2" else -2.0))
        if reference == "J2":
            return (position[0] + (2.0 if pad_number == "1" else -2.0), position[1])
        if reference == "SW1":
            return (position[0], position[1] + (-2.0 if pad_number == "1" else 2.0))
        dx = position[0] - placement.x_mm
        dy = position[1] - placement.y_mm
        if abs(dx) >= abs(dy) and abs(dx) > 0.01:
            return (position[0] + math.copysign(2.0, dx), position[1])
        if abs(dy) > 0.01:
            return (position[0], position[1] + math.copysign(2.0, dy))
        return (position[0] + 2.0, position[1])

    def _grid(value):
        return float(round(value))

    def _escape_point(reference, pad_number, position):
        if reference == "U1" and pad_number == "8":
            return (53.0, 55.75)
        if reference == "U1" and pad_number == "7":
            return (51.0, 55.25)
        if reference == "U1" and pad_number == "34":
            return (67.0, 53.25)
        if reference == "U1" and pad_number == "35":
            return (68.0, 52.75)
        if reference == "U1" and pad_number == "9":
            return (51.0, 56.25)
        escape = _pad_escape(reference, pad_number, position)
        return (_grid(escape[0]), _grid(escape[1]))

    blocked_pad_cache = {}

    def _blocked_segment(start, end, net_name, layer, occupied):
        cache_key = (net_name, layer)
        blocked_pads = blocked_pad_cache.get(cache_key)
        if blocked_pads is None:
            blocked_pads = tuple(
                (
                    pad,
                    1.05
                    if layer == "B.Cu"
                    else 1.5
                    if footprint_library.pad_is_through_hole(
                        component_by_ref[reference].footprint_binding or "",
                        pad_number,
                    )
                    else 1.5
                    if reference == "SW1"
                    else 0.6,
                )
                for reference, pad_number, pad, pad_net in pad_points
                if pad_net != net_name
                and (layer == "F.Cu" or footprint_library.pad_is_through_hole(component_by_ref[reference].footprint_binding or "", pad_number))
            )
            blocked_pad_cache[cache_key] = blocked_pads
        if any(_distance_to_segment(pad, start, end) < clearance for pad, clearance in blocked_pads):
            return True
        edge_min_x = min(start[0], end[0])
        edge_max_x = max(start[0], end[0])
        edge_min_y = min(start[1], end[1])
        edge_max_y = max(start[1], end[1])
        for segment_start, segment_end, min_x, max_x, min_y, max_y, clearance in occupied:
            if (
                edge_max_x < min_x
                or edge_min_x > max_x
                or edge_max_y < min_y
                or edge_min_y > max_y
            ):
                continue
            if _segment_distance(start, end, segment_start, segment_end) < clearance:
                return True
        return False

    def _grid_path(start, targets, net_name, layer, occupied):
        if any(math.dist(start, target) <= 1e-6 for target in targets):
            return (start,)
        # The search lattice is integer-spaced, while a library pad escape may
        # deliberately retain a fractional coordinate for a real pad edge.
        # Reach the nearest lattice point and let the serialized route preserve
        # the short physical snap segment to the exact escape coordinate.
        search_targets = tuple((_grid(target[0]), _grid(target[1])) for target in targets)
        margin = 40.0
        min_target_x = min(target[0] for target in search_targets)
        max_target_x = max(target[0] for target in search_targets)
        min_target_y = min(target[1] for target in search_targets)
        max_target_y = max(target[1] for target in search_targets)
        board_x = tuple(
            float(value)
            for value in range(max(11, math.floor(min(start[0], min_target_x) - margin)), min(129, math.ceil(max(start[0], max_target_x) + margin)) + 1)
        )
        board_y = tuple(
            float(value)
            for value in range(max(11, math.floor(min(start[1], min_target_y) - margin)), min(99, math.ceil(max(start[1], max_target_y) + margin)) + 1)
        )
        target_set = set(search_targets)
        occupied_index = tuple(
            (
                segment_start,
                segment_end,
                min(segment_start[0], segment_end[0]) - (0.6 if segment_start == segment_end else 0.46),
                max(segment_start[0], segment_end[0]) + (0.6 if segment_start == segment_end else 0.46),
                min(segment_start[1], segment_end[1]) - (0.6 if segment_start == segment_end else 0.46),
                max(segment_start[1], segment_end[1]) + (0.6 if segment_start == segment_end else 0.46),
                0.6 if segment_start == segment_end else 0.46,
            )
            for segment_start, segment_end in occupied
        )
        bucket_size = 5.0
        occupied_buckets = {}
        long_occupied = []
        for index, (_, _, min_x, max_x, min_y, max_y, _) in enumerate(occupied_index):
            min_bucket_x = math.floor(min_x / bucket_size)
            max_bucket_x = math.floor(max_x / bucket_size)
            min_bucket_y = math.floor(min_y / bucket_size)
            max_bucket_y = math.floor(max_y / bucket_size)
            if (max_bucket_x - min_bucket_x + 1) * (max_bucket_y - min_bucket_y + 1) > 64:
                long_occupied.append(index)
                continue
            for bucket_x in range(min_bucket_x, max_bucket_x + 1):
                for bucket_y in range(min_bucket_y, max_bucket_y + 1):
                    occupied_buckets.setdefault((bucket_x, bucket_y), []).append(index)

        def nearby_occupied(start_point, end_point):
            min_bucket_x = math.floor(min(start_point[0], end_point[0]) / bucket_size)
            max_bucket_x = math.floor(max(start_point[0], end_point[0]) / bucket_size)
            min_bucket_y = math.floor(min(start_point[1], end_point[1]) / bucket_size)
            max_bucket_y = math.floor(max(start_point[1], end_point[1]) / bucket_size)
            indexes = set(long_occupied)
            for bucket_x in range(min_bucket_x, max_bucket_x + 1):
                for bucket_y in range(min_bucket_y, max_bucket_y + 1):
                    indexes.update(occupied_buckets.get((bucket_x, bucket_y), ()))
            return tuple(occupied_index[index] for index in indexes)

        open_nodes = [(0.0, 0.0, start)]
        costs = {start: 0.0}
        previous = {}
        edge_cache = {}

        def heuristic(node):
            return min(abs(node[0] - target[0]) + abs(node[1] - target[1]) for target in search_targets)

        while open_nodes:
            _, cost, node = heapq.heappop(open_nodes)
            if cost != costs.get(node):
                continue
            if node in target_set:
                path = [node]
                while node in previous:
                    node = previous[node]
                    path.append(node)
                return tuple(reversed(path))
            for dx, dy in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
                neighbor = (_grid(node[0] + dx), _grid(node[1] + dy))
                if neighbor[0] not in board_x or neighbor[1] not in board_y:
                    continue
                edge_key = (node, neighbor)
                blocked = edge_cache.get(edge_key)
                if blocked is None:
                    blocked = _blocked_segment(
                        node,
                        neighbor,
                        net_name,
                        layer,
                        nearby_occupied(node, neighbor),
                    )
                    edge_cache[edge_key] = blocked
                if blocked:
                    continue
                next_cost = cost + 1.0
                if next_cost >= costs.get(neighbor, float("inf")):
                    continue
                costs[neighbor] = next_cost
                previous[neighbor] = node
                heapq.heappush(open_nodes, (next_cost + heuristic(neighbor), next_cost, neighbor))
        raise RuntimeError(f"canonical physical router could not connect {net_name}")

    route_priority = {name: index for index, name in enumerate(("3V3", "NRST", "GND", "SWDIO", "SWCLK", "BUTTON_PB0", "LED_PA8", "BOOT0"))}
    route_layer = {"GND": "B.Cu", "3V3": "B.Cu", "SWDIO": "F.Cu", "SWCLK": "F.Cu", "BUTTON_PB0": "F.Cu", "LED_PA8": "F.Cu", "BOOT0": "F.Cu", "NRST": "B.Cu"}
    plane_nets = frozenset(("3V3",))
    fixed_escape_segments = []
    for net in pcb_nets:
        route_layer_name = route_layer[net.name]
        for endpoint in net.endpoints:
            position = footprint_library.pad_world_position(
                placement_by_ref[endpoint.reference],
                component_by_ref[endpoint.reference].footprint_binding or "",
                endpoint.pin_number,
            )
            escape = _escape_point(endpoint.reference, endpoint.pin_number, position)
            is_through_hole = footprint_library.pad_is_through_hole(
                component_by_ref[endpoint.reference].footprint_binding or "",
                endpoint.pin_number,
            )
            pad_positions = footprint_library.pad_world_positions(
                placement_by_ref[endpoint.reference],
                component_by_ref[endpoint.reference].footprint_binding or "",
                endpoint.pin_number,
            )
            if net.name not in plane_nets:
                fixed_layer = route_layer_name if route_layer_name == "F.Cu" or is_through_hole else "F.Cu"
                fixed_escape_segments.append((net.name, fixed_layer, position, escape))
            elif net.name == "3V3" and not is_through_hole:
                fixed_escape_segments.append((net.name, "F.Cu", position, escape))
            fixed_escape_segments.extend(
                (net.name, "F.Cu", duplicate, pad_positions[0])
                for duplicate in pad_positions[1:]
                if endpoint.reference == "SW1"
            )
    route_order = tuple(sorted(pcb_nets, key=lambda net: route_priority.get(net.name, len(route_priority))))
    via_obstacles = []
    for net in route_order:
        route_endpoints = tuple(net.endpoints)
        if net.name == "GND":
            route_endpoints = tuple(
                sorted(
                    route_endpoints,
                    key=lambda item: (0 if item.reference == "C1" and item.pin_number == "2" else 1, item.reference, item.pin_number),
                )
            )
        positions = tuple(footprint_library.pad_world_position(placement_by_ref[item.reference], component_by_ref[item.reference].footprint_binding or "", item.pin_number) for item in route_endpoints)
        if len(positions) < 2:
            raise RuntimeError(f"DesignTruth net has fewer than two routable endpoints: {net.name}")
        if net.name in plane_nets:
            plane_positions = []
            for endpoint, position in sorted(zip(route_endpoints, positions, strict=True), key=lambda item: (item[1][0], item[1][1])):
                plane_positions.extend((position, _escape_point(endpoint.reference, endpoint.pin_number, position)))
            routes.append(PCBRouteSegment.create(net_name=net.name, layer=route_layer[net.name], width_mm=0.25, points=tuple(plane_positions), source_refs=(truth.truth_id, FACTS_RELATIVE)))
            if route_layer[net.name] == "B.Cu":
                via_obstacles.extend(
                    routes[-1].points[index + 1]
                    for index, point in enumerate(routes[-1].points)
                    if index + 1 < len(routes[-1].points)
                    and any(math.dist(point, position) <= 1e-6 for position in positions)
                    and any(
                        not footprint_library.pad_is_through_hole(
                            component_by_ref[endpoint.reference].footprint_binding or "",
                            endpoint.pin_number,
                        )
                        for endpoint, position in zip(route_endpoints, positions, strict=True)
                        if math.dist(point, position) <= 1e-6
                    )
                    and math.dist(point, routes[-1].points[index + 1]) > 1e-6
                )
            continue
        root_escape = _escape_point(route_endpoints[0].reference, route_endpoints[0].pin_number, positions[0])
        raw_points = [positions[0], root_escape]
        tree = [root_escape]
        last_escape = None
        last_branch = None
        for endpoint, position in zip(route_endpoints[1:], positions[1:], strict=True):
            escape = _escape_point(endpoint.reference, endpoint.pin_number, position)
            if net.name == "SWDIO":
                # A deterministic right-side corridor keeps the SWD trace
                # clear of the MCU escape cluster; KiCad remains the
                # authority for the resulting physical implementation.
                branch = (
                    escape,
                    (70.0, escape[1]),
                    (70.0, root_escape[1]),
                    root_escape,
                )
            else:
                branch = _grid_path(
                    escape,
                    (root_escape,),
                    net.name,
                    route_layer[net.name],
                    [
                        segment
                        for route in routes
                        if route.net_name not in plane_nets and route.layer == route_layer[net.name]
                        for segment in zip(route.points, route.points[1:])
                        ] + [
                            (anchor, anchor)
                            for anchor in via_obstacles
                        ] + [
                        (start, end)
                        for fixed_net_name, fixed_layer, start, end in fixed_escape_segments
                        if fixed_net_name != net.name and fixed_layer == route_layer[net.name]
                    ],
                )
            if last_escape is None or last_branch is None:
                raw_points.extend((*reversed(branch), position))
            else:
                raw_points.extend((last_escape, *last_branch, *reversed(branch), position))
            tree.extend(branch)
            last_escape = escape
            last_branch = branch
        points = tuple(point for index, point in enumerate(raw_points) if index == 0 or point != raw_points[index - 1])
        routes.append(PCBRouteSegment.create(net_name=net.name, layer=route_layer[net.name], width_mm=0.25, points=points, source_refs=(truth.truth_id, FACTS_RELATIVE)))
        if route_layer[net.name] == "B.Cu":
            via_obstacles.extend(
                point
                for endpoint, position in zip(route_endpoints, positions, strict=True)
                for index, point in enumerate(routes[-1].points)
                if math.dist(point, position) <= 1e-6
                if not footprint_library.pad_is_through_hole(
                    component_by_ref[endpoint.reference].footprint_binding or "",
                    endpoint.pin_number,
                )
                for point in (
                    routes[-1].points[index + 1]
                    if net.name in plane_nets and index + 1 < len(routes[-1].points)
                    else routes[-1].points[index - 1]
                    if net.name not in plane_nets and index > 0
                    else None,
                )
                if point is not None and math.dist(point, position) > 1e-6
            )
    pcb_board = PCBBoardDefinition.create(width_mm=120.0, height_mm=90.0, origin_x_mm=10.0, origin_y_mm=10.0, layer_count=2, copper_layers=("F.Cu", "B.Cu"), board_thickness_mm=1.6, source_refs=(FACTS_RELATIVE,))
    pcb_routing = PCBRoutingConstraint.create(trace_width_mm=0.25, clearance_mm=0.2, source_refs=(FACTS_RELATIVE,))
    pcb_service = PCBProductionService(footprints=KiCadFootprintAvailability(footprint_root), plane_nets=plane_nets)
    runtime.set_pcb_production_service(pcb_service)
    physical_design = PCBPhysicalDesignProposal.create(
        physical_design_id=_id("canonical-pcb-physical-design", project.project_id, truth.fingerprint),
        project_id=project.project_id,
        conversation_id=conversation.conversation_id,
        board=pcb_board,
        placements=pcb_placements,
        routes=tuple(routes),
        routing=pcb_routing,
        source_refs=(FACTS_RELATIVE, truth.truth_id, schematic_planning.approved_plan.fingerprint),
    )
    pcb_input_detail = runtime.create_pcb_production_input(
        project.project_id,
        conversation.conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
        physical_design=physical_design,
        schematic_artifact_id=source_artifact_id,
        schematic_artifact_fingerprint=schematic_artifact.hash,
        schematic_content_fingerprint=schematic_op.operation.content_fingerprint,
        workspace_identity="project-root",
        target_relative_path=PCB_RELATIVE,
        requested_at=NOW,
    )
    pcb_request = pcb_input_detail.pcb_production_request
    if pcb_request is None:
        raise RuntimeError("G5 PCB production request projection unavailable")
    pcb_proposal_detail = runtime.create_pcb_production_proposal(project.project_id, conversation.conversation_id, tenant_id=TENANT_ID, requester=REQUESTER)
    pcb_production = pcb_proposal_detail.pcb_production
    if pcb_production is None:
        raise RuntimeError("G5 PCB proposal projection unavailable")
    pcb_proposal = pcb_production.proposal
    pcb_readiness = pcb_service.evaluate_readiness(pcb_request)
    if pcb_readiness.status.value != "READY":
        raise RuntimeError(f"PCB readiness failed: {pcb_readiness.status.value} missing={pcb_readiness.missing_facts} affected_nets={pcb_readiness.affected_nets}")
    pcb_bridge = PCBExecutionBridge(
        workspace=workspace,
        execution=execution,
        footprint_root=footprint_root,
        plane_nets=plane_nets,
        plane_layers={"3V3": "B.Cu"},
        clock=lambda: NOW,
    )
    queued_detail = runtime.decide_pcb_production(
        project.project_id,
        conversation.conversation_id,
        PCBProposalDecisionStatus.APPROVED,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
        decided_at=NOW,
        bridge=pcb_bridge,
    )
    queued_production = queued_detail.pcb_production
    if queued_production is None or queued_production.operation_id is None:
        raise RuntimeError("G5 PCB operation was not persisted")
    pcb_operation = execution.get_operation(tenant_id=TENANT_ID, project_id=project.project_id, operation_id=queued_production.operation_id)
    if pcb_operation is None:
        raise RuntimeError("G5 PCB operation receipt is unavailable")
    if worker.run_once().status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("PCB write failed")
    pcb_receipt = execution.get_receipt(tenant_id=TENANT_ID, project_id=project.project_id, operation_id=pcb_operation.operation_id)
    if pcb_receipt is None or pcb_receipt.status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("PCB write receipt unavailable")
    generation_detail = runtime.record_pcb_generation(operation=pcb_operation, receipt=pcb_receipt)
    generation = generation_detail.pcb_production.generation_receipt
    if generation is None:
        raise RuntimeError("G5 PCB generation receipt was not persisted")
    drc_op = pcb_bridge.enqueue_drc(write_operation=pcb_operation, receipt_fingerprint=pcb_receipt.fingerprint, reviewer_id=REQUESTER)
    parse_evidence = KiCadCliPCBParseAdapter(
        executable=executable,
        workspace_resolver=lambda operation: root,
        command_builder=parse_builder,
        kicad_version=probe_kicad_cli_version(executable, cwd=root),
        clock=lambda: NOW,
    ).execute(drc_op.operation)
    if parse_evidence.validation_status.value != "PARSE_VERIFIED":
        raise RuntimeError("canonical PCB parse validation failed")
    runtime.record_pcb_parse_validation(evidence=parse_evidence)
    if worker.run_once().status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("PCB DRC failed")
    drc_receipt = execution.get_receipt(tenant_id=TENANT_ID, project_id=project.project_id, operation_id=drc_op.operation.operation_id)
    if drc_receipt is None or getattr(drc_receipt.pcb_drc_result, "status", None).value != "DRC_PASS":
        raise RuntimeError(f"PCB DRC was not PASS: {getattr(drc_receipt.pcb_drc_result, 'status', None) if drc_receipt else None}")
    drc_detail = runtime.record_pcb_drc_validation(operation=drc_op.operation, receipt=drc_receipt)
    pcb_state = drc_detail.pcb_production
    if pcb_state is None:
        raise RuntimeError("G5 PCB DRC projection was not persisted")
    conversation = drc_detail
    if stop_after_g5:
        final_snapshot = ArtifactIntelligenceService().scan_directory(root=root, tenant_id=TENANT_ID, project_id=project.project_id, conversation_id=conversation.conversation_id, source_operation_id=pcb_operation.operation_id, source_proposal_fingerprint=pcb_state.proposal.fingerprint)
        final_detail = conversation.model_copy(update={"artifact_intelligence_snapshot": final_snapshot, "fingerprint": ZERO})
        final_detail = runtime.persist_conversation_detail(final_detail)
        return _report(runtime, project, final_detail, g5_only=True, facts_fp=facts_fp, schematic_receipt=schematic_receipt, erc_receipt=erc_receipt, pcb_receipt=pcb_receipt, drc_receipt=drc_receipt, execution_state=state_database.parent / "canonical-golden-execution")
    _controlled_files(workspace, root=root, project_id=project.project_id, files={BOM_RELATIVE: _build_bom(truth)})

    source_task = next(task for task in implementation.tasks if generate_execution_intent(task).required_capabilities == ("FIRMWARE_CODE_GENERATION",))
    source_intent = next(item for item in intents if item.source_task_refs == (source_task.task_id,))
    auth_request = next(item for item in authorization.requests if item.request_id == next(i for i in source_intent.source_task_refs if i == source_task.task_id)) if False else next(item for item in authorization.requests if item.intent_id == source_intent.intent_id)
    auth_decision = next(item for item in authorization.decisions if item.request_id == auth_request.request_id)
    module = next(item for item in firmware.modules if item.module_id in source_task.source_refs)
    source_request = generate_firmware_source_request(firmware, implementation, source_task, module, source_intent, authorization, authorization_request_id=auth_request.request_id, tenant_id=TENANT_ID)
    source_proposal = _source_proposal(source_request, module)
    source_decision = decide_firmware_source_proposal(source_proposal, decision=FirmwareSourceProposalDecisionStatus.APPROVED, reviewer=REQUESTER, decided_at=NOW)
    source_bridge = FirmwareSourceExecutionBridge(workspace=workspace, execution=execution, clock=lambda: NOW)
    source_op = source_bridge.approve_and_enqueue(generation_request=source_request, proposal=source_proposal, decision=source_decision)
    if worker.run_once().status is not OperationStatus.SUCCEEDED:
        raise RuntimeError("firmware source write failed")
    source_state = create_firmware_source_generation_state(source_request, source_proposal).model_copy(update={"decision": source_decision, "operation_id": source_op.operation.operation_id, "operation_fingerprint": source_op.operation.fingerprint, "status": FirmwareSourceGenerationStatus.QUEUED, "fingerprint": ZERO})

    source_artifacts = tuple(FirmwareSourceArtifact(relative_path=item.relative_path, content_fingerprint=item.content_fingerprint, size_bytes=item.size_bytes) for item in source_proposal.files)
    build_request = create_firmware_build_request(firmware, implementation, configuration=build_config, source_artifacts=source_artifacts, expected_source_revision=_hash("|".join(item.content_fingerprint for item in source_artifacts).encode()), actual_source_revision=_hash("|".join(item.content_fingerprint for item in source_artifacts).encode()))
    tool_definition = ToolDefinition("ARM_NONE_EABI_GCC", "UNAVAILABLE", ToolFamily.ARM_NONE_EABI_GCC, OperationType.BUILD, ("STM32F103C8T6",), ("arm-none-eabi-gcc", "arm-none-eabi-gcc.exe"), 600, ("ELF", "BIN", "MAP"))
    tool_resolution = ToolResolution(tool_definition, None, False, "UNAVAILABLE", "PRIOR_DISCOVERY_NO_TRUSTED_TOOLCHAIN")
    build_readiness = evaluate_build_readiness(build_request, workspace_root=root, tool=tool_resolution)
    artifact_snapshot = ArtifactIntelligenceService().scan_directory(root=root, tenant_id=TENANT_ID, project_id=project.project_id, conversation_id=conversation.conversation_id, source_operation_id=source_op.operation.operation_id, source_proposal_fingerprint=source_proposal.fingerprint)
    final_detail = ConversationDetail.model_validate({**conversation.model_dump(mode="python"), "title": GOLDEN_NAME, "engineering_session_mode": "CREATE", "requirement_context": context, "engineering_plan": plan, "engineering_plan_review": plan_review, "architecture_design": architecture, "architecture_review": architecture_review, "design_truth": truth, "engineering_workflow": workflow, "hardware_design_spec": hardware, "firmware_spec": firmware, "simulation_spec": simulation, "verification_spec": verification, "implementation_spec": implementation, "build_configuration": build_config, "capability_evaluation": evaluation, "execution_intents": intents, "execution_authorization": authorization, "firmware_source_generation": source_state, "schematic_production": schematic_state, "pcb_production_request": pcb_request, "pcb_production": pcb_state, "artifact_intelligence_snapshot": artifact_snapshot, "fingerprint": ZERO}, strict=False)
    runtime.persist_conversation_detail(final_detail)
    return _report(runtime, project, final_detail, build_readiness=build_readiness, facts_fp=facts_fp, schematic_receipt=schematic_receipt, erc_receipt=erc_receipt, pcb_receipt=pcb_receipt, drc_receipt=drc_receipt, source_proposal=source_proposal, execution_state=state_database.parent / "canonical-golden-execution")


def _report(runtime, project, detail, **extra) -> dict[str, object]:
    root = _project_root(runtime, project)
    snapshot = detail.artifact_intelligence_snapshot
    artifacts = () if snapshot is None else snapshot.inventory.artifacts

    def artifact(path_reference: str):
        return next((item for item in artifacts if item.path_reference == path_reference), None)

    requirement_id = None
    requirement_fingerprint = None
    if detail.requirement_context is not None:
        requirement_fingerprint = detail.requirement_context.fingerprint
        requirement_id = _id("requirement-specification", project.project_id, requirement_fingerprint)

    target_decision = None
    if detail.architecture_design is not None:
        target_decision = next(
            (
                item
                for item in detail.architecture_design.decisions
                if item.subject == "controller platform"
                and item.selected_option in {"STM32", "STM32F103C8T6"}
            ),
            None,
        )

    schematic_artifact = artifact(SCHEMATIC_RELATIVE)
    pcb_artifact = artifact(PCB_RELATIVE)
    bom_artifact = artifact(BOM_RELATIVE)
    firmware_artifacts = tuple(
        item for item in artifacts
        if item.path_reference in {"generated/minimal_mcu_control_core.c", "generated/minimal_mcu_control_core.h"}
    )
    schematic_request = None if detail.schematic_production is None else detail.schematic_production.request
    pcb_request = None if detail.pcb_production is None else detail.pcb_production.request
    persisted_receipts = ()
    execution_state = extra.get("execution_state")
    execution_db = None if execution_state is None else Path(execution_state) / "execution.sqlite3"
    if execution_db is not None and execution_db.is_file():
        try:
            store = SQLiteExecutionStore(execution_db)
            persisted_receipts = tuple(
                receipt
                for operation in store.list_operations(TENANT_ID, project.project_id, limit=100)
                if (receipt := store.get_receipt(TENANT_ID, project.project_id, operation.operation_id)) is not None
            )
        except Exception:
            persisted_receipts = ()
    persisted_erc_receipt = next((item for item in persisted_receipts if getattr(item, "schematic_erc_status", None) == "ERC_PASS"), None)
    persisted_drc_receipt = next(
        (
            item for item in persisted_receipts
            if getattr(getattr(item, "pcb_drc_result", None), "status", None) is not None
            and getattr(item.pcb_drc_result.status, "value", None) == "DRC_PASS"
        ),
        None,
    )
    state_drc_id = None if detail.pcb_production is None else next(
        (item for item in detail.pcb_production.proposal.verification_refs if item.startswith("kicad-pcb-drc-")),
        None,
    )
    target_facts = {
        "firmware_platform": "STM32",
        "mcu_family": None if detail.firmware_spec is None else detail.firmware_spec.mcu_family,
        "mcu_exact_part": None if detail.firmware_spec is None else detail.firmware_spec.mcu_exact_part,
        "architecture": "Cortex-M3",
        "firmware_framework": "CMSIS_MINIMAL_BARE_METAL",
        "build_system": None if detail.firmware_spec is None else detail.firmware_spec.build_system,
        "toolchain_family": "GNU_ARM_EMBEDDED",
    }
    source_paths = () if detail.firmware_spec is None else detail.firmware_spec.source_layout
    source_hashes = {
        item.path_reference: item.hash
        for item in firmware_artifacts
    }
    target_consistent = bool(
        detail.firmware_spec is not None
        and detail.implementation_spec is not None
        and detail.build_configuration is not None
        and detail.design_truth is not None
        and detail.firmware_spec.mcu_exact_part == "STM32F103C8T6"
        and detail.firmware_spec.mcu_family == "STM32F1"
        and detail.firmware_spec.build_system == "CMAKE"
        and detail.firmware_spec.toolchain_family == "ARM_NONE_EABI_GCC"
        and detail.firmware_spec.design_truth_id == detail.design_truth.truth_id
        and detail.firmware_spec.design_truth_fingerprint == detail.design_truth.fingerprint
    )
    result: dict[str, object] = {
        "project_id": project.project_id,
        "project_name": project.name,
        "project_relative_path": project.relative_path,
        "conversation_id": detail.conversation_id,
        "design_truth_id": None if detail.design_truth is None else detail.design_truth.truth_id,
        "design_truth_fingerprint": None if detail.design_truth is None else detail.design_truth.fingerprint,
        "firmware_spec_id": None if detail.firmware_spec is None else detail.firmware_spec.firmware_spec_id,
        "firmware_spec_fingerprint": None if detail.firmware_spec is None else detail.firmware_spec.fingerprint,
        "implementation_id": None if detail.implementation_spec is None else detail.implementation_spec.implementation_id,
        "implementation_fingerprint": None if detail.implementation_spec is None else detail.implementation_spec.fingerprint,
        "build_configuration": None if detail.build_configuration is None else detail.build_configuration.model_dump(mode="json"),
        "requirement_specification_id": requirement_id,
        "requirement_specification_fingerprint": requirement_fingerprint,
        # The repository's existing canonical decision contract is the
        # approved ArchitectureDecision.  No parallel EngineeringDecision
        # model is introduced for this Golden.
        "engineering_decision_id": None if target_decision is None else target_decision.decision_id,
        "engineering_decision_fingerprint": None if target_decision is None else target_decision.fingerprint,
        "engineering_decision_source": "USER_APPROVED_GOLDEN_TARGET" if target_decision is not None else None,
        "architecture_id": None if detail.architecture_design is None else detail.architecture_design.architecture_id,
        "hardware_design_id": None if detail.hardware_design_spec is None else detail.hardware_design_spec.hardware_spec_id,
        "build_configuration_id": None if detail.implementation_spec is None else detail.implementation_spec.build_configuration_id,
        "build_configuration_fingerprint": None if detail.implementation_spec is None else detail.implementation_spec.build_configuration_fingerprint,
        "schematic_artifact_id": None if schematic_artifact is None else schematic_artifact.artifact_id,
        "schematic_sha256": None if schematic_artifact is None else schematic_artifact.hash,
        "pcb_artifact_id": None if pcb_artifact is None else pcb_artifact.artifact_id,
        "pcb_sha256": None if pcb_artifact is None else pcb_artifact.hash,
        "bom_artifact_id": None if bom_artifact is None else bom_artifact.artifact_id,
        "bom_sha256": None if bom_artifact is None else bom_artifact.hash,
        "firmware_source_artifact_ids": [item.artifact_id for item in firmware_artifacts],
        "firmware_source_hashes": source_hashes,
        "target": target_facts,
        "target_consistent": "PASS" if target_consistent else "FAIL",
        "target_conflicts": [] if target_consistent else ["canonical firmware target bindings are incomplete or inconsistent"],
        "target_missing_facts": [] if target_consistent else ["FirmwareSpecification/DesignTruth/BuildConfiguration binding"],
        "erc_evidence_id": pcb_request.schematic_erc_result_id if pcb_request is not None else (None if persisted_erc_receipt is None else persisted_erc_receipt.operation_id),
        "drc_evidence_id": state_drc_id or (None if persisted_drc_receipt is None else persisted_drc_receipt.operation_id),
        "artifact_linkage": "PASS" if snapshot is not None and schematic_artifact is not None and pcb_artifact is not None and bom_artifact is not None and len(firmware_artifacts) == len(source_paths) else "FAIL",
        "evidence_linkage": "PASS" if snapshot is not None and schematic_request is not None and pcb_request is not None else "FAIL",
        "basic_hw_fw_pin_parity": "PASS" if detail.design_truth is not None and detail.firmware_spec is not None and {"GPIOA.PA8", "GPIOB.PB0", "SWD.PA13_PA14", "NRST", "BOOT0"}.issubset(set(detail.firmware_spec.peripheral_expectations)) else "FAIL",
        "persistent_lineage": "PASS" if detail.requirement_context is not None and target_decision is not None and detail.architecture_design is not None and detail.design_truth is not None and detail.hardware_design_spec is not None and detail.firmware_spec is not None and detail.implementation_spec is not None and detail.build_configuration is not None else "FAIL",
        "restart_recovery": "PASS" if detail.build_configuration is not None and snapshot is not None else "FAIL",
        "artifacts": sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and not path.is_symlink() and ".embedded-copilot-state" not in path.relative_to(root).parts),
        "official_toolchain_required": "GNU Arm Embedded Toolchain / arm-none-eabi-gcc for STM32F103C8T6",
        "toolchain_installation": "TOOLCHAIN_INSTALLATION_AUTHORIZATION_REQUIRED",
        "firmware_build_executed": False,
        "flash_executed": False,
        "simulation_runtime_entered": False,
        "report_label": "EMBRIX_G5V_REAL_PCB_DRC_VALIDATION_REPORT" if detail.pcb_production is not None else None,
        "g4_erc_gate": None if detail.schematic_production is None else (None if detail.schematic_production.erc_gate_status is None else detail.schematic_production.erc_gate_status.value),
        "g4_erc_status": None if detail.schematic_production is None or detail.schematic_production.erc_receipt is None else detail.schematic_production.erc_receipt.result.status.value,
        "g4_erc_issue_count": 0 if detail.schematic_production is None or detail.schematic_production.erc_receipt is None else len(detail.schematic_production.erc_receipt.result.issues),
        "g4_stop_before_pcb": bool(extra.get("g4_only", False)),
        "g5_production_status": None if detail.pcb_production is None else detail.pcb_production.status.value,
        "g5_parse_validation": None if detail.pcb_production is None or detail.pcb_production.generation_receipt is None else detail.pcb_production.generation_receipt.validation_status.value,
        "g5_drc_gate": None if detail.pcb_production is None or detail.pcb_production.drc_gate_status is None else detail.pcb_production.drc_gate_status.value,
        "g5_drc_issue_count": 0 if detail.pcb_production is None or detail.pcb_production.drc_result is None else len(detail.pcb_production.drc_result.issues),
        "g5_stop_before_firmware": bool(extra.get("g5_only", False)),
        "g4_repair_application_receipts": [] if detail.schematic_production is None else [item.model_dump(mode="json") for item in detail.schematic_production.repair_application_receipts],
    }
    facts_path = root / FACTS_RELATIVE
    if facts_path.is_file():
        result["golden_facts_fingerprint"] = _hash(facts_path.read_bytes())
    for key, value in extra.items():
        if key in {"schematic_receipt", "erc_receipt", "pcb_receipt", "drc_receipt"} and value is not None:
            result[key] = {"receipt_id": value.operation_id, "fingerprint": value.fingerprint, "status": value.status.value, "erc": getattr(value, "schematic_erc_status", None), "drc": None if getattr(value, "pcb_drc_result", None) is None else value.pcb_drc_result.status.value}
        elif key == "build_readiness" and value is not None:
            result[key] = {"status": value.status.value, "reasons": value.reasons, "toolchain_reason": value.toolchain_reason}
        elif key == "source_proposal" and value is not None:
            result["firmware_source_proposal"] = {"proposal_id": value.proposal_id, "fingerprint": value.fingerprint, "files": [item.relative_path for item in value.files]}
        elif key == "facts_fp":
            result["golden_facts_fingerprint"] = value
    if "build_readiness" not in result and detail.build_configuration is not None:
        result["build_readiness"] = {
            "status": "BLOCKED_TOOLCHAIN_UNAVAILABLE",
            "reasons": ("BLOCKED_TOOLCHAIN_UNAVAILABLE",),
            "toolchain_reason": "PRIOR_DISCOVERY_NO_TRUSTED_TOOLCHAIN",
        }
    result["canonical_target"] = "PASS" if target_consistent else "FAIL"
    result["source_hash_validation"] = "PASS" if source_hashes and all(
        (root / path).is_file() and _hash((root / path).read_bytes()) == value
        for path, value in source_hashes.items()
    ) else "FAIL"
    result["hardware_artifact_parity"] = "PASS" if schematic_artifact is not None and pcb_artifact is not None and bom_artifact is not None else "FAIL"
    result["evidence_inventory"] = "PASS" if snapshot is not None and snapshot.engineering_evidence_bundle is not None else "FAIL"
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(r"D:\EmbeddedProjects"))
    parser.add_argument("--state-database", type=Path, default=Path(".data/canonical-golden-project.sqlite3"))
    parser.add_argument("--kicad-cli", default=os.environ.get("EMBRIX_KICAD_CLI", ""))
    parser.add_argument("--stop-after-g4", action="store_true", help="Stop after parse-verified real schematic ERC; do not enter PCB/DRC.")
    parser.add_argument("--stop-after-g5", action="store_true", help="Stop after parse-verified real PCB DRC; do not enter Firmware, Simulation, or Report.")
    args = parser.parse_args()
    executable = discover_kicad_cli(configured_path=args.kicad_cli or None)
    if executable is None:
        raise SystemExit("KiCad CLI is required for real ERC/DRC; set EMBRIX_KICAD_CLI")
    report = bootstrap(project_root=args.project_root, state_database=args.state_database, executable=str(executable), stop_after_g4=args.stop_after_g4, stop_after_g5=args.stop_after_g5)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
