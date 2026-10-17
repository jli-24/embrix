"""P2 reviewed standard applicability stays bound to current S02 owners."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from embedded_copilot.engineering_copilot.schematic_evidence import ParameterConditions, ParameterLocator
from embedded_copilot.engineering_copilot.schematic_parameter_readers import StandardDocumentFact
from embedded_copilot.project_catalog import ProjectCatalogConflict
from tests.project_catalog.test_p1_project_supply import _approved_runtime

DIGEST = "sha256:" + "a" * 64


class _MappedOriginals:
    def __init__(self, project_id):
        self.project_id = project_id

    def read_device_conditions(self, source, *, tenant_id, project_id):
        assert (tenant_id, project_id) == ("tenant-1", self.project_id)
        return ParameterConditions(
            voltage_min="1.62", voltage_max="3.6", temperature_min="5",
            temperature_max="60", mode="I2C_STANDARD", package=source.package,
        )

    def read_standard_fact(self, *, tenant_id, project_id, **values):
        assert (tenant_id, project_id) == ("tenant-1", self.project_id)
        return StandardDocumentFact(**values)


def _runtime(tmp_path, monkeypatch):
    catalog, detail, relation, selectors, circuit, source = _approved_runtime(
        tmp_path, monkeypatch, full_s02=True,
    )
    state = catalog.create_project_supply_candidate(
        detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,),
    )
    catalog.review_project_supply_candidate(
        detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester,
    )
    catalog.set_schematic_evidence_context(
        execution=SimpleNamespace(set_schematic_evidence_authorizer=lambda _f: None),
        parameter_port=_MappedOriginals(detail.project_id),
    )
    return catalog, detail, circuit, source


def _values(catalog, detail):
    current = catalog.conversation(
        detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
    )
    preview, _, _ = catalog._project_supply_checked(current, current.project_supply.candidate)
    bus = next(item for item in preview.interfaces if item.protocol == "I2C")
    selected = {
        item.slot_id: item.source
        for item in current.hardware_selection.proposal.selected_components
    }
    instances = tuple(
        item for item in preview.component_instances
        if item.design_truth_component_id in bus.participants
    )
    targets = tuple(
        {
            "component_instance_id": item.instance_id,
            "source_record_id": selected[item.hardware_slot_id].source_id,
            "source_record_fingerprint": selected[item.hardware_slot_id].fingerprint,
            "device_conditions": ParameterConditions(
                voltage_min="1.62", voltage_max="3.6",
                temperature_min="5", temperature_max="60",
                mode="I2C_STANDARD", package=selected[item.hardware_slot_id].package,
            ),
        }
        for item in instances
    )
    return {
        "document_uri": "isolated-test://um10204.pdf",
        "document_revision": "Rev. 7.0 - 1 October 2021",
        "content_sha256": DIGEST,
        "locator": ParameterLocator(page=44, table="Table 11", row="SCL clock frequency"),
        "mode": "I2C_STANDARD", "frequency_max_hz": "100000",
        "target_net_ids": tuple(
            item.net_id for item in preview.nets if item.signal_id in bus.signal_ids
        ),
        "target_components": targets,
        "requirement_id": current.engineering_plan.requirement_id,
        "requirement_fingerprint": current.requirement_context.fingerprint,
        "plan_id": current.engineering_plan.plan_id,
        "plan_fingerprint": current.engineering_plan.fingerprint,
    }


def test_standard_candidate_review_current_and_source_drift(tmp_path, monkeypatch):
    catalog, detail, _circuit, _source = _runtime(tmp_path, monkeypatch)
    values = _values(catalog, detail)
    candidate = catalog.create_standard_applicability(
        detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, values=values,
    )
    assert candidate.review is None
    reviewed = catalog.review_standard_applicability(
        detail.project_id, candidate.evidence.evidence_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        evidence_fingerprint=candidate.evidence.fingerprint, decision="APPROVED",
    )
    assert reviewed.review.decision == "APPROVED"
    assert catalog.standard_applicability(
        detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
    )[0][1] == "APPROVED"
    with pytest.raises(ProjectCatalogConflict):
        catalog.review_standard_applicability(
            detail.project_id, candidate.evidence.evidence_id,
            tenant_id=detail.tenant_id, requester=detail.requester,
            evidence_fingerprint=candidate.evidence.fingerprint, decision="REJECTED",
        )
    port = catalog._schematic_parameter_port
    read_standard_fact = port.read_standard_fact
    monkeypatch.setattr(
        port, "read_standard_fact",
        lambda **values: replace(read_standard_fact(**values), frequency_max_hz="999"),
    )
    assert catalog.standard_applicability(
        detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
    )[0][1] == "STALE"


@pytest.mark.parametrize("changed", ("target_net_ids", "target_components", "requirement_fingerprint", "plan_id"))
def test_standard_wrong_binding_never_persists(tmp_path, monkeypatch, changed):
    catalog, detail, _circuit, _source = _runtime(tmp_path, monkeypatch)
    values = _values(catalog, detail)
    values[changed] = {
        "target_net_ids": ("wrong-net",),
        "target_components": (values["target_components"][0],),
        "requirement_fingerprint": "sha256:" + "0" * 64,
        "plan_id": "wrong-plan",
    }[changed]
    with pytest.raises(ProjectCatalogConflict):
        catalog.create_standard_applicability(
            detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester, values=values,
        )
    assert catalog.conversation(
        detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
    ).standard_applicability is None

def test_standard_persisted_review_reopens_without_identity_changes(tmp_path, monkeypatch):
    from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
    from embedded_copilot.project_catalog.models import ConversationDetail
    from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort

    catalog, detail, _circuit, _source = _runtime(tmp_path, monkeypatch)
    before = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert "standard_applicability" not in before.model_dump(mode="json")
    assert ConversationDetail.model_validate_json(before.model_dump_json()).fingerprint == before.fingerprint
    candidate = catalog.create_standard_applicability(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, values=_values(catalog, detail))
    reviewed = catalog.review_standard_applicability(detail.project_id, candidate.evidence.evidence_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        evidence_fingerprint=candidate.evidence.fingerprint, decision="APPROVED")
    current = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert ConversationDetail.model_validate_json(current.model_dump_json()) == current
    source = catalog.hardware_component_source_for(current)
    assert isinstance(source, PublishedComponentSourcePort)
    catalog._repository = SQLiteProjectCatalogRepository(catalog._repository._database)
    catalog.set_hardware_component_source(PublishedComponentSourcePort(
        catalog._repository, tenant_id=source.tenant_id, publisher_id=source.publisher_id,
    ))
    reopened = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert reopened == current
    assert reopened.standard_applicability == (reviewed,)
    assert catalog.standard_applicability(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester) == ((reviewed, "APPROVED"),)
    assert catalog.review_standard_applicability(detail.project_id, candidate.evidence.evidence_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        evidence_fingerprint=candidate.evidence.fingerprint, decision="APPROVED") == reviewed


@pytest.mark.parametrize("scope", ({"tenant_id": "other"}, {"requester": "other"}))
def test_standard_wrong_owner_cannot_read_create_or_review(tmp_path, monkeypatch, scope):
    from embedded_copilot.project_catalog.exceptions import ProjectCatalogError
    catalog, detail, _circuit, _source = _runtime(tmp_path, monkeypatch)
    values = _values(catalog, detail)
    candidate = catalog.create_standard_applicability(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, values=values)
    identity = dict(tenant_id=detail.tenant_id, requester=detail.requester)
    identity.update(scope)
    for invoke in (
        lambda: catalog.standard_applicability(detail.project_id, detail.conversation_id, **identity),
        lambda: catalog.create_standard_applicability(detail.project_id, detail.conversation_id,
            values=values, **identity),
        lambda: catalog.review_standard_applicability(detail.project_id, candidate.evidence.evidence_id,
            evidence_fingerprint=candidate.evidence.fingerprint, decision="APPROVED", **identity),
    ):
        with pytest.raises(ProjectCatalogError):
            invoke()
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).standard_applicability == (candidate,)


@pytest.mark.parametrize("fault", ("source", "conditions", "circuit", "owner_during_io"))
def test_standard_changed_current_sources_cannot_be_reviewed(tmp_path, monkeypatch, fault):
    from embedded_copilot.project_catalog.exceptions import ProjectCatalogError
    from embedded_copilot.project_catalog.models import ConversationDetail
    catalog, detail, _circuit, _source = _runtime(tmp_path, monkeypatch)
    candidate = catalog.create_standard_applicability(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, values=_values(catalog, detail))
    port = catalog._schematic_parameter_port
    if fault == "source":
        monkeypatch.setattr(catalog, "_require_current_model_selection",
            lambda _selection, *, detail=None: (_ for _ in ()).throw(ProjectCatalogConflict("changed source")))
    elif fault == "conditions":
        original = port.read_device_conditions
        monkeypatch.setattr(port, "read_device_conditions", lambda *a, **k:
            original(*a, **k).model_copy(update={"temperature_max": "25"}))
    elif fault == "circuit":
        monkeypatch.setattr(catalog, "current_circuit_intents", lambda *a, **k: ())
    else:
        def change_owner():
            current = catalog.conversation(detail.project_id, detail.conversation_id,
                tenant_id=detail.tenant_id, requester=detail.requester)
            values = current.model_dump(mode="python")
            from datetime import timedelta
            values["updated_at"] += timedelta(seconds=1)
            values["fingerprint"] = "sha256:" + "0" * 64
            catalog.persist_conversation_detail(ConversationDetail.model_validate(values))
        original = port.read_standard_fact
        def drift(**values):
            result = original(**values)
            change_owner()
            return result
        monkeypatch.setattr(port, "read_standard_fact", drift)
    with pytest.raises(ProjectCatalogError):
        catalog.review_standard_applicability(detail.project_id, candidate.evidence.evidence_id,
            tenant_id=detail.tenant_id, requester=detail.requester,
            evidence_fingerprint=candidate.evidence.fingerprint, decision="APPROVED")
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).standard_applicability[0].review is None
    assert catalog.standard_applicability(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)[0][1] == "STALE"


def test_standard_json_rejects_tamper_and_preserves_strict_fields(tmp_path, monkeypatch):
    from embedded_copilot.project_catalog.models import ConversationDetail
    catalog, detail, _circuit, _source = _runtime(tmp_path, monkeypatch)
    catalog.create_standard_applicability(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, values=_values(catalog, detail))
    current = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    for field, value in (("schema_version", 3), ("frequency_max_hz", 100000),
                         ("frequency_max_hz", "999"), ("unapproved_field", "x")):
        body = current.model_dump(mode="json")
        body["standard_applicability"][0]["evidence"][field] = value
        body["fingerprint"] = "sha256:" + "0" * 64
        with pytest.raises(ValueError):
            ConversationDetail.model_validate(body)


def test_standard_calculation_context_tracks_review_generation_and_original_currentness(tmp_path, monkeypatch):
    from embedded_copilot.engineering_runtime import CalculationRecord
    from embedded_copilot.project_catalog.models import ConversationDetail, StandardApplicabilityReviewV2, ReviewedStandardApplicabilityV2
    catalog, detail, circuit, _source = _runtime(tmp_path, monkeypatch)
    candidate = catalog.create_standard_applicability(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, values=_values(catalog, detail))
    reviewed = catalog.review_standard_applicability(detail.project_id, candidate.evidence.evidence_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        evidence_fingerprint=candidate.evidence.fingerprint, decision="APPROVED")
    current = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    proposal, _truth, _circuit = catalog._project_supply_checked(current, current.project_supply.candidate)
    target = reviewed.evidence.target_components[0]
    corner = ParameterConditions(voltage_min="3.0", voltage_max="3.6", temperature_min="20", temperature_max="30", mode="I2C_STANDARD").model_dump_json(exclude={"package"})
    calculation = CalculationRecord(calculation_id="standard-calculation", owner_ref="test-support",
        equation="S02_RC_DECOUPLING_V1", inputs=(("frequency_limit_hz", "100000"),), corner=corner,
        input_sources=({"input_name":"frequency_limit_hz", "value":"100000", "unit":"Hz",
            "source_kind":"ENGINEERING_EVIDENCE", "source_id":reviewed.evidence.evidence_id,
            "source_fingerprint":reviewed.evidence.fingerprint, "content_fingerprint":DIGEST,
            "locator":reviewed.evidence.locator.model_dump_json(), "condition":corner,
            "component_instance_id":target.component_instance_id},))
    selected = SimpleNamespace(fingerprint=circuit.fingerprint, calculations=(calculation,))
    monkeypatch.setattr(catalog, "circuit_intent_revision", lambda *a, **k: selected)
    def canonical(*args, **kwargs):
        latest = catalog.conversation(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester)
        return latest, proposal, SimpleNamespace(artifact_fingerprint=DIGEST, fingerprint=DIGEST), SimpleNamespace(operation_id="write-test"), SimpleNamespace(fingerprint=DIGEST)
    monkeypatch.setattr(catalog, "_canonical_schematic_sources", canonical)
    selectors = dict(tenant_id=detail.tenant_id, requester=detail.requester,
        intent_id=proposal.circuit_intent_id, intent_revision=proposal.circuit_intent_revision,
        intent_fingerprint=proposal.circuit_intent_fingerprint, plan_id=proposal.plan_id,
        plan_fingerprint=proposal.fingerprint, artifact_fingerprint=DIGEST,
        generation_operation_id="write-test", erc_operation_id=None, parse_operation_id=None,
        readback_operation_id=None, parameter_evidence_ids=())
    def material():
        return catalog._s02_verification_material(detail.project_id, detail.conversation_id, **selectors)
    original = material()
    assert original[-2] == (False,) * 8  # No physical/parameter receipts: never a schematic PASS.
    body = reviewed.review.model_dump(mode="python")
    body.update(review_id="replacement-review", fingerprint="sha256:" + "0" * 64)
    replacement = ReviewedStandardApplicabilityV2(evidence=reviewed.evidence, review=StandardApplicabilityReviewV2(**body))
    payload = current.model_dump(mode="python")
    payload.update(standard_applicability=(replacement,), fingerprint="sha256:" + "0" * 64)
    catalog.persist_conversation_detail(ConversationDetail.model_validate(payload))
    assert material()[-1] != original[-1]
    port = catalog._schematic_parameter_port
    original_fact = port.read_standard_fact
    monkeypatch.setattr(port, "read_standard_fact", lambda **values:
        replace(original_fact(**values), frequency_max_hz="999"))
    with pytest.raises(ProjectCatalogConflict):
        material()
