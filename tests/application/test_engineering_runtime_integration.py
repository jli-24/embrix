from __future__ import annotations

import pytest

from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRoutingContext,
    ApplicationRuntime,
    FakeApplicationAuditSink,
    FakeApplicationContext,
    FakeApplicationModelGateway,
    FakeApplicationSupervisor,
    FixedApplicationClock,
    ModelPolicy,
)
from embedded_copilot.application.engineering_runtime_integration import (
    CircuitIntentSpec,
    EngineeringObjectFlowError,
    EngineeringObjectFlowErrorCode,
    EngineeringObjectIntegrationService,
    EvidenceReceiptReference,
    EvidenceReferenceStatus,
)
from embedded_copilot.engineering_copilot import (
    DesignComponent,
    DesignEvidenceBinding,
    DesignInterface,
    DesignPin,
    DesignSignal,
    DesignSource,
    DesignTruth,
    DesignTruthOrigin,
    DesignTruthStatus,
    PlanProvenance,
    TruthLevel,
)
from embedded_copilot.engineering_requirements import (
    AcceptanceCriterion,
    RequirementContext,
    RequirementItem,
    RequirementItemStatus,
    RequirementLifecycle,
    RequirementProvenance,
    RequirementStatus,
)
from embedded_copilot.engineering_runtime import (
    CalculationRecord,
    CircuitBlock,
    CircuitDecision,
    EngineeringObjectRuntime,
    RelationType,
    VerificationRequirement,
)

from .fixtures import NOW, request, snapshot

FP = "sha256:" + "a" * 64


class FakeReceiptReferencePort:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, request_value, reference):
        self.calls += 1
        assert request_value.project_id == reference.project_id
        return reference


class FixtureQualifiedReceiptReferencePort(FakeReceiptReferencePort):
    def resolve(self, request_value, reference):
        self.calls += 1
        assert request_value.project_id == reference.project_id
        return reference.model_copy(
            update={
                "source": "KICAD_EXECUTION_WORKER",
                "tool": "kicad-cli sch erc",
                "scope": "SCHEMATIC_ERC",
                "result_summary": "KICAD_ERC_PASS",
                "verification_status": EvidenceReferenceStatus.VERIFIED,
                "receipt_fingerprint": FP,
                "artifact_ref": "hardware/main.kicad_sch",
                "artifact_fingerprint": FP,
                "schematic_revision": "r1",
                "tool_version": "10.0.5",
                "execution_result": "ERC_PASS",
            }
        )


def _requirements(*, project_id: str = "project-1"):
    requirement = RequirementItem(
        requirement_id="requirement-stm32",
        project_id=project_id,
        conversation_id="conversation-1",
        category="HARDWARE",
        normalized_value="STM32F103 control board with CAN, SWD, and 3V3 power",
        status=RequirementItemStatus.CONFIRMED,
        source_turn_id="turn-1",
        provenance=RequirementProvenance.USER_UTTERANCE,
    )
    criterion = AcceptanceCriterion(
        criterion_id="criterion-stm32",
        project_id=project_id,
        conversation_id="conversation-1",
        requirement_id=requirement.requirement_id,
        given="the board is powered",
        when="the design is reviewed",
        then="CAN, SWD, and 3V3 intent is traceable",
        verification="architecture review and scoped ERC",
        status=RequirementItemStatus.CONFIRMED,
        source_turn_id="turn-1",
        provenance=RequirementProvenance.USER_UTTERANCE,
    )
    return RequirementContext(
        project_id=project_id,
        conversation_id="conversation-1",
        requirements=(requirement,),
        acceptance_criteria=(criterion,),
        status=RequirementStatus.CONFIRMED,
        lifecycle=RequirementLifecycle.DESIGN_READY,
    )


def _binding(binding_id: str, entity_id: str) -> DesignEvidenceBinding:
    return DesignEvidenceBinding.create(
        binding_id=binding_id,
        entity_id=entity_id,
        project_id="project-1",
        source=DesignSource.SCHEMATIC,
        source_id="artifact-stm32-control-board",
        artifact_ref="artifact-stm32-control-board",
        locator=f"stm32-control-board:{entity_id}",
        artifact_revision="v1",
        source_fingerprint=FP,
        relationship="deterministic golden fixture reference",
        truth_level=TruthLevel.EXPLICIT,
    )


def _design_truth() -> DesignTruth:
    mcu = DesignComponent.create(
        component_id="component-mcu",
        name="STM32F103",
        type="CONTROLLER",
        category="MCU",
        description="STM32 controller",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-1",),
    )
    transceiver = DesignComponent.create(
        component_id="component-can",
        name="CAN transceiver",
        type="TRANSCEIVER",
        category="INTERFACE",
        description="CAN physical interface",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-2",),
    )
    interface = DesignInterface.create(
        interface_id="interface-can",
        source="component-mcu",
        target="component-can",
        protocol="CAN",
        direction="SOURCE_TO_TARGET",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-3",),
    )
    signal = DesignSignal.create(
        signal_id="signal-can-tx",
        name="CAN_TX",
        type="CAN",
        source="component-mcu",
        targets=("component-can",),
        protocol="CAN",
        direction="SOURCE_TO_TARGET",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-4",),
    )
    pin = DesignPin.create(
        pin_id="pin-pa12",
        component_id="component-mcu",
        pin_name="PA12",
        pin_number="PA12",
        function="CAN_TX",
        signal_id="signal-can-tx",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-5",),
    )
    entity_ids = (
        "truth-stm32",
        mcu.component_id,
        transceiver.component_id,
        interface.interface_id,
        signal.signal_id,
        pin.pin_id,
    )
    bindings = tuple(
        _binding(f"binding-{index}", entity_id)
        for index, entity_id in enumerate(entity_ids)
    )
    return DesignTruth.create(
        truth_id="truth-stm32",
        project_id="project-1",
        conversation_id="conversation-1",
        design_revision="v1",
        origin=DesignTruthOrigin.REVERSE,
        status=DesignTruthStatus.APPROVED,
        components=(mcu, transceiver),
        interfaces=(interface,),
        signals=(signal,),
        pins=(pin,),
        evidence_bindings=bindings,
        provenance=(
            PlanProvenance.create(
                project_id="project-1",
                source_type="SCHEMATIC",
                source_id="artifact-stm32-control-board",
                source_fingerprint=FP,
            ),
        ),
    )


def _evidence(*, project_id: str = "project-1") -> EvidenceReceiptReference:
    return EvidenceReceiptReference(
        reference_id="erc-reference-stm32",
        receipt_id="erc-receipt-stm32",
        tenant_id="default-tenant",
        project_id=project_id,
        source="FAKE_KICAD",
        tool="ERC",
        scope="Electrical rule check only",
        result_summary="PASS (deterministic fake receipt; not a real KiCad run)",
        occurred_at=NOW,
        source_fingerprint=FP,
        verification_status=EvidenceReferenceStatus.FAKE_UNVERIFIED,
    )


def _runtime(
    *,
    route: ApplicationRoute = ApplicationRoute.GREENFIELD_DESIGN,
    receipt_port=None,
):
    req = request(
        request_id="engineering-object-request",
        question="为 STM32 控制板生成电路设计意图",
    )
    decision = ApplicationRouteDecision.for_request(req, route=route)
    supervisor = FakeApplicationSupervisor(decision)
    source_context = FakeApplicationContext(
        snapshot(project_id=req.project_id, task=req.question)
    )
    receipt_port = receipt_port or FakeReceiptReferencePort()
    integration = EngineeringObjectIntegrationService(
        EngineeringObjectRuntime(project_id=req.project_id),
        evidence_port=receipt_port,
        context_provider=source_context,
    )
    application = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=supervisor,
            context_provider=source_context,
            model_gateway=FakeApplicationModelGateway(),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )
    routing = ApplicationRoutingContext(
        project_creation_mode="CREATE_NEW",
        conversation_id="conversation-1",
        requirement_context=_requirements(),
        design_truth=_design_truth(),
    )
    spec = CircuitIntentSpec(
        function="Provide protected 3V3 power and CAN connectivity for STM32F103",
        topology=("5V input -> protection -> 3V3 regulator", "MCU CAN -> transceiver -> connector"),
        parameters=("3V3 rail", "CAN_TX/CAN_RX", "SWD debug"),
        calculation=("regulator thermal margin must be checked",),
        assumptions=("component identities remain qualified by the existing hardware flow",),
        risks=("power stability and CAN common-mode behavior",),
        verification_requirements=("review linked ERC evidence", "bench verification later"),
    )
    return application, integration, req, routing, spec, receipt_port, source_context


def test_fake_evidence_cannot_enter_engineering_object_context() -> None:
    application, integration, req, routing, spec, receipt_port, source_context = _runtime()

    with pytest.raises(EngineeringObjectFlowError) as error:
        application.run_engineering_object_flow(
            req,
            routing_context=routing,
            circuit_intent=spec,
            evidence_reference=_evidence(),
            integration=integration,
            reviewer="engineer-reviewer",
        )

    assert error.value.code is EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE
    assert receipt_port.calls == 1
    assert source_context.call_count == 1


def test_non_design_route_is_rejected_before_projection() -> None:
    application, integration, req, routing, spec, receipt_port, _ = _runtime(
        route=ApplicationRoute.KNOWLEDGE
    )

    with pytest.raises(EngineeringObjectFlowError) as error:
        application.run_engineering_object_flow(
            req,
            routing_context=routing,
            circuit_intent=spec,
            evidence_reference=_evidence(),
            integration=integration,
        )

    assert error.value.code is EngineeringObjectFlowErrorCode.ROUTE_REJECTED
    assert receipt_port.calls == 0


def test_cross_project_evidence_is_rejected_fail_closed() -> None:
    application, integration, req, routing, spec, _, _ = _runtime()

    with pytest.raises(EngineeringObjectFlowError) as error:
        application.run_engineering_object_flow(
            req,
            routing_context=routing,
            circuit_intent=spec,
            evidence_reference=_evidence(project_id="project-2"),
            integration=integration,
        )

    assert error.value.code is EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH


def test_unapproved_apply_and_tampered_proposal_are_rejected() -> None:
    application, integration, req, routing, spec, _, _ = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    prepared = application.run_engineering_object_flow(
        req,
        routing_context=routing,
        circuit_intent=spec,
        evidence_reference=_evidence(),
        integration=integration,
    )

    with pytest.raises(EngineeringObjectFlowError) as approval_error:
        integration.commit(prepared)
    assert approval_error.value.code is EngineeringObjectFlowErrorCode.APPROVAL_REQUIRED

    approved = integration.review(prepared, reviewer="engineer-reviewer")
    tampered_proposal = approved.proposal.model_copy(
        update={"fingerprint": "sha256:" + "b" * 64}
    )
    tampered = approved.model_copy(update={"proposal": tampered_proposal})
    with pytest.raises(EngineeringObjectFlowError) as tamper_error:
        integration.commit(tampered)
    assert tamper_error.value.code is EngineeringObjectFlowErrorCode.PROPOSAL_TAMPERED


def test_stale_requirement_context_is_rejected_before_external_evidence() -> None:
    application, integration, req, routing, spec, receipt_port, _ = _runtime()
    stale_values = _requirements().model_dump(mode="python")
    stale_values["status"] = RequirementStatus.DRAFT
    stale_values["lifecycle"] = RequirementLifecycle.DISCOVERY_STARTED
    stale_values.pop("fingerprint", None)
    stale_routing = routing.model_copy(
        update={
            "requirement_context": RequirementContext.model_validate(stale_values)
        }
    )

    with pytest.raises(EngineeringObjectFlowError) as error:
        application.run_engineering_object_flow(
            req,
            routing_context=stale_routing,
            circuit_intent=spec,
            evidence_reference=_evidence(),
            integration=integration,
        )

    assert error.value.code is EngineeringObjectFlowErrorCode.STALE_CONTEXT
    assert receipt_port.calls == 0


def test_missing_evidence_reference_is_rejected_without_receipt_resolution() -> None:
    application, integration, req, routing, spec, receipt_port, _ = _runtime()

    with pytest.raises(EngineeringObjectFlowError) as error:
        application.run_engineering_object_flow(
            req,
            routing_context=routing,
            circuit_intent=spec,
            evidence_reference=None,
            integration=integration,
        )

    assert error.value.code is EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE
    assert receipt_port.calls == 0


def test_application_flow_does_not_invoke_filesystem_shell_or_model_provider(monkeypatch) -> None:
    import builtins
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("filesystem, shell, and provider calls are out of scope")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    application, integration, req, routing, spec, receipt_port, source_context = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    result = application.run_engineering_object_flow(
        req,
        routing_context=routing,
        circuit_intent=spec,
        evidence_reference=_evidence(),
        integration=integration,
    )

    assert result.proposal.status.value == "CREATED"
    assert receipt_port.calls == 1
    assert source_context.call_count == 1


def test_application_projection_preserves_structured_circuit_intent_and_relations() -> None:
    application, integration, req, routing, spec, _, _ = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    structured = spec.model_copy(
        update={
            "blocks": (
                CircuitBlock(
                    block_id="block-power",
                    function="3V3 power regulation",
                    requirement_refs=("requirement-stm32",),
                    architecture_refs=("hardware-stm32",),
                    topology_ref="decision-power",
                    decision_refs=("decision-power",),
                    calculation_refs=("calculation-power",),
                    verification_refs=("verification-power",),
                    schematic_binding_refs=("mapping-power",),
                ),
            ),
            "decisions": (
                CircuitDecision(
                    decision_id="decision-power",
                    block_ref="block-power",
                    decision_type="TOPOLOGY",
                    selected_option="DESIGN_CONSTRAINT:LDO",
                    alternatives=("DESIGN_CONSTRAINT:BUCK",),
                    rationale="The approved load constraint permits the simpler topology.",
                    tradeoffs=("Reduced complexity.",),
                    calculation_refs=("calculation-power",),
                    evidence_refs=("evidence-calculation",),
                    status="DRAFT",
                ),
            ),
            "calculations": (
                CalculationRecord(
                    calculation_id="calculation-power",
                    owner_ref="decision-power",
                    equation="P_loss=(Vin-Vout)*Iload",
                    inputs={"Vin": 5.0, "Vout": 3.3, "Iload": "I_MAX"},
                    outputs={"P_loss": 0.17},
                    units=("V", "A", "W"),
                    corner="VIN_MIN__I_MAX__TA_MAX",
                    margin=0.2,
                    acceptance_limit=0.0,
                    tool_reference="calculator:power-v1",
                    model_reference="model:approved-load-constraint",
                    evidence_refs=("evidence-calculation",),
                    status="EXECUTED",
                ),
            ),
            "verification_requirements": (
                VerificationRequirement(
                    verification_id="verification-power",
                    owner_ref="block-power",
                    claim="Power block has positive thermal margin.",
                    verification_type="CALCULATION",
                    method="deterministic power calculator",
                    acceptance_limit=0.0,
                    required_evidence_refs=("evidence-calculation",),
                    status="PLANNED",
                    currentness="CURRENT",
                ),
            ),
            "legacy_verification_requirements": (),
        }
    )

    result = application.run_engineering_object_flow(
        req,
        routing_context=routing,
        circuit_intent=structured,
        evidence_reference=_evidence(),
        integration=integration,
        reviewer="engineer-reviewer",
    )

    assert result.circuit_intent.blocks[0].block_id == "block-power"
    assert result.circuit_intent.decisions[0].selected_option == "DESIGN_CONSTRAINT:LDO"
    assert result.circuit_intent.calculations[0].inputs[0] == ("Iload", "I_MAX")
    assert result.circuit_intent.verification_requirements[0].status == "PLANNED"
    relations = integration._runtime.relations.list_relations()
    assert {
        (item.source.object_type, item.relation_type, item.target.object_type)
        for item in relations
    } >= {
        (result.requirement.object_type, RelationType.SATISFIED_BY, result.hardware_architecture.object_type),
        (result.hardware_architecture.object_type, RelationType.IMPLEMENTED_BY, result.circuit_intent.object_type),
        (result.circuit_intent.object_type, RelationType.EXPRESSED_BY, result.schematic_design.object_type),
        (result.schematic_design.object_type, RelationType.VALIDATED_BY, result.evidence.object_type),
    }
