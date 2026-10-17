"""Owner-bound append-only Selection lifecycle over the canonical Catalog DB."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from uuid import uuid4

from embedded_copilot.engineering_copilot import (
    DesignTruthBuilder,
    HardwareSelectionDecision,
    HardwareSelectionService,
    HardwareSelectionState,
    HardwareSelectionStatus,
    generate_knowledge_model,
)

from ._selection_revision_projection import withdraw_selection_revision_projection
from .exceptions import ProjectCatalogConflict, ProjectCatalogUnavailable
from .models import ConversationDetail, SourcePublicationBindingV1
from .source_publication import PublishedComponentSourcePort

ZERO = "sha256:" + "0" * 64


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return "sha256:" + hashlib.sha256(encode(value).encode()).hexdigest()


def canonical_parent_choices(parent_choices):
    """Validate and order the complete root selector without changing its identity."""
    from embedded_copilot.engineering_runtime.common import (
        validate_fingerprint,
        validate_identifier,
    )

    if (
        type(parent_choices) is not tuple
        or not parent_choices
        or len(parent_choices) > 64
    ):
        raise ProjectCatalogConflict("SELECTION_PARENT_CHOICES_INVALID")
    try:
        if len(encode(parent_choices)) > 16384:
            raise ValueError("parent choices too long")
        for item in parent_choices:
            if type(item) is not tuple or len(item) != 3:
                raise ValueError("parent choice shape")
            validate_identifier(item[0], field="slot_id")
            validate_identifier(item[1], field="source_id")
            validate_fingerprint(item[2])
        ordered = tuple(sorted(parent_choices, key=lambda item: item[0]))
        if len({item[0] for item in ordered}) != len(ordered):
            raise ValueError("parent slot repeated")
        return ordered
    except (TypeError, ValueError) as error:
        raise ProjectCatalogConflict("SELECTION_PARENT_CHOICES_INVALID") from error


class SelectionRevisionLifecycle:
    def __init__(self, runtime):
        self.runtime = runtime
        self.repo = runtime._repository

    def detail(self, project_id, conversation_id, tenant_id, requester):
        return self.runtime.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )

    @staticmethod
    def scope(detail):
        return (detail.tenant_id, detail.project_id, detail.conversation_id)

    @staticmethod
    def validate_revision(row):
        try:
            state = HardwareSelectionState.model_validate_json(row["selection_body"])
            bindings = tuple(
                SourcePublicationBindingV1.model_validate(item)
                for item in json.loads(row["source_bindings_body"])
            )
            selected = {
                item.source.source_id: item.source.fingerprint
                for item in state.proposal.selected_components
            }
            if (
                state.status is not HardwareSelectionStatus.REVIEW
                or row["revision_number"] < 1
                or (state.proposal.proposal_id, state.proposal.fingerprint)
                != (row["selection_id"], row["selection_fingerprint"])
                or state.proposal.project_id != row["project_id"]
                or state.proposal.conversation_id != row["conversation_id"]
                or {item.source_id: item.source_record_fingerprint for item in bindings}
                != selected
            ):
                raise ValueError("revision binding invalid")
            expected = digest(
                {
                    "revision_id": row["revision_id"],
                    "number": row["revision_number"],
                    "predecessor": (
                        row["predecessor_revision_id"],
                        row["predecessor_selection_id"],
                        row["predecessor_selection_fingerprint"],
                    ),
                    "selection": state.model_dump(mode="json"),
                    "source_publications": tuple(
                        item.model_dump(mode="json") for item in bindings
                    ),
                }
            )
            if expected != row["revision_fingerprint"]:
                raise ValueError("revision fingerprint mismatch")
        except (TypeError, ValueError, KeyError) as error:
            raise ProjectCatalogUnavailable(
                "SELECTION_REVISION_INTEGRITY_FAILED"
            ) from error

    def head(self, detail, connection=None):
        if connection is None:
            with self.repo._connect() as db:
                db.row_factory = sqlite3.Row
                return self.head(detail, db)
        row = connection.execute(
            "SELECT r.*,h.status,h.decision_fingerprint FROM hardware_selection_revision_heads h "
            "JOIN hardware_selection_revisions r ON h.revision_id=r.revision_id "
            "WHERE h.tenant_id=? AND h.project_id=? AND h.conversation_id=?",
            self.scope(detail),
        ).fetchone()
        if row is not None:
            self.validate_revision(row)
        return None if row is None else dict(row)

    def require_current(self, selection):
        with self.repo._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT r.*,h.status,h.decision_fingerprint FROM hardware_selection_revisions r LEFT JOIN hardware_selection_revision_heads h ON h.revision_id=r.revision_id WHERE r.selection_id=?",
                (selection.proposal.proposal_id,),
            ).fetchone()
            if row is None:
                return  # Legacy semantics remain unchanged.
            raw = db.execute(
                "SELECT body FROM conversations WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                (row["tenant_id"], row["project_id"], row["conversation_id"]),
            ).fetchone()
        if raw is None:
            raise ProjectCatalogConflict("SELECTION_REVISION_STALE")
        detail = self.repo._conversation_body(raw[0])
        projected = self.project(detail, dict(row), is_head=row["status"] is not None)
        if (
            projected["head"]["currentness"] != "CURRENT"
            or projected["head"]["status"] != "APPROVED"
            or projected["selection"] != selection.model_dump(mode="json")
            or detail.hardware_selection != selection
        ):
            raise ProjectCatalogConflict("SELECTION_REVISION_STALE")

    def expected(self, detail, head=None):
        if head is None:
            head = self.head(detail)
        if head is not None:
            return (
                head["revision_id"],
                head["selection_id"],
                head["selection_fingerprint"],
            )
        state = detail.hardware_selection
        return (
            (None, None, None)
            if state is None
            else (None, state.proposal.proposal_id, state.proposal.fingerprint)
        )

    def publications(self, detail, records):
        source = self.runtime.hardware_component_source_for(detail)
        if (
            type(source) is not PublishedComponentSourcePort
            or source.repository is not self.repo
            or source.tenant_id != detail.tenant_id
        ):
            raise ProjectCatalogConflict("SELECTION_SOURCE_PUBLICATION_UNAVAILABLE")
        bindings = {}
        for record in records:
            publication = source.current_publication(
                detail.project_id, record.source_id
            )
            if (
                publication is None
                or publication.record != record
                or publication.publisher_id != source.publisher_id
            ):
                raise ProjectCatalogConflict("SELECTION_SOURCE_PUBLICATION_STALE")
            binding = SourcePublicationBindingV1(
                source_id=record.source_id,
                source_record_fingerprint=record.fingerprint,
                publisher_id=publication.publisher_id,
                publication_id=publication.publication_id,
                generation=publication.generation,
                publication_fingerprint=publication.fingerprint,
            )
            previous = bindings.setdefault(record.source_id, binding)
            if previous != binding:
                raise ProjectCatalogConflict("SELECTION_SOURCE_AMBIGUOUS")
        if not bindings:
            raise ProjectCatalogUnavailable("SELECTION_CHOICES_UNAVAILABLE")
        return tuple(bindings[key] for key in sorted(bindings))

    def proposal(self, detail, selections=None, *, parent_selections=None):
        if self.runtime.hardware_component_source_for(detail) is None:
            raise ProjectCatalogUnavailable("SELECTION_SOURCE_PUBLICATION_UNAVAILABLE")
        if (
            detail.architecture_review is None
            or detail.architecture_design is None
            or detail.engineering_plan_review is None
            or detail.engineering_workflow is None
            or detail.requirement_context is None
        ):
            raise ProjectCatalogConflict("SELECTION_UPSTREAM_UNAVAILABLE")
        return HardwareSelectionService(
            self.runtime.hardware_component_source_for(detail)
        ).create_proposal(
            detail.architecture_design,
            detail.requirement_context,
            workflow=detail.engineering_workflow,
            conversation_id=detail.conversation_id,
            candidate_selections=selections,
            parent_selections=parent_selections,
        )

    def preview(self, detail, schema_version=2, parent_choices=None):
        if schema_version not in (2, 3) or type(schema_version) is not int:
            raise ProjectCatalogConflict("SELECTION_SCHEMA_VERSION_INVALID")
        if schema_version == 2 and parent_choices is not None:
            raise ProjectCatalogConflict("SELECTION_PARENT_CHOICES_INVALID")
        parents = (
            canonical_parent_choices(parent_choices)
            if parent_choices is not None
            else None
        )
        try:
            proposal = self.proposal(detail, parent_selections=parents)
        except (TypeError, ValueError) as error:
            if schema_version == 2:
                raise
            if parents is not None and (
                "parent" in str(error) or "model candidate" in str(error)
            ):
                raise ProjectCatalogConflict(
                    "SELECTION_PARENT_CHOICES_INVALID"
                ) from error
            raise ProjectCatalogUnavailable("SELECTION_TREE_UNAVAILABLE") from error
        if schema_version == 3:
            for parent in proposal.selected_components:
                if parent.supporting_for is not None:
                    continue
                for requirement in parent.source.supporting_requirements:
                    if (
                        requirement.role not in {"I2C_PULLUPS", "DECOUPLING"}
                        or not requirement.connection_expectations
                        or requirement.quantity
                        != len(requirement.connection_expectations)
                    ):
                        raise ProjectCatalogUnavailable("SELECTION_TREE_UNAVAILABLE")
        root_slots = tuple(
            sorted(
                item.slot_id
                for item in proposal.requirements
                if item.supporting_for is None
            )
        )
        if schema_version == 3 and (not root_slots or len(root_slots) > 64):
            raise ProjectCatalogUnavailable("SELECTION_ROOT_SLOTS_UNAVAILABLE")
        selected_parents = tuple(
            sorted(
                (
                    (item.slot_id, item.source.source_id, item.source.fingerprint)
                    for item in proposal.selected_components
                    if item.supporting_for is None
                ),
                key=lambda item: item[0],
            )
        )
        if parents is not None and selected_parents != parents:
            raise ProjectCatalogConflict("SELECTION_PARENT_CHOICES_INVALID")
        choices = tuple(
            {
                "slot_id": item.slot_id,
                "source_id": item.source.source_id,
                "source_fingerprint": item.source.fingerprint,
            }
            for item in proposal.evaluations
            if item.selectable
        )
        publications = self.publications(
            detail, (item.source for item in proposal.evaluations if item.selectable)
        )
        value = {
            "kind": "PREVIEW",
            "schema_version": schema_version,
            "project_id": detail.project_id,
            "conversation_id": detail.conversation_id,
            "expected_head": self.expected(detail),
            "conversation_fingerprint": detail.fingerprint,
            "architecture_review_fingerprint": detail.architecture_review.fingerprint,
            "engineering_plan_review_fingerprint": detail.engineering_plan_review.fingerprint,
            "required_slots": tuple(item.slot_id for item in proposal.requirements),
            "choices": choices,
            "source_publications": tuple(
                item.model_dump(mode="json") for item in publications
            ),
        }
        if schema_version == 3:
            value["root_slots"] = root_slots
            value["selected_parents"] = tuple(
                {
                    "slot_id": slot,
                    "source_id": source,
                    "source_fingerprint": fingerprint,
                }
                for slot, source, fingerprint in selected_parents
            )
        return {**value, "preview_fingerprint": digest(value)}

    def sources_current(self, detail, row):
        try:
            source = self.runtime.hardware_component_source_for(detail)
            if (
                type(source) is not PublishedComponentSourcePort
                or source.repository is not self.repo
                or source.tenant_id != detail.tenant_id
            ):
                return False
            for item in json.loads(row["source_bindings_body"]):
                publication = source.current_publication(
                    detail.project_id, item["source_id"]
                )
                if (
                    publication is None
                    or publication.fingerprint != item["publication_fingerprint"]
                ):
                    return False
            state = HardwareSelectionState.model_validate_json(row["selection_body"])
            return (
                detail.architecture_design is not None
                and detail.requirement_context is not None
                and state.proposal.architecture_fingerprint
                == detail.architecture_design.fingerprint
                and state.proposal.requirements_fingerprint
                == detail.requirement_context.fingerprint
                and detail.architecture_review is not None
                and detail.architecture_review.fingerprint
                == row["architecture_review_fingerprint"]
                and detail.engineering_plan_review is not None
                and detail.engineering_plan_review.fingerprint
                == row["plan_review_fingerprint"]
            )
        except (ValueError, TypeError, AttributeError, OSError):
            return False

    def project(self, detail, row, *, is_head):
        self.validate_revision(row)
        current = is_head and self.sources_current(detail, row)
        state = HardwareSelectionState.model_validate_json(row["selection_body"])
        decision_fp = row.get("decision_fingerprint")
        status = row.get("status") or "REVIEW"
        if not is_head:
            with self.repo._connect() as db:
                event = db.execute(
                    "SELECT fingerprint,decision,state_body FROM hardware_selection_revision_decisions WHERE revision_id=? ORDER BY rowid DESC LIMIT 1",
                    (row["revision_id"],),
                ).fetchone()
            if event is not None:
                decision_fp, status = event[0], event[1]
        if decision_fp:
            with self.repo._connect() as db:
                event = db.execute(
                    "SELECT state_body,event_body FROM hardware_selection_revision_decisions WHERE fingerprint=? AND revision_id=?",
                    (decision_fp, row["revision_id"]),
                ).fetchone()
            if event is None:
                raise ProjectCatalogUnavailable("SELECTION_DECISION_MISSING")
            event_body = json.loads(event[1])
            if (
                digest(event_body) != decision_fp
                or event_body["decision"] != status
                or (
                    event_body["revision_id"],
                    event_body["selection_id"],
                    event_body["selection_fingerprint"],
                )
                != (
                    row["revision_id"],
                    row["selection_id"],
                    row["selection_fingerprint"],
                )
            ):
                raise ProjectCatalogUnavailable("SELECTION_DECISION_INTEGRITY_FAILED")
            state = (
                None
                if event[0] is None
                else HardwareSelectionState.model_validate_json(event[0])
            )
            if event_body["state_fingerprint"] != (
                None if state is None else digest(state.model_dump(mode="json"))
            ):
                raise ProjectCatalogUnavailable(
                    "SELECTION_DECISION_STATE_INTEGRITY_FAILED"
                )
        head = {
            "revision_id": row["revision_id"],
            "revision_number": row["revision_number"],
            "selection_id": row["selection_id"],
            "selection_fingerprint": row["selection_fingerprint"],
            "fingerprint": row["revision_fingerprint"],
            "status": status,
            "decision_fingerprint": decision_fp,
            "currentness": "CURRENT" if current else "STALE",
            "reason": None
            if current
            else "NON_HEAD"
            if not is_head
            else "SOURCE_OR_UPSTREAM_CHANGED",
        }
        return {
            "kind": "REVISION",
            "schema_version": 2,
            "head": head,
            "selection": None if state is None else state.model_dump(mode="json"),
            "source_publications": json.loads(row["source_bindings_body"]),
        }

    def view(
        self,
        project_id,
        conversation_id,
        *,
        tenant_id,
        requester,
        view="CURRENT",
        revision_id=None,
        schema_version=2,
        parent_choices=None,
    ):
        detail = self.detail(project_id, conversation_id, tenant_id, requester)
        if (
            view not in {"CURRENT", "PREVIEW", "HISTORY"}
            or (view != "HISTORY" and revision_id is not None)
            or (
                view != "PREVIEW"
                and (schema_version != 2 or parent_choices is not None)
            )
        ):
            raise ProjectCatalogConflict("SELECTION_VIEW_SELECTOR_INVALID")
        if view == "PREVIEW":
            return self.preview(detail, schema_version, parent_choices)
        head = self.head(detail)
        if view == "HISTORY":
            with self.repo._connect() as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    "SELECT r.*,h.status,h.decision_fingerprint FROM hardware_selection_revisions r LEFT JOIN hardware_selection_revision_heads h ON h.revision_id=r.revision_id WHERE r.tenant_id=? AND r.project_id=? AND r.conversation_id=?"
                    + (" AND r.revision_id=?" if revision_id else "")
                    + " ORDER BY r.revision_number",
                    self.scope(detail) + ((revision_id,) if revision_id else ()),
                ).fetchall()
            if revision_id and not rows:
                raise ProjectCatalogUnavailable("SELECTION_REVISION_NOT_FOUND")
            values = tuple(
                self.project(
                    detail,
                    dict(row),
                    is_head=head is not None
                    and row["revision_id"] == head["revision_id"],
                )
                for row in rows
            )
            return (
                values[0]
                if revision_id
                else {"kind": "HISTORY", "schema_version": 2, "revisions": values}
            )
        if head is None:
            return {
                "kind": "LEGACY",
                "schema_version": 2,
                "expected_head": self.expected(detail),
                "selection": None
                if detail.hardware_selection is None
                else detail.hardware_selection.model_dump(mode="json"),
                "currentness": "UNVERIFIED",
                "reason": "LEGACY_UNVERSIONED",
            }
        return self.project(detail, head, is_head=True)

    def check_publications(self, connection, detail, bindings):
        for item in bindings:
            row = connection.execute(
                "SELECT fingerprint FROM component_source_heads WHERE tenant_id=? AND project_id=? AND source_id=?",
                (detail.tenant_id, detail.project_id, item.source_id),
            ).fetchone()
            if row is None or row[0] != item.publication_fingerprint:
                raise ProjectCatalogConflict("SELECTION_SOURCE_HEAD_CHANGED")

    def archive_plan_anchor(self, connection, detail, raw):
        planning = detail.schematic_planning
        if planning is None:
            return
        plan = planning.approved_plan or planning.proposal
        head = self.repo._plan_revision_head_checked(
            connection,
            tenant_id=detail.tenant_id,
            project_id=detail.project_id,
            conversation_id=detail.conversation_id,
        )
        revision_id = None if head is None else head["revision_id"]
        if head is None:
            legacy = connection.execute(
                "SELECT plan_id,plan_fingerprint,conversation_body FROM schematic_plan_legacy_snapshots WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                self.scope(detail),
            ).fetchone()
            if legacy is None:
                connection.execute(
                    "INSERT INTO schematic_plan_legacy_snapshots(tenant_id,project_id,conversation_id,plan_id,plan_fingerprint,conversation_body) VALUES(?,?,?,?,?,?)",
                    self.scope(detail) + (plan.plan_id, plan.fingerprint, raw),
                )
            else:
                original = self.repo._conversation_body(legacy["conversation_body"])
                original_plan = (
                    None
                    if original.schematic_planning is None
                    else original.schematic_planning.approved_plan
                    or original.schematic_planning.proposal
                )
                if (
                    original_plan is None
                    or (legacy["plan_id"], legacy["plan_fingerprint"])
                    != (plan.plan_id, plan.fingerprint)
                    or (original_plan.plan_id, original_plan.fingerprint)
                    != (plan.plan_id, plan.fingerprint)
                ):
                    raise ProjectCatalogUnavailable(
                        "SELECTION_LEGACY_PLAN_ANCHOR_CHANGED"
                    )
        connection.execute(
            "INSERT INTO selection_schematic_plan_anchors(tenant_id,project_id,conversation_id,revision_id,plan_id,plan_fingerprint,planning_body,conversation_body) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,project_id,conversation_id) DO UPDATE SET revision_id=excluded.revision_id,plan_id=excluded.plan_id,plan_fingerprint=excluded.plan_fingerprint,planning_body=excluded.planning_body,conversation_body=excluded.conversation_body",
            self.scope(detail)
            + (
                revision_id,
                plan.plan_id,
                plan.fingerprint,
                planning.model_dump_json(),
                raw,
            ),
        )

    def create(
        self,
        project_id,
        conversation_id,
        *,
        tenant_id,
        requester,
        request_id,
        expected_head,
        preview_fingerprint,
        selections,
        schema_version=2,
        parent_choices=None,
    ):
        detail = self.detail(project_id, conversation_id, tenant_id, requester)
        if schema_version not in (2, 3) or type(schema_version) is not int:
            raise ProjectCatalogConflict("SELECTION_SCHEMA_VERSION_INVALID")
        if schema_version == 2 and parent_choices is not None:
            raise ProjectCatalogConflict("SELECTION_PARENT_CHOICES_INVALID")
        if schema_version == 3 and parent_choices is None:
            raise ProjectCatalogConflict("SELECTION_PARENT_CHOICES_REQUIRED")
        parents = (
            canonical_parent_choices(parent_choices)
            if parent_choices is not None
            else None
        )
        if type(selections) is not tuple or any(
            type(item) is not tuple or len(item) != 3 for item in selections
        ):
            raise ProjectCatalogConflict("SELECTION_INPUT_INVALID")
        expected_head = tuple(expected_head)
        from embedded_copilot.engineering_runtime.common import (
            validate_fingerprint,
            validate_identifier,
        )

        try:
            validate_identifier(request_id, field="request_id")
            validate_fingerprint(preview_fingerprint)
            if (
                len(expected_head) != 3
                or (expected_head[1] is None) != (expected_head[2] is None)
                or (expected_head[0] is not None and expected_head[1] is None)
            ):
                raise ValueError("selection head incomplete")
            for identity in expected_head[:2]:
                if identity is not None:
                    validate_identifier(identity, field="selection_head")
            if expected_head[2] is not None:
                validate_fingerprint(expected_head[2])
            if not selections or len({item[0] for item in selections}) != len(
                selections
            ):
                raise ValueError("selection slots repeated")
            for slot, source, fingerprint in selections:
                validate_identifier(slot, field="slot_id")
                validate_identifier(source, field="source_id")
                validate_fingerprint(fingerprint)
        except (TypeError, ValueError) as error:
            raise ProjectCatalogConflict("SELECTION_INPUT_INVALID") from error
        request_material = {
            "request_id": request_id,
            "expected_head": expected_head,
            "preview_fingerprint": preview_fingerprint,
            "selections": selections,
        }
        if schema_version == 3:
            request_material["schema_version"] = 3
            request_material["parent_choices"] = parents
        request_fp = digest(request_material)
        with self.repo._connect() as db:
            db.row_factory = sqlite3.Row
            prior = db.execute(
                "SELECT * FROM hardware_selection_revisions WHERE tenant_id=? AND project_id=? AND conversation_id=? AND request_id=?",
                self.scope(detail) + (request_id,),
            ).fetchone()
        if prior is not None:
            if prior["request_fingerprint"] != request_fp:
                raise ProjectCatalogConflict("SELECTION_REQUEST_CONFLICT")
            return self.view(
                project_id,
                conversation_id,
                tenant_id=tenant_id,
                requester=requester,
                view="HISTORY",
                revision_id=prior["revision_id"],
            )
        preview = self.preview(detail, schema_version, parents)
        if schema_version == 3:
            selected_roots = tuple(
                sorted(
                    (slot, source, fingerprint)
                    for slot, source, fingerprint in selections
                    if slot in preview["root_slots"]
                )
            )
            if selected_roots != parents:
                raise ProjectCatalogConflict("SELECTION_PARENT_CHOICES_MISMATCH")
        if (
            tuple(preview["expected_head"]) != expected_head
            or preview["preview_fingerprint"] != preview_fingerprint
        ):
            raise ProjectCatalogConflict("SELECTION_PREVIEW_STALE")
        shown = {
            (item["slot_id"], item["source_id"], item["source_fingerprint"])
            for item in preview["choices"]
        }
        if {item[0] for item in selections} != set(preview["required_slots"]) or any(
            item not in shown for item in selections
        ):
            raise ProjectCatalogConflict("SELECTION_PREVIEW_CHOICES_MISMATCH")
        proposal = self.proposal(detail, selections)
        proposal = type(proposal).create(
            **{
                **{
                    name: getattr(proposal, name)
                    for name in type(proposal).model_fields
                    if name != "fingerprint"
                },
                "proposal_id": "selection-" + uuid4().hex,
            }
        )
        state = HardwareSelectionState(
            proposal=proposal, status=HardwareSelectionStatus.REVIEW
        )
        bindings = self.publications(
            detail, (item.source for item in proposal.selected_components)
        )
        after = withdraw_selection_revision_projection(detail, hardware_selection=state)
        after = ConversationDetail.model_validate(
            {
                **after.model_dump(mode="python"),
                "updated_at": self.runtime._now(),
                "fingerprint": ZERO,
            }
        )
        revision_id = "selection-revision-" + uuid4().hex
        with self.repo._lock, self.repo._connect() as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            self.repo._circuit_owner(db, project_id, tenant_id, requester)
            concurrent = db.execute(
                "SELECT r.*,h.status,h.decision_fingerprint FROM hardware_selection_revisions r LEFT JOIN hardware_selection_revision_heads h ON h.revision_id=r.revision_id WHERE r.tenant_id=? AND r.project_id=? AND r.conversation_id=? AND r.request_id=?",
                self.scope(detail) + (request_id,),
            ).fetchone()
            if concurrent is not None:
                if concurrent["request_fingerprint"] != request_fp:
                    raise ProjectCatalogConflict("SELECTION_REQUEST_CONFLICT")
                return self.project(
                    detail,
                    dict(concurrent),
                    is_head=self.head(detail, db)["revision_id"]
                    == concurrent["revision_id"],
                )
            row = db.execute(
                "SELECT body FROM conversations WHERE tenant_id=? AND project_id=? AND conversation_id=? AND requester=?",
                self.scope(detail) + (requester,),
            ).fetchone()
            if (
                row is None
                or self.repo._conversation_body(row["body"]).fingerprint
                != detail.fingerprint
            ):
                raise ProjectCatalogConflict("SELECTION_CONVERSATION_CHANGED")
            head = self.head(detail, db)
            if self.expected(detail, head) != expected_head:
                raise ProjectCatalogConflict("SELECTION_HEAD_CHANGED")
            self.check_publications(
                db,
                detail,
                tuple(
                    SourcePublicationBindingV1.model_validate(item)
                    for item in preview["source_publications"]
                ),
            )
            self.archive_plan_anchor(db, detail, row["body"])
            number = 1 if head is None else head["revision_number"] + 1
            revision_fp = digest(
                {
                    "revision_id": revision_id,
                    "number": number,
                    "predecessor": expected_head,
                    "selection": state.model_dump(mode="json"),
                    "source_publications": tuple(
                        item.model_dump(mode="json") for item in bindings
                    ),
                }
            )
            db.execute(
                "INSERT INTO hardware_selection_revisions(tenant_id,project_id,conversation_id,revision_id,revision_number,predecessor_revision_id,predecessor_selection_id,predecessor_selection_fingerprint,request_id,request_fingerprint,selection_id,selection_fingerprint,selection_body,source_bindings_body,revision_fingerprint,predecessor_conversation_body,architecture_review_fingerprint,plan_review_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self.scope(detail)
                + (
                    revision_id,
                    number,
                    *expected_head,
                    request_id,
                    request_fp,
                    proposal.proposal_id,
                    proposal.fingerprint,
                    state.model_dump_json(),
                    encode(tuple(item.model_dump(mode="json") for item in bindings)),
                    revision_fp,
                    row["body"],
                    detail.architecture_review.fingerprint,
                    detail.engineering_plan_review.fingerprint,
                    self.runtime._now().isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO hardware_selection_revision_heads(tenant_id,project_id,conversation_id,revision_id,revision_number,status,decision_fingerprint) VALUES(?,?,?,?,?,'REVIEW',NULL) ON CONFLICT(tenant_id,project_id,conversation_id) DO UPDATE SET revision_id=excluded.revision_id,revision_number=excluded.revision_number,status=excluded.status,decision_fingerprint=NULL",
                self.scope(detail) + (revision_id, number),
            )
            db.execute(
                "UPDATE conversations SET body=?,updated_at=? WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                (
                    after.model_dump_json(),
                    after.updated_at.isoformat(),
                    *self.scope(detail),
                ),
            )
        return self.view(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )

    def decide(
        self,
        project_id,
        conversation_id,
        *,
        tenant_id,
        requester,
        revision_id,
        selection_id,
        selection_fingerprint,
        expected_decision_fingerprint,
        decision,
    ):
        detail = self.detail(project_id, conversation_id, tenant_id, requester)
        head = self.head(detail)
        if (
            head is None
            or (revision_id, selection_id, selection_fingerprint)
            != (
                head["revision_id"],
                head["selection_id"],
                head["selection_fingerprint"],
            )
            or head["decision_fingerprint"] != expected_decision_fingerprint
        ):
            raise ProjectCatalogConflict("SELECTION_DECISION_SELECTOR_STALE")
        if decision not in {"APPROVED", "REJECTED", "REVOKED"} or head["status"] != (
            "APPROVED" if decision == "REVOKED" else "REVIEW"
        ):
            raise ProjectCatalogConflict("SELECTION_DECISION_TRANSITION_INVALID")
        if decision != "REVOKED" and not self.sources_current(detail, head):
            raise ProjectCatalogConflict("SELECTION_SOURCE_OR_UPSTREAM_STALE")
        state = HardwareSelectionState.model_validate_json(head["selection_body"])
        if decision == "REVOKED":
            after = withdraw_selection_revision_projection(
                detail, hardware_selection=None
            )
            next_state = None
        else:
            service = HardwareSelectionService(self.runtime.hardware_component_source_for(detail))
            next_state = service.decide(
                state.proposal,
                HardwareSelectionDecision(decision),
                reviewer=requester,
                decided_at=self.runtime._now(),
            )
            truth = detail.design_truth
            if decision == "APPROVED":
                if truth is None:
                    truth = DesignTruthBuilder().build_from_approved_architecture(
                        detail.architecture_design,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        architecture_review=detail.architecture_review,
                    )
                truth = service.project_design_truth(
                    truth,
                    next_state.hardware_truth,
                    architecture_review=detail.architecture_review,
                )
            after = ConversationDetail.model_validate(
                {
                    **detail.model_dump(mode="python"),
                    "hardware_selection": next_state,
                    "design_truth": truth,
                    "engineering_knowledge_model": None
                    if truth is None
                    else generate_knowledge_model(truth),
                    "teaching_context": None,
                    "fingerprint": ZERO,
                }
            )
        after = ConversationDetail.model_validate(
            {
                **after.model_dump(mode="python"),
                "updated_at": self.runtime._now(),
                "fingerprint": ZERO,
            }
        )
        event = {
            "revision_id": revision_id,
            "selection_id": selection_id,
            "selection_fingerprint": selection_fingerprint,
            "previous_decision_fingerprint": expected_decision_fingerprint,
            "decision": decision,
            "reviewer": requester,
            "decided_at": self.runtime._now().isoformat(),
            "state_fingerprint": None
            if next_state is None
            else digest(next_state.model_dump(mode="json")),
        }
        event_fp = digest(event)
        with self.repo._lock, self.repo._connect() as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            self.repo._circuit_owner(db, project_id, tenant_id, requester)
            current = self.head(detail, db)
            row = db.execute(
                "SELECT body FROM conversations WHERE tenant_id=? AND project_id=? AND conversation_id=? AND requester=?",
                self.scope(detail) + (requester,),
            ).fetchone()
            if (
                current != head
                or row is None
                or self.repo._conversation_body(row["body"]).fingerprint
                != detail.fingerprint
            ):
                raise ProjectCatalogConflict("SELECTION_DECISION_CAS_CHANGED")
            if decision != "REVOKED":
                bindings = tuple(
                    SourcePublicationBindingV1.model_validate(item)
                    for item in json.loads(head["source_bindings_body"])
                )
                self.check_publications(db, detail, bindings)
            self.archive_plan_anchor(db, detail, row["body"])
            db.execute(
                "INSERT INTO hardware_selection_revision_decisions(event_id,revision_id,decision,fingerprint,previous_decision_fingerprint,state_body,predecessor_conversation_body,event_body) VALUES(?,?,?,?,?,?,?,?)",
                (
                    "selection-decision-" + uuid4().hex,
                    revision_id,
                    decision,
                    event_fp,
                    expected_decision_fingerprint,
                    None if next_state is None else next_state.model_dump_json(),
                    row["body"],
                    encode(event),
                ),
            )
            db.execute(
                "UPDATE hardware_selection_revision_heads SET status=?,decision_fingerprint=? WHERE tenant_id=? AND project_id=? AND conversation_id=? AND revision_id=?",
                (decision, event_fp, *self.scope(detail), revision_id),
            )
            db.execute(
                "UPDATE conversations SET body=?,updated_at=? WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                (
                    after.model_dump_json(),
                    after.updated_at.isoformat(),
                    *self.scope(detail),
                ),
            )
        return self.view(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
