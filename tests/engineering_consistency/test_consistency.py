from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from embedded_copilot.engineering_consistency import (
    ConsistencyCheckStatus,
    ConsistencyOverallStatus,
    HardwareFirmwareConsistencyService,
    LogicLevel,
    SQLiteConsistencyStore,
    ToolCommandEvidence,
    VerificationLevel,
)
from embedded_copilot.engineering_consistency.adapters import (
    CanonicalProjectConsistencyAdapter,
)
from embedded_copilot.engineering_copilot.common import ZERO

PROJECT_ROOT = Path(
    os.environ.get(
        "EMBRIX_CANONICAL_PROJECT_ROOT",
        r"D:\EmbeddedProjects\Minimal MCU Control Core",
    )
)
TOOLCHAIN_BIN = Path(
    os.environ.get(
        "EMBRIX_CANONICAL_TOOLCHAIN_BIN",
        r"D:\EmbeddedToolchains\ArmGNU\15.3.rel1\bin",
    )
)


@pytest.fixture(scope="module")
def canonical_input():
    if not PROJECT_ROOT.is_dir() or not TOOLCHAIN_BIN.is_dir():
        pytest.skip("canonical project or trusted ARM toolchain is unavailable")
    return CanonicalProjectConsistencyAdapter(
        PROJECT_ROOT,
        project_id="minimal-mcu-control-core",
        toolchain_bin=TOOLCHAIN_BIN,
    ).collect()


@pytest.fixture(scope="module")
def canonical_result(canonical_input):
    return HardwareFirmwareConsistencyService().evaluate(canonical_input)


def _rebuild(model, **updates):
    payload = model.model_dump(mode="python")
    payload.update(updates)
    payload["fingerprint"] = ZERO
    return type(model).model_validate(payload)


def _result_with_input(canonical_input, **updates):
    return HardwareFirmwareConsistencyService().evaluate(
        _rebuild(canonical_input, **updates)
    )


@pytest.mark.real_toolchain
def test_real_golden_has_all_required_passes_and_l3(canonical_input, canonical_result):
    assert canonical_result.overall_status is ConsistencyOverallStatus.CONSISTENT
    assert (
        canonical_result.verification_level is VerificationLevel.L3_ENGINEERING_VERIFIED
    )
    assert canonical_result.evidence.deterministic is True
    assert canonical_result.evidence.tool_observed is True
    assert len(canonical_result.checks) == 15
    assert {item.status for item in canonical_result.checks} == {
        ConsistencyCheckStatus.PASS
    }
    assert {item.check_id for item in canonical_result.checks} == {
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
    }
    assert len(canonical_result.evidence.lineage) >= 12
    assert canonical_result.design_truth_id == canonical_input.truth.truth_id
    assert canonical_result.firmware_specification_id == canonical_input.firmware.firmware_specification_id
    assert canonical_result.implementation_specification_id == canonical_input.firmware.implementation_id
    assert canonical_result.artifact_integrity is ConsistencyCheckStatus.PASS
    assert canonical_result.evidence.vector_table_status is ConsistencyCheckStatus.PASS
    assert canonical_result.evidence.swd_mapping_status is ConsistencyCheckStatus.PASS
    assert canonical_result.l4_status == "NOT_ACHIEVED"
    assert canonical_result.l5_status == "NOT_ACHIEVED"
    assert canonical_result.fabrication_ready is False
    assert {
        command.tool for command in canonical_result.evidence.elf_inspection.commands
    } == {
        "readelf",
        "objdump",
        "size",
    }


def test_mcu_conflict_is_not_reported_as_unknown(canonical_input):
    firmware = _rebuild(canonical_input.firmware, mcu_exact_part="STM32F103CBT6")
    result = _result_with_input(canonical_input, firmware=firmware)
    checks = {item.check_id: item for item in result.checks}
    assert result.overall_status is ConsistencyOverallStatus.CONFLICT
    assert checks["MCU_IDENTITY"].status is ConsistencyCheckStatus.CONFLICT


def test_wrong_elf_target_and_memory_are_conflicts(canonical_input):
    elf = _rebuild(canonical_input.elf_inspection, machine="MIPS")
    result = _result_with_input(canonical_input, elf_inspection=elf)
    checks = {item.check_id: item for item in result.checks}
    assert checks["ELF_TARGET_BINDING"].status is ConsistencyCheckStatus.CONFLICT

    overflow = _rebuild(
        canonical_input.elf_inspection,
        flash_load_end=0x08010001,
    )
    result = _result_with_input(canonical_input, elf_inspection=overflow)
    checks = {item.check_id: item for item in result.checks}
    assert checks["MEMORY_LAYOUT"].status is ConsistencyCheckStatus.CONFLICT

    elf_lineage = canonical_input.build.elf.model_copy(
        update={"content_fingerprint": "sha256:" + "3" * 64, "fingerprint": ZERO}
    )
    build = canonical_input.build.model_copy(
        update={"elf": elf_lineage, "fingerprint": ZERO}
    )
    result = _result_with_input(canonical_input, build=build)
    assert {item.check_id: item.status for item in result.checks}[
        "ELF_TARGET_BINDING"
    ] is ConsistencyCheckStatus.CONFLICT


def test_observed_soft_float_accepts_legacy_configuration_without_explicit_abi_flag(
    canonical_input,
):
    configuration = canonical_input.build_configuration.model_copy(
        update={"compiler_flags": ("-mcpu=cortex-m3", "-mthumb")}
    )
    build = _rebuild(canonical_input.build, build_configuration=configuration)
    result = _result_with_input(
        canonical_input,
        build=build,
        build_configuration=configuration,
    )

    assert {item.check_id: item.status for item in result.checks}[
        "ARCHITECTURE"
    ] is ConsistencyCheckStatus.PASS


def test_stale_source_and_design_artifacts_are_conflicts(canonical_input):
    stale_source = _rebuild(
        canonical_input.firmware,
        source_fingerprints=("sha256:" + "1" * 64,),
    )
    result = _result_with_input(canonical_input, firmware=stale_source)
    assert {item.check_id: item.status for item in result.checks}[
        "FIRMWARE_SOURCE_BINDING"
    ] is ConsistencyCheckStatus.CONFLICT

    stale_truth = _rebuild(
        canonical_input.truth,
        schematic_fingerprint="sha256:" + "2" * 64,
    )
    result = _result_with_input(canonical_input, truth=stale_truth)
    assert {item.check_id: item.status for item in result.checks}[
        "SCHEMATIC_BINDING"
    ] is ConsistencyCheckStatus.CONFLICT

    bom_items = list(canonical_input.hardware.bom_items)
    mcu_index = next(
        index for index, item in enumerate(bom_items) if item.reference == "U1"
    )
    bom_items[mcu_index] = bom_items[mcu_index].model_copy(
        update={"value": "STM32F103CBT6", "fingerprint": ZERO}
    )
    hardware = canonical_input.hardware.model_copy(
        update={"bom_items": tuple(bom_items), "fingerprint": ZERO}
    )
    result = _result_with_input(canonical_input, hardware=hardware)
    assert {item.check_id: item.status for item in result.checks}["BOM_BINDING"] is ConsistencyCheckStatus.CONFLICT


def test_selective_stale_inputs_cover_g6_authority_without_unrelated_pcb_noise(
    canonical_input,
):
    firmware_spec = _rebuild(
        canonical_input.firmware_specification,
        title="changed firmware authority",
    )
    result = _result_with_input(
        canonical_input,
        firmware_specification=firmware_spec,
    )
    assert "FIRMWARE_SOURCE_BINDING" in result.stale_inputs

    stale_source = _rebuild(
        canonical_input.firmware,
        source_fingerprints=("sha256:" + "1" * 64,),
    )
    result = _result_with_input(canonical_input, firmware=stale_source)
    assert "FIRMWARE_SOURCE_BINDING" in result.stale_inputs

    configuration = canonical_input.build_configuration.model_copy(
        update={"profile": "Debug"}
    )
    result = _result_with_input(
        canonical_input,
        build_configuration=configuration,
    )
    assert "BUILD_CONFIGURATION_BINDING" in result.stale_inputs

    truth = _rebuild(canonical_input.truth, architecture="ARM Cortex-M4")
    result = _result_with_input(canonical_input, truth=truth)
    assert "ARCHITECTURE" in result.stale_inputs

    unrelated_pcb_projection = canonical_input.hardware.model_copy(
        update={"fingerprint": ZERO}
    )
    result = _result_with_input(
        canonical_input,
        hardware=unrelated_pcb_projection,
    )
    assert result.overall_status is ConsistencyOverallStatus.CONSISTENT
    assert result.stale_inputs == ()


def test_wrong_gpio_and_polarity_are_conflicts(canonical_input):
    connections = list(canonical_input.hardware.board_connections)
    index = next(
        index for index, item in enumerate(connections) if item.pin_number == "29"
    )
    connections[index] = connections[index].model_copy(
        update={"net_name": "WRONG_NET", "fingerprint": ZERO}
    )
    hardware = canonical_input.hardware.model_copy(
        update={"board_connections": tuple(connections), "fingerprint": ZERO}
    )
    result = _result_with_input(canonical_input, hardware=hardware)
    assert {item.check_id: item.status for item in result.checks}[
        "GPIO_PIN_MAPPING"
    ] is ConsistencyCheckStatus.CONFLICT

    observations = [
        item.model_copy(update={"active_level": item.active_level, "fingerprint": ZERO})
        for item in canonical_input.firmware.pin_observations
    ]
    led_index = next(
        index for index, item in enumerate(observations) if item.signal == "PA8"
    )
    observations[led_index] = observations[led_index].model_copy(
        update={"active_level": LogicLevel.LOW, "fingerprint": ZERO}
    )
    firmware = canonical_input.firmware.model_copy(
        update={"pin_observations": tuple(observations), "fingerprint": ZERO}
    )
    result = _result_with_input(canonical_input, firmware=firmware)
    assert {item.check_id: item.status for item in result.checks}[
        "GPIO_LOGIC_POLARITY"
    ] is ConsistencyCheckStatus.CONFLICT


def test_explicit_firmware_spec_pin_binding_is_not_inferred_from_source(canonical_input):
    bindings = list(canonical_input.firmware.specification_pin_observations)
    led_index = next(index for index, item in enumerate(bindings) if item.signal == "PA8")
    bindings[led_index] = bindings[led_index].model_copy(
        update={"gpio_number": 7, "fingerprint": ZERO}
    )
    firmware = _rebuild(
        canonical_input.firmware,
        specification_pin_observations=tuple(bindings),
    )
    result = _result_with_input(canonical_input, firmware=firmware)
    assert {item.check_id: item.status for item in result.checks}["GPIO_PIN_MAPPING"] is ConsistencyCheckStatus.CONFLICT


def test_vector_table_and_artifact_integrity_are_fail_closed(canonical_input):
    elf = _rebuild(canonical_input.elf_inspection, vector_table_present=False)
    result = _result_with_input(canonical_input, elf_inspection=elf)
    checks = {item.check_id: item for item in result.checks}
    assert checks["RESET_RUNTIME"].status is ConsistencyCheckStatus.CONFLICT
    assert result.evidence.vector_table_status is ConsistencyCheckStatus.CONFLICT

    pcb = canonical_input.hardware.pcb.model_copy(
        update={"content_fingerprint": "sha256:" + "4" * 64, "fingerprint": ZERO}
    )
    hardware = canonical_input.hardware.model_copy(update={"pcb": pcb, "fingerprint": ZERO})
    result = _result_with_input(canonical_input, hardware=hardware)
    assert result.artifact_integrity is ConsistencyCheckStatus.CONFLICT
    assert result.overall_status is ConsistencyOverallStatus.CONFLICT


def test_missing_required_fact_is_incomplete_and_unverified(canonical_input):
    firmware = canonical_input.firmware.model_copy(
        update={"pin_observations": (), "fingerprint": ZERO}
    )
    result = _result_with_input(canonical_input, firmware=firmware)
    assert result.overall_status is ConsistencyOverallStatus.INCOMPLETE
    assert result.verification_level is VerificationLevel.UNVERIFIED
    assert any(item.status is ConsistencyCheckStatus.UNKNOWN for item in result.checks)


def test_consistency_result_survives_restart(canonical_result, tmp_path):
    database = tmp_path / "consistency.sqlite3"
    SQLiteConsistencyStore(database).save(canonical_result)
    recovered = SQLiteConsistencyStore(database).get_latest(canonical_result.project_id)
    assert recovered is not None
    assert recovered.fingerprint == canonical_result.fingerprint
    assert recovered.evidence.fingerprint == canonical_result.evidence.fingerprint
    assert recovered.checks == canonical_result.checks
    assert SQLiteConsistencyStore(database).get_current(
        canonical_result.project_id, canonical_result.request_fingerprint
    ) == canonical_result
    assert SQLiteConsistencyStore(database).get_current(
        canonical_result.project_id, "sha256:" + "9" * 64
    ) is None


def test_tampered_persisted_result_is_rejected(canonical_result, tmp_path):
    database = tmp_path / "tampered-consistency.sqlite3"
    SQLiteConsistencyStore(database).save(canonical_result)
    with sqlite3.connect(database) as connection:
        body = connection.execute(
            "SELECT body FROM engineering_consistency_results"
        ).fetchone()[0]
        connection.execute(
            "UPDATE engineering_consistency_results SET body=?",
            (
                body.replace(
                    '"overall_status":"CONSISTENT"', '"overall_status":"CONFLICT"', 1
                ),
            ),
        )
    with pytest.raises(ValueError):
        SQLiteConsistencyStore(database).get_latest(canonical_result.project_id)


def test_tool_evidence_rejects_sensitive_output():
    with pytest.raises(ValueError):
        ToolCommandEvidence.create(
            tool="readelf",
            arguments=(),
            exit_code=0,
            stdout="access_token=secret",
        )
    with pytest.raises(ValueError):
        ToolCommandEvidence.create(
            tool="readelf",
            arguments=(),
            exit_code=0,
            stdout=r"C:\Users\private\firmware.elf",
        )
