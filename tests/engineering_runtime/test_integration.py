from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_runtime import (
    CircuitIntentObject,
    EngineeringObjectRuntime,
    EvidenceObject,
    EvidenceQualificationStatus,
    HardwareArchitectureObject,
    LifecycleActor,
    LifecycleStatus,
    Provenance,
    RelationType,
    RequirementObject,
    SchematicDesignObject,
)


def prov(ref: str) -> Provenance:
    return Provenance.create(
        source="USER_INPUT" if ref.startswith("user") else "MVP_FIXTURE",
        source_ref=ref,
        created_by="mvp-test",
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


def active(runtime: EngineeringObjectRuntime, obj):
    runtime.register_object(obj)
    for target, actor in (
        (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
        (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
        (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
    ):
        obj = runtime.transition_object(obj.object_id, obj.revision, target, actor)
    return obj


def test_stm32_control_board_engineering_object_runtime_mvp() -> None:
    runtime = EngineeringObjectRuntime(project_id="stm32-control-board")
    requirement = active(
        runtime,
        RequirementObject.create(
            object_id="req-stm32-board",
            project_id="stm32-control-board",
            description="Design an STM32 control board.",
            source="user",
            acceptance_criteria=("3.3V supply", "CAN communication", "SWD debug"),
            verification_method="review, scoped ERC, and later bench verification",
            provenance=prov("user:stm32-board"),
        ),
    )
    hardware = active(
        runtime,
        HardwareArchitectureObject.create(
            object_id="hw-stm32-board",
            project_id="stm32-control-board",
            mcu="STM32F103",
            peripherals=("CAN controller",),
            interfaces=("CAN", "SWD"),
            power_domains=("3V3",),
            constraints=("exact component identity remains separately qualified",),
            provenance=prov("architecture:stm32-board"),
        ),
    )
    circuit = active(
        runtime,
        CircuitIntentObject.create(
            object_id="circuit-stm32-board",
            project_id="stm32-control-board",
            function="3V3 power and CAN interface intent",
            topology=(
                "5V input -> protection -> 3V3 regulator",
                "MCU -> CAN transceiver -> connector",
            ),
            parameters=("3V3 power domain", "CAN TX/RX and CANH/CANL"),
            calculation=(
                "regulator power and thermal margin require qualified evidence",
            ),
            assumptions=("regulator and transceiver MPN are not selected in this MVP",),
            risks=("power stability, protection, and CAN common-mode behavior",),
            verification_requirements=(
                "datasheet review",
                "scoped ERC",
                "later bench verification",
            ),
            provenance=prov("proposal:circuit-stm32-board"),
        ),
    )
    schematic = active(
        runtime,
        SchematicDesignObject.create(
            object_id="schematic-stm32-board",
            project_id="stm32-control-board",
            symbols=("U1:STM32F103", "U2:CAN_TRANSCEIVER", "J1:SWD"),
            pins=("U1:CAN_TX", "U1:CAN_RX", "J1:SWDIO", "J1:SWCLK"),
            nets=("3V3", "CANH", "CANL", "SWDIO", "SWCLK"),
            provenance=prov("proposal:schematic-stm32-board"),
        ),
    )
    evidence = active(
        runtime,
        EvidenceObject.create(
            object_id="evidence-erc-stm32-board",
            project_id="stm32-control-board",
            source="KiCad",
            tool="ERC",
            result="PASS",
            timestamp=datetime(2026, 9, 21, tzinfo=UTC),
            scope="Electrical rule check only",
            canonical_evidence_ref="engineering-evidence:erc-stm32-board",
            qualification_status=EvidenceQualificationStatus.QUALIFIED,
            subject_object_id=schematic.object_id,
            subject_revision=schematic.revision,
            subject_fingerprint=schematic.fingerprint,
            provenance=prov("tool:kicad-erc"),
        ),
    )

    runtime.add_relation(requirement, RelationType.SATISFIED_BY, hardware)
    runtime.add_relation(hardware, RelationType.IMPLEMENTED_BY, circuit)
    runtime.add_relation(circuit, RelationType.EXPRESSED_BY, schematic)
    runtime.add_relation(schematic, RelationType.VALIDATED_BY, evidence)

    snapshot = runtime.resolve_context_for_circuit(
        requirement_id=requirement.object_id,
        hardware_id=hardware.object_id,
        evidence_ids=(evidence.object_id,),
    )

    assert [item.object_id for item in snapshot.objects] == [
        requirement.object_id,
        hardware.object_id,
    ]
    assert snapshot.evidence_bundle[0].result == "PASS"
    assert snapshot.evidence_bundle[0].scope == "Electrical rule check only"
    assert (
        runtime.relations.list_relations(source_id=schematic.object_id)[0].relation_type
        is RelationType.VALIDATED_BY
    )
