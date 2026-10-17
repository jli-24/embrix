from __future__ import annotations

import pytest

from embedded_copilot.engineering_copilot import EngineeringRequirement
from embedded_copilot.hardware_intelligence import (
    DatasheetEvidence,
    GraphProjection,
    HardwareAnalysisService,
)
from tests.engineering_copilot.test_planner import _snapshot


def _requirement() -> EngineeringRequirement:
    return EngineeringRequirement.create(
        requirement_id="r1",
        project_id="project-1",
        description="hardware",
        evidence_refs=("ds-1",),
    )


def _datasheet(project_id="project-1"):
    return DatasheetEvidence.create(
        evidence_id="ds-1",
        project_id=project_id,
        subject="STM32F4",
        summary="MCU family",
        source_reference="datasheet:1",
        confidence=0.9,
    )


def test_hardware_proposal_is_evidence_bound_and_deterministic() -> None:
    graph = GraphProjection.create(
        project_id="project-1", relations=("SPI",), evidence_refs=("graph-1",)
    )
    proposal = HardwareAnalysisService().analyze(
        _requirement(), _snapshot(), (_datasheet(),), graph
    )
    assert proposal.mcu_recommendation == "STM32F4"
    assert proposal.evidence_refs == ("ds-1", "graph-1")
    assert proposal == HardwareAnalysisService().analyze(
        _requirement(), _snapshot(), (_datasheet(),), graph
    )


def test_hardware_project_isolation_and_no_mutation() -> None:
    graph = GraphProjection.create(
        project_id="project-1", relations=(), evidence_refs=("graph-1",)
    )
    evidence = _datasheet("project-2")
    with pytest.raises(ValueError, match="evidence project rejected"):
        HardwareAnalysisService().analyze(
            _requirement(), _snapshot(), (evidence,), graph
        )
    assert evidence.project_id == "project-2"
