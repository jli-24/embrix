from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from embedded_copilot.firmware_engineering.models import canonical_fingerprint

from .contracts import (
    ArtifactLineage,
    ConsistencyCheck,
    ConsistencyCheckStatus,
    ConsistencyEvidence,
    ConsistencyInput,
    ConsistencyOverallStatus,
    FirmwarePinObservation,
    HardwareFirmwareConsistencyResult,
    HardwarePinTruth,
    LogicLevel,
    PullStrategy,
    TruthDirection,
    VerificationLevel,
)

_CHECKS = (
    "MCU_IDENTITY",
    "ARCHITECTURE",
    "MEMORY_LAYOUT",
    "GPIO_PIN_MAPPING",
    "GPIO_DIRECTION",
    "GPIO_LOGIC_POLARITY",
    "BOOT_CONFIG",
    "RESET_RUNTIME",
    "FIRMWARE_SOURCE_BINDING",
    "BUILD_CONFIGURATION_BINDING",
    "ELF_TARGET_BINDING",
    "DESIGN_TRUTH_FINGERPRINT_BINDING",
    "SCHEMATIC_BINDING",
    "PCB_BINDING",
    "BOM_BINDING",
)


def _id(value: str) -> str:
    import hashlib

    return "consistency-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:48]


def _ref(check_id: str) -> str:
    return "evidence-" + check_id.casefold().replace("_", "-")


def _check(
    check_id: str,
    status: ConsistencyCheckStatus,
    *,
    required: bool = True,
    summary: str,
    expected: str | None = None,
    observed: str | None = None,
) -> ConsistencyCheck:
    return ConsistencyCheck.create(
        check_id=check_id,
        status=status,
        required=required,
        summary=summary,
        expected=expected,
        observed=observed,
        evidence_refs=(_ref(check_id),),
    )


def _all_equal(values: Iterable[object]) -> bool:
    values = tuple(values)
    return bool(values) and all(value == values[0] for value in values[1:])


def _pin_aliases(signal: str) -> frozenset[str]:
    aliases = {
        "PA8": {"PA8", "LED", "LED_PA8"},
        "PB0": {"PB0", "BUTTON", "BUTTON_PB0"},
        "BOOT0": {"BOOT0"},
        "NRST": {"NRST"},
        "PA13-SWDIO": {"PA13-SWDIO", "SWDIO"},
        "PA14-SWCLK": {"PA14-SWCLK", "SWCLK"},
    }
    return frozenset(aliases.get(signal, {signal}))


def _observations_by_signal(
    observations: tuple[FirmwarePinObservation, ...],
) -> dict[str, FirmwarePinObservation]:
    return {item.signal: item for item in observations}


def _board_pin_matches(input_value: ConsistencyInput, truth: HardwarePinTruth) -> bool:
    aliases = _pin_aliases(truth.signal)
    return any(
        item.component_reference == "U1"
        and item.pin_number == truth.mcu_pin
        and item.net_name in aliases
        for item in input_value.hardware.board_connections
    )


def _status_for_matches(
    *,
    expected: tuple[str, ...],
    observed: tuple[str, ...],
    summary: str,
) -> tuple[ConsistencyCheckStatus, str]:
    if not expected or not observed:
        return (
            ConsistencyCheckStatus.UNKNOWN,
            summary + ": required fact is unavailable",
        )
    if _all_equal((expected, observed)):
        return ConsistencyCheckStatus.PASS, summary
    return (
        ConsistencyCheckStatus.CONFLICT,
        summary + ": observed values conflict with truth",
    )


def _build_configuration_fingerprint(value: object) -> str:
    return canonical_fingerprint(value)


class HardwareFirmwareConsistencyService:
    """Deterministic HW/FW consistency projection; it never mutates engineering assets."""

    def evaluate(
        self, input_value: ConsistencyInput
    ) -> HardwareFirmwareConsistencyResult:
        if not isinstance(input_value, ConsistencyInput):
            raise TypeError("consistency input is invalid")
        checks = self._checks(input_value)
        artifact_integrity = self._artifact_integrity(input_value)
        stale_inputs = self._stale_inputs(checks, artifact_integrity)
        conflicts = any(
            item.required and item.status is ConsistencyCheckStatus.CONFLICT
            for item in checks
        )
        unknowns = any(
            item.required and item.status is ConsistencyCheckStatus.UNKNOWN
            for item in checks
        )
        if artifact_integrity is ConsistencyCheckStatus.CONFLICT:
            conflicts = True
        elif artifact_integrity is ConsistencyCheckStatus.UNKNOWN:
            unknowns = True
        overall = (
            ConsistencyOverallStatus.CONFLICT
            if conflicts
            else ConsistencyOverallStatus.INCOMPLETE
            if unknowns
            else ConsistencyOverallStatus.CONSISTENT
        )
        lineage = (
            input_value.hardware.schematic,
            input_value.hardware.pcb,
            input_value.hardware.bom,
            *input_value.build.source_artifacts,
            input_value.build.elf,
            input_value.build.binary,
        )
        if input_value.build.map_file is not None:
            lineage += (input_value.build.map_file,)
        evidence = ConsistencyEvidence.create(
            project_id=input_value.project_id,
            conversation_id=input_value.conversation_id
            or input_value.truth.conversation_id,
            design_truth_id=input_value.truth.truth_id,
            firmware_specification_id=None
            if input_value.firmware_specification is None
            else input_value.firmware_specification.firmware_spec_id,
            implementation_specification_id=None
            if input_value.implementation_specification is None
            else input_value.implementation_specification.implementation_id,
            build_configuration_id=input_value.build.build_configuration_id,
            build_request_id=input_value.build.build_request_id,
            build_operation_id=input_value.build.build_operation_id,
            build_receipt_id=input_value.build.build_receipt_id,
            schematic_artifact_id=input_value.hardware.schematic.artifact_id,
            pcb_artifact_id=input_value.hardware.pcb.artifact_id,
            bom_artifact_id=input_value.hardware.bom.artifact_id,
            firmware_source_artifact_ids=tuple(
                item.artifact_id for item in input_value.build.source_artifacts
            ),
            elf_artifact_id=input_value.build.elf.artifact_id,
            bin_artifact_id=input_value.build.binary.artifact_id,
            map_artifact_id=None
            if input_value.build.map_file is None
            else input_value.build.map_file.artifact_id,
            deterministic=True,
            tool_observed=input_value.build.tool_observed
            and input_value.elf_inspection is not None,
            design_truth_fingerprint=input_value.truth.design_truth_fingerprint,
            schematic_fingerprint=input_value.hardware.schematic.content_fingerprint,
            pcb_fingerprint=input_value.hardware.pcb.content_fingerprint,
            bom_fingerprint=input_value.hardware.bom.content_fingerprint,
            firmware_fingerprints=input_value.firmware.source_fingerprints,
            firmware_spec_fingerprint=input_value.build.firmware_spec_fingerprint,
            implementation_fingerprint=input_value.build.implementation_fingerprint,
            build_configuration_fingerprint=input_value.build.build_configuration_fingerprint,
            source_fingerprints=tuple(
                item.content_fingerprint for item in input_value.build.source_artifacts
            ),
            elf_fingerprint=input_value.build.elf.content_fingerprint,
            bin_fingerprint=input_value.build.binary.content_fingerprint,
            map_fingerprint=None
            if input_value.build.map_file is None
            else input_value.build.map_file.content_fingerprint,
            checks=checks,
            elf_inspection=input_value.elf_inspection,
            lineage=lineage,
            artifact_integrity=artifact_integrity,
            vector_table_status=self._vector_table_status(input_value),
            swd_mapping_status=self._swd_mapping_status(input_value),
            linker_script_fingerprint=input_value.build.linker_script_fingerprint,
            stale_inputs=stale_inputs,
        )
        l3 = (
            overall is ConsistencyOverallStatus.CONSISTENT
            and evidence.tool_observed
            and artifact_integrity is ConsistencyCheckStatus.PASS
            and evidence.vector_table_status is ConsistencyCheckStatus.PASS
            and evidence.swd_mapping_status is ConsistencyCheckStatus.PASS
            and input_value.firmware_specification is not None
            and input_value.implementation_specification is not None
            and input_value.build_configuration is not None
        )
        computed_at = datetime.now(UTC)
        return HardwareFirmwareConsistencyResult.create(
            result_id=_id(input_value.request_fingerprint or input_value.fingerprint),
            project_id=input_value.project_id,
            request_fingerprint=input_value.request_fingerprint
            or input_value.fingerprint,
            overall_status=overall,
            verification_level=(
                VerificationLevel.L3_ENGINEERING_VERIFIED
                if l3
                else VerificationLevel.UNVERIFIED
            ),
            checks=checks,
            evidence=evidence,
            computed_at=computed_at,
            created_at=computed_at,
            conversation_id=evidence.conversation_id,
            design_truth_id=evidence.design_truth_id,
            firmware_specification_id=evidence.firmware_specification_id,
            implementation_specification_id=evidence.implementation_specification_id,
            build_configuration_id=evidence.build_configuration_id,
            schematic_artifact_id=evidence.schematic_artifact_id,
            pcb_artifact_id=evidence.pcb_artifact_id,
            bom_artifact_id=evidence.bom_artifact_id,
            firmware_source_artifact_ids=evidence.firmware_source_artifact_ids,
            elf_artifact_id=evidence.elf_artifact_id,
            bin_artifact_id=evidence.bin_artifact_id,
            map_artifact_id=evidence.map_artifact_id,
            build_request_id=evidence.build_request_id,
            build_operation_id=evidence.build_operation_id,
            build_receipt_id=evidence.build_receipt_id,
            design_truth_fingerprint=evidence.design_truth_fingerprint,
            schematic_fingerprint=evidence.schematic_fingerprint,
            pcb_fingerprint=evidence.pcb_fingerprint,
            bom_fingerprint=evidence.bom_fingerprint,
            firmware_specification_fingerprint=evidence.firmware_spec_fingerprint,
            implementation_fingerprint=evidence.implementation_fingerprint,
            build_configuration_fingerprint=evidence.build_configuration_fingerprint,
            firmware_source_fingerprints=evidence.firmware_fingerprints,
            elf_fingerprint=evidence.elf_fingerprint,
            bin_fingerprint=evidence.bin_fingerprint,
            linker_script_fingerprint=evidence.linker_script_fingerprint,
            artifact_integrity=artifact_integrity,
            vector_table_status=evidence.vector_table_status,
            swd_mapping_status=evidence.swd_mapping_status,
            stale_inputs=stale_inputs,
            l1_structural_valid=all(
                item.status is not ConsistencyCheckStatus.UNKNOWN for item in checks
            ),
            l2_rule_verified=all(
                item.status in {
                    ConsistencyCheckStatus.PASS,
                    ConsistencyCheckStatus.NOT_APPLICABLE,
                }
                for item in checks
            )
            and artifact_integrity is ConsistencyCheckStatus.PASS,
            fabrication_ready=False,
        )

    def _checks(self, value: ConsistencyInput) -> tuple[ConsistencyCheck, ...]:
        target = value.truth
        firmware = value.firmware
        build = value.build
        elf = value.elf_inspection
        schematic_values = dict(value.hardware.schematic_components)
        bom_items = {item.reference: item for item in value.hardware.bom_items}
        observations = _observations_by_signal(firmware.pin_observations)
        specification_observations = _observations_by_signal(
            firmware.specification_pin_observations
        )
        firmware_spec = value.firmware_specification
        implementation_spec = value.implementation_specification
        build_configuration = value.build_configuration or build.build_configuration

        target_values = (
            target.mcu_exact_part,
            None if firmware_spec is None else firmware_spec.mcu_exact_part,
            firmware.mcu_exact_part,
            build.target_kind,
            None if build_configuration is None else build_configuration.target_kind,
            schematic_values.get("U1"),
        )
        target_status = (
            ConsistencyCheckStatus.UNKNOWN
            if any(item is None for item in target_values)
            else (
                ConsistencyCheckStatus.PASS
                if _all_equal(target_values)
                else ConsistencyCheckStatus.CONFLICT
            )
        )
        checks: list[ConsistencyCheck] = [
            _check(
                "MCU_IDENTITY",
                target_status,
                summary="MCU identity is bound across DesignTruth, firmware facts, build target, and schematic.",
                expected=target.mcu_exact_part,
                observed="|".join(
                    str(item) for item in target_values if item is not None
                ),
            )
        ]

        architecture_values = (
            target.architecture,
            None if firmware_spec is None else firmware_spec.architecture,
            firmware.architecture,
            elf.machine if elf else None,
        )
        compiler_flags = () if build_configuration is None else build_configuration.compiler_flags
        architecture_status = (
            ConsistencyCheckStatus.UNKNOWN
            if any(item is None for item in architecture_values)
            else (
                ConsistencyCheckStatus.PASS
                if architecture_values[0] == architecture_values[1]
                and architecture_values[1] == architecture_values[2]
                and architecture_values[3] == "ARM"
                and elf is not None
                and "soft-float" in elf.flags.casefold()
                and "-mcpu=cortex-m3" in compiler_flags
                and "-mthumb" in compiler_flags
                else ConsistencyCheckStatus.CONFLICT
            )
        )
        checks.append(
            _check(
                "ARCHITECTURE",
                architecture_status,
                summary="Firmware target architecture agrees with the observed ELF machine.",
                expected=target.architecture,
                observed="|".join(
                    str(item) for item in architecture_values if item is not None
                ),
            )
        )

        checks.append(self._memory_check(value))

        pin_results: list[str] = []
        for truth_pin in target.pins:
            observation = observations.get(truth_pin.signal)
            specification_observation = specification_observations.get(truth_pin.signal)
            board_match = _board_pin_matches(value, truth_pin)
            if observation is None and specification_observation is None and not board_match:
                pin_results.append("UNKNOWN")
            elif (
                observation is not None
                and truth_pin.gpio_port is not None
                and truth_pin.gpio_number is not None
            ):
                pin_results.append(
                    "PASS"
                    if observation.gpio_port == truth_pin.gpio_port
                    and observation.gpio_number == truth_pin.gpio_number
                    and (
                        specification_observation is None
                        or (
                            specification_observation.gpio_port == truth_pin.gpio_port
                            and specification_observation.gpio_number == truth_pin.gpio_number
                        )
                    )
                    and board_match
                    else "CONFLICT"
                )
            elif specification_observation is not None:
                pin_results.append(
                    "PASS"
                    if board_match
                    and specification_observation.gpio_port == truth_pin.gpio_port
                    and specification_observation.gpio_number == truth_pin.gpio_number
                    else "CONFLICT"
                )
            else:
                pin_results.append("PASS" if board_match else "UNKNOWN")
        checks.append(
            self._aggregate_pin_check(
                "GPIO_PIN_MAPPING",
                pin_results,
                "GPIO pin mapping agrees with DesignTruth, structured firmware observations, and PCB net bindings.",
            )
        )

        direction_results = [
            "PASS"
            if observation.direction is truth_pin.direction
            and (
                specification_observation is None
                or specification_observation.direction is truth_pin.direction
            )
            else "CONFLICT"
            for truth_pin in target.pins
            for observation in (observations.get(truth_pin.signal),)
            for specification_observation in (specification_observations.get(truth_pin.signal),)
            if observation is not None
            and truth_pin.direction is not TruthDirection.UNKNOWN
        ]
        checks.append(
            self._aggregate_pin_check(
                "GPIO_DIRECTION",
                direction_results,
                "Observed firmware GPIO direction agrees with DesignTruth.",
            )
        )

        polarity_results = [
            "PASS"
            if observation.active_level is truth_pin.active_level
            and (
                specification_observation is None
                or specification_observation.active_level is truth_pin.active_level
            )
            else "CONFLICT"
            for truth_pin in target.pins
            for observation in (observations.get(truth_pin.signal),)
            for specification_observation in (specification_observations.get(truth_pin.signal),)
            if observation is not None
            and truth_pin.active_level is not LogicLevel.UNKNOWN
        ]
        checks.append(
            self._aggregate_pin_check(
                "GPIO_LOGIC_POLARITY",
                polarity_results,
                "Observed firmware logic polarity agrees with enriched DesignTruth semantics.",
            )
        )

        checks.append(self._boot_check(value, bom_items))
        checks.append(self._reset_check(value, bom_items))

        source_fps = tuple(item.content_fingerprint for item in build.source_artifacts)
        source_status = (
            ConsistencyCheckStatus.UNKNOWN
            if firmware.parse_issues
            or not firmware.source_fingerprints
            or not source_fps
            or firmware_spec is None
            or firmware.firmware_specification_fingerprint is None
            else ConsistencyCheckStatus.PASS
            if set(firmware.source_fingerprints) == set(source_fps)
            and firmware.firmware_specification_fingerprint == firmware_spec.fingerprint
            else ConsistencyCheckStatus.CONFLICT
        )
        checks.append(
            _check(
                "FIRMWARE_SOURCE_BINDING",
                source_status,
                summary="Firmware source observations are bound to the source snapshot used by the build.",
                expected="|".join(source_fps),
                observed="|".join(firmware.source_fingerprints),
            )
        )

        build_systems = (
            target.build_system,
            None if firmware_spec is None else firmware_spec.build_system,
            firmware.build_system,
            build.build_system,
            None if build_configuration is None else build_configuration.build_system,
        )
        normalized_build_systems = tuple(
            None if item is None else str(item).casefold()
            for item in build_systems
        )
        toolchains = (
            target.toolchain_family,
            None if firmware_spec is None else firmware_spec.toolchain_family,
            firmware.toolchain_family,
            build.toolchain_family,
            None if build_configuration is None else build_configuration.toolchain_id,
        )
        build_targets = (
            target.mcu_exact_part,
            None if firmware_spec is None else firmware_spec.mcu_exact_part,
            build.target_kind,
            None if build_configuration is None else build_configuration.target_kind,
        )
        config_status = (
            ConsistencyCheckStatus.PASS
            if (
                _all_equal(normalized_build_systems)
                and _all_equal(toolchains)
                and _all_equal(build_targets)
                and build.build_result_status == "BUILD_PASS"
                and build.tool_observed
                and build_configuration is not None
                and build.build_configuration_fingerprint
                == _build_configuration_fingerprint(build_configuration)
            )
            else (
                ConsistencyCheckStatus.CONFLICT
                if build.build_result_status == "BUILD_FAIL"
                or not _all_equal(normalized_build_systems)
                or not _all_equal(toolchains)
                or not _all_equal(build_targets)
                or build_configuration is not None
                and build.build_configuration_fingerprint
                != _build_configuration_fingerprint(build_configuration)
                else ConsistencyCheckStatus.UNKNOWN
            )
        )
        checks.append(
            _check(
                "BUILD_CONFIGURATION_BINDING",
                config_status,
                summary="Build configuration, toolchain, target, and real build evidence are bound.",
                expected=f"{target.build_system}|{target.toolchain_family}|BUILD_PASS",
                observed=f"{build.build_system}|{build.toolchain_family}|{build.build_result_status}",
            )
        )

        elf_status = self._elf_target_status(value)
        checks.append(
            _check(
                "ELF_TARGET_BINDING",
                elf_status,
                summary="Observed ELF identity and artifact fingerprint are bound to the build snapshot.",
                expected=f"ARM|{build.elf.content_fingerprint}",
                observed=None
                if elf is None
                else f"{elf.machine}|{elf.artifact_fingerprint}",
            )
        )

        truth_values = (
            target.design_truth_fingerprint,
            firmware.design_truth_fingerprint,
            None if firmware_spec is None else firmware_spec.design_truth_fingerprint,
            None
            if implementation_spec is None
            else implementation_spec.design_truth_fingerprint,
            build.design_truth_fingerprint,
        )
        truth_status = (
            ConsistencyCheckStatus.UNKNOWN
            if any(item is None for item in truth_values)
            else (
                ConsistencyCheckStatus.PASS
                if _all_equal(truth_values)
                else ConsistencyCheckStatus.CONFLICT
            )
        )
        checks.append(
            _check(
                "DESIGN_TRUTH_FINGERPRINT_BINDING",
                truth_status,
                summary="DesignTruth fingerprint is retained across firmware and build lineage.",
                expected=target.design_truth_fingerprint,
                observed="|".join(
                    str(item) for item in truth_values if item is not None
                ),
            )
        )

        checks.append(
            self._artifact_binding_check(
                "SCHEMATIC_BINDING",
                target.schematic_fingerprint,
                value.hardware.schematic,
                "Schematic fingerprint and MCU component identity are current.",
                observed_component=schematic_values.get("U1"),
                expected_component=target.mcu_exact_part,
            )
        )
        checks.append(
            self._artifact_binding_check(
                "PCB_BINDING",
                target.pcb_fingerprint,
                value.hardware.pcb,
                "PCB fingerprint and required board bindings are current.",
                observed_component=None,
                expected_component=None,
            )
        )
        bom_status = self._bom_status(value, bom_items)
        checks.append(
            _check(
                "BOM_BINDING",
                bom_status,
                summary="BOM fingerprint and populated component references bind to the design artifacts.",
                expected=target.bom_fingerprint,
                observed=value.hardware.bom.content_fingerprint,
            )
        )
        return tuple(checks)

    @staticmethod
    def _aggregate_pin_check(
        check_id: str, results: list[str], summary: str
    ) -> ConsistencyCheck:
        if not results or "UNKNOWN" in results:
            status = ConsistencyCheckStatus.UNKNOWN
        elif "CONFLICT" in results:
            status = ConsistencyCheckStatus.CONFLICT
        else:
            status = ConsistencyCheckStatus.PASS
        return _check(
            check_id, status, summary=summary, observed="|".join(results) or None
        )

    @staticmethod
    def _memory_check(value: ConsistencyInput) -> ConsistencyCheck:
        elf = value.elf_inspection
        regions = {item.name: item for item in value.build.linker_regions}
        if elf is None or "FLASH" not in regions or "RAM" not in regions:
            return _check(
                "MEMORY_LAYOUT",
                ConsistencyCheckStatus.UNKNOWN,
                summary="Linker memory regions and ELF load spans are required.",
            )
        flash = regions["FLASH"]
        ram = regions["RAM"]
        flash_ok = flash.origin <= elf.flash_load_end <= flash.origin + flash.length
        ram_ok = ram.origin <= elf.ram_load_end <= ram.origin + ram.length
        linker = next(
            (
                item
                for item in value.build.source_artifacts
                if item.artifact_type == "LINKER"
            ),
            None,
        )
        linker_ok = (
            linker is not None
            and value.build.linker_script_fingerprint is not None
            and linker.content_fingerprint == value.build.linker_script_fingerprint
        )
        section_ok = True
        for section in elf.sections:
            if section.name in {".isr_vector", ".text", ".init_array", ".fini_array"}:
                section_ok = section_ok and (
                    flash.origin <= section.address <= flash.origin + flash.length
                    and section.address + section.size <= flash.origin + flash.length
                )
            elif section.name in {".data", ".bss", "._user_heap_stack"}:
                section_ok = section_ok and (
                    ram.origin <= section.address <= ram.origin + ram.length
                    and section.address + section.size <= ram.origin + ram.length
                )
        usage_ok = (
            elf.text_bytes + elf.data_bytes <= flash.length
            and elf.data_bytes + elf.bss_bytes <= ram.length
        )
        return _check(
            "MEMORY_LAYOUT",
            ConsistencyCheckStatus.PASS
            if flash_ok and ram_ok and linker_ok and section_ok and usage_ok
            else ConsistencyCheckStatus.CONFLICT,
            summary="Linker regions, section placement, size usage, and linker fingerprint contain the observed ELF.",
            expected=f"FLASH 0x{flash.origin:x}-0x{flash.origin + flash.length:x}; RAM 0x{ram.origin:x}-0x{ram.origin + ram.length:x}; linker_bound={linker_ok}",
            observed=f"FLASH end 0x{elf.flash_load_end:x}; RAM end 0x{elf.ram_load_end:x}; usage={elf.text_bytes + elf.data_bytes}/{elf.data_bytes + elf.bss_bytes}",
        )

    @staticmethod
    def _boot_check(
        value: ConsistencyInput, bom_items: dict[str, object]
    ) -> ConsistencyCheck:
        pin = next((item for item in value.truth.pins if item.signal == "BOOT0"), None)
        item = bom_items.get("R2")
        board = _board_pin_matches(value, pin) if pin is not None else False
        description = (
            getattr(item, "description", "").casefold() if item is not None else ""
        )
        if pin is None or item is None:
            return _check(
                "BOOT_CONFIG",
                ConsistencyCheckStatus.UNKNOWN,
                summary="BOOT0 truth, board binding, and pulldown evidence are required.",
            )
        status = (
            ConsistencyCheckStatus.PASS
            if board and pin.pull is PullStrategy.DOWN and "pulldown" in description
            else ConsistencyCheckStatus.CONFLICT
        )
        return _check(
            "BOOT_CONFIG",
            status,
            summary="BOOT0 pin and pulldown strategy agree with the board.",
            expected="pin 44 + R2 pulldown",
            observed=f"pin {pin.mcu_pin} + {description}",
        )

    @staticmethod
    def _reset_check(
        value: ConsistencyInput, bom_items: dict[str, object]
    ) -> ConsistencyCheck:
        pin = next((item for item in value.truth.pins if item.signal == "NRST"), None)
        item = bom_items.get("R3")
        board = _board_pin_matches(value, pin) if pin is not None else False
        description = (
            getattr(item, "description", "").casefold() if item is not None else ""
        )
        vector_status = HardwareFirmwareConsistencyService._vector_table_status(value)
        if pin is None or item is None or value.elf_inspection is None:
            return _check(
                "RESET_RUNTIME",
                ConsistencyCheckStatus.UNKNOWN,
                summary="NRST truth, board binding, and pullup evidence are required.",
            )
        status = (
            ConsistencyCheckStatus.PASS
            if board
            and pin.active_level is LogicLevel.LOW
            and "pullup" in description
            and vector_status is ConsistencyCheckStatus.PASS
            else ConsistencyCheckStatus.CONFLICT
        )
        return _check(
            "RESET_RUNTIME",
            status,
            summary="NRST runtime polarity and pullup strategy agree with the board.",
            expected="pin 7 + R3 pullup + active low + .isr_vector + Reset_Handler",
            observed=f"pin {pin.mcu_pin} + {description} + {pin.active_level.value} + vector={vector_status.value}",
        )

    @staticmethod
    def _vector_table_status(value: ConsistencyInput) -> ConsistencyCheckStatus:
        elf = value.elf_inspection
        regions = {item.name: item for item in value.build.linker_regions}
        if elf is None or "FLASH" not in regions or "RAM" not in regions:
            return ConsistencyCheckStatus.UNKNOWN
        flash = regions["FLASH"]
        ram = regions["RAM"]
        if not elf.vector_table_present or not elf.reset_handler_present:
            return ConsistencyCheckStatus.CONFLICT
        if elf.vector_table_address != flash.origin:
            return ConsistencyCheckStatus.CONFLICT
        if elf.vector_table_initial_stack is None or not (
            ram.origin <= elf.vector_table_initial_stack <= ram.origin + ram.length
        ):
            return ConsistencyCheckStatus.CONFLICT
        if elf.reset_handler_address is None or not (
            flash.origin <= (elf.reset_handler_address & ~1) < flash.origin + flash.length
        ):
            return ConsistencyCheckStatus.CONFLICT
        if (elf.entry_point & ~1) != (elf.reset_handler_address & ~1):
            return ConsistencyCheckStatus.CONFLICT
        return ConsistencyCheckStatus.PASS

    @staticmethod
    def _swd_mapping_status(value: ConsistencyInput) -> ConsistencyCheckStatus:
        required = {"PA13-SWDIO": "34", "PA14-SWCLK": "37"}
        pins = {item.signal: item for item in value.truth.pins}
        if any(signal not in pins for signal in required):
            return ConsistencyCheckStatus.UNKNOWN
        matches = [
            pins[signal].mcu_pin == pin and _board_pin_matches(value, pins[signal])
            for signal, pin in required.items()
        ]
        if all(matches):
            return ConsistencyCheckStatus.PASS
        return ConsistencyCheckStatus.CONFLICT

    @staticmethod
    def _artifact_integrity(value: ConsistencyInput) -> ConsistencyCheckStatus:
        source = tuple(value.build.source_artifacts)
        if not source or value.elf_inspection is None:
            return ConsistencyCheckStatus.UNKNOWN
        if value.elf_inspection.artifact_fingerprint != value.build.elf.content_fingerprint:
            return ConsistencyCheckStatus.CONFLICT
        if (
            value.elf_inspection.binary_fingerprint is not None
            and value.elf_inspection.binary_fingerprint != value.build.binary.content_fingerprint
        ):
            return ConsistencyCheckStatus.CONFLICT
        if set(value.firmware.source_fingerprints) != {
            item.content_fingerprint for item in source
        }:
            return ConsistencyCheckStatus.CONFLICT
        if value.truth.schematic_fingerprint != value.hardware.schematic.content_fingerprint:
            return ConsistencyCheckStatus.CONFLICT
        if value.truth.pcb_fingerprint != value.hardware.pcb.content_fingerprint:
            return ConsistencyCheckStatus.CONFLICT
        if value.truth.bom_fingerprint != value.hardware.bom.content_fingerprint:
            return ConsistencyCheckStatus.CONFLICT
        if any(not item.content_fingerprint.startswith("sha256:") for item in source):
            return ConsistencyCheckStatus.UNKNOWN
        return ConsistencyCheckStatus.PASS

    @staticmethod
    def _stale_inputs(
        checks: tuple[ConsistencyCheck, ...], artifact_integrity: ConsistencyCheckStatus
    ) -> tuple[str, ...]:
        stale: list[str] = []
        for item in checks:
            if item.status is ConsistencyCheckStatus.CONFLICT:
                stale.append(item.check_id)
        if artifact_integrity is not ConsistencyCheckStatus.PASS:
            stale.append("ARTIFACT_INTEGRITY")
        return tuple(sorted(set(stale)))


    @staticmethod
    def _artifact_binding_check(
        check_id: str,
        expected_fingerprint: str | None,
        artifact: ArtifactLineage,
        summary: str,
        *,
        observed_component: str | None,
        expected_component: str | None,
    ) -> ConsistencyCheck:
        if expected_fingerprint is None:
            status = ConsistencyCheckStatus.UNKNOWN
        elif (
            expected_fingerprint != artifact.content_fingerprint
            or expected_component is not None
            and observed_component != expected_component
        ):
            status = ConsistencyCheckStatus.CONFLICT
        else:
            status = ConsistencyCheckStatus.PASS
        return _check(
            check_id,
            status,
            summary=summary,
            expected=expected_fingerprint,
            observed=artifact.content_fingerprint,
        )

    @staticmethod
    def _bom_status(
        value: ConsistencyInput, bom_items: dict[str, object]
    ) -> ConsistencyCheckStatus:
        expected = {item.reference for item in value.hardware.bom_items}
        schematic = {reference for reference, _ in value.hardware.schematic_components}
        if not expected or not schematic:
            return ConsistencyCheckStatus.UNKNOWN
        if expected != schematic:
            return ConsistencyCheckStatus.CONFLICT
        mcu = bom_items.get("U1")
        if mcu is None:
            return ConsistencyCheckStatus.UNKNOWN
        if getattr(mcu, "value", None) != value.truth.mcu_exact_part:
            return ConsistencyCheckStatus.CONFLICT
        return (
            ConsistencyCheckStatus.PASS
            if value.truth.bom_fingerprint == value.hardware.bom.content_fingerprint
            else ConsistencyCheckStatus.CONFLICT
        )

    @staticmethod
    def _elf_target_status(value: ConsistencyInput) -> ConsistencyCheckStatus:
        elf = value.elf_inspection
        if elf is None:
            return ConsistencyCheckStatus.UNKNOWN
        if (
            elf.machine != "ARM"
            or elf.artifact_fingerprint != value.build.elf.content_fingerprint
        ):
            return ConsistencyCheckStatus.CONFLICT
        if (
            elf.binary_fingerprint is not None
            and elf.binary_fingerprint != value.build.binary.content_fingerprint
        ):
            return ConsistencyCheckStatus.CONFLICT
        return ConsistencyCheckStatus.PASS


__all__ = ("HardwareFirmwareConsistencyService",)
