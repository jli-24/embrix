from __future__ import annotations

import os
from pathlib import Path

import pytest

from embedded_copilot.engineering_consistency import (
    ConsistencyCheckStatus,
    ConsistencyOverallStatus,
    HardwareFirmwareConsistencyService,
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


def _rebuild(model, **updates):
    payload = model.model_dump(mode="python")
    payload.update(updates)
    payload["fingerprint"] = ZERO
    return type(model).model_validate(payload)


def test_canonical_golden_is_real_consistent(canonical_input):
    result = HardwareFirmwareConsistencyService().evaluate(canonical_input)
    assert result.overall_status is ConsistencyOverallStatus.CONSISTENT
    assert result.verification_level is VerificationLevel.L3_ENGINEERING_VERIFIED
    assert all(item.status is ConsistencyCheckStatus.PASS for item in result.checks)
    assert result.artifact_integrity is ConsistencyCheckStatus.PASS
    assert result.evidence.tool_observed is True


def test_canonical_conflict_goldens_are_fail_closed(canonical_input):
    service = HardwareFirmwareConsistencyService()

    wrong_mcu = service.evaluate(
        _rebuild(
            canonical_input,
            firmware=_rebuild(
                canonical_input.firmware,
                mcu_exact_part="STM32F401C8T6",
            ),
        )
    )
    assert wrong_mcu.overall_status is ConsistencyOverallStatus.CONFLICT
    assert next(item for item in wrong_mcu.checks if item.check_id == "MCU_IDENTITY").status is ConsistencyCheckStatus.CONFLICT

    stale_source = service.evaluate(
        _rebuild(
            canonical_input,
            firmware=_rebuild(
                canonical_input.firmware,
                source_fingerprints=("sha256:" + "a" * 64,),
            ),
        )
    )
    assert stale_source.overall_status is ConsistencyOverallStatus.CONFLICT
    assert next(item for item in stale_source.checks if item.check_id == "FIRMWARE_SOURCE_BINDING").status is ConsistencyCheckStatus.CONFLICT

    overflow = service.evaluate(
        _rebuild(
            canonical_input,
            elf_inspection=_rebuild(
                canonical_input.elf_inspection,
                flash_load_end=0x08010001,
            ),
        )
    )
    assert overflow.overall_status is ConsistencyOverallStatus.CONFLICT
    assert next(item for item in overflow.checks if item.check_id == "MEMORY_LAYOUT").status is ConsistencyCheckStatus.CONFLICT
