"""Offline exact parent-tree Selection v3 coverage over the real Catalog DB."""

from __future__ import annotations

import copy
import sqlite3

import pytest

from embedded_copilot.component_recommendation.contracts import (
    ComponentSourceRecord,
    SupportingComponentRequirement,
    SupportingConnectionExpectation,
)
from embedded_copilot.engineering_copilot import HardwareSelectionService
from embedded_copilot.project_catalog._selection_revision_lifecycle import (
    SelectionRevisionLifecycle,
)
from embedded_copilot.project_catalog.exceptions import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogUnavailable,
)
from embedded_copilot.project_catalog.source_publication import (
    ComponentSourcePublishCommandV1,
    PublishedComponentSourcePort,
    TrustedComponentSourcePublisher,
)
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
from tests.engineering_copilot.test_s02_supporting_fulfillment import _support_part
from tests.project_catalog.test_source_replan_contract import _approved_v3_legacy_plan


def _fixture(tmp_path, monkeypatch, *, with_support_part=True):
    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    source = catalog._hardware_component_source
    publisher = TrustedComponentSourcePublisher(
        catalog._repository,
        tenant_id=detail.tenant_id,
        project_id=detail.project_id,
        publisher_id=source.publisher_id,
    )
    sensor = source.current_publication(detail.project_id, "catalog-sensor").record
    support = SupportingComponentRequirement.create(
        role="DECOUPLING",
        description="Local bypass",
        quantity=1,
        evidence_refs=("offline-parent-tree",),
        connection_expectations=(
            SupportingConnectionExpectation(
                target_pin_number="1",
                target_net_name="3V3",
                signal_pin_number="1",
                signal_net_name="3V3",
                return_pin_number="2",
                return_net_name="GND",
            ),
        ),
    )
    records = []
    for label in ("a", "b"):
        record = ComponentSourceRecord.create(
            **{
                **sensor.model_dump(mode="python", exclude={"fingerprint"}),
                "source_id": "catalog-sensor-parent-" + label,
                "description": "Parent evidence " + label,
                "supporting_requirements": (support,),
            }
        )
        records.append(record)
    if with_support_part:
        records.append(_support_part("DECOUPLING", "C-V3-100N"))
    for index, record in enumerate(records):
        publisher.publish(
            ComponentSourcePublishCommandV1(
                schema_version=1,
                request_id=f"v3-parent-source-{index}",
                source_id=record.source_id,
                expected_head_publication_fingerprint=None,
                record=record,
            )
        )
    detail = catalog.conversation(
        detail.project_id, detail.conversation_id, **_scope(detail)
    )
    return catalog, detail, tuple(records[:2]), publisher


def _scope(detail):
    return {"tenant_id": detail.tenant_id, "requester": detail.requester}


def _parents(initial, record):
    return tuple(
        sorted(
            (
                (item["slot_id"], record.source_id, record.fingerprint)
                if item["source_id"] == "catalog-sensor"
                else (item["slot_id"], item["source_id"], item["source_fingerprint"])
                for item in initial["selected_parents"]
            ),
            key=lambda item: item[0],
        )
    )


def _choose_tree(preview):
    parents = {
        item["slot_id"]: (
            item["slot_id"],
            item["source_id"],
            item["source_fingerprint"],
        )
        for item in preview["selected_parents"]
    }
    return tuple(
        parents[slot]
        if slot in parents
        else next(
            (item["slot_id"], item["source_id"], item["source_fingerprint"])
            for item in preview["choices"]
            if item["slot_id"] == slot
        )
        for slot in preview["required_slots"]
    )


def _create(lifecycle, detail, preview, parents, request_id, selections=None):
    return lifecycle.create(
        detail.project_id,
        detail.conversation_id,
        request_id=request_id,
        expected_head=tuple(preview["expected_head"]),
        preview_fingerprint=preview["preview_fingerprint"],
        selections=_choose_tree(preview) if selections is None else selections,
        schema_version=3,
        parent_choices=parents,
        **_scope(detail),
    )


def _decide(lifecycle, detail, result):
    head = result["head"]
    return lifecycle.decide(
        detail.project_id,
        detail.conversation_id,
        revision_id=head["revision_id"],
        selection_id=head["selection_id"],
        selection_fingerprint=head["selection_fingerprint"],
        expected_decision_fingerprint=head["decision_fingerprint"],
        decision="APPROVED",
        **_scope(detail),
    )


def test_two_parent_trees_exact_create_approve_and_restart(tmp_path, monkeypatch):
    catalog, detail, records, _ = _fixture(tmp_path, monkeypatch)
    lifecycle = SelectionRevisionLifecycle(catalog)
    raw_before_get = catalog._repository._database.read_bytes()
    initial = lifecycle.preview(detail, schema_version=3)
    assert initial["schema_version"] == 3
    assert tuple(sorted(initial["root_slots"])) == initial["root_slots"]
    parents_a, parents_b = (_parents(initial, record) for record in records)
    preview_a = lifecycle.preview(detail, 3, parents_a)
    preview_b = lifecycle.preview(detail, 3, parents_b)
    assert catalog._repository._database.read_bytes() == raw_before_get
    children_a = set(preview_a["required_slots"]) - set(preview_a["root_slots"])
    children_b = set(preview_b["required_slots"]) - set(preview_b["root_slots"])
    assert len(children_a) == len(children_b) == 1
    assert children_a.isdisjoint(children_b)
    assert records[0].mpn == records[1].mpn
    assert records[0].fingerprint != records[1].fingerprint
    assert preview_a["preview_fingerprint"] != preview_b["preview_fingerprint"]
    assert {item["source_id"] for item in preview_a["source_publications"]} >= {
        records[0].source_id,
        "catalog-decoupling",
    }
    first = _create(lifecycle, detail, preview_a, parents_a, "tree-a")
    assert _decide(lifecycle, detail, first)["head"]["status"] == "APPROVED"
    after_a = lifecycle.detail(
        detail.project_id, detail.conversation_id, **_scope(detail)
    )
    preview_b = lifecycle.preview(after_a, 3, parents_b)
    second = _create(lifecycle, detail, preview_b, parents_b, "tree-b")
    assert _decide(lifecycle, detail, second)["head"]["status"] == "APPROVED"
    other = copy.copy(catalog)
    other._repository = SQLiteProjectCatalogRepository(catalog._repository._database)
    other.set_hardware_component_source(
        PublishedComponentSourcePort(
            other._repository,
            tenant_id=detail.tenant_id,
            publisher_id=catalog._hardware_component_source.publisher_id,
        )
    )
    restored = SelectionRevisionLifecycle(other).view(
        detail.project_id,
        detail.conversation_id,
        **_scope(detail),
    )
    assert restored["head"]["revision_id"] == second["head"]["revision_id"]
    assert restored["head"]["status"] == "APPROVED"
    assert restored["head"]["currentness"] == "CURRENT"
    old = SelectionRevisionLifecycle(other).view(
        detail.project_id,
        detail.conversation_id,
        view="HISTORY",
        revision_id=first["head"]["revision_id"],
        **_scope(detail),
    )
    assert old["head"]["currentness"] == "STALE"


def test_bad_roots_mixed_tree_source_and_v2_rejected_without_writes(
    tmp_path, monkeypatch
):
    catalog, detail, records, _ = _fixture(tmp_path, monkeypatch)
    lifecycle = SelectionRevisionLifecycle(catalog)
    initial = lifecycle.preview(detail, 3)
    parents_a, parents_b = (_parents(initial, record) for record in records)
    preview_a = lifecycle.preview(detail, 3, parents_a)
    preview_b = lifecycle.preview(detail, 3, parents_b)
    raw = catalog._repository._database.read_bytes()
    with pytest.raises(ValueError, match="cannot be mixed"):
        HardwareSelectionService(catalog._hardware_component_source).create_proposal(
            detail.architecture_design,
            detail.requirement_context,
            workflow=detail.engineering_workflow,
            conversation_id=detail.conversation_id,
            candidate_selections=_choose_tree(preview_a),
            parent_selections=parents_a,
        )
    with pytest.raises(ProjectCatalogConflict):
        lifecycle.preview(detail, 2, parents_a)
    for bad in (
        parents_a[:-1],
        parents_a + (parents_a[0],),
        parents_a + (("extra-root", records[0].source_id, records[0].fingerprint),),
        tuple(
            (slot, "fake-source", fp)
            if source == records[0].source_id
            else (slot, source, fp)
            for slot, source, fp in parents_a
        ),
    ):
        with pytest.raises(ProjectCatalogConflict):
            lifecycle.preview(detail, 3, bad)
    with pytest.raises(ProjectCatalogConflict):
        lifecycle.view(
            detail.project_id,
            detail.conversation_id,
            view="CURRENT",
            schema_version=3,
            **_scope(detail),
        )
    with pytest.raises(ProjectCatalogConflict):
        lifecycle.create(
            detail.project_id,
            detail.conversation_id,
            request_id="v2-with-parent",
            expected_head=tuple(preview_a["expected_head"]),
            preview_fingerprint=preview_a["preview_fingerprint"],
            selections=_choose_tree(preview_a),
            parent_choices=parents_a,
            **_scope(detail),
        )
    with pytest.raises(ProjectCatalogConflict):
        _create(
            lifecycle,
            detail,
            preview_b,
            parents_b,
            "mixed-tree",
            selections=_choose_tree(preview_a),
        )
    wrong_source = tuple(
        (slot, "fake-source", fp)
        if slot in preview_a["root_slots"]
        else (slot, source, fp)
        for slot, source, fp in _choose_tree(preview_a)
    )
    with pytest.raises(ProjectCatalogConflict):
        _create(
            lifecycle,
            detail,
            preview_a,
            parents_a,
            "fake-source",
            selections=wrong_source,
        )
    assert catalog._repository._database.read_bytes() == raw


def test_v3_idempotency_cas_owner_and_publication_aba(tmp_path, monkeypatch):
    catalog, detail, records, publisher = _fixture(tmp_path, monkeypatch)
    lifecycle = SelectionRevisionLifecycle(catalog)
    initial = lifecycle.preview(detail, 3)
    parents = _parents(initial, records[0])
    preview = lifecycle.preview(detail, 3, parents)
    with pytest.raises(ProjectCatalogForbidden):
        lifecycle.view(
            detail.project_id,
            detail.conversation_id,
            view="PREVIEW",
            schema_version=3,
            parent_choices=parents,
            tenant_id=detail.tenant_id,
            requester="other-owner",
        )
    old = catalog._hardware_component_source.current_publication(
        detail.project_id, records[0].source_id
    )
    changed = ComponentSourceRecord.create(
        **{
            **old.record.model_dump(mode="python", exclude={"fingerprint"}),
            "description": "ABA interim evidence",
        }
    )
    middle, _ = publisher.publish(
        ComponentSourcePublishCommandV1(
            schema_version=1,
            request_id="v3-aba-b",
            source_id=old.source_id,
            expected_head_publication_fingerprint=old.fingerprint,
            record=changed,
        )
    )
    publisher.publish(
        ComponentSourcePublishCommandV1(
            schema_version=1,
            request_id="v3-aba-a",
            source_id=old.source_id,
            expected_head_publication_fingerprint=middle.fingerprint,
            record=old.record,
        )
    )
    with pytest.raises(ProjectCatalogConflict):
        _create(lifecycle, detail, preview, parents, "old-publication")
    fresh = lifecycle.preview(detail, 3, parents)
    result = _create(lifecycle, detail, fresh, parents, "tree-idem")
    repeated = _create(lifecycle, detail, fresh, parents, "tree-idem")
    assert result["head"]["revision_id"] == repeated["head"]["revision_id"]
    with pytest.raises(ProjectCatalogConflict):
        _create(lifecycle, detail, fresh, parents, "tree-cas")
    with pytest.raises(ProjectCatalogConflict):
        _create(
            lifecycle,
            detail,
            fresh,
            parents,
            "tree-idem",
            selections=_choose_tree(fresh)[:-1],
        )
    with sqlite3.connect(catalog._repository._database) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM hardware_selection_revisions").fetchone()[
                0
            ]
            == 1
        )


def test_missing_support_and_request_version_identity(tmp_path, monkeypatch):
    catalog, detail, records, _ = _fixture(
        tmp_path,
        monkeypatch,
        with_support_part=False,
    )
    lifecycle = SelectionRevisionLifecycle(catalog)
    initial = lifecycle.preview(detail, 3)
    parents = _parents(initial, records[0])
    raw = catalog._repository._database.read_bytes()
    with pytest.raises(ProjectCatalogUnavailable, match="SELECTION_TREE_UNAVAILABLE"):
        lifecycle.preview(detail, 3, parents)
    assert catalog._repository._database.read_bytes() == raw

    publisher = TrustedComponentSourcePublisher(
        catalog._repository,
        tenant_id=detail.tenant_id,
        project_id=detail.project_id,
        publisher_id=catalog._hardware_component_source.publisher_id,
    )
    record = _support_part("DECOUPLING", "C-V3-100N")
    publisher.publish(
        ComponentSourcePublishCommandV1(
            schema_version=1,
            request_id="v3-support-after-unavailable",
            source_id=record.source_id,
            expected_head_publication_fingerprint=None,
            record=record,
        )
    )
    v3 = lifecycle.preview(detail, 3, parents)
    result = _create(lifecycle, detail, v3, parents, "version-bound")
    v2 = lifecycle.preview(
        lifecycle.detail(detail.project_id, detail.conversation_id, **_scope(detail))
    )
    with pytest.raises(ProjectCatalogConflict, match="SELECTION_REQUEST_CONFLICT"):
        lifecycle.create(
            detail.project_id,
            detail.conversation_id,
            request_id="version-bound",
            expected_head=tuple(v2["expected_head"]),
            preview_fingerprint=v2["preview_fingerprint"],
            selections=tuple(
                next(
                    (item["slot_id"], item["source_id"], item["source_fingerprint"])
                    for item in v2["choices"]
                    if item["slot_id"] == slot
                )
                for slot in v2["required_slots"]
            ),
            **_scope(detail),
        )
    assert result["head"]["revision_number"] == 1


def test_v3_transaction_abort_preserves_raw_and_retries(tmp_path, monkeypatch):
    catalog, detail, records, _ = _fixture(tmp_path, monkeypatch)
    lifecycle = SelectionRevisionLifecycle(catalog)
    initial = lifecycle.preview(detail, 3)
    parents = _parents(initial, records[0])
    preview = lifecycle.preview(detail, 3, parents)
    with sqlite3.connect(catalog._repository._database) as db:
        raw = db.execute(
            "SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,),
        ).fetchone()[0]
        db.execute(
            "CREATE TRIGGER abort_v3_selection BEFORE UPDATE ON conversations "
            "BEGIN SELECT RAISE(ABORT,'offline v3 rollback probe'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        _create(lifecycle, detail, preview, parents, "v3-rollback")
    with sqlite3.connect(catalog._repository._database) as db:
        assert (
            db.execute(
                "SELECT body FROM conversations WHERE conversation_id=?",
                (detail.conversation_id,),
            ).fetchone()[0]
            == raw
        )
        assert (
            db.execute("SELECT COUNT(*) FROM hardware_selection_revisions").fetchone()[
                0
            ]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM selection_schematic_plan_anchors"
            ).fetchone()[0]
            == 0
        )
        db.execute("DROP TRIGGER abort_v3_selection")
    result = _create(lifecycle, detail, preview, parents, "v3-rollback")
    assert result["head"]["revision_number"] == 1


def test_same_source_id_new_fingerprint_changes_support_slot(tmp_path, monkeypatch):
    catalog, detail, records, publisher = _fixture(tmp_path, monkeypatch)
    lifecycle = SelectionRevisionLifecycle(catalog)
    initial = lifecycle.preview(detail, 3)
    old_parents = _parents(initial, records[0])
    old_preview = lifecycle.preview(detail, 3, old_parents)
    old = catalog._hardware_component_source.current_publication(
        detail.project_id,
        records[0].source_id,
    )
    changed = ComponentSourceRecord.create(
        **{
            **old.record.model_dump(mode="python", exclude={"fingerprint"}),
            "description": "Updated evidence for same nominal part",
        }
    )
    publisher.publish(
        ComponentSourcePublishCommandV1(
            schema_version=1,
            request_id="v3-same-id-new-fingerprint",
            source_id=old.source_id,
            expected_head_publication_fingerprint=old.fingerprint,
            record=changed,
        )
    )
    new_parents = _parents(initial, changed)
    new_preview = lifecycle.preview(detail, 3, new_parents)
    assert old.record.source_id == changed.source_id
    assert old.record.mpn == changed.mpn
    assert old.record.fingerprint != changed.fingerprint
    assert (
        set(old_preview["required_slots"]) - set(old_preview["root_slots"])
    ).isdisjoint(set(new_preview["required_slots"]) - set(new_preview["root_slots"]))
    assert old_preview["preview_fingerprint"] != new_preview["preview_fingerprint"]


def test_v3_does_not_hide_unsupported_parent_support_semantics(tmp_path, monkeypatch):
    catalog, detail, records, publisher = _fixture(tmp_path, monkeypatch)
    lifecycle = SelectionRevisionLifecycle(catalog)
    initial = lifecycle.preview(detail, 3)
    old = records[0]
    requirement = SupportingComponentRequirement.create(
        role="UNSUPPORTED_AUXILIARY",
        description="Required auxiliary with no supported projection",
        quantity=1,
        evidence_refs=("offline-unsupported",),
    )
    changed = ComponentSourceRecord.create(
        **{
            **old.model_dump(mode="python", exclude={"fingerprint"}),
            "supporting_requirements": (requirement,),
        }
    )
    publication = catalog._hardware_component_source.current_publication(
        detail.project_id, old.source_id
    )
    publisher.publish(
        ComponentSourcePublishCommandV1(
            schema_version=1,
            request_id="unsupported-parent-head",
            source_id=changed.source_id,
            expected_head_publication_fingerprint=publication.fingerprint,
            record=changed,
        )
    )
    before = catalog._repository._database.read_bytes()
    with pytest.raises(ProjectCatalogUnavailable, match="SELECTION_TREE_UNAVAILABLE"):
        lifecycle.preview(detail, 3, _parents(initial, changed))
    assert catalog._repository._database.read_bytes() == before
