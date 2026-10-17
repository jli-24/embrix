from __future__ import annotations

from types import SimpleNamespace

from embedded_copilot.engineering_copilot import (
    DesignTruthOrigin,
    DesignTruthStatus,
    ImplementationArtifactType,
    SchematicReadinessStatus,
    evaluate_schematic_readiness,
)


FP = "sha256:" + "a" * 64


def _inputs(*, symbol: str | None = "Device:R", signal_id: str | None = "sig-a", node: str = "R1.1", ground_net: str = "GND"):
    component = SimpleNamespace(component_id="R1", symbol_binding=symbol)
    pin = SimpleNamespace(component_id="R1", pin_number="1", pin_name="1", signal_id=signal_id)
    truth = SimpleNamespace(
        fingerprint=FP,
        project_id="project-1",
        truth_id="truth-1",
        origin=DesignTruthOrigin.CREATE,
        status=DesignTruthStatus.APPROVED,
        components=(component,),
        pins=(pin,),
        signals=(SimpleNamespace(signal_id="sig-a"),),
        nets=(SimpleNamespace(signal="sig-a", net_name="GND", nodes=(node, "R1.1")),),
        power_domains=(SimpleNamespace(power_domain_id="power-1", voltage="3V3", ground_net=ground_net, component_refs=("R1",)),),
    )
    hardware = SimpleNamespace(
        project_id="project-1",
        hardware_spec_id="hardware-1",
        architecture_id="architecture-1",
        schematic_plan=object(),
    )
    task = SimpleNamespace(task_id="task-1", expected_artifacts=("SCHEMATIC",))
    implementation = SimpleNamespace(
        project_id="project-1",
        conversation_id="conversation-1",
        hardware_design_id="hardware-1",
        expected_artifacts=(),
        tasks=(task,),
    )
    return truth, hardware, implementation


def test_readiness_requires_explicit_pin_signal_and_endpoint_truth() -> None:
    truth, hardware, implementation = _inputs()
    ready = evaluate_schematic_readiness(truth, hardware, implementation)
    assert ready.status is SchematicReadinessStatus.READY

    missing_signal = _inputs(signal_id=None)
    assert evaluate_schematic_readiness(*missing_signal).status is SchematicReadinessStatus.BLOCKED_MISSING_NET

    unknown_endpoint = _inputs(node="UNKNOWN.1")
    assert evaluate_schematic_readiness(*unknown_endpoint).status is SchematicReadinessStatus.BLOCKED_MISSING_NET


def test_readiness_rejects_missing_symbol_and_power_fact_without_fabrication() -> None:
    missing_symbol = _inputs(symbol=None)
    assert evaluate_schematic_readiness(*missing_symbol).status is SchematicReadinessStatus.BLOCKED_MISSING_SYMBOL

    missing_power = _inputs(ground_net="UNKNOWN_NET")
    assert evaluate_schematic_readiness(*missing_power).status is SchematicReadinessStatus.BLOCKED_MISSING_POWER_FACT


def test_readiness_does_not_choose_between_multiple_schematic_targets() -> None:
    truth, hardware, implementation = _inputs()
    implementation.expected_artifacts = (
        SimpleNamespace(artifact_id="schematic-a", artifact_type=ImplementationArtifactType.SCHEMATIC),
        SimpleNamespace(artifact_id="schematic-b", artifact_type=ImplementationArtifactType.SCHEMATIC),
    )
    implementation.tasks = (
        SimpleNamespace(task_id="task-1", expected_artifacts=("schematic-a", "schematic-b")),
    )
    result = evaluate_schematic_readiness(truth, hardware, implementation)
    assert result.status is SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
    assert result.required_next_action == "SCHEMATIC_TARGET_SELECTION_REQUIRED"
