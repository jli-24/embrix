from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from embedded_copilot.engineering_pcb import (
    PCBApplicability,
    PCBArtifactReference,
    PCBArtifactType,
    PCBConstraintCompiler,
    PCBConstraintType,
    PCBEngineeringProjector,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBSignalClass,
    PCBSourceFormat,
)
from embedded_copilot.pcb.models import (
    PCBComponent as ParsedComponent,
)
from embedded_copilot.pcb.models import (
    PCBLayer,
    PCBNetNode,
    PCBPosition,
    PCBTrack,
    UnifiedPCBModel,
)
from embedded_copilot.pcb.models import (
    PCBNet as ParsedNet,
)


def _artifact() -> PCBArtifactReference:
    return PCBArtifactReference.create(
        project_id="project-g7a",
        artifact_id="pcb-artifact-g7a",
        artifact_type=PCBArtifactType.BOARD,
        source_format=PCBSourceFormat.KICAD_PCB,
        source_reference="artifact://pcb/pcb-artifact-g7a",
        filename="main.kicad_pcb",
        byte_size=128,
        content_fingerprint="sha256:" + "1" * 64,
        captured_at=datetime(2026, 9, 18, tzinfo=UTC),
    )


def _parsed_board() -> UnifiedPCBModel:
    return UnifiedPCBModel(
        board_name="main",
        source_format="kicad_pcb",
        layers=(
            PCBLayer(name="F.Cu", index=0, type="signal"),
            PCBLayer(name="B.Cu", index=31, type="signal"),
        ),
        components=(
            ParsedComponent(
                reference="U1",
                value="MCU",
                footprint="Package_QFP:LQFP-48",
                library="Package_QFP",
                position=PCBPosition(x_mm=10.0, y_mm=20.0),
                layer="F.Cu",
                pins=(),
            ),
        ),
        nets=(
            ParsedNet(
                name="CTRL",
                net_type="signal",
                nodes=(PCBNetNode(reference="U1", pin="1"),),
            ),
        ),
        tracks=(
            PCBTrack(
                start=PCBPosition(x_mm=10.0, y_mm=20.0),
                end=PCBPosition(x_mm=11.0, y_mm=20.0),
                width_mm=0.2,
                layer="F.Cu",
                net_name="CTRL",
            ),
        ),
    )


def test_g7a_projects_structured_state_with_stable_non_display_identities() -> None:
    evidence = PCBFoundationEvidence.create(
        evidence_id="evidence-pcb-g7a",
        project_id="project-g7a",
        source_type=PCBFoundationEvidenceSource.ARTIFACT,
        source_id="pcb-artifact-g7a",
        source_fingerprint="sha256:" + "1" * 64,
        claim="Observed from the current G5 PCB artifact.",
        locator="artifact://pcb/pcb-artifact-g7a",
    )
    state = PCBEngineeringProjector().project(
        project_id="project-g7a",
        revision="sha256:" + "1" * 64,
        source_artifact=_artifact(),
        parsed=_parsed_board(),
        evidence_refs=(evidence,),
    )

    assert state.state_fingerprint == state.fingerprint
    assert state.components[0].component_id != state.components[0].reference
    assert state.footprints[0].footprint_id != state.footprints[0].library_identifier
    assert state.nets[0].net_id != state.nets[0].name
    assert state.placement[0].component_id == state.components[0].component_id
    assert state.routing[0].net_id == state.nets[0].net_id
    assert state.signal_classes[0].signal_class is PCBSignalClass.UNKNOWN
    assert state.unknowns
    assert state.pcb_artifact_fingerprint == _artifact().content_fingerprint


def test_g7a_rejects_schematic_artifact_as_pcb_source() -> None:
    schematic = PCBArtifactReference.create(
        project_id="project-g7a",
        artifact_id="schematic-artifact-g7a",
        artifact_type=PCBArtifactType.SCHEMATIC,
        source_format=PCBSourceFormat.KICAD_SCH,
        source_reference="artifact://pcb/schematic-artifact-g7a",
        filename="main.kicad_sch",
        byte_size=128,
        content_fingerprint="sha256:" + "2" * 64,
        captured_at=datetime(2026, 9, 18, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="BOARD artifact"):
        PCBEngineeringProjector().project(
            project_id="project-g7a",
            revision="sha256:" + "2" * 64,
            source_artifact=schematic,
            parsed=_parsed_board(),
        )


def test_g7a_compiler_is_evidence_bound_and_builds_applicability_plan() -> None:
    evidence = PCBFoundationEvidence.create(
        evidence_id="evidence-pcb-g7a-2",
        project_id="project-g7a",
        source_type=PCBFoundationEvidenceSource.ARTIFACT,
        source_id="pcb-artifact-g7a",
        source_fingerprint="sha256:" + "1" * 64,
        claim="Observed from the current G5 PCB artifact.",
        locator="artifact://pcb/pcb-artifact-g7a",
    )
    state = PCBEngineeringProjector().project(
        project_id="project-g7a",
        revision="sha256:" + "1" * 64,
        source_artifact=_artifact(),
        parsed=_parsed_board(),
        evidence_refs=(evidence,),
    )
    constraint_set = PCBConstraintCompiler().compile(state)
    plan = PCBConstraintCompiler().build_plan(state, constraint_set)

    assert constraint_set.constraint_set_fingerprint == constraint_set.fingerprint
    assert constraint_set.state_fingerprint == state.fingerprint
    assert plan.pcb_state_fingerprint == state.fingerprint
    assert plan.constraint_set_fingerprint == constraint_set.fingerprint
    assert "DifferentialPairVerifier" in plan.not_applicable_verifiers
    assert "ReturnPathVerifier" in plan.unknown_applicability
    general = next(
        item for item in constraint_set.constraints
        if item.constraint_type is PCBConstraintType.GENERAL_ROUTING
    )
    assert general.applicability is PCBApplicability.REQUIRED
    assert general.evidence_refs
    assert "RoutingVerifier" in plan.not_applicable_verifiers


def test_g7a_preserves_design_truth_and_records_connectivity_conflict() -> None:
    artifact_evidence = PCBFoundationEvidence.create(
        evidence_id="evidence-pcb-g7a-conflict-artifact",
        project_id="project-g7a",
        source_type=PCBFoundationEvidenceSource.ARTIFACT,
        source_id="pcb-artifact-g7a",
        source_fingerprint="sha256:" + "1" * 64,
        claim="Observed from the current G5 PCB artifact.",
        locator="artifact://pcb/pcb-artifact-g7a",
    )
    design_evidence = PCBFoundationEvidence.create(
        evidence_id="evidence-pcb-g7a-conflict-design",
        project_id="project-g7a",
        source_type=PCBFoundationEvidenceSource.DESIGN_TRUTH,
        source_id="truth-g7a",
        source_fingerprint="sha256:" + "2" * 64,
        claim="Bound to the approved DesignTruth connectivity.",
        locator="pcb://truth-g7a",
    )
    truth = SimpleNamespace(
        fingerprint="sha256:" + "2" * 64,
        components=(),
        pins=(),
        signals=(),
        interfaces=(),
        power_domains=(),
        nets=(SimpleNamespace(net_name="CTRL", binding_id="binding-ctrl", nodes=("U1.2",), signal="signal-ctrl"),),
    )

    state = PCBEngineeringProjector().project(
        project_id="project-g7a",
        revision="sha256:" + "1" * 64,
        source_artifact=_artifact(),
        parsed=_parsed_board(),
        evidence_refs=(artifact_evidence, design_evidence),
        design_truth=truth,
    )

    assert state.design_truth_fingerprint == truth.fingerprint
    assert len(state.conflicts) == 1
    assert state.conflicts[0].category == "design_truth_connectivity"
    assert state.conflicts[0].subject == "CTRL"
    assert state.conflicts[0].evidence_refs == (artifact_evidence, design_evidence)
