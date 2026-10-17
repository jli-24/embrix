from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_runtime import (
    CircuitIntentObject,
    EngineeringObjectRuntime,
    HardwareArchitectureObject,
    LifecycleActor,
    LifecycleStatus,
    Provenance,
    RelationType,
    RequirementObject,
    SchematicDesignObject,
)


def provenance(ref: str) -> Provenance:
    return Provenance.create(
        source="USER_INPUT",
        source_ref=ref,
        created_by="test-user",
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


def activate(runtime: EngineeringObjectRuntime, obj):
    runtime.register_object(obj)
    for target, actor in (
        (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
        (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
        (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
    ):
        obj = runtime.transition_object(obj.object_id, obj.revision, target, actor)
    return obj


def test_relation_manager_builds_traceability_and_propagates_stale() -> None:
    runtime = EngineeringObjectRuntime(project_id="project-001")
    req = activate(
        runtime,
        RequirementObject.create(
            object_id="req-001",
            project_id="project-001",
            description="STM32 control board",
            source="user",
            acceptance_criteria=("3.3V", "CAN", "SWD"),
            verification_method="review",
            provenance=provenance("user:req"),
        ),
    )
    hardware = activate(
        runtime,
        HardwareArchitectureObject.create(
            object_id="hw-001",
            project_id="project-001",
            mcu="STM32F103",
            peripherals=("CAN",),
            interfaces=("CAN", "SWD"),
            power_domains=("3V3",),
            constraints=("MCU identity requires datasheet evidence",),
            provenance=provenance("architecture:hw"),
        ),
    )
    circuit = activate(
        runtime,
        CircuitIntentObject.create(
            object_id="circuit-001",
            project_id="project-001",
            function="Power and CAN interface intent",
            topology=(
                "5V -> protection -> 3V3 regulator",
                "MCU -> CAN transceiver -> connector",
            ),
            parameters=("3V3 rail", "CAN physical interface"),
            calculation=("power margin requires datasheet verification",),
            assumptions=("exact regulator is not selected",),
            risks=("regulator stability and CAN protection",),
            verification_requirements=(
                "datasheet review",
                "scoped ERC",
            ),
            provenance=provenance("design:circuit"),
        ),
    )
    schematic = activate(
        runtime,
        SchematicDesignObject.create(
            object_id="schematic-001",
            project_id="project-001",
            symbols=("U1:STM32F103", "U2:CAN_TRANSCEIVER"),
            pins=("U1:CAN_TX", "U1:CAN_RX", "J1:SWD"),
            nets=("3V3", "CANH", "CANL", "SWDIO", "SWCLK"),
            provenance=provenance("schematic:proposal"),
        ),
    )

    runtime.add_relation(req, RelationType.SATISFIED_BY, hardware)
    runtime.add_relation(hardware, RelationType.IMPLEMENTED_BY, circuit)
    runtime.add_relation(circuit, RelationType.EXPRESSED_BY, schematic)

    dependents = runtime.propagate_stale(req.object_id)

    assert {item.object_id for item in dependents} == {
        "hw-001",
        "circuit-001",
        "schematic-001",
    }
    assert runtime.get_active_revision("hw-001").status is LifecycleStatus.STALE
    assert runtime.get_active_revision("circuit-001").status is LifecycleStatus.STALE
    assert runtime.get_active_revision("schematic-001").status is LifecycleStatus.STALE
