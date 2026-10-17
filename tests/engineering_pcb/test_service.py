from __future__ import annotations

from datetime import timedelta

import pytest

from embedded_copilot.engineering_pcb import (
    DeterministicPCBAnalyzer,
    DeterministicPCBGraphBuilder,
    EngineeringPCBService,
    PCBAnalysisRequest,
    PCBModelConflict,
    PCBRepairHandoffStatus,
    PCBReviewRejected,
)
from embedded_copilot.engineering_pcb.adapters import (
    FakePCBArtifactSourceAdapter,
    FakePCBContextSourceAdapter,
    FakePCBDatasheetSourceAdapter,
    FakePCBOptimizationAdapter,
    FakePCBParserAdapter,
    FakePCBRepairAdapter,
    FakePCBValidationAdapter,
)
from embedded_copilot.engineering_review import (
    HumanDecision,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    ReviewTargetReference,
    ReviewTargetType,
)

from .conftest import NOW, PROJECT


def make_service(artifacts, parsed_artifacts, snapshot, **overrides):
    dependencies = {
        "artifact_source": FakePCBArtifactSourceAdapter(artifacts),
        "parser": FakePCBParserAdapter(
            {item.artifact.artifact_id: item for item in parsed_artifacts.values()}
        ),
        "graph": DeterministicPCBGraphBuilder(),
        "analyzer": DeterministicPCBAnalyzer(),
        "context_source": FakePCBContextSourceAdapter({PROJECT: snapshot}),
        "datasheet_source": FakePCBDatasheetSourceAdapter(),
        "validation": FakePCBValidationAdapter(),
        "optimization": FakePCBOptimizationAdapter(),
        "repair": FakePCBRepairAdapter(),
    }
    dependencies.update(overrides)
    return EngineeringPCBService(**dependencies)


@pytest.fixture
def service(artifacts, parsed_artifacts, snapshot):
    return make_service(artifacts, parsed_artifacts, snapshot)


@pytest.fixture
def context(service, context_request):
    return service.build_context(context_request)


@pytest.fixture
def analysis_request(context):
    return PCBAnalysisRequest.create(
        analysis_id="pcb-analysis",
        project_id=PROJECT,
        context_fingerprint=context.fingerprint,
        rule_scope=("POWER_NETS", "GROUND_NETS", "FLOATING_PINS"),
        requester="engineer",
        requested_at=NOW,
    )


def test_build_context_binds_all_four_artifacts(context):
    assert len(context.artifacts) == 4
    assert context.components[0].manufacturer_part_number == "MCU-1"
    assert {item.net_id for item in context.nets} == {"VCC", "GND"}


def test_build_context_is_deterministic(service, context_request):
    assert (
        service.build_context(context_request).fingerprint
        == service.build_context(context_request).fingerprint
    )


def test_build_context_rejects_artifact_drift(
    artifacts, parsed_artifacts, snapshot, context_request
):
    drifted = list(artifacts)
    material = drifted[0].model_dump(mode="python")
    material.pop("fingerprint")
    material["captured_at"] = NOW + timedelta(seconds=1)
    drifted[0] = type(drifted[0]).create(**material)
    service = make_service(tuple(drifted), parsed_artifacts, snapshot)
    with pytest.raises(PCBModelConflict):
        service.build_context(context_request)


def test_build_context_rejects_cross_artifact_component_mismatch(
    artifacts, parsed_artifacts, snapshot, context_request
):
    board = parsed_artifacts["board"]
    material = board.model_dump(mode="python")
    component = material["components"][0]
    component.pop("fingerprint")
    component["value"] = "OTHER"
    material.pop("fingerprint")
    from embedded_copilot.engineering_pcb import PCBBoardArtifact

    bad_board = PCBBoardArtifact.create(**material)
    parsed = dict(parsed_artifacts)
    parsed["board"] = bad_board
    service = make_service(artifacts, parsed, snapshot)
    with pytest.raises(PCBModelConflict):
        service.build_context(context_request)


def test_build_context_rejects_connectivity_mismatch(
    artifacts, parsed_artifacts, snapshot, context_request
):
    board = parsed_artifacts["board"]
    material = board.model_dump(mode="python")
    material.pop("fingerprint")
    material["connections"] = material["connections"][:-1]
    from embedded_copilot.engineering_pcb import PCBBoardArtifact

    bad_board = PCBBoardArtifact.create(**material)
    parsed = dict(parsed_artifacts)
    parsed["board"] = bad_board
    service = make_service(artifacts, parsed, snapshot)
    with pytest.raises(PCBModelConflict):
        service.build_context(context_request)


def test_graph_has_component_pin_net_and_bom_nodes(context):
    graph = DeterministicPCBGraphBuilder().build(context)
    types = {item.node_type.value for item in graph.nodes}
    assert {"COMPONENT", "PIN", "NET", "BOM_ITEM"}.issubset(types)


def test_graph_is_deterministic(context):
    builder = DeterministicPCBGraphBuilder()
    assert builder.build(context).fingerprint == builder.build(context).fingerprint


def test_analysis_does_not_claim_drc_or_measurement(service, context, analysis_request):
    analysis = service.analyze(analysis_request, context)
    output = analysis.model_dump_json().casefold()
    assert "drc passed" not in output
    assert "measured" not in output
    assert not analysis.findings


def test_analysis_request_must_bind_context(service, context):
    request = PCBAnalysisRequest.create(
        analysis_id="pcb-analysis",
        project_id=PROJECT,
        context_fingerprint="sha256:" + "1" * 64,
        rule_scope=("POWER_NETS",),
        requester="engineer",
        requested_at=NOW,
    )
    with pytest.raises(PCBModelConflict):
        service.analyze(request, context)


def test_analysis_rejects_unknown_rule(service, context):
    request = PCBAnalysisRequest.create(
        analysis_id="pcb-analysis",
        project_id=PROJECT,
        context_fingerprint=context.fingerprint,
        rule_scope=("RUN_DRC",),
        requester="engineer",
        requested_at=NOW,
    )
    with pytest.raises(ValueError):
        service.analyze(request, context)


def test_governance_projection_uses_existing_contracts(service, context):
    request = PCBAnalysisRequest.create(
        analysis_id="pcb-analysis",
        project_id=PROJECT,
        context_fingerprint=context.fingerprint,
        rule_scope=("DATASHEET_COVERAGE",),
        requester="engineer",
        requested_at=NOW,
    )
    analysis = service.analyze(request, context)
    bundle = service.prepare_governance(analysis)
    assert bundle.validation_findings == ()
    assert bundle.optimization_proposals == ()


def _analysis_with_finding(service, context):
    material = context.model_dump(mode="python")
    material.pop("fingerprint")
    material["nets"] = tuple(
        item for item in material["nets"] if item["net_type"].value != "POWER"
    )
    material["connections"] = tuple(
        item for item in material["connections"] if item["net_id"] != "VCC"
    )
    from embedded_copilot.engineering_pcb import PCBContext

    no_power = PCBContext.create(**material)
    request = PCBAnalysisRequest.create(
        analysis_id="pcb-analysis-finding",
        project_id=PROJECT,
        context_fingerprint=no_power.fingerprint,
        rule_scope=("POWER_NETS",),
        requester="engineer",
        requested_at=NOW,
    )
    graph = DeterministicPCBGraphBuilder().build(no_power)
    return DeterministicPCBAnalyzer().analyze(request, no_power, graph)


def test_governance_projects_finding_and_proposal(service, context):
    bundle = service.prepare_governance(_analysis_with_finding(service, context))
    assert len(bundle.validation_findings) == 1
    assert len(bundle.optimization_proposals) == 1
    assert bundle.optimization_proposals[0].target.value in {"HARDWARE", "POWER"}


def make_review(bundle, decision=ReviewDecision.APPROVE):
    proposal = bundle.optimization_proposals[0]
    target = ReviewTargetReference.create(
        target_type=ReviewTargetType.OPTIMIZATION,
        target_id=proposal.proposal_id,
        target_fingerprint=proposal.fingerprint,
    )
    request = ReviewRequest.create(
        review_id="pcb-review",
        project_id=PROJECT,
        target_reference=target,
        requester="requester",
        created_at=NOW,
    )
    human = HumanDecision.create(
        decision_id="pcb-decision",
        review_id="pcb-review",
        project_id=PROJECT,
        decision=decision,
        reviewer="reviewer",
        comment="Reviewed PCB evidence",
        decided_at=NOW + timedelta(minutes=1),
    )
    return ReviewRecord.create(review_request=request, decision=human)


def test_approved_review_records_repair_handoff(service, context):
    bundle = service.prepare_governance(_analysis_with_finding(service, context))
    receipt = service.record_repair_handoff(bundle, make_review(bundle))
    assert receipt.status is PCBRepairHandoffStatus.RECORDED


@pytest.mark.parametrize(
    "decision", (ReviewDecision.REJECT, ReviewDecision.REQUEST_CHANGE)
)
def test_non_approved_review_cannot_record_handoff(service, context, decision):
    bundle = service.prepare_governance(_analysis_with_finding(service, context))
    with pytest.raises(PCBReviewRejected):
        service.record_repair_handoff(bundle, make_review(bundle, decision))


def test_handoff_is_idempotent(service, context):
    bundle = service.prepare_governance(_analysis_with_finding(service, context))
    review = make_review(bundle)
    assert service.record_repair_handoff(
        bundle, review
    ) == service.record_repair_handoff(bundle, review)


def test_service_has_no_agent_or_execution_api():
    public = {name for name in dir(EngineeringPCBService) if not name.startswith("_")}
    assert public == {
        "analyze",
        "build_context",
        "prepare_governance",
        "record_repair_handoff",
    }
