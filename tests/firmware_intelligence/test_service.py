from __future__ import annotations

import pytest

from embedded_copilot.engineering_copilot import EngineeringRequirement
from embedded_copilot.firmware_intelligence import (
    FirmwareContext,
    FirmwarePlanningService,
    InterfaceContract,
)


def test_firmware_projection_records_assumptions_and_never_executes() -> None:
    requirement = EngineeringRequirement.create(
        requirement_id="r1", project_id="project-1", description="firmware"
    )
    context = FirmwareContext.create(
        project_id="project-1",
        platform="STM32F4",
        sdk_hal="STM32 HAL",
        rtos="FreeRTOS",
        clock_assumptions=("84MHz",),
        pin_assumptions=("PA5=SCK",),
        peripheral_assumptions=("SPI1",),
        interrupt_assumptions=("ISR non-blocking",),
    )
    interface = InterfaceContract.create(
        name="SPI", direction="bidirectional", signal="SPI1"
    )
    proposal = FirmwarePlanningService().plan(
        requirement, context, (interface,), ("ds-1",)
    )
    assert proposal.assumptions == (
        "platform:STM32F4",
        "sdk_hal:STM32 HAL",
        "rtos:FreeRTOS",
        "clock:84MHz",
        "pin:PA5=SCK",
        "peripheral:SPI1",
        "interrupt:ISR non-blocking",
    )
    assert proposal.code_skeleton == ("void spi_init(void);",)
    assert "build" not in type(proposal).model_fields
    assert "flash" not in type(proposal).model_fields


def test_firmware_requires_project_and_evidence_binding() -> None:
    requirement = EngineeringRequirement.create(
        requirement_id="r1", project_id="p1", description="firmware"
    )
    context = FirmwareContext.create(
        project_id="p2", platform="STM32", sdk_hal="HAL", rtos="bare-metal"
    )
    with pytest.raises(ValueError, match="project or evidence binding rejected"):
        FirmwarePlanningService().plan(requirement, context, (), ())
