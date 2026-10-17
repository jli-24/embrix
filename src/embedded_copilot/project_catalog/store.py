from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    CapabilityEvaluation,
    DesignTruth,
    EngineeringPlan,
    ExecutionAuthorization,
    ExecutionIntent,
    HardwareDesignSpecification,
    ImplementationSpecification,
    PlanReviewDecision,
    create_schematic_production_state,
    evaluate_authorization,
    evaluate_capabilities,
    evaluate_schematic_readiness,
    generate_execution_intent,
    generate_execution_requests,
    generate_schematic_production_request,
    generate_schematic_proposal,
    is_schematic_generation_request,
)
from embedded_copilot.engineering_runtime.common import canonical_fingerprint
from embedded_copilot.engineering_runtime.evidence import EvidenceReceiptReference
from embedded_copilot.engineering_runtime.objects.models import CircuitIntentObject
from embedded_copilot.engineering_runtime.proposals import (
    ApprovalRecord,
    ObjectMutationRecord,
    ObjectProposal,
    ProposalStatus,
)

from .brownfield_build import BrownfieldBuildContext
from .exceptions import (
    ProjectCatalogConflict,
    ProjectCatalogExpired,
    ProjectCatalogForbidden,
    ProjectCatalogRejected,
    ProjectCatalogUnavailable,
)
from .migration import migrate_conversation_payload
from .source_publication import ComponentSourcePublicationV1
from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord
from embedded_copilot.engineering_runtime.common import validate_fingerprint, validate_identifier
from .models import (
    CircuitProposalCandidate,
    CircuitPublication,
    ConversationDetail,
    LegacyProjectCreationPreview,
    LegacyProjectView,
    PredesignCircuitCandidate,
    ProjectSupplyStateV2,
    ProjectSupplyStateV3,
    ProjectContextView,
    ProjectCreationMode,
    ProjectCreationPreview,
    ProjectLegacyMode,
    ProjectLocationView,
    ProjectType,
    ProjectView,
    SchematicVerificationRecord,
    circuit_source_fingerprint,
    predesign_source_snapshot,
)


class SQLiteProjectCatalogRepository:
    def __init__(self, database: Path) -> None:
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _conversation_body(body: str) -> ConversationDetail:
        original = json.loads(body)
        legacy_plan = isinstance(original.get("engineering_plan"), dict) and original["engineering_plan"].get("model_operation_schema_version", 1) == 1
        if legacy_plan:
            # Verify the stored historical bytes before migration fills fields.
            # Full ConversationDetail validation here would reject older rows
            # that legitimately need the existing migration path.
            plan = EngineeringPlan.model_validate(original["engineering_plan"], strict=False)
            review_body = original.get("engineering_plan_review")
            if review_body is not None:
                review = PlanReviewDecision.model_validate(review_body, strict=False)
                if review.plan_id != plan.plan_id or review.proposal_fingerprint != plan.content_fingerprint:
                    raise ValueError("legacy plan review fingerprint mismatch")
            canonical_body = json.dumps(
                {key: value for key, value in original.items() if key != "fingerprint"},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            if original.get("fingerprint") != "sha256:" + hashlib.sha256(canonical_body).hexdigest():
                raise ValueError("legacy conversation fingerprint mismatch")
        material = migrate_conversation_payload(original)
        for turn in material.get("turns", ()):
            if "analysis_state" not in turn:
                if turn.get("status") == "COMPLETED":
                    state = "COMPLETED"
                elif (
                    turn.get("status") == "FAILED"
                    and not turn.get("error_code")
                    and not turn.get("analysis_reason_code")
                    and turn.get("route", "UNKNOWN") == "UNKNOWN"
                ):
                    state = "WAITING_FOR_CLARIFICATION"
                    turn["status"] = "WAITING_FOR_CLARIFICATION"
                else:
                    state = "FAILED" if turn.get("status") == "FAILED" else "REQUESTED"
                turn["analysis_state"] = state
            turn.setdefault("analysis_reason_code", turn.get("error_code"))
            if turn.get("analysis_state") == "WAITING_FOR_CLARIFICATION":
                turn["status"] = "WAITING_FOR_CLARIFICATION"
                turn["error_code"] = None
                turn.setdefault("analysis_reason_code", "CLARIFICATION_REQUIRED")
                turn.setdefault("terminal_event", "ANSWER_FAILED")
            turn["fingerprint"] = "sha256:" + "0" * 64
        if (
            material.get("implementation_spec") is not None
            and "capability_evaluation" not in material
            and "execution_intents" not in material
        ):
            implementation_spec = ImplementationSpecification.model_validate(
                material["implementation_spec"], strict=False
            )
            material["capability_evaluation"] = evaluate_capabilities(
                implementation_spec
            ).model_dump(mode="python")
            material["execution_intents"] = [
                generate_execution_intent(task).model_dump(mode="python")
                for task in implementation_spec.tasks
            ]
        if (
            material.get("capability_evaluation") is not None
            and material.get("execution_intents")
            and "execution_authorization" not in material
        ):
            capability_evaluation = CapabilityEvaluation.model_validate(
                material["capability_evaluation"], strict=False
            )
            execution_intents = tuple(
                ExecutionIntent.model_validate(item, strict=False)
                for item in material["execution_intents"]
            )
            execution_requests = generate_execution_requests(
                capability_evaluation,
                execution_intents,
                conversation_id=material["conversation_id"],
                requested_by=material["requester"],
            )
            material["execution_authorization"] = evaluate_authorization(
                capability_evaluation,
                execution_intents,
                execution_requests,
            ).model_dump(mode="python")
        if (
            "schematic_production" not in material
            and material.get("design_truth") is not None
            and material.get("hardware_design_spec") is not None
            and material.get("implementation_spec") is not None
            and material.get("execution_authorization") is not None
            and material.get("execution_intents")
        ):
            try:
                command = next(
                    (turn.get("question") for turn in reversed(material.get("turns", ())) if is_schematic_generation_request(turn.get("question"))),
                    None,
                )
                if command is not None:
                    readiness = evaluate_schematic_readiness(
                        DesignTruth.model_validate(material["design_truth"], strict=False),
                        HardwareDesignSpecification.model_validate(material["hardware_design_spec"], strict=False),
                        ImplementationSpecification.model_validate(material["implementation_spec"], strict=False),
                    )
                    if readiness.status.value == "READY":
                        intents = tuple(ExecutionIntent.model_validate(item, strict=False) for item in material["execution_intents"])
                        authorization = ExecutionAuthorization.model_validate(material["execution_authorization"], strict=False)
                        request = generate_schematic_production_request(
                            design_truth=DesignTruth.model_validate(material["design_truth"], strict=False),
                            hardware_design=HardwareDesignSpecification.model_validate(material["hardware_design_spec"], strict=False),
                            implementation=ImplementationSpecification.model_validate(material["implementation_spec"], strict=False),
                            authorization=authorization,
                            readiness=readiness,
                            execution_intents=intents,
                            conversation_id=material["conversation_id"],
                            requester=material["requester"],
                        )
                        proposal = generate_schematic_proposal(request, DesignTruth.model_validate(material["design_truth"], strict=False))
                        material["schematic_production"] = create_schematic_production_state(request, proposal).model_dump(mode="python")
            except (KeyError, TypeError, ValueError):
                pass
        if not legacy_plan:
            material["fingerprint"] = "sha256:" + "0" * 64
        return ConversationDetail.model_validate(material)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_previews (
                    preview_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    body TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    location_id TEXT,
                    directory_name TEXT,
                    creation_mode TEXT,
                    body TEXT NOT NULL,
                    UNIQUE (tenant_id, relative_path)
                );
                CREATE TABLE IF NOT EXISTS project_contexts (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS brownfield_build_contexts (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
                    tenant_id TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    tenant_id TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id)
                );
                CREATE TABLE IF NOT EXISTS project_audit (
                    audit_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    project_fingerprint TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_owners (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
                    requester TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_circuit_proposals (
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('CREATED', 'REJECTED', 'APPLIED')),
                    conversation_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    base_revision TEXT,
                    base_fingerprint TEXT,
                    context_fingerprint TEXT NOT NULL,
                    conversation_fingerprint TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    constraints_body TEXT NOT NULL,
                    route TEXT NOT NULL,
                    evidence_receipt_id TEXT NOT NULL,
                    evidence_receipt_fingerprint TEXT NOT NULL,
                    proposal_fingerprint TEXT NOT NULL,
                    proposal_body TEXT NOT NULL,
                    evidence_reference_body TEXT NOT NULL,
                    decision_body TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, proposal_id),
                    UNIQUE (project_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS project_circuit_intents (
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'SUPERSEDED')),
                    object_fingerprint TEXT NOT NULL,
                    base_revision TEXT,
                    base_fingerprint TEXT,
                    proposal_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    proposal_fingerprint TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    approval_fingerprint TEXT NOT NULL,
                    apply_id TEXT NOT NULL,
                    apply_fingerprint TEXT NOT NULL,
                    circuit_body TEXT NOT NULL,
                    proposal_body TEXT NOT NULL,
                    approval_body TEXT NOT NULL,
                    apply_body TEXT NOT NULL,
                    PRIMARY KEY (project_id, object_id, revision),
                    UNIQUE (project_id, proposal_id),
                    UNIQUE (project_id, idempotency_key),
                    UNIQUE (project_id, apply_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS project_circuit_one_active
                    ON project_circuit_intents(project_id, object_id) WHERE status = 'ACTIVE';
                CREATE INDEX IF NOT EXISTS project_circuit_by_project
                    ON project_circuit_intents(project_id, status);
                CREATE TABLE IF NOT EXISTS project_predesign_circuit_proposals (
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (project_id, proposal_id),
                    UNIQUE (project_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS project_schematic_verifications (
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    verification_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (project_id, verification_id)
                );
                CREATE TABLE IF NOT EXISTS project_schematic_evidence_v1 (
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY(project_id, kind, evidence_id)
                );
                """
            )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS component_source_publications (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    publisher_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    publication_id TEXT NOT NULL,
                    generation INTEGER NOT NULL CHECK(generation > 0),
                    fingerprint TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, project_id, source_id, generation),
                    UNIQUE(tenant_id, project_id, publisher_id, source_id, request_id),
                    UNIQUE(publication_id)
                );
                CREATE TABLE IF NOT EXISTS hardware_selection_revisions (
                    tenant_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    revision_id TEXT PRIMARY KEY,revision_number INTEGER NOT NULL,
                    predecessor_revision_id TEXT,predecessor_selection_id TEXT,predecessor_selection_fingerprint TEXT,
                    request_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,
                    selection_id TEXT NOT NULL UNIQUE,selection_fingerprint TEXT NOT NULL,
                    selection_body TEXT NOT NULL,source_bindings_body TEXT NOT NULL,
                    revision_fingerprint TEXT NOT NULL,predecessor_conversation_body TEXT NOT NULL,
                    architecture_review_fingerprint TEXT NOT NULL,plan_review_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tenant_id,project_id,conversation_id,revision_number),
                    UNIQUE(tenant_id,project_id,conversation_id,request_id)
                );
                CREATE TABLE IF NOT EXISTS hardware_selection_revision_heads (
                    tenant_id TEXT NOT NULL,project_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL REFERENCES hardware_selection_revisions(revision_id) ON DELETE CASCADE,
                    revision_number INTEGER NOT NULL,status TEXT NOT NULL,decision_fingerprint TEXT,
                    PRIMARY KEY(tenant_id,project_id,conversation_id)
                );
                CREATE TABLE IF NOT EXISTS hardware_selection_revision_decisions (
                    event_id TEXT PRIMARY KEY,revision_id TEXT NOT NULL REFERENCES hardware_selection_revisions(revision_id) ON DELETE CASCADE,
                    decision TEXT NOT NULL,fingerprint TEXT NOT NULL UNIQUE,previous_decision_fingerprint TEXT,
                    state_body TEXT,predecessor_conversation_body TEXT NOT NULL,event_body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS selection_schematic_plan_anchors (
                    tenant_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    revision_id TEXT,plan_id TEXT NOT NULL,plan_fingerprint TEXT NOT NULL,
                    planning_body TEXT NOT NULL,conversation_body TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,project_id,conversation_id)
                );
                CREATE TABLE IF NOT EXISTS schematic_plan_revisions (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    revision_id TEXT PRIMARY KEY,
                    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
                    predecessor_revision_id TEXT,
                    request_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    plan_id TEXT NOT NULL UNIQUE,
                    plan_fingerprint TEXT NOT NULL,
                    planning_body TEXT NOT NULL,
                    base_truth_body TEXT NOT NULL,
                    predecessor_conversation_body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tenant_id,project_id,conversation_id,revision_number),
                    UNIQUE(tenant_id,project_id,conversation_id,request_id)
                );
                CREATE TABLE IF NOT EXISTS schematic_plan_revision_heads (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    revision_id TEXT NOT NULL REFERENCES schematic_plan_revisions(revision_id),
                    revision_number INTEGER NOT NULL,
                    PRIMARY KEY(tenant_id,project_id,conversation_id)
                );
                CREATE TABLE IF NOT EXISTS schematic_plan_revision_decisions (
                    revision_id TEXT PRIMARY KEY REFERENCES schematic_plan_revisions(revision_id),
                    decision TEXT NOT NULL CHECK(decision IN ('APPROVED','REJECTED')),
                    planning_body TEXT NOT NULL,
                    predecessor_conversation_body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schematic_legacy_owner_snapshots (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL,
                    candidate_fingerprint TEXT NOT NULL,
                    conversation_body TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,project_id,conversation_id)
                );
                CREATE TABLE IF NOT EXISTS schematic_plan_legacy_snapshots (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    plan_id TEXT NOT NULL,
                    plan_fingerprint TEXT NOT NULL,
                    conversation_body TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,project_id,conversation_id)
                );
                CREATE TABLE IF NOT EXISTS component_source_heads (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL,
                    publisher_id TEXT NOT NULL,
                    publication_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, project_id, source_id)
                );
                """
            )
            verification_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(project_schematic_verifications)")
            }
            if "conversation_id" not in verification_columns:
                connection.execute("ALTER TABLE project_schematic_verifications ADD COLUMN conversation_id TEXT")
                for source_project_id, verification_id, body in connection.execute(
                    "SELECT project_id, verification_id, body FROM project_schematic_verifications"
                ).fetchall():
                    record = SchematicVerificationRecord.model_validate_json(body, strict=False)
                    connection.execute(
                        "UPDATE project_schematic_verifications SET conversation_id=? WHERE project_id=? AND verification_id=?",
                        (record.conversation_id, source_project_id, verification_id),
                    )
            self._migrate_conversations(connection)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
            for name in ("location_id", "directory_name", "creation_mode"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE projects ADD COLUMN {name} TEXT")
            rows = connection.execute("SELECT project_id, body FROM projects").fetchall()
            for project_id, body in rows:
                try:
                    ProjectView.model_validate_json(body)
                    continue
                except ValidationError:
                    legacy = LegacyProjectView.model_validate_json(body)
                directory_name = Path(legacy.relative_path).name
                creation_mode = (
                    ProjectCreationMode.ATTACH_EXISTING
                    if legacy.project_type is ProjectType.EXISTING_FIRMWARE
                    else ProjectCreationMode.CREATE_NEW
                )
                migrated = ProjectView(
                    project_id=legacy.project_id,
                    tenant_id=legacy.tenant_id,
                    name=legacy.name,
                    location_id="default-project-root",
                    directory_name=directory_name,
                    creation_mode=creation_mode,
                    relative_path=legacy.relative_path,
                    project_type=legacy.project_type,
                    initialization_status=legacy.initialization_status,
                    platform=legacy.platform,
                    version=legacy.version,
                    branch=legacy.branch,
                    failure_code=legacy.failure_code,
                    created_at=legacy.created_at,
                    updated_at=legacy.updated_at,
                    last_activity_at=legacy.last_activity_at,
                )
                connection.execute(
                    "UPDATE projects SET location_id=?, directory_name=?, creation_mode=?, body=? WHERE project_id=?",
                    (migrated.location_id, migrated.directory_name, migrated.creation_mode.value, migrated.model_dump_json(), project_id),
                )
            self._initialize_eda_imports(connection)
            self._initialize_eda_native_moves(connection)

    @staticmethod
    def _initialize_eda_imports(connection: sqlite3.Connection) -> None:
        # Keep this additive migration atomic on both empty and old Catalogs.
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS project_eda_import_previews (
                preview_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL,
                requester TEXT NOT NULL,
                backend TEXT NOT NULL,
                native_entry_relative_path TEXT NOT NULL,
                preview_fingerprint TEXT NOT NULL,
                expected_head_revision_id TEXT,
                expected_head_revision_fingerprint TEXT,
                project_binding_fingerprint TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('OPEN', 'COMMITTED')),
                committed_revision_id TEXT,
                body TEXT NOT NULL,
                CHECK ((status='OPEN' AND committed_revision_id IS NULL) OR
                       (status='COMMITTED' AND committed_revision_id IS NOT NULL))
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS project_eda_import_revisions (
                import_revision_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL,
                backend TEXT NOT NULL,
                native_entry_relative_path TEXT NOT NULL,
                import_revision_fingerprint TEXT NOT NULL,
                source_set_fingerprint TEXT NOT NULL,
                parser_ruleset_id TEXT NOT NULL,
                supported_subset_id TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                created_at TEXT NOT NULL,
                body TEXT NOT NULL
            )"""
        )
        expected_columns = {
            "project_eda_import_previews": {
                "preview_id", "project_id", "tenant_id", "requester", "backend",
                "native_entry_relative_path", "preview_fingerprint", "expected_head_revision_id",
                "expected_head_revision_fingerprint", "project_binding_fingerprint",
                "expires_at", "status", "committed_revision_id", "body",
            },
            "project_eda_import_revisions": {
                "import_revision_id", "project_id", "tenant_id", "backend",
                "native_entry_relative_path", "import_revision_fingerprint",
                "source_set_fingerprint", "parser_ruleset_id", "supported_subset_id",
                "active", "created_at", "body",
            },
        }
        for table, expected in expected_columns.items():
            actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if actual != expected:
                raise ProjectCatalogUnavailable("EDA import schema is incompatible")
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS project_eda_import_one_active
                ON project_eda_import_revisions(project_id,backend,native_entry_relative_path)
                WHERE active=1"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS project_eda_import_history
                ON project_eda_import_revisions(project_id,backend,native_entry_relative_path,created_at,import_revision_id)"""
        )

    @staticmethod
    def _initialize_eda_native_moves(connection: sqlite3.Connection) -> None:
        # Additive records; C1 revisions and historical operation rows are untouched.
        connection.execute(
            """CREATE TABLE IF NOT EXISTS project_eda_native_move_candidates (
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                candidate_fingerprint TEXT NOT NULL,
                import_revision_id TEXT NOT NULL,
                import_revision_fingerprint TEXT NOT NULL,
                requester TEXT NOT NULL,
                created_at TEXT NOT NULL,
                body TEXT NOT NULL,
                PRIMARY KEY (project_id, candidate_id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS project_eda_native_mapping_reviews (
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL,
                mapping_review_id TEXT NOT NULL,
                mapping_review_fingerprint TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                candidate_fingerprint TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                decision TEXT NOT NULL CHECK (decision IN ('ACCEPT_MAPPING','REJECT_MAPPING')),
                created_at TEXT NOT NULL,
                body TEXT NOT NULL,
                PRIMARY KEY (project_id, mapping_review_id),
                UNIQUE (project_id, candidate_id),
                FOREIGN KEY (project_id, candidate_id)
                    REFERENCES project_eda_native_move_candidates(project_id, candidate_id) ON DELETE CASCADE
            )"""
        )
        expected_columns = {
            "project_eda_native_move_candidates": {
                "project_id", "tenant_id", "candidate_id", "candidate_fingerprint",
                "import_revision_id", "import_revision_fingerprint", "requester", "created_at", "body",
            },
            "project_eda_native_mapping_reviews": {
                "project_id", "tenant_id", "mapping_review_id", "mapping_review_fingerprint",
                "candidate_id", "candidate_fingerprint", "reviewer_id", "decision", "created_at", "body",
            },
        }
        for table, expected in expected_columns.items():
            actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if actual != expected:
                raise ProjectCatalogUnavailable("EDA native move schema is incompatible")

    @staticmethod
    def _eda_native_move_fingerprint(binding: object) -> str:
        if not isinstance(binding, dict):
            raise ProjectCatalogRejected("NATIVE_MOVE_RECORD_INVALID")
        try:
            encoded = json.dumps(
                binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ProjectCatalogRejected("NATIVE_MOVE_RECORD_INVALID") from error
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _eda_native_move_record(cls, row: sqlite3.Row, *, review: bool) -> dict:
        try:
            body = json.loads(row["body"])
            id_field = "mapping_review_id" if review else "candidate_id"
            fp_field = "mapping_review_fingerprint" if review else "candidate_fingerprint"
            fields = (
                ("project_id", "tenant_id", id_field, fp_field, "candidate_id", "candidate_fingerprint",
                 "reviewer_id", "decision", "created_at")
                if review else
                ("project_id", "tenant_id", id_field, fp_field, "import_revision_id",
                 "import_revision_fingerprint", "requester", "created_at")
            )
            if not isinstance(body, dict) or any(body.get(name) != row[name] for name in fields):
                raise ValueError("native move record binding")
            if body[fp_field] != cls._eda_native_move_fingerprint(body["binding"]):
                raise ValueError("native move fingerprint")
            binding = body["binding"]
            bound_names = ("project_id", "tenant_id", "candidate_id", "candidate_fingerprint",
                           "reviewer_id", "decision") if review else (
                "project_id", "tenant_id", "requester", "import_revision_id", "import_revision_fingerprint"
            )
            if any(binding.get(name) != body[name] for name in bound_names):
                raise ValueError("native move payload binding")
            return body
        except (KeyError, TypeError, ValueError, ProjectCatalogRejected) as error:
            raise ProjectCatalogUnavailable("EDA native move record is invalid") from error

    @staticmethod
    def _migrate_conversations(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT conversation_id, body FROM conversations"
        ).fetchall()
        for conversation_id, body in rows:
            material = json.loads(body)
            migrated = migrate_conversation_payload(material)
            if migrated == material:
                continue
            connection.execute(
                "UPDATE conversations SET body = ? WHERE conversation_id = ?",
                (json.dumps(migrated, ensure_ascii=False, separators=(",", ":")), conversation_id),
            )

    def save_preview(self, value: ProjectCreationPreview) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO project_previews(preview_id, tenant_id, requester, body, fingerprint) VALUES(?,?,?,?,?)",
                (value.preview_id, value.tenant_id, value.requester, value.model_dump_json(), value.fingerprint),
            )

    def preview(self, preview_id: str) -> tuple[ProjectCreationPreview, bool, str] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body, consumed, fingerprint FROM project_previews WHERE preview_id = ?", (preview_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            value = ProjectCreationPreview.model_validate_json(row[0])
        except ValidationError:
            legacy = LegacyProjectCreationPreview.model_validate_json(row[0])
            creation_mode = ProjectCreationMode.CREATE_NEW if legacy.mode is ProjectLegacyMode.CREATE else ProjectCreationMode.ATTACH_EXISTING
            value = ProjectCreationPreview(
                preview_id=legacy.preview_id,
                tenant_id=legacy.tenant_id,
                requester=legacy.requester,
                name=legacy.name,
                location=ProjectLocationView(location_id="default-project-root", display_label="Embedded Projects", availability="AVAILABLE"),
                directory_name=Path(legacy.relative_path).name,
                creation_mode=creation_mode,
                relative_path=legacy.relative_path,
                project_type=legacy.project_type,
                mode=legacy.mode,
                created_at=legacy.created_at,
                expires_at=legacy.expires_at,
                write_summary=legacy.write_summary,
            )
        return value, bool(row[1]), row[2]

    def consume_preview(self, preview_id: str, fingerprint: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE project_previews SET consumed = 1 WHERE preview_id = ? AND fingerprint = ? AND consumed = 0",
                (preview_id, fingerprint),
            )
            if cursor.rowcount != 1:
                raise ProjectCatalogConflict("project preview was already consumed")

    def save_project(self, value: ProjectView) -> None:
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    "INSERT INTO projects(project_id, tenant_id, relative_path, location_id, directory_name, creation_mode, body) VALUES(?,?,?,?,?,?,?)",
                    (value.project_id, value.tenant_id, value.relative_path, value.location_id, value.directory_name, value.creation_mode.value, value.model_dump_json()),
                )
        except sqlite3.IntegrityError as error:
            raise ProjectCatalogConflict("project already exists") from error

    def commit_project(self, preview_id: str, preview_fingerprint: str, value: ProjectView, *, requester: str) -> None:
        try:
            with self._lock, self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE project_previews SET consumed = 1 WHERE preview_id = ? AND fingerprint = ? AND consumed = 0",
                    (preview_id, preview_fingerprint),
                )
                if cursor.rowcount != 1:
                    raise ProjectCatalogConflict("project preview was already consumed")
                connection.execute(
                    "INSERT INTO projects(project_id, tenant_id, relative_path, location_id, directory_name, creation_mode, body) VALUES(?,?,?,?,?,?,?)",
                    (value.project_id, value.tenant_id, value.relative_path, value.location_id, value.directory_name, value.creation_mode.value, value.model_dump_json()),
                )
                connection.execute(
                    "INSERT INTO project_owners(project_id, requester) VALUES(?,?)",
                    (value.project_id, requester),
                )
        except sqlite3.IntegrityError as error:
            raise ProjectCatalogConflict("project already exists") from error

    def update_project(self, value: ProjectView) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE projects SET location_id=?, directory_name=?, creation_mode=?, body=? WHERE project_id=? AND tenant_id=?",
                (value.location_id, value.directory_name, value.creation_mode.value, value.model_dump_json(), value.project_id, value.tenant_id),
            )
            if cursor.rowcount != 1:
                raise ProjectCatalogUnavailable("project update failed")

    def project(self, project_id: str) -> ProjectView | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT body FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        return None if row is None else ProjectView.model_validate_json(row[0])

    def project_owner(self, project_id: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT requester FROM project_owners WHERE project_id = ?", (project_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    @staticmethod
    def _eda_binding_fingerprint(
        connection: sqlite3.Connection, project_id: str, tenant_id: str, requester: str,
    ) -> str:
        SQLiteProjectCatalogRepository._circuit_owner(connection, project_id, tenant_id, requester)
        row = connection.execute(
            "SELECT location_id,relative_path FROM projects WHERE project_id=?", (project_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise ProjectCatalogUnavailable("EDA import project binding is unavailable")
        material = {
            "schema": "eda-import-project-binding-v1",
            "project_id": project_id,
            "tenant_id": tenant_id,
            "location_id": row[0],
            "relative_path": row[1],
            "requester": requester,
        }
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def eda_import_project_binding_fingerprint(
        self, project_id: str, *, tenant_id: str, requester: str,
    ) -> str:
        with self._lock, self._connect() as connection:
            return self._eda_binding_fingerprint(connection, project_id, tenant_id, requester)

    @staticmethod
    def _eda_entry(entry: object) -> str:
        if not isinstance(entry, str) or not entry or entry.startswith("/") or "\\" in entry or ":" in entry or "\x00" in entry:
            raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID")
        if entry != unicodedata.normalize("NFC", entry) or any(
            part in ("", ".", "..") for part in entry.split("/")
        ):
            raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID")
        return entry

    @staticmethod
    def _eda_coverage(revision: dict) -> None:
        if "project_coverage" not in revision:
            return
        from embedded_copilot.engineering_eda.kicad_project_coverage_models import (
            ProjectCoverageV1,
        )
        raw = json.dumps(revision["project_coverage"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > 32 * 1024 * 1024:
            raise ValueError("COVERAGE_SIZE_INVALID")
        coverage = ProjectCoverageV1.model_validate_json(raw)
        if revision.get("backend") != "KICAD" or coverage.source_set_fingerprint != revision.get("source_set_fingerprint") or coverage.entry_files.project != revision.get("native_entry_relative_path"):
            raise ValueError("COVERAGE_BINDING_INVALID")
        actual = {(f.relative_path, f.byte_size, f.sha256) for f in coverage.files}
        supplied = [i for i in revision["manifest"] if i["kind"] == "FILE"]
        expected = {(i["relative_path"], i["byte_size"], i["sha256"]) for i in supplied}
        if actual != expected or len(expected) != len(supplied):
            raise ValueError("COVERAGE_MANIFEST_INVALID")
        source = {"schema": "eda-source-set-v1", "scope_policy_id": revision["scope_policy_id"],
            "native_entry_relative_path": revision["native_entry_relative_path"],
            "scope_root_relative_path": revision["scope_root_relative_path"], "manifest": revision["manifest"]}
        source_digest = "sha256:" + hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        if coverage.source_set_fingerprint != source_digest or revision.get("verified") is not False or revision.get("can_modify") is not False:
            raise ValueError("COVERAGE_SOURCE_SET_INVALID")
        material = {k: v for k, v in revision.items() if k != "import_revision_fingerprint"}
        digest = "sha256:" + hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        if revision.get("import_revision_fingerprint") != digest:
            raise ValueError("COVERAGE_REVISION_INVALID")

    @staticmethod
    def _eda_revision(row: sqlite3.Row) -> dict:
        try:
            body = json.loads(row["body"])
            if not isinstance(body, dict):
                raise ValueError("revision body")
            SQLiteProjectCatalogRepository._eda_coverage(body)
        except (TypeError, ValueError) as error:
            raise ProjectCatalogUnavailable("EDA import revision is invalid") from error
        if body.get("import_revision_fingerprint") != row["import_revision_fingerprint"]:
            raise ProjectCatalogUnavailable("EDA import revision fingerprint is invalid")
        return {
            **body,
            "import_revision_id": row["import_revision_id"],
            "project_id": row["project_id"],
            "tenant_id": row["tenant_id"],
            "backend": row["backend"],
            "native_entry_relative_path": row["native_entry_relative_path"],
            "created_at": row["created_at"],
            "active": bool(row["active"]),
        }

    def save_eda_import_preview(self, candidate: dict) -> None:
        try:
            project_id = candidate["project_id"]
            tenant_id = candidate["tenant_id"]
            requester = candidate["requester"]
        except (KeyError, TypeError) as error:
            raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID") from error
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = self._eda_binding_fingerprint(connection, project_id, tenant_id, requester)
            try:
                revision = candidate["revision"]
                entry = self._eda_entry(candidate["native_entry_relative_path"])
                if (
                    not isinstance(revision, dict)
                    or candidate["project_binding_fingerprint"] != binding
                    or not candidate["preview_id"]
                    or not candidate["preview_fingerprint"]
                    or not candidate["backend"]
                    or not revision["import_revision_fingerprint"]
                    or not revision["source_set_fingerprint"]
                    or not revision["parser_ruleset_id"]
                    or not revision["supported_subset_id"]
                    or revision.get("verified") is not False
                    or revision.get("can_modify") is not False
                    or (candidate["expected_head_revision_id"] is None)
                    != (candidate["expected_head_revision_fingerprint"] is None)
                ):
                    raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID")
                self._eda_coverage(revision)
                expires_at = datetime.fromisoformat(candidate["expires_at"])
                created_at = datetime.fromisoformat(candidate["created_at"])
                if expires_at.tzinfo is None or created_at.tzinfo is None or expires_at <= created_at:
                    raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID")
                body = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (KeyError, TypeError, ValueError) as error:
                raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID") from error
            try:
                connection.execute(
                    """INSERT INTO project_eda_import_previews(
                        preview_id,project_id,tenant_id,requester,backend,native_entry_relative_path,
                        preview_fingerprint,expected_head_revision_id,expected_head_revision_fingerprint,
                        project_binding_fingerprint,expires_at,status,committed_revision_id,body
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'OPEN',NULL,?)""",
                    (candidate["preview_id"], project_id, tenant_id, requester,
                     candidate["backend"], entry, candidate["preview_fingerprint"],
                     candidate["expected_head_revision_id"], candidate["expected_head_revision_fingerprint"],
                     binding, candidate["expires_at"], body),
                )
            except sqlite3.IntegrityError as error:
                raise ProjectCatalogConflict("IMPORT_PREVIEW_ALREADY_EXISTS") from error

    def eda_import_preview(
        self, project_id: str, preview_id: str, *, tenant_id: str, requester: str,
    ) -> dict | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM project_eda_import_previews WHERE project_id=? AND preview_id=?",
                (project_id, preview_id),
            ).fetchone()
        if row is None:
            return None
        if row["tenant_id"] != tenant_id or row["requester"] != requester:
            raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID")
        try:
            body = json.loads(row["body"])
            if not isinstance(body, dict) or body.get("preview_fingerprint") != row["preview_fingerprint"]:
                raise ValueError("preview body")
            self._eda_coverage(body["revision"])
        except (TypeError, ValueError) as error:
            raise ProjectCatalogUnavailable("EDA import preview is invalid") from error
        return {**body, "status": row["status"], "committed_revision_id": row["committed_revision_id"]}

    def commit_eda_import_preview(
        self, project_id: str, preview_id: str, preview_fingerprint: str, *,
        tenant_id: str, requester: str, now: datetime, current_project_fingerprint: str,
    ) -> tuple[dict, str]:
        if now.tzinfo is None:
            raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID")
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            binding = self._eda_binding_fingerprint(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM project_eda_import_previews WHERE project_id=? AND preview_id=?",
                (project_id, preview_id),
            ).fetchone()
            if row is None or row["tenant_id"] != tenant_id or row["requester"] != requester or row["preview_fingerprint"] != preview_fingerprint:
                raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID")
            if row["status"] == "COMMITTED":
                committed = connection.execute(
                    "SELECT * FROM project_eda_import_revisions WHERE project_id=? AND import_revision_id=?",
                    (project_id, row["committed_revision_id"]),
                ).fetchone()
                if committed is None:
                    raise ProjectCatalogUnavailable("EDA import committed revision is unavailable")
                if (
                    committed["tenant_id"] != tenant_id
                    or committed["backend"] != row["backend"]
                    or committed["native_entry_relative_path"] != row["native_entry_relative_path"]
                ):
                    raise ProjectCatalogUnavailable("EDA import committed revision binding is invalid")
                return self._eda_revision(committed), "ALREADY_COMMITTED"
            if row["status"] != "OPEN":
                raise ProjectCatalogUnavailable("EDA import preview status is invalid")
            try:
                candidate = json.loads(row["body"])
                revision = candidate["revision"]
                self._eda_coverage(revision)
                if (
                    candidate["preview_fingerprint"] != row["preview_fingerprint"]
                    or candidate["project_binding_fingerprint"] != row["project_binding_fingerprint"]
                    or candidate["native_entry_relative_path"] != row["native_entry_relative_path"]
                    or candidate["backend"] != row["backend"]
                ):
                    raise ValueError("preview binding")
            except (TypeError, ValueError, KeyError) as error:
                raise ProjectCatalogUnavailable("EDA import preview is invalid") from error
            if datetime.fromisoformat(row["expires_at"]) <= now:
                raise ProjectCatalogExpired("IMPORT_PREVIEW_EXPIRED")
            if binding != row["project_binding_fingerprint"] or binding != current_project_fingerprint:
                raise ProjectCatalogConflict("IMPORT_PROJECT_CHANGED")
            head = connection.execute(
                """SELECT * FROM project_eda_import_revisions
                   WHERE project_id=? AND backend=? AND native_entry_relative_path=? AND active=1""",
                (project_id, row["backend"], row["native_entry_relative_path"]),
            ).fetchone()
            actual_id = None if head is None else head["import_revision_id"]
            actual_fingerprint = None if head is None else head["import_revision_fingerprint"]
            if (row["expected_head_revision_id"], row["expected_head_revision_fingerprint"]) != (actual_id, actual_fingerprint):
                raise ProjectCatalogConflict("IMPORT_HEAD_CHANGED")
            if head is not None and head["import_revision_fingerprint"] == revision["import_revision_fingerprint"]:
                revision_id = head["import_revision_id"]
                outcome = "REUSED_ACTIVE"
            else:
                revision_id = "eda-import-" + uuid4().hex
                connection.execute(
                    """UPDATE project_eda_import_revisions SET active=0
                       WHERE project_id=? AND backend=? AND native_entry_relative_path=? AND active=1""",
                    (project_id, row["backend"], row["native_entry_relative_path"]),
                )
                connection.execute(
                    """INSERT INTO project_eda_import_revisions(
                        import_revision_id,project_id,tenant_id,backend,native_entry_relative_path,
                        import_revision_fingerprint,source_set_fingerprint,parser_ruleset_id,
                        supported_subset_id,active,created_at,body
                    ) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (revision_id, project_id, tenant_id, row["backend"], row["native_entry_relative_path"],
                     revision["import_revision_fingerprint"], revision["source_set_fingerprint"],
                     revision["parser_ruleset_id"], revision["supported_subset_id"],
                     now.isoformat(), json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)),
                )
                outcome = "CREATED"
            cursor = connection.execute(
                """UPDATE project_eda_import_previews SET status='COMMITTED',committed_revision_id=?
                   WHERE preview_id=? AND project_id=? AND status='OPEN'""",
                (revision_id, preview_id, project_id),
            )
            if cursor.rowcount != 1:
                raise ProjectCatalogConflict("IMPORT_PREVIEW_INVALID")
            committed = connection.execute(
                "SELECT * FROM project_eda_import_revisions WHERE project_id=? AND import_revision_id=?",
                (project_id, revision_id),
            ).fetchone()
            if committed is None:
                raise ProjectCatalogUnavailable("EDA import committed revision is unavailable")
            return self._eda_revision(committed), outcome

    def eda_import_revision(
        self, project_id: str, revision_id: str, fingerprint: str, *, tenant_id: str, requester: str,
    ) -> dict | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                """SELECT * FROM project_eda_import_revisions
                   WHERE project_id=? AND import_revision_id=? AND import_revision_fingerprint=?""",
                (project_id, revision_id, fingerprint),
            ).fetchone()
        return None if row is None else self._eda_revision(row)

    def eda_import_revisions(
        self, project_id: str, backend: str, native_entry_relative_path: str, *,
        tenant_id: str, requester: str,
    ) -> tuple[dict, ...]:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            rows = connection.execute(
                """SELECT * FROM project_eda_import_revisions
                   WHERE project_id=? AND backend=? AND native_entry_relative_path=?
                   ORDER BY created_at,rowid""",
                (project_id, backend, self._eda_entry(native_entry_relative_path)),
            ).fetchall()
        return tuple(self._eda_revision(row) for row in rows)

    def has_imported_pcb_target(self, project_id: str, relative_path: str, *,
                                tenant_id: str, requester: str) -> bool:
        """Only active C1 source manifest PCB paths reserve the generic write target."""
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            rows = connection.execute(
                "SELECT * FROM project_eda_import_revisions WHERE project_id=? AND tenant_id=? AND active=1",
                (project_id, tenant_id),
            ).fetchall()
        for row in rows:
            revision = self._eda_revision(row)
            if any(
                item.get("kind") == "FILE"
                and isinstance(item.get("relative_path"), str)
                and item["relative_path"].casefold() == relative_path.casefold()
                and item["relative_path"].casefold().endswith(".kicad_pcb")
                for item in revision.get("manifest", ())
            ):
                return True
        return False

    def eda_import_head(
        self, project_id: str, backend: str, native_entry_relative_path: str, *,
        tenant_id: str, requester: str,
    ) -> dict | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                """SELECT * FROM project_eda_import_revisions
                   WHERE project_id=? AND backend=? AND native_entry_relative_path=? AND active=1""",
                (project_id, backend, self._eda_entry(native_entry_relative_path)),
            ).fetchone()
        return None if row is None else self._eda_revision(row)

    def save_eda_native_move_candidate(
        self, candidate: dict, *, tenant_id: str, requester: str,
    ) -> dict:
        project_id = candidate.get("project_id") if isinstance(candidate, dict) else None
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, project_id, tenant_id, requester)
            try:
                binding = candidate["binding"]
                expected = self._eda_native_move_fingerprint(binding)
                if (
                    candidate["candidate_fingerprint"] != expected
                    or candidate["tenant_id"] != tenant_id
                    or candidate["requester"] != requester
                    or any(binding.get(name) != candidate[name] for name in (
                        "tenant_id", "project_id", "requester", "import_revision_id",
                        "import_revision_fingerprint",
                    ))
                ):
                    raise ValueError("candidate binding")
                body = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                values = (
                    project_id, tenant_id, candidate["candidate_id"], expected,
                    candidate["import_revision_id"], candidate["import_revision_fingerprint"],
                    requester, candidate["created_at"], body,
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ProjectCatalogRejected("NATIVE_MOVE_RECORD_INVALID") from error
            row = connection.execute(
                "SELECT * FROM project_eda_native_move_candidates WHERE project_id=? AND candidate_id=?",
                (project_id, candidate["candidate_id"]),
            ).fetchone()
            if row is not None:
                existing = self._eda_native_move_record(row, review=False)
                if existing != candidate:
                    raise ProjectCatalogConflict("NATIVE_MOVE_CANDIDATE_CONFLICT")
                return existing
            connection.execute(
                """INSERT INTO project_eda_native_move_candidates(
                    project_id,tenant_id,candidate_id,candidate_fingerprint,import_revision_id,
                    import_revision_fingerprint,requester,created_at,body
                ) VALUES(?,?,?,?,?,?,?,?,?)""", values,
            )
            return candidate

    def eda_native_move_candidate(
        self, project_id: str, candidate_id: str, candidate_fingerprint: str, *,
        tenant_id: str, requester: str,
    ) -> dict | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                """SELECT * FROM project_eda_native_move_candidates
                   WHERE project_id=? AND candidate_id=? AND candidate_fingerprint=?""",
                (project_id, candidate_id, candidate_fingerprint),
            ).fetchone()
        return None if row is None else self._eda_native_move_record(row, review=False)

    def save_eda_native_mapping_review(
        self, review: dict, *, tenant_id: str, requester: str,
    ) -> dict:
        project_id = review.get("project_id") if isinstance(review, dict) else None
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, project_id, tenant_id, requester)
            try:
                binding = review["binding"]
                expected = self._eda_native_move_fingerprint(binding)
                if (
                    review["mapping_review_fingerprint"] != expected
                    or review["tenant_id"] != tenant_id
                    or review["reviewer_id"] != requester
                    or review["decision"] not in {"ACCEPT_MAPPING", "REJECT_MAPPING"}
                    or any(binding.get(name) != review[name] for name in (
                        "tenant_id", "project_id", "candidate_id", "candidate_fingerprint",
                        "reviewer_id", "decision",
                    ))
                ):
                    raise ValueError("review binding")
                body = json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (KeyError, TypeError, ValueError) as error:
                raise ProjectCatalogRejected("NATIVE_MOVE_RECORD_INVALID") from error
            candidate = connection.execute(
                """SELECT * FROM project_eda_native_move_candidates
                   WHERE project_id=? AND candidate_id=?""",
                (project_id, review["candidate_id"]),
            ).fetchone()
            if candidate is None or candidate["tenant_id"] != tenant_id or candidate["candidate_fingerprint"] != review["candidate_fingerprint"]:
                raise ProjectCatalogConflict("NATIVE_MOVE_CANDIDATE_CONFLICT")
            self._eda_native_move_record(candidate, review=False)
            existing = connection.execute(
                """SELECT * FROM project_eda_native_mapping_reviews
                   WHERE project_id=? AND (candidate_id=? OR mapping_review_id=?)""",
                (project_id, review["candidate_id"], review["mapping_review_id"]),
            ).fetchone()
            if existing is not None:
                restored = self._eda_native_move_record(existing, review=True)
                if restored != review:
                    raise ProjectCatalogConflict("NATIVE_MOVE_REVIEW_CONFLICT")
                return restored
            connection.execute(
                """INSERT INTO project_eda_native_mapping_reviews(
                    project_id,tenant_id,mapping_review_id,mapping_review_fingerprint,
                    candidate_id,candidate_fingerprint,reviewer_id,decision,created_at,body
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (project_id, tenant_id, review["mapping_review_id"], expected,
                 review["candidate_id"], review["candidate_fingerprint"], requester,
                 review["decision"], review["created_at"], body),
            )
            return review

    def eda_native_mapping_review(
        self, project_id: str, mapping_review_id: str, mapping_review_fingerprint: str, *,
        tenant_id: str, requester: str,
    ) -> dict | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                """SELECT * FROM project_eda_native_mapping_reviews
                   WHERE project_id=? AND mapping_review_id=? AND mapping_review_fingerprint=?""",
                (project_id, mapping_review_id, mapping_review_fingerprint),
            ).fetchone()
        return None if row is None else self._eda_native_move_record(row, review=True)

    def eda_native_mapping_review_for_candidate(
        self, project_id: str, candidate_id: str, *, tenant_id: str, requester: str,
    ) -> dict | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                """SELECT * FROM project_eda_native_mapping_reviews
                   WHERE project_id=? AND candidate_id=?""", (project_id, candidate_id),
            ).fetchone()
        return None if row is None else self._eda_native_move_record(row, review=True)

    def brownfield_build_context(self, project_id: str) -> BrownfieldBuildContext | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM brownfield_build_contexts WHERE project_id = ?", (project_id,)
            ).fetchone()
        return None if row is None else BrownfieldBuildContext.model_validate_json(row[0])

    def save_brownfield_build_context(
        self, value: BrownfieldBuildContext, *, previous_fingerprint: str | None
    ) -> None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM brownfield_build_contexts WHERE project_id = ?", (value.project_id,)
            ).fetchone()
            current = None if row is None else BrownfieldBuildContext.model_validate_json(row[0])
            if (None if current is None else current.fingerprint) != previous_fingerprint:
                raise ProjectCatalogConflict("brownfield build context changed")
            connection.execute(
                "INSERT INTO brownfield_build_contexts(project_id, tenant_id, requester, body) VALUES(?,?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET tenant_id=excluded.tenant_id, requester=excluded.requester, body=excluded.body",
                (value.project_id, value.tenant_id, value.requester, value.model_dump_json()),
            )

    @staticmethod
    def _circuit_owner(connection: sqlite3.Connection, project_id: str, tenant_id: str, requester: str) -> None:
        row = connection.execute(
            "SELECT p.tenant_id, o.requester FROM projects p LEFT JOIN project_owners o ON o.project_id=p.project_id WHERE p.project_id=?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise ProjectCatalogUnavailable("circuit project is unavailable")
        if row[0] != tenant_id or row[1] != requester:
            raise ProjectCatalogForbidden("project requester mismatch")

    @staticmethod
    def _circuit_conversation(connection: sqlite3.Connection, candidate: CircuitProposalCandidate) -> None:
        row = connection.execute(
            "SELECT body FROM conversations WHERE conversation_id=? AND project_id=? AND tenant_id=? AND requester=?",
            (candidate.conversation_id, candidate.project_id, candidate.tenant_id, candidate.proposal.proposer),
        ).fetchone()
        if row is None:
            raise ProjectCatalogUnavailable("circuit conversation is unavailable")
        try:
            detail = SQLiteProjectCatalogRepository._conversation_body(row[0])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise ProjectCatalogUnavailable("circuit conversation is invalid") from error
        if circuit_source_fingerprint(detail, candidate.turn_id) != candidate.source_fingerprint:
            raise ProjectCatalogConflict("circuit source changed")
        turn = next((item for item in detail.turns if item.turn_id == candidate.turn_id), None)
        if turn is None or turn.status.value != "COMPLETED" or turn.route != candidate.route:
            raise ProjectCatalogConflict("circuit source turn changed")

    @staticmethod
    def _decode_circuit_candidate(row: sqlite3.Row) -> CircuitProposalCandidate:
        try:
            candidate = CircuitProposalCandidate.model_validate({
                "tenant_id": row["tenant_id"], "project_id": row["project_id"],
                "proposal": ObjectProposal.model_validate_json(row["proposal_body"], strict=False),
                "evidence_reference": EvidenceReceiptReference.model_validate_json(row["evidence_reference_body"], strict=False),
                "idempotency_key": row["idempotency_key"], "request_fingerprint": row["request_fingerprint"],
                "conversation_id": row["conversation_id"], "turn_id": row["turn_id"],
                "object_id": row["object_id"], "base_revision": row["base_revision"],
                "base_fingerprint": row["base_fingerprint"], "context_fingerprint": row["context_fingerprint"],
                "conversation_fingerprint": row["conversation_fingerprint"], "source_fingerprint": row["source_fingerprint"],
                "constraints": tuple(json.loads(row["constraints_body"])), "route": row["route"],
                "created_at": datetime.fromisoformat(row["created_at"]), "status": ProposalStatus(row["status"]),
                "decision": None if row["decision_body"] is None else ApprovalRecord.model_validate_json(row["decision_body"], strict=False),
            })
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise ProjectCatalogUnavailable("circuit candidate is invalid") from error
        if candidate.proposal.proposal_id != row["proposal_id"] or candidate.proposal.fingerprint != row["proposal_fingerprint"] or candidate.evidence_reference.receipt_id != row["evidence_receipt_id"] or candidate.evidence_reference.receipt_fingerprint != row["evidence_receipt_fingerprint"]:
            raise ProjectCatalogUnavailable("circuit candidate identity is invalid")
        return candidate

    @staticmethod
    def _decode_circuit_publication(row: sqlite3.Row) -> CircuitPublication:
        try:
            publication = CircuitPublication.model_validate({
                "tenant_id": row["tenant_id"], "project_id": row["project_id"],
                "idempotency_key": row["idempotency_key"], "request_fingerprint": row["request_fingerprint"],
                "base_revision": row["base_revision"], "base_fingerprint": row["base_fingerprint"],
                "circuit": CircuitIntentObject.model_validate_json(row["circuit_body"], strict=False), "proposal": ObjectProposal.model_validate_json(row["proposal_body"], strict=False),
                "approval": ApprovalRecord.model_validate_json(row["approval_body"], strict=False), "apply_record": ObjectMutationRecord.model_validate_json(row["apply_body"], strict=False),
            })
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise ProjectCatalogUnavailable("circuit publication is invalid") from error
        if (publication.circuit.object_id != row["object_id"] or publication.circuit.revision != row["revision"] or publication.circuit.fingerprint != row["object_fingerprint"] or publication.proposal.proposal_id != row["proposal_id"] or publication.proposal.fingerprint != row["proposal_fingerprint"] or publication.approval.approval_id != row["approval_id"] or publication.approval.fingerprint != row["approval_fingerprint"] or publication.apply_record.apply_id != row["apply_id"] or publication.apply_record.fingerprint != row["apply_fingerprint"]):
            raise ProjectCatalogUnavailable("circuit publication identity is invalid")
        return publication

    @staticmethod
    def _predesign_conversation(connection: sqlite3.Connection, candidate: PredesignCircuitCandidate) -> None:
        row = connection.execute(
            "SELECT body FROM conversations WHERE conversation_id=? AND project_id=? AND tenant_id=? AND requester=?",
            (candidate.conversation_id, candidate.project_id, candidate.tenant_id, candidate.proposal.proposer),
        ).fetchone()
        if row is None:
            raise ProjectCatalogUnavailable("predesign conversation is unavailable")
        try:
            detail = SQLiteProjectCatalogRepository._conversation_body(row[0])
            actual = predesign_source_snapshot(detail, candidate.turn_id)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise ProjectCatalogConflict("predesign source is invalid") from error
        if actual != candidate.source_snapshot:
            raise ProjectCatalogConflict("predesign source changed")

    @staticmethod
    def _decode_predesign(row: sqlite3.Row) -> PredesignCircuitCandidate:
        try:
            candidate = PredesignCircuitCandidate.model_validate_json(row["body"], strict=False)
        except (ValueError, TypeError) as error:
            raise ProjectCatalogUnavailable("predesign proposal is invalid") from error
        if (candidate.project_id != row["project_id"] or candidate.tenant_id != row["tenant_id"]
                or candidate.proposal.proposal_id != row["proposal_id"]
                or candidate.idempotency_key != row["idempotency_key"]):
            raise ProjectCatalogUnavailable("predesign proposal identity is invalid")
        return candidate

    def save_predesign_proposal(self, candidate: PredesignCircuitCandidate, *, requester: str) -> PredesignCircuitCandidate:
        candidate = PredesignCircuitCandidate.model_validate(candidate.model_dump(mode="python"))
        if candidate.status is not ProposalStatus.CREATED or candidate.proposal.proposer != requester:
            raise ProjectCatalogConflict("predesign proposal is not new")
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, candidate.project_id, candidate.tenant_id, requester)
            self._predesign_conversation(connection, candidate)
            row = connection.execute(
                "SELECT * FROM project_predesign_circuit_proposals WHERE project_id=? AND idempotency_key=?",
                (candidate.project_id, candidate.idempotency_key),
            ).fetchone()
            if row is not None:
                existing = self._decode_predesign(row)
                if (existing.request_fingerprint != candidate.request_fingerprint
                        or existing.proposal.fingerprint != candidate.proposal.fingerprint):
                    raise ProjectCatalogConflict("predesign idempotency key was reused")
                return existing
            if connection.execute(
                "SELECT 1 FROM project_circuit_proposals WHERE project_id=? AND idempotency_key=?",
                (candidate.project_id, candidate.idempotency_key),
            ).fetchone() is not None:
                raise ProjectCatalogConflict("Circuit idempotency key was reused")
            try:
                connection.execute(
                    "INSERT INTO project_predesign_circuit_proposals(project_id,tenant_id,proposal_id,idempotency_key,body) VALUES(?,?,?,?,?)",
                    (candidate.project_id, candidate.tenant_id, candidate.proposal.proposal_id,
                     candidate.idempotency_key, candidate.model_dump_json()),
                )
            except sqlite3.IntegrityError as error:
                raise ProjectCatalogConflict("predesign proposal already exists") from error
        return candidate

    def predesign_proposal(self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str) -> PredesignCircuitCandidate:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM project_predesign_circuit_proposals WHERE project_id=? AND proposal_id=?",
                (project_id, proposal_id),
            ).fetchone()
        if row is None:
            raise ProjectCatalogUnavailable("predesign proposal is unavailable")
        return self._decode_predesign(row)

    def predesign_proposal_by_idempotency(self, project_id: str, key: str, *, tenant_id: str, requester: str) -> PredesignCircuitCandidate | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM project_predesign_circuit_proposals WHERE project_id=? AND idempotency_key=?",
                (project_id, key),
            ).fetchone()
        return None if row is None else self._decode_predesign(row)

    def decide_predesign_proposal(
        self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str,
        proposal_fingerprint: str, decision: ApprovalRecord,
        publication: CircuitPublication | None,
    ) -> PredesignCircuitCandidate | CircuitPublication:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM project_predesign_circuit_proposals WHERE project_id=? AND proposal_id=?",
                (project_id, proposal_id),
            ).fetchone()
            if row is None:
                raise ProjectCatalogUnavailable("predesign proposal is unavailable")
            candidate = self._decode_predesign(row)
            if (candidate.proposal.fingerprint != proposal_fingerprint
                    or decision.project_id != project_id or decision.proposal_id != proposal_id
                    or decision.proposal_fingerprint != proposal_fingerprint
                    or decision.reviewer != requester
                    or decision.fingerprint != canonical_fingerprint(decision, exclude={"fingerprint"})):
                raise ProjectCatalogConflict("predesign decision binding changed")
            if candidate.status is ProposalStatus.APPLIED:
                if candidate.decision != decision:
                    raise ProjectCatalogConflict("predesign proposal already decided")
                persisted = connection.execute(
                    "SELECT * FROM project_circuit_intents WHERE project_id=? AND proposal_id=?",
                    (project_id, proposal_id),
                ).fetchone()
                if persisted is None:
                    raise ProjectCatalogUnavailable("predesign publication is unavailable")
                return self._decode_circuit_publication(persisted)
            if candidate.status is ProposalStatus.REJECTED:
                if candidate.decision == decision:
                    return candidate
                raise ProjectCatalogConflict("predesign proposal already decided")
            self._predesign_conversation(connection, candidate)
            current = connection.execute(
                "SELECT * FROM project_circuit_intents WHERE project_id=? AND object_id=? AND status='ACTIVE'",
                (project_id, candidate.object_id),
            ).fetchone()
            if current is None:
                if candidate.base_revision is not None or candidate.proposal.proposed_object.revision != "r1":
                    raise ProjectCatalogConflict("predesign base revision changed")
            else:
                active = self._decode_circuit_publication(current)
                if (candidate.base_revision != active.circuit.revision
                        or candidate.base_fingerprint != active.circuit.fingerprint
                        or candidate.proposal.proposed_object.revision != f"r{int(active.circuit.revision[1:]) + 1}"):
                    raise ProjectCatalogConflict("predesign base revision changed")
            if decision.decision is ProposalStatus.REJECTED:
                if publication is not None:
                    raise ProjectCatalogConflict("rejected predesign cannot publish")
                rejected = candidate.model_copy(update={"status": ProposalStatus.REJECTED, "decision": decision})
                connection.execute(
                    "UPDATE project_predesign_circuit_proposals SET body=? WHERE project_id=? AND proposal_id=?",
                    (rejected.model_dump_json(), project_id, proposal_id),
                )
                return rejected
            if publication is None or decision.decision is not ProposalStatus.APPROVED:
                raise ProjectCatalogConflict("predesign approval is incomplete")
            publication = CircuitPublication.model_validate(publication.model_dump(mode="python"))
            if (publication.project_id != project_id or publication.tenant_id != tenant_id
                    or publication.idempotency_key != candidate.idempotency_key
                    or publication.request_fingerprint != candidate.request_fingerprint
                    or publication.proposal.fingerprint != candidate.proposal.fingerprint
                    or publication.approval != decision
                    or publication.base_revision != candidate.base_revision
                    or publication.base_fingerprint != candidate.base_fingerprint):
                raise ProjectCatalogConflict("predesign publication binding changed")
            try:
                if current is not None:
                    connection.execute(
                        "UPDATE project_circuit_intents SET status='SUPERSEDED' WHERE project_id=? AND object_id=? AND status='ACTIVE'",
                        (project_id, candidate.object_id),
                    )
                connection.execute(
                    "INSERT INTO project_circuit_intents(project_id,tenant_id,object_id,revision,status,object_fingerprint,base_revision,base_fingerprint,proposal_id,idempotency_key,request_fingerprint,proposal_fingerprint,approval_id,approval_fingerprint,apply_id,apply_fingerprint,circuit_body,proposal_body,approval_body,apply_body) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, tenant_id, publication.circuit.object_id, publication.circuit.revision,
                     "ACTIVE", publication.circuit.fingerprint, publication.base_revision,
                     publication.base_fingerprint, proposal_id, publication.idempotency_key,
                     publication.request_fingerprint, publication.proposal.fingerprint,
                     publication.approval.approval_id, publication.approval.fingerprint,
                     publication.apply_record.apply_id, publication.apply_record.fingerprint,
                     publication.circuit.model_dump_json(), publication.proposal.model_dump_json(),
                     publication.approval.model_dump_json(), publication.apply_record.model_dump_json()),
                )
                applied = candidate.model_copy(update={"status": ProposalStatus.APPLIED, "decision": decision})
                connection.execute(
                    "UPDATE project_predesign_circuit_proposals SET body=? WHERE project_id=? AND proposal_id=?",
                    (applied.model_dump_json(), project_id, proposal_id),
                )
            except sqlite3.IntegrityError as error:
                raise ProjectCatalogConflict("predesign publication conflicts with current revision") from error
        return publication

    def predesign_published_candidate(
        self, project_id: str, object_id: str, revision: str, *, tenant_id: str, requester: str,
    ) -> PredesignCircuitCandidate | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT p.* FROM project_circuit_intents i JOIN project_predesign_circuit_proposals p "
                "ON p.project_id=i.project_id AND p.proposal_id=i.proposal_id "
                "WHERE i.project_id=? AND i.object_id=? AND i.revision=? AND i.status='ACTIVE'",
                (project_id, object_id, revision),
            ).fetchone()
        return None if row is None else self._decode_predesign(row)

    def save_schematic_evidence(self, *, project_id: str, tenant_id: str, conversation_id: str,
                                requester: str, kind: str, evidence_id: str, body: str,
                                expected_conversation_fingerprint: str) -> str:
        if kind not in {"PARSE", "READBACK", "PARAMETER", "PARAMETER_REVIEW"}:
            raise ProjectCatalogRejected("schematic evidence kind is invalid")
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute("SELECT body FROM conversations WHERE project_id=? AND tenant_id=? AND conversation_id=? AND requester=?",
                                     (project_id, tenant_id, conversation_id, requester)).fetchone()
            if row is None or self._conversation_body(row["body"]).fingerprint != expected_conversation_fingerprint:
                raise ProjectCatalogConflict("schematic evidence conversation changed")
            prior = connection.execute("SELECT body FROM project_schematic_evidence_v1 WHERE project_id=? AND tenant_id=? AND kind=? AND evidence_id=?",
                                       (project_id, tenant_id, kind, evidence_id)).fetchone()
            if prior is not None:
                if json.loads(prior["body"]) != json.loads(body):
                    raise ProjectCatalogConflict("schematic evidence identity conflict")
                return prior["body"]
            connection.execute("INSERT INTO project_schematic_evidence_v1 VALUES(?,?,?,?,?,?)",
                               (project_id, tenant_id, conversation_id, kind, evidence_id, body))
        return body

    def schematic_evidence(self, *, project_id: str, tenant_id: str, requester: str,
                           kind: str, evidence_id: str) -> str | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute("SELECT body FROM project_schematic_evidence_v1 WHERE project_id=? AND tenant_id=? AND kind=? AND evidence_id=?",
                                     (project_id, tenant_id, kind, evidence_id)).fetchone()
        return None if row is None else row["body"]

    def save_schematic_verification(
        self, record: SchematicVerificationRecord, *, requester: str,
        expected_conversation_fingerprint: str,
        expected_source_publications: tuple[tuple[str, str], ...] = (),
    ) -> SchematicVerificationRecord:
        record = SchematicVerificationRecord.model_validate(record.model_dump(mode="python"))
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, record.project_id, record.tenant_id, requester)
            row = connection.execute(
                "SELECT body FROM conversations WHERE project_id=? AND conversation_id=? AND tenant_id=? AND requester=?",
                (record.project_id, record.conversation_id, record.tenant_id, requester),
            ).fetchone()
            if row is None:
                raise ProjectCatalogUnavailable("schematic verification conversation is unavailable")
            detail = self._conversation_body(row["body"])
            if detail.fingerprint != expected_conversation_fingerprint:
                raise ProjectCatalogConflict("schematic verification source changed")
            for source_id, publication_fingerprint in expected_source_publications:
                source_head = connection.execute(
                    "SELECT fingerprint FROM component_source_heads WHERE tenant_id=? AND project_id=? AND source_id=?",
                    (record.tenant_id, record.project_id, source_id),
                ).fetchone()
                if source_head is None or source_head["fingerprint"] != publication_fingerprint:
                    raise ProjectCatalogConflict("schematic verification source publication changed")
            prior = connection.execute(
                "SELECT body FROM project_schematic_verifications WHERE project_id=? AND verification_id=?",
                (record.project_id, record.verification_id),
            ).fetchone()
            if prior is not None:
                existing = SchematicVerificationRecord.model_validate_json(prior["body"], strict=False)
                if existing.model_dump(exclude={"created_at", "fingerprint"}) != record.model_dump(exclude={"created_at", "fingerprint"}):
                    raise ProjectCatalogConflict("schematic verification identity was reused")
                return existing
            try:
                connection.execute(
                    "INSERT INTO project_schematic_verifications(project_id,tenant_id,conversation_id,verification_id,body) VALUES(?,?,?,?,?)",
                    (record.project_id, record.tenant_id, record.conversation_id, record.verification_id, record.model_dump_json()),
                )
            except sqlite3.IntegrityError as error:
                raise ProjectCatalogConflict("schematic verification already exists") from error
        return record

    def schematic_verification(
        self, project_id: str, verification_id: str, *, tenant_id: str, requester: str,
    ) -> SchematicVerificationRecord:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM project_schematic_verifications WHERE project_id=? AND verification_id=?",
                (project_id, verification_id),
            ).fetchone()
        if row is None:
            raise ProjectCatalogUnavailable("schematic verification is unavailable")
        try:
            record = SchematicVerificationRecord.model_validate_json(row["body"], strict=False)
        except (ValueError, TypeError) as error:
            raise ProjectCatalogUnavailable("schematic verification is invalid") from error
        if (record.project_id != row["project_id"] or record.tenant_id != row["tenant_id"]
                or record.conversation_id != row["conversation_id"]
                or record.verification_id != row["verification_id"]):
            raise ProjectCatalogUnavailable("schematic verification identity is invalid")
        return record

    def schematic_verifications(
        self, project_id: str, conversation_id: str, *, tenant_id: str,
        requester: str, limit: int,
    ) -> tuple[str, ...]:
        if not 1 <= limit <= 100:
            raise ProjectCatalogConflict("schematic verification list limit is invalid")
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            conversation = connection.execute(
                "SELECT 1 FROM conversations WHERE project_id=? AND conversation_id=? AND tenant_id=? AND requester=?",
                (project_id, conversation_id, tenant_id, requester),
            ).fetchone()
            if conversation is None:
                raise ProjectCatalogUnavailable("schematic verification conversation is unavailable")
            rows = connection.execute(
                "SELECT verification_id FROM project_schematic_verifications "
                "WHERE project_id=? AND tenant_id=? AND conversation_id=? "
                "ORDER BY verification_id LIMIT ?",
                (project_id, tenant_id, conversation_id, limit),
            ).fetchall()
        return tuple(row["verification_id"] for row in rows)

    def save_circuit_proposal(self, candidate: CircuitProposalCandidate, *, requester: str) -> CircuitProposalCandidate:
        candidate = CircuitProposalCandidate.model_validate(candidate.model_dump(mode="python"))
        if candidate.status is not ProposalStatus.CREATED or candidate.proposal.proposer != requester:
            raise ProjectCatalogConflict("circuit candidate is not new")
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, candidate.project_id, candidate.tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM project_circuit_proposals WHERE project_id=? AND idempotency_key=?",
                (candidate.project_id, candidate.idempotency_key),
            ).fetchone()
            if row is not None:
                existing = self._decode_circuit_candidate(row)
                if existing.request_fingerprint != candidate.request_fingerprint or existing.proposal.fingerprint != candidate.proposal.fingerprint:
                    raise ProjectCatalogConflict("circuit idempotency key was reused")
                return existing
            if connection.execute(
                "SELECT 1 FROM project_predesign_circuit_proposals WHERE project_id=? AND idempotency_key=?",
                (candidate.project_id, candidate.idempotency_key),
            ).fetchone() is not None:
                raise ProjectCatalogConflict("Circuit idempotency key was reused")
            self._circuit_conversation(connection, candidate)
            try:
                connection.execute(
                    "INSERT INTO project_circuit_proposals(project_id,tenant_id,proposal_id,idempotency_key,request_fingerprint,status,conversation_id,turn_id,object_id,base_revision,base_fingerprint,context_fingerprint,conversation_fingerprint,source_fingerprint,constraints_body,route,evidence_receipt_id,evidence_receipt_fingerprint,proposal_fingerprint,proposal_body,evidence_reference_body,decision_body,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (candidate.project_id, candidate.tenant_id, candidate.proposal.proposal_id, candidate.idempotency_key, candidate.request_fingerprint, candidate.status.value, candidate.conversation_id, candidate.turn_id, candidate.object_id, candidate.base_revision, candidate.base_fingerprint, candidate.context_fingerprint, candidate.conversation_fingerprint, candidate.source_fingerprint, json.dumps(candidate.constraints), candidate.route, candidate.evidence_reference.receipt_id, candidate.evidence_reference.receipt_fingerprint, candidate.proposal.fingerprint, candidate.proposal.model_dump_json(), candidate.evidence_reference.model_dump_json(), None, candidate.created_at.isoformat()),
                )
            except sqlite3.IntegrityError as error:
                raise ProjectCatalogConflict("circuit proposal already exists") from error
        return candidate

    def circuit_proposal(self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str) -> CircuitProposalCandidate:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM project_circuit_proposals WHERE project_id=? AND proposal_id=?",
                (project_id, proposal_id),
            ).fetchone()
        if row is None:
            raise ProjectCatalogUnavailable("circuit proposal is unavailable")
        return self._decode_circuit_candidate(row)

    def circuit_proposal_by_idempotency(self, project_id: str, idempotency_key: str, *, tenant_id: str, requester: str) -> CircuitProposalCandidate | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute("SELECT * FROM project_circuit_proposals WHERE project_id=? AND idempotency_key=?", (project_id, idempotency_key)).fetchone()
        return None if row is None else self._decode_circuit_candidate(row)

    def decide_circuit_proposal(self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str, proposal_fingerprint: str, decision: ApprovalRecord, publication: CircuitPublication | None) -> CircuitProposalCandidate | CircuitPublication:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute("SELECT * FROM project_circuit_proposals WHERE project_id=? AND proposal_id=?", (project_id, proposal_id)).fetchone()
            if row is None:
                raise ProjectCatalogUnavailable("circuit proposal is unavailable")
            candidate = self._decode_circuit_candidate(row)
            if candidate.proposal.fingerprint != proposal_fingerprint or decision.project_id != project_id or decision.proposal_id != proposal_id or decision.proposal_fingerprint != proposal_fingerprint or decision.reviewer != requester or decision.fingerprint != canonical_fingerprint(decision, exclude={"fingerprint"}):
                raise ProjectCatalogConflict("circuit decision binding changed")
            if candidate.status is ProposalStatus.APPLIED:
                if candidate.decision is None or candidate.decision.fingerprint != decision.fingerprint:
                    raise ProjectCatalogConflict("circuit proposal was already decided")
                existing = connection.execute("SELECT * FROM project_circuit_intents WHERE project_id=? AND proposal_id=?", (project_id, proposal_id)).fetchone()
                if existing is None:
                    raise ProjectCatalogUnavailable("circuit publication is unavailable")
                persisted = self._decode_circuit_publication(existing)
                if publication is not None and persisted.apply_record.fingerprint != publication.apply_record.fingerprint:
                    raise ProjectCatalogConflict("circuit retry differs from publication")
                return persisted
            if candidate.status is ProposalStatus.REJECTED:
                if candidate.decision is not None and candidate.decision.fingerprint == decision.fingerprint and decision.decision is ProposalStatus.REJECTED:
                    return candidate
                raise ProjectCatalogConflict("circuit proposal was already decided")
            self._circuit_conversation(connection, candidate)
            current = connection.execute("SELECT * FROM project_circuit_intents WHERE project_id=? AND object_id=? AND status='ACTIVE'", (project_id, candidate.object_id)).fetchone()
            if current is None:
                if candidate.base_revision is not None or candidate.proposal.proposed_object.revision != "r1":
                    raise ProjectCatalogConflict("circuit base revision changed")
            else:
                active = self._decode_circuit_publication(current)
                if candidate.base_revision != active.circuit.revision or candidate.base_fingerprint != active.circuit.fingerprint or candidate.proposal.proposed_object.revision != f"r{int(active.circuit.revision[1:]) + 1}":
                    raise ProjectCatalogConflict("circuit base revision changed")
            if decision.decision is ProposalStatus.REJECTED:
                if publication is not None:
                    raise ProjectCatalogConflict("rejected circuit cannot be published")
                connection.execute("UPDATE project_circuit_proposals SET status='REJECTED', decision_body=? WHERE project_id=? AND proposal_id=? AND status='CREATED'", (decision.model_dump_json(), project_id, proposal_id))
                return candidate.model_copy(update={"status": ProposalStatus.REJECTED, "decision": decision})
            if decision.decision is not ProposalStatus.APPROVED or publication is None:
                raise ProjectCatalogConflict("circuit approval is incomplete")
            publication = CircuitPublication.model_validate(publication.model_dump(mode="python"))
            if publication.tenant_id != tenant_id or publication.project_id != project_id or publication.idempotency_key != candidate.idempotency_key or publication.request_fingerprint != candidate.request_fingerprint or publication.base_revision != candidate.base_revision or publication.base_fingerprint != candidate.base_fingerprint or publication.proposal.fingerprint != candidate.proposal.fingerprint or publication.approval != decision or publication.circuit.object_id != candidate.object_id:
                raise ProjectCatalogConflict("circuit publication binding changed")
            try:
                if current is not None:
                    connection.execute("UPDATE project_circuit_intents SET status='SUPERSEDED' WHERE project_id=? AND object_id=? AND status='ACTIVE'", (project_id, candidate.object_id))
                connection.execute(
                    "INSERT INTO project_circuit_intents(project_id,tenant_id,object_id,revision,status,object_fingerprint,base_revision,base_fingerprint,proposal_id,idempotency_key,request_fingerprint,proposal_fingerprint,approval_id,approval_fingerprint,apply_id,apply_fingerprint,circuit_body,proposal_body,approval_body,apply_body) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, tenant_id, publication.circuit.object_id, publication.circuit.revision, "ACTIVE", publication.circuit.fingerprint, publication.base_revision, publication.base_fingerprint, proposal_id, publication.idempotency_key, publication.request_fingerprint, publication.proposal.fingerprint, publication.approval.approval_id, publication.approval.fingerprint, publication.apply_record.apply_id, publication.apply_record.fingerprint, publication.circuit.model_dump_json(), publication.proposal.model_dump_json(), publication.approval.model_dump_json(), publication.apply_record.model_dump_json()),
                )
                connection.execute("UPDATE project_circuit_proposals SET status='APPLIED', decision_body=? WHERE project_id=? AND proposal_id=? AND status='CREATED'", (decision.model_dump_json(), project_id, proposal_id))
            except sqlite3.IntegrityError as error:
                raise ProjectCatalogConflict("circuit publication conflicts with current revision") from error
        return publication

    def current_circuit_intents(self, project_id: str, *, tenant_id: str, requester: str) -> tuple[CircuitIntentObject, ...]:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            rows = connection.execute("SELECT * FROM project_circuit_intents WHERE project_id=? AND status='ACTIVE' ORDER BY object_id", (project_id,)).fetchall()
        return tuple(self._decode_circuit_publication(row).circuit for row in rows)

    def circuit_intent_revision(self, project_id: str, object_id: str, revision: str, fingerprint: str, *, tenant_id: str, requester: str) -> CircuitIntentObject:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute("SELECT * FROM project_circuit_intents WHERE project_id=? AND object_id=? AND revision=? AND status='ACTIVE'", (project_id, object_id, revision)).fetchone()
        if row is None:
            raise ProjectCatalogUnavailable("circuit revision is unavailable")
        circuit = self._decode_circuit_publication(row).circuit
        if circuit.fingerprint != fingerprint:
            raise ProjectCatalogConflict("circuit fingerprint changed")
        return circuit

    def circuit_source_current(self, project_id: str, object_id: str, revision: str, *, tenant_id: str, requester: str) -> bool:
        """Check the published Circuit's original authoritative source without changing history."""
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT p.*, c.body AS conversation_body FROM project_circuit_intents i "
                "JOIN project_circuit_proposals p ON p.project_id=i.project_id AND p.proposal_id=i.proposal_id "
                "LEFT JOIN conversations c ON c.conversation_id=p.conversation_id AND c.project_id=p.project_id "
                "WHERE i.project_id=? AND i.object_id=? AND i.revision=? AND i.status='ACTIVE'",
                (project_id, object_id, revision),
            ).fetchone()
        if row is None:
            return False
        candidate = self._decode_circuit_candidate(row)
        if row["conversation_body"] is None:
            return False
        try:
            detail = self._conversation_body(row["conversation_body"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
        turn = next((item for item in detail.turns if item.turn_id == candidate.turn_id), None)
        return (
            detail.tenant_id == tenant_id
            and detail.requester == requester
            and circuit_source_fingerprint(detail, candidate.turn_id) == candidate.source_fingerprint
            and turn is not None
            and turn.status.value == "COMPLETED"
            and turn.route == candidate.route
        )

    def circuit_published_candidate(self, project_id: str, object_id: str, revision: str, *, tenant_id: str, requester: str) -> CircuitProposalCandidate | None:
        """Return the typed source and evidence bound to an active revision."""
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT p.* FROM project_circuit_intents i "
                "JOIN project_circuit_proposals p ON p.project_id=i.project_id AND p.proposal_id=i.proposal_id "
                "WHERE i.project_id=? AND i.object_id=? AND i.revision=? AND i.status='ACTIVE'",
                (project_id, object_id, revision),
            ).fetchone()
        return None if row is None else self._decode_circuit_candidate(row)

    def delete_project(
        self,
        project_id: str,
        *,
        tenant_id: str,
        requester: str,
        project_fingerprint: str,
        deleted_at: str,
    ) -> ProjectView | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM projects WHERE project_id = ? AND tenant_id = ?",
                (project_id, tenant_id),
            ).fetchone()
            if row is None:
                return None
            project = ProjectView.model_validate_json(row[0])
            if project.fingerprint != project_fingerprint:
                raise ProjectCatalogConflict("project fingerprint mismatch")
            owner = connection.execute(
                "SELECT requester FROM project_owners WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if owner is None or owner[0] != requester:
                raise ProjectCatalogForbidden("project requester mismatch")
            connection.execute("DELETE FROM project_owners WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM brownfield_build_contexts WHERE project_id = ?", (project_id,))
            connection.execute(
                "DELETE FROM conversation_sessions WHERE conversation_id IN (SELECT conversation_id FROM conversations WHERE project_id = ?)",
                (project_id,),
            )
            connection.execute("DELETE FROM conversations WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM project_contexts WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM projects WHERE project_id = ? AND tenant_id = ?", (project_id, tenant_id))
            connection.execute(
                "INSERT INTO project_audit(audit_id, project_id, project_fingerprint, tenant_id, requester, operation, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (f"audit-{project_id}-{deleted_at}", project_id, project_fingerprint, tenant_id, requester, "PROJECT_DELETED", "COMPLETED", deleted_at),
            )
            return project

    def projects(self, tenant_id: str) -> tuple[ProjectView, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT body FROM projects WHERE tenant_id = ?", (tenant_id,)).fetchall()
        return tuple(sorted((ProjectView.model_validate_json(row[0]) for row in rows), key=lambda item: item.last_activity_at, reverse=True))

    def registered_relative_paths(self, location_id: str | None = None) -> set[str]:
        with self._lock, self._connect() as connection:
            if location_id is None:
                rows = connection.execute("SELECT relative_path FROM projects").fetchall()
            else:
                rows = connection.execute("SELECT relative_path FROM projects WHERE location_id = ?", (location_id,)).fetchall()
        return {row[0] for row in rows}

    def pending_projects(self) -> tuple[ProjectView, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT body FROM projects").fetchall()
        return tuple(
            item for item in (ProjectView.model_validate_json(row[0]) for row in rows)
            if item.initialization_status.value in {"PENDING", "INITIALIZING"}
        )

    def save_context(self, value: ProjectContextView) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO project_contexts(project_id, body) VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET body=excluded.body",
                (value.project_id, value.model_dump_json()),
            )

    def context(self, project_id: str) -> ProjectContextView | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT body FROM project_contexts WHERE project_id = ?", (project_id,)).fetchone()
        return None if row is None else ProjectContextView.model_validate_json(row[0])

    @staticmethod
    def _source_publication_row(row: sqlite3.Row) -> ComponentSourcePublicationV1:
        try:
            publication = ComponentSourcePublicationV1.model_validate_json(row["body"])
        except (TypeError, ValueError) as error:
            raise ProjectCatalogUnavailable("source publication is invalid") from error
        if any((publication.tenant_id != row["tenant_id"],
                publication.project_id != row["project_id"],
                publication.source_id != row["source_id"],
                publication.publisher_id != row["publisher_id"],
                publication.publication_id != row["publication_id"],
                publication.generation != row["generation"],
                publication.fingerprint != row["fingerprint"])):
            raise ProjectCatalogUnavailable("source publication identity is invalid")
        return publication

    def current_component_source_publication(self, *, tenant_id: str, project_id: str,
                                              source_id: str) -> ComponentSourcePublicationV1 | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT p.* FROM component_source_heads h JOIN component_source_publications p "
                "ON p.publication_id=h.publication_id "
                "WHERE h.tenant_id=? AND h.project_id=? AND h.source_id=? "
                "AND p.tenant_id=h.tenant_id AND p.project_id=h.project_id "
                "AND p.source_id=h.source_id AND p.publisher_id=h.publisher_id "
                "AND p.generation=h.generation AND p.fingerprint=h.fingerprint",
                (tenant_id, project_id, source_id),
            ).fetchone()
            head = connection.execute(
                "SELECT * FROM component_source_heads WHERE tenant_id=? AND project_id=? AND source_id=?",
                (tenant_id, project_id, source_id),
            ).fetchone()
            latest = connection.execute(
                "SELECT MAX(generation) FROM component_source_publications WHERE tenant_id=? AND project_id=? AND source_id=?",
                (tenant_id, project_id, source_id),
            ).fetchone()[0]
            prior = None if head is None or head["generation"] == 1 else connection.execute(
                "SELECT * FROM component_source_publications WHERE tenant_id=? AND project_id=? "
                "AND source_id=? AND generation=?",
                (tenant_id, project_id, source_id, head["generation"] - 1),
            ).fetchone()
        if (head is None and latest is not None) or (head is not None and (
                row is None or latest != head["generation"])):
            raise ProjectCatalogUnavailable("source publication head is invalid")
        if row is None:
            return None
        publication = self._source_publication_row(row)
        if publication.generation > 1:
            if prior is None:
                raise ProjectCatalogUnavailable("source publication chain is invalid")
            predecessor = self._source_publication_row(prior)
            if (predecessor.publisher_id != publication.publisher_id
                    or predecessor.source_id != publication.source_id
                    or predecessor.generation + 1 != publication.generation
                    or predecessor.fingerprint != publication.predecessor_publication_fingerprint):
                raise ProjectCatalogUnavailable("source publication chain is invalid")
        return publication

    def current_component_source_publications(self, *, tenant_id: str,
                                               project_id: str) -> tuple[ComponentSourcePublicationV1, ...]:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            heads = connection.execute(
                "SELECT source_id FROM component_source_heads WHERE tenant_id=? AND project_id=? ORDER BY source_id",
                (tenant_id, project_id),
            ).fetchall()
        return tuple(self.current_component_source_publication(
            tenant_id=tenant_id, project_id=project_id, source_id=row["source_id"],
        ) for row in heads)

    def published_component_source_heads_for_host(self, *, tenant_id: str, project_id: str, publisher_id: str):
        """Read the entire existing head set; this never qualifies or imports data."""
        with self._lock, self._connect() as connection:
            ids = {row[0] for row in connection.execute(
                "SELECT DISTINCT source_id FROM component_source_publications WHERE tenant_id=? AND project_id=?",
                (tenant_id, project_id))}
            heads = {row[0] for row in connection.execute(
                "SELECT source_id FROM component_source_heads WHERE tenant_id=? AND project_id=?",
                (tenant_id, project_id))}
        if ids != heads:
            raise ProjectCatalogUnavailable("source publication head set is incomplete")
        publications = self.current_component_source_publications(tenant_id=tenant_id, project_id=project_id)
        if {item.source_id for item in publications if item is not None} != ids or any(
                item is None or item.publisher_id != publisher_id for item in publications):
            raise ProjectCatalogUnavailable("source publication host scope is unavailable")
        return publications

    def publish_component_source(self, *, tenant_id: str, project_id: str,
                                 publisher_id: str, request_id: str, source_id: str,
                                 expected_head_publication_fingerprint: str | None,
                                 record: ComponentSourceRecord, requester: str | None = None) -> tuple[ComponentSourcePublicationV1, bool]:
        for name, value in (("tenant_id", tenant_id), ("project_id", project_id),
                            ("publisher_id", publisher_id), ("request_id", request_id),
                            ("source_id", source_id)):
            validate_identifier(value, field=name)
        if expected_head_publication_fingerprint is not None:
            validate_fingerprint(expected_head_publication_fingerprint, field="expected_head_publication_fingerprint")
        checked = ComponentSourceRecord.model_validate(record.model_dump(mode="python"))
        if checked.source_id != source_id:
            raise ProjectCatalogConflict("source publication record identity mismatch")
        request_body = json.dumps({"schema_version": 1, "source_id": source_id,
            "expected_head_publication_fingerprint": expected_head_publication_fingerprint,
            "record": checked.model_dump(mode="json")}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request_fingerprint = "sha256:" + hashlib.sha256(request_body).hexdigest()
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            project = connection.execute(
                "SELECT 1 FROM projects WHERE project_id=? AND tenant_id=?",
                (project_id, tenant_id),
            ).fetchone()
            if project is None:
                raise ProjectCatalogUnavailable("source publication project is unavailable")
            if requester is not None:
                self._circuit_owner(connection, project_id, tenant_id, requester)
            previous = connection.execute(
                "SELECT * FROM component_source_publications WHERE tenant_id=? AND project_id=? "
                "AND publisher_id=? AND source_id=? AND request_id=?",
                (tenant_id, project_id, publisher_id, source_id, request_id),
            ).fetchone()
            head = connection.execute(
                "SELECT * FROM component_source_heads WHERE tenant_id=? AND project_id=? AND source_id=?",
                (tenant_id, project_id, source_id),
            ).fetchone()
            latest = connection.execute(
                "SELECT MAX(generation) FROM component_source_publications WHERE tenant_id=? AND project_id=? AND source_id=?",
                (tenant_id, project_id, source_id),
            ).fetchone()[0]
            if (head is None and latest is not None) or (head is not None and head["generation"] != latest):
                raise ProjectCatalogUnavailable("source publication head is invalid")
            if head is not None:
                head_row = connection.execute(
                    "SELECT * FROM component_source_publications WHERE publication_id=?",
                    (head["publication_id"],),
                ).fetchone()
                if head_row is None:
                    raise ProjectCatalogUnavailable("source publication head is invalid")
                current = self._source_publication_row(head_row)
                if (current.tenant_id, current.project_id, current.source_id,
                    current.publisher_id, current.generation, current.fingerprint) != (
                    tenant_id, project_id, source_id, head["publisher_id"],
                    head["generation"], head["fingerprint"]):
                    raise ProjectCatalogUnavailable("source publication head is invalid")
                if current.generation > 1:
                    prior_row = connection.execute(
                        "SELECT * FROM component_source_publications WHERE tenant_id=? AND project_id=? "
                        "AND source_id=? AND generation=?",
                        (tenant_id, project_id, source_id, current.generation - 1),
                    ).fetchone()
                    if prior_row is None:
                        raise ProjectCatalogUnavailable("source publication chain is invalid")
                    prior_publication = self._source_publication_row(prior_row)
                    if (prior_publication.publisher_id != current.publisher_id
                            or prior_publication.fingerprint != current.predecessor_publication_fingerprint):
                        raise ProjectCatalogUnavailable("source publication chain is invalid")
            if previous is not None:
                if previous["request_fingerprint"] != request_fingerprint:
                    raise ProjectCatalogConflict("source publication request ID was reused")
                publication = self._source_publication_row(previous)
                return publication, head is not None and head["fingerprint"] == publication.fingerprint
            if (head is None and expected_head_publication_fingerprint is not None) or (
                head is not None and (head["fingerprint"] != expected_head_publication_fingerprint
                                      or head["publisher_id"] != publisher_id)):
                raise ProjectCatalogConflict("source publication head changed")
            publication = ComponentSourcePublicationV1.create(
                schema_version=1, tenant_id=tenant_id, project_id=project_id,
                publisher_id=publisher_id, source_id=source_id,
                publication_id=f"component-source-publication-{uuid4()}",
                generation=1 if head is None else head["generation"] + 1,
                predecessor_publication_fingerprint=None if head is None else head["fingerprint"],
                record=checked,
            )
            connection.execute(
                "INSERT INTO component_source_publications(tenant_id,project_id,publisher_id,source_id,request_id,request_fingerprint,publication_id,generation,fingerprint,body) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (tenant_id, project_id, publisher_id, source_id, request_id, request_fingerprint,
                 publication.publication_id, publication.generation, publication.fingerprint,
                 publication.model_dump_json()),
            )
            connection.execute(
                "INSERT INTO component_source_heads(tenant_id,project_id,source_id,publisher_id,publication_id,generation,fingerprint) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(tenant_id,project_id,source_id) DO UPDATE SET "
                "publisher_id=excluded.publisher_id,publication_id=excluded.publication_id,"
                "generation=excluded.generation,fingerprint=excluded.fingerprint",
                (tenant_id, project_id, source_id, publisher_id, publication.publication_id,
                 publication.generation, publication.fingerprint),
            )
        return publication, True

    @staticmethod
    def _plan_revision_head_checked(connection: sqlite3.Connection, *, tenant_id: str,
                                    project_id: str, conversation_id: str) -> sqlite3.Row | None:
        head = connection.execute(
            "SELECT * FROM schematic_plan_revision_heads WHERE tenant_id=? AND project_id=? AND conversation_id=?",
            (tenant_id, project_id, conversation_id),
        ).fetchone()
        latest = connection.execute(
            "SELECT MAX(revision_number) FROM schematic_plan_revisions WHERE tenant_id=? AND project_id=? AND conversation_id=?",
            (tenant_id, project_id, conversation_id),
        ).fetchone()[0]
        if (head is None and latest is not None) or (head is not None and head["revision_number"] != latest):
            raise ProjectCatalogUnavailable("schematic plan revision head is invalid")
        return head

    @staticmethod
    def _revision_project(row: sqlite3.Row, *, is_current: bool) -> dict[str, object]:
        from embedded_copilot.engineering_copilot.schematic_planning import SchematicPlanningState
        from embedded_copilot.engineering_copilot import DesignTruth
        decision = row["decision_body"] if "decision_body" in row.keys() else None
        planning = SchematicPlanningState.model_validate_json(decision or row["planning_body"])
        base = DesignTruth.model_validate_json(row["base_truth_body"])
        selected = planning.approved_plan or planning.proposal
        if (selected.plan_id != row["plan_id"] or selected.fingerprint != row["plan_fingerprint"]
                or row["revision_number"] < 1):
            raise ProjectCatalogUnavailable("schematic plan revision body is invalid")
        return {"schema_version": 2, "revision_id": row["revision_id"],
                "revision_number": row["revision_number"],
                "predecessor_revision_id": row["predecessor_revision_id"],
                "plan_id": row["plan_id"], "plan_fingerprint": row["plan_fingerprint"],
                "planning": planning, "base_design_truth": base,
                "is_current": is_current, "request_fingerprint": row["request_fingerprint"],
                "predecessor_conversation_body": row["predecessor_conversation_body"]}

    def schematic_plan_revision_head(self, *, tenant_id: str, project_id: str,
                                     conversation_id: str, requester: str) -> dict[str, object] | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            head = self._plan_revision_head_checked(connection, tenant_id=tenant_id,
                project_id=project_id, conversation_id=conversation_id)
            if head is None:
                return None
            row = connection.execute(
                "SELECT r.*,d.planning_body AS decision_body FROM schematic_plan_revisions r "
                "LEFT JOIN schematic_plan_revision_decisions d ON d.revision_id=r.revision_id "
                "WHERE r.revision_id=? AND r.tenant_id=? AND r.project_id=? AND r.conversation_id=?",
                (head["revision_id"], tenant_id, project_id, conversation_id),
            ).fetchone()
            if row is None or row["revision_number"] != head["revision_number"]:
                raise ProjectCatalogUnavailable("schematic plan revision head is invalid")
        return self._revision_project(row, is_current=True)

    def schematic_plan_revision(self, *, tenant_id: str, project_id: str,
                                conversation_id: str, requester: str, revision_id: str,
                                plan_id: str, plan_fingerprint: str) -> dict[str, object]:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            head = self._plan_revision_head_checked(connection, tenant_id=tenant_id,
                project_id=project_id, conversation_id=conversation_id)
            row = connection.execute(
                "SELECT r.*,d.planning_body AS decision_body FROM schematic_plan_revisions r "
                "LEFT JOIN schematic_plan_revision_decisions d ON d.revision_id=r.revision_id "
                "WHERE r.tenant_id=? AND r.project_id=? AND r.conversation_id=? "
                "AND r.revision_id=? AND r.plan_id=? AND r.plan_fingerprint=?",
                (tenant_id, project_id, conversation_id, revision_id, plan_id, plan_fingerprint),
            ).fetchone()
        if row is None:
            raise ProjectCatalogUnavailable("schematic plan revision is unavailable")
        return self._revision_project(row, is_current=head is not None and head["revision_id"] == revision_id)

    def schematic_plan_revision_by_request(self, *, tenant_id: str, project_id: str,
                                            conversation_id: str, requester: str,
                                            request_id: str, request_fingerprint: str) -> dict[str, object] | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            head = self._plan_revision_head_checked(connection, tenant_id=tenant_id,
                project_id=project_id, conversation_id=conversation_id)
            row = connection.execute(
                "SELECT r.*,d.planning_body AS decision_body FROM schematic_plan_revisions r "
                "LEFT JOIN schematic_plan_revision_decisions d ON d.revision_id=r.revision_id "
                "WHERE r.tenant_id=? AND r.project_id=? AND r.conversation_id=? AND r.request_id=?",
                (tenant_id, project_id, conversation_id, request_id),
            ).fetchone()
        if row is None:
            return None
        if row["request_fingerprint"] != request_fingerprint:
            raise ProjectCatalogConflict("schematic plan request ID was reused")
        return self._revision_project(row, is_current=head is not None and head["revision_id"] == row["revision_id"])

    @staticmethod
    def _schematic_receipt_references(body: str, *, plan_id: str,
                                      plan_fingerprint: str) -> tuple[dict[str, object], ...]:
        try:
            payload = json.loads(body)
            planning = payload.get("schematic_planning")
            if not isinstance(planning, dict):
                return ()
            plan = planning.get("approved_plan") or planning.get("proposal")
            if not isinstance(plan, dict) or (plan.get("plan_id"), plan.get("fingerprint")) != (
                    plan_id, plan_fingerprint):
                raise ProjectCatalogUnavailable("schematic receipt history plan binding is invalid")
            production = payload.get("schematic_production")
            if not isinstance(production, dict):
                return ()
            refs = []
            for kind, field in (("GENERATION", "generation_receipt"),
                                ("ERC", "erc_receipt")):
                receipt = production.get(field)
                if receipt is None:
                    continue
                if not isinstance(receipt, dict) or receipt.get("schematic_plan_fingerprint") != plan_fingerprint:
                    raise ProjectCatalogUnavailable("schematic receipt history binding is invalid")
                if kind == "GENERATION" and receipt.get("schematic_plan_id") != plan_id:
                    raise ProjectCatalogUnavailable("schematic receipt history binding is invalid")
                refs.append({"kind": kind, "receipt_id": receipt["receipt_id"],
                             "fingerprint": receipt["fingerprint"],
                             "operation_id": receipt["operation_id"],
                             "operation_receipt_fingerprint": receipt["operation_receipt_fingerprint"]})
            return tuple(refs)
        except (KeyError, TypeError, ValueError) as error:
            raise ProjectCatalogUnavailable("schematic receipt history is invalid") from error

    def schematic_plan_revision_receipts(self, *, tenant_id: str, project_id: str,
                                         conversation_id: str, requester: str,
                                         revision_id: str, plan_id: str,
                                         plan_fingerprint: str) -> tuple[dict[str, object], ...]:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT revision_number FROM schematic_plan_revisions WHERE tenant_id=? AND project_id=? "
                "AND conversation_id=? AND revision_id=? AND plan_id=? AND plan_fingerprint=?",
                (tenant_id, project_id, conversation_id, revision_id, plan_id, plan_fingerprint),
            ).fetchone()
            if row is None:
                raise ProjectCatalogUnavailable("schematic plan revision is unavailable")
            successor = connection.execute(
                "SELECT predecessor_conversation_body FROM schematic_plan_revisions WHERE tenant_id=? "
                "AND project_id=? AND conversation_id=? AND revision_number=?",
                (tenant_id, project_id, conversation_id, row["revision_number"] + 1),
            ).fetchone()
            if successor is None:
                current = connection.execute(
                    "SELECT body FROM conversations WHERE tenant_id=? AND project_id=? AND conversation_id=? AND requester=?",
                    (tenant_id, project_id, conversation_id, requester),
                ).fetchone()
                if current is None:
                    raise ProjectCatalogUnavailable("schematic plan conversation is unavailable")
                body = current["body"]
            else:
                body = successor["predecessor_conversation_body"]
        return self._schematic_receipt_references(body, plan_id=plan_id,
            plan_fingerprint=plan_fingerprint)

    def schematic_plan_legacy_snapshot(self, *, tenant_id: str, project_id: str,
                                       conversation_id: str, requester: str,
                                       plan_id: str | None = None,
                                       plan_fingerprint: str | None = None) -> dict[str, object] | None:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT * FROM schematic_plan_legacy_snapshots WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                (tenant_id, project_id, conversation_id),
            ).fetchone()
        if row is None:
            return None
        if (plan_id is not None and plan_id != row["plan_id"]) or (
                plan_fingerprint is not None and plan_fingerprint != row["plan_fingerprint"]):
            raise ProjectCatalogUnavailable("legacy schematic plan selector is unavailable")
        original = json.loads(row["conversation_body"])
        planning = original.get("schematic_planning")
        if not isinstance(planning, dict):
            raise ProjectCatalogUnavailable("legacy schematic plan snapshot is invalid")
        plan = planning.get("approved_plan") or planning.get("proposal")
        if not isinstance(plan, dict) or (plan.get("plan_id"), plan.get("fingerprint")) != (
                row["plan_id"], row["plan_fingerprint"]):
            raise ProjectCatalogUnavailable("legacy schematic plan snapshot is invalid")
        return {"schema_version": 2, "revision_id": None, "revision_number": 0,
                "predecessor_revision_id": None, "plan_id": row["plan_id"],
                "plan_fingerprint": row["plan_fingerprint"], "is_current": False,
                "currentness": "STALE", "planning": planning,
                "receipt_references": self._schematic_receipt_references(
                    row["conversation_body"], plan_id=row["plan_id"],
                    plan_fingerprint=row["plan_fingerprint"])}

    def schematic_plan_revision_history(self, *, tenant_id: str, project_id: str,
                                        conversation_id: str, requester: str) -> tuple[dict[str, object], ...]:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            head = self._plan_revision_head_checked(connection, tenant_id=tenant_id,
                project_id=project_id, conversation_id=conversation_id)
            rows = connection.execute(
                "SELECT r.*,d.planning_body AS decision_body FROM schematic_plan_revisions r "
                "LEFT JOIN schematic_plan_revision_decisions d ON d.revision_id=r.revision_id "
                "WHERE r.tenant_id=? AND r.project_id=? AND r.conversation_id=? ORDER BY r.revision_number",
                (tenant_id, project_id, conversation_id),
            ).fetchall()
        return tuple(self._revision_project(row, is_current=head is not None and
            head["revision_id"] == row["revision_id"]) for row in rows)

    def selection_schematic_plan_anchor(self, *, tenant_id, project_id, conversation_id, requester):
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute("SELECT * FROM selection_schematic_plan_anchors WHERE tenant_id=? AND project_id=? AND conversation_id=?", (tenant_id,project_id,conversation_id)).fetchone()
            if row is None:
                return None
            value = dict(row)
            original = self._conversation_body(value["conversation_body"])
            planning = original.schematic_planning
            old = None if planning is None else planning.approved_plan or planning.proposal
            if old is None or (old.plan_id,old.fingerprint) != (value["plan_id"],value["plan_fingerprint"]) or planning.model_dump_json() != value["planning_body"]:
                raise ProjectCatalogUnavailable("selection Plan anchor invalid")
            return {**value,"planning":planning}

    def append_schematic_plan_revision(self, *, before: ConversationDetail,
                                       after: ConversationDetail, base_truth: DesignTruth,
                                       revision_id: str, request_id: str,
                                       request_fingerprint: str,
                                       expected_head: tuple[str | None, str | None, str | None],
                                       expected_source_publications: tuple[tuple[str, str], ...]) -> dict[str, object]:
        planning = after.schematic_planning
        if planning is None or planning.status.value != "REVIEW":
            raise ProjectCatalogConflict("new schematic revision must be reviewable")
        plan = planning.proposal
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, before.project_id, before.tenant_id, before.requester)
            prior = connection.execute(
                "SELECT r.*,d.planning_body AS decision_body FROM schematic_plan_revisions r "
                "LEFT JOIN schematic_plan_revision_decisions d ON d.revision_id=r.revision_id "
                "WHERE r.tenant_id=? AND r.project_id=? AND r.conversation_id=? AND r.request_id=?",
                (before.tenant_id, before.project_id, before.conversation_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior["request_fingerprint"] != request_fingerprint:
                    raise ProjectCatalogConflict("schematic plan request ID was reused")
                head = self._plan_revision_head_checked(connection, tenant_id=before.tenant_id,
                    project_id=before.project_id, conversation_id=before.conversation_id)
                return self._revision_project(prior, is_current=head is not None and
                    head["revision_id"] == prior["revision_id"])
            row = connection.execute(
                "SELECT body FROM conversations WHERE tenant_id=? AND project_id=? AND conversation_id=? AND requester=?",
                (before.tenant_id, before.project_id, before.conversation_id, before.requester),
            ).fetchone()
            if row is None or self._conversation_body(row["body"]).fingerprint != before.fingerprint:
                raise ProjectCatalogConflict("schematic plan conversation changed")
            head = self._plan_revision_head_checked(connection, tenant_id=before.tenant_id,
                project_id=before.project_id, conversation_id=before.conversation_id)
            anchor = None
            prior_planning = before.schematic_planning
            if prior_planning is None:
                anchor = connection.execute("SELECT * FROM selection_schematic_plan_anchors WHERE tenant_id=? AND project_id=? AND conversation_id=?", (before.tenant_id,before.project_id,before.conversation_id)).fetchone()
                if anchor is not None:
                    original = self._conversation_body(anchor["conversation_body"])
                    prior_planning = original.schematic_planning
                    anchored_plan = None if prior_planning is None else prior_planning.approved_plan or prior_planning.proposal
                    if anchored_plan is None or (anchored_plan.plan_id,anchored_plan.fingerprint) != (anchor["plan_id"],anchor["plan_fingerprint"]) or prior_planning.model_dump_json() != anchor["planning_body"] or anchor["revision_id"] != (None if head is None else head["revision_id"]):
                        raise ProjectCatalogUnavailable("selection Plan anchor changed")
            if head is None:
                legacy = prior_planning
                old_plan = None if legacy is None else legacy.approved_plan or legacy.proposal
                actual = (None, None if old_plan is None else old_plan.plan_id,
                          None if old_plan is None else old_plan.fingerprint)
            else:
                current = connection.execute(
                    "SELECT * FROM schematic_plan_revisions WHERE revision_id=?",
                    (head["revision_id"],),
                ).fetchone()
                if current is None or current["revision_number"] != head["revision_number"]:
                    raise ProjectCatalogUnavailable("schematic plan revision head is invalid")
                actual = (head["revision_id"], current["plan_id"], current["plan_fingerprint"])
                old_plan = prior_planning
                old_plan = None if old_plan is None else old_plan.approved_plan or old_plan.proposal
                if old_plan is None or (old_plan.plan_id, old_plan.fingerprint) != actual[1:]:
                    raise ProjectCatalogUnavailable("schematic plan conversation head is invalid")
                projected = connection.execute(
                    "SELECT r.*,d.planning_body AS decision_body FROM schematic_plan_revisions r "
                    "LEFT JOIN schematic_plan_revision_decisions d ON d.revision_id=r.revision_id "
                    "WHERE r.revision_id=? AND r.tenant_id=? AND r.project_id=? AND r.conversation_id=?",
                    (head["revision_id"], before.tenant_id, before.project_id, before.conversation_id),
                ).fetchone()
                if projected is None or prior_planning != self._revision_project(
                        projected, is_current=True)["planning"]:
                    raise ProjectCatalogUnavailable("schematic plan conversation decision head is invalid")
            if actual != expected_head:
                raise ProjectCatalogConflict("schematic plan expected head changed")
            for source_id, publication_fingerprint in expected_source_publications:
                source_head = connection.execute(
                    "SELECT fingerprint FROM component_source_heads WHERE tenant_id=? AND project_id=? AND source_id=?",
                    (before.tenant_id, before.project_id, source_id),
                ).fetchone()
                if source_head is None or source_head["fingerprint"] != publication_fingerprint:
                    raise ProjectCatalogConflict("source publication changed during replan")
            if head is None and old_plan is not None:
                legacy_body = row["body"] if anchor is None else anchor["conversation_body"]
                early = connection.execute(
                    "SELECT conversation_body FROM schematic_legacy_owner_snapshots "
                    "WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                    (before.tenant_id, before.project_id, before.conversation_id),
                ).fetchone()
                if early is not None:
                    original = self._conversation_body(early["conversation_body"])
                    early_state = original.schematic_planning
                    early_plan = None if early_state is None else early_state.approved_plan or early_state.proposal
                    if (early_plan is not None and (early_plan.plan_id, early_plan.fingerprint)
                            == (old_plan.plan_id, old_plan.fingerprint)):
                        legacy_body = early["conversation_body"]
                saved = connection.execute(
                    "SELECT plan_id,plan_fingerprint,conversation_body FROM schematic_plan_legacy_snapshots "
                    "WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                    (before.tenant_id, before.project_id, before.conversation_id),
                ).fetchone()
                if saved is not None:
                    saved_detail = self._conversation_body(saved["conversation_body"])
                    saved_state = saved_detail.schematic_planning
                    saved_plan = None if saved_state is None else saved_state.approved_plan or saved_state.proposal
                    if (saved["plan_id"], saved["plan_fingerprint"]) != (
                            old_plan.plan_id, old_plan.fingerprint) or saved_plan is None or (
                            saved_plan.plan_id, saved_plan.fingerprint) != (
                            old_plan.plan_id, old_plan.fingerprint):
                        raise ProjectCatalogUnavailable("legacy schematic plan snapshot head changed")
                if saved is None:
                    connection.execute(
                        "INSERT INTO schematic_plan_legacy_snapshots(tenant_id,project_id,conversation_id,plan_id,plan_fingerprint,conversation_body) VALUES(?,?,?,?,?,?)",
                        (before.tenant_id, before.project_id, before.conversation_id,
                         old_plan.plan_id, old_plan.fingerprint, legacy_body),
                    )
            number = 1 if head is None else head["revision_number"] + 1
            connection.execute(
                "INSERT INTO schematic_plan_revisions(tenant_id,project_id,conversation_id,revision_id,revision_number,predecessor_revision_id,request_id,request_fingerprint,plan_id,plan_fingerprint,planning_body,base_truth_body,predecessor_conversation_body,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (before.tenant_id, before.project_id, before.conversation_id,
                 revision_id, number, None if head is None else head["revision_id"],
                 request_id, request_fingerprint, plan.plan_id, plan.fingerprint,
                 planning.model_dump_json(), base_truth.model_dump_json(), row["body"],
                 after.updated_at.isoformat()),
            )
            connection.execute(
                "INSERT INTO schematic_plan_revision_heads(tenant_id,project_id,conversation_id,revision_id,revision_number) "
                "VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,project_id,conversation_id) DO UPDATE SET "
                "revision_id=excluded.revision_id,revision_number=excluded.revision_number",
                (before.tenant_id, before.project_id, before.conversation_id, revision_id, number),
            )
            connection.execute(
                "UPDATE conversations SET updated_at=?, body=? WHERE conversation_id=?",
                (after.updated_at.isoformat(), after.model_dump_json(), before.conversation_id),
            )
        return self.schematic_plan_revision(tenant_id=before.tenant_id, project_id=before.project_id,
            conversation_id=before.conversation_id, requester=before.requester,
            revision_id=revision_id, plan_id=plan.plan_id, plan_fingerprint=plan.fingerprint)

    def decide_schematic_plan_revision(self, *, before: ConversationDetail,
                                       after: ConversationDetail, revision_id: str,
                                       plan_id: str, plan_fingerprint: str,
                                       decision: str,
                                       expected_source_publications: tuple[tuple[str, str], ...]) -> dict[str, object]:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, before.project_id, before.tenant_id, before.requester)
            row = connection.execute(
                "SELECT * FROM schematic_plan_revisions WHERE tenant_id=? AND project_id=? AND conversation_id=? "
                "AND revision_id=? AND plan_id=? AND plan_fingerprint=?",
                (before.tenant_id, before.project_id, before.conversation_id,
                 revision_id, plan_id, plan_fingerprint),
            ).fetchone()
            if row is None:
                raise ProjectCatalogUnavailable("schematic plan revision is unavailable")
            old_decision = connection.execute(
                "SELECT * FROM schematic_plan_revision_decisions WHERE revision_id=?", (revision_id,),
            ).fetchone()
            head = self._plan_revision_head_checked(connection, tenant_id=before.tenant_id,
                project_id=before.project_id, conversation_id=before.conversation_id)
            if old_decision is not None:
                if old_decision["decision"] != decision:
                    raise ProjectCatalogConflict("schematic plan revision was already decided")
                projected = dict(row)
                projected["decision_body"] = old_decision["planning_body"]
                return self._revision_project(projected, is_current=head is not None and
                    head["revision_id"] == revision_id)
            if head is None or head["revision_id"] != revision_id:
                raise ProjectCatalogConflict("schematic plan decision is not current")
            current = connection.execute(
                "SELECT body FROM conversations WHERE tenant_id=? AND project_id=? AND conversation_id=? AND requester=?",
                (before.tenant_id, before.project_id, before.conversation_id, before.requester),
            ).fetchone()
            if current is None or self._conversation_body(current["body"]).fingerprint != before.fingerprint:
                raise ProjectCatalogConflict("schematic plan conversation changed")
            state = before.schematic_planning
            durable = self._revision_project(dict(row, decision_body=None), is_current=True)
            if state != durable["planning"]:
                raise ProjectCatalogUnavailable("schematic plan conversation decision head is invalid")
            if (state is None or state.status.value != "REVIEW"
                    or state.proposal.plan_id != plan_id or state.proposal.fingerprint != plan_fingerprint
                    or after.schematic_planning is None
                    or after.schematic_planning.approval is None
                    or after.schematic_planning.approval.decision.value != decision):
                raise ProjectCatalogConflict("schematic plan decision binding changed")
            for source_id, publication_fingerprint in expected_source_publications:
                source_head = connection.execute(
                    "SELECT fingerprint FROM component_source_heads WHERE tenant_id=? AND project_id=? AND source_id=?",
                    (before.tenant_id, before.project_id, source_id),
                ).fetchone()
                if source_head is None or source_head["fingerprint"] != publication_fingerprint:
                    raise ProjectCatalogConflict("source publication changed during decision")
            connection.execute(
                "INSERT INTO schematic_plan_revision_decisions(revision_id,decision,planning_body,predecessor_conversation_body) VALUES(?,?,?,?)",
                (revision_id, decision, after.schematic_planning.model_dump_json(), current["body"]),
            )
            connection.execute(
                "UPDATE conversations SET updated_at=?, body=? WHERE conversation_id=?",
                (after.updated_at.isoformat(), after.model_dump_json(), before.conversation_id),
            )
        return self.schematic_plan_revision(tenant_id=before.tenant_id, project_id=before.project_id,
            conversation_id=before.conversation_id, requester=before.requester,
            revision_id=revision_id, plan_id=plan_id, plan_fingerprint=plan_fingerprint)

    def save_conversation(self, value: ConversationDetail) -> None:
        body = value.model_dump_json()
        if value.engineering_plan is not None and value.engineering_plan.model_operation_schema_version == 1:
            exclude = {"engineering_plan": {"model_operation_schema_version", "model_operation_bindings"}}
            for omitted in (set(), set(type(value).model_fields) - value.model_fields_set):
                candidate = value.model_dump(mode="json", exclude={**exclude, **{name: True for name in omitted}})
                canonical_body = json.dumps(
                    {key: item for key, item in candidate.items() if key != "fingerprint"},
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
                if candidate["fingerprint"] == "sha256:" + hashlib.sha256(canonical_body).hexdigest():
                    body = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
                    break
            else:
                raise ValueError("legacy conversation fingerprint cannot be preserved")
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO conversations(conversation_id, project_id, tenant_id, requester, updated_at, body) VALUES(?,?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET updated_at=excluded.updated_at, body=excluded.body",
                (value.conversation_id, value.project_id, value.tenant_id, value.requester, value.updated_at.isoformat(), body),
            )

    def save_conversation_if_fingerprint(self, value: ConversationDetail, *, expected_conversation_fingerprint: str,
                                         expected_source_publications: tuple[tuple[str, str], ...] = ()) -> None:
        """Atomically update a current Owner conversation row without losing concurrent review state."""
        body = value.model_dump_json()
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            self._circuit_owner(connection, value.project_id, value.tenant_id, value.requester)
            row = connection.execute(
                "SELECT body FROM conversations WHERE project_id=? AND tenant_id=? AND conversation_id=? AND requester=?",
                (value.project_id, value.tenant_id, value.conversation_id, value.requester),
            ).fetchone()
            previous = None if row is None else self._conversation_body(row["body"])
            if previous is None or previous.fingerprint != expected_conversation_fingerprint:
                raise ProjectCatalogConflict("project supply conversation changed")
            for source_id, publication_fingerprint in expected_source_publications:
                head = connection.execute(
                    "SELECT fingerprint FROM component_source_heads WHERE tenant_id=? AND project_id=? AND source_id=?",
                    (value.tenant_id, value.project_id, source_id),
                ).fetchone()
                if head is None or head["fingerprint"] != publication_fingerprint:
                    raise ProjectCatalogConflict("source publication changed during conversation update")
            if (type(previous.project_supply) is ProjectSupplyStateV2
                    and type(value.project_supply) is ProjectSupplyStateV3):
                candidate = previous.project_supply.candidate
                connection.execute(
                    "INSERT OR IGNORE INTO schematic_legacy_owner_snapshots"
                    "(tenant_id,project_id,conversation_id,candidate_id,candidate_fingerprint,conversation_body) "
                    "VALUES(?,?,?,?,?,?)",
                    (value.tenant_id, value.project_id, value.conversation_id,
                     candidate.candidate_id, candidate.fingerprint, row["body"]),
                )
            if (type(value.project_supply) is ProjectSupplyStateV3
                    and previous.project_supply is not None
                    and previous.project_supply.candidate.fingerprint
                        != value.project_supply.candidate.fingerprint):
                planning = previous.schematic_planning
                old_plan = None if planning is None else planning.approved_plan or planning.proposal
                if old_plan is not None:
                    head = self._plan_revision_head_checked(connection,
                        tenant_id=value.tenant_id, project_id=value.project_id,
                        conversation_id=value.conversation_id)
                    if head is None:
                        legacy_body = row["body"]
                        early = connection.execute(
                            "SELECT conversation_body FROM schematic_legacy_owner_snapshots "
                            "WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                            (value.tenant_id, value.project_id, value.conversation_id),
                        ).fetchone()
                        if early is not None:
                            early_state = self._conversation_body(early["conversation_body"]).schematic_planning
                            early_plan = None if early_state is None else early_state.approved_plan or early_state.proposal
                            if (early_plan is not None and (early_plan.plan_id, early_plan.fingerprint)
                                    == (old_plan.plan_id, old_plan.fingerprint)):
                                legacy_body = early["conversation_body"]
                        saved = connection.execute(
                            "SELECT plan_id,plan_fingerprint,conversation_body FROM schematic_plan_legacy_snapshots "
                            "WHERE tenant_id=? AND project_id=? AND conversation_id=?",
                            (value.tenant_id, value.project_id, value.conversation_id),
                        ).fetchone()
                        if saved is not None:
                            saved_detail = self._conversation_body(saved["conversation_body"])
                            saved_state = saved_detail.schematic_planning
                            saved_plan = None if saved_state is None else saved_state.approved_plan or saved_state.proposal
                            if (saved["plan_id"], saved["plan_fingerprint"]) != (
                                    old_plan.plan_id, old_plan.fingerprint) or saved_plan is None or (
                                    saved_plan.plan_id, saved_plan.fingerprint) != (
                                    old_plan.plan_id, old_plan.fingerprint):
                                raise ProjectCatalogUnavailable("legacy schematic plan snapshot head changed")
                        if saved is None:
                            connection.execute(
                                "INSERT INTO schematic_plan_legacy_snapshots"
                                "(tenant_id,project_id,conversation_id,plan_id,plan_fingerprint,conversation_body) "
                                "VALUES(?,?,?,?,?,?)",
                                (value.tenant_id, value.project_id, value.conversation_id,
                                 old_plan.plan_id, old_plan.fingerprint, legacy_body),
                            )
            connection.execute(
                "UPDATE conversations SET updated_at=?, body=? WHERE conversation_id=?",
                (value.updated_at.isoformat(), body, value.conversation_id),
            )

    def legacy_project_supply_raw(self, *, tenant_id: str, project_id: str,
                                  conversation_id: str, requester: str,
                                  candidate_id: str, candidate_fingerprint: str) -> str | None:
        """Private exact-selector read of the first V2 Owner body displaced by V3."""
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            self._circuit_owner(connection, project_id, tenant_id, requester)
            row = connection.execute(
                "SELECT conversation_body FROM schematic_legacy_owner_snapshots "
                "WHERE tenant_id=? AND project_id=? AND conversation_id=? "
                "AND candidate_id=? AND candidate_fingerprint=?",
                (tenant_id, project_id, conversation_id, candidate_id, candidate_fingerprint),
            ).fetchone()
        if row is None:
            return None
        raw = row["conversation_body"]
        original = self._conversation_body(raw)
        state = original.project_supply
        if (type(state) is not ProjectSupplyStateV2
                or state.candidate.candidate_id != candidate_id
                or state.candidate.fingerprint != candidate_fingerprint):
            raise ProjectCatalogUnavailable("legacy project supply snapshot is invalid")
        return raw

    def conversation(self, conversation_id: str) -> ConversationDetail | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT body FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
        return None if row is None else self._conversation_body(row[0])

    def conversations(self, project_id: str, tenant_id: str, requester: str) -> tuple[ConversationDetail, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT body FROM conversations WHERE project_id = ? AND tenant_id = ? AND requester = ? ORDER BY updated_at DESC",
                (project_id, tenant_id, requester),
            ).fetchall()
        return tuple(self._conversation_body(row[0]) for row in rows)

    def delete_conversation(
        self,
        conversation_id: str,
        *,
        project_id: str,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail | None:
        """Delete one owned conversation and its session bindings.

        Conversation deletion is intentionally scoped to the catalog record. It
        never touches the project directory or any workspace files.
        """
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM conversations WHERE conversation_id = ? AND project_id = ? AND tenant_id = ? AND requester = ?",
                (conversation_id, project_id, tenant_id, requester),
            ).fetchone()
            if row is None:
                return None
            value = self._conversation_body(row[0])
            connection.execute(
                "DELETE FROM conversation_sessions WHERE conversation_id = ?",
                (conversation_id,),
            )
            connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ? AND project_id = ? AND tenant_id = ? AND requester = ?",
                (conversation_id, project_id, tenant_id, requester),
            )
            return value

    def bind_session(self, session_id: str, conversation_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO conversation_sessions(session_id, conversation_id) VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET conversation_id=excluded.conversation_id",
                (session_id, conversation_id),
            )

    def conversation_for_session(self, session_id: str) -> ConversationDetail | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT c.body FROM conversations c JOIN conversation_sessions s ON s.conversation_id=c.conversation_id WHERE s.session_id = ?",
                (session_id,),
            ).fetchone()
        return None if row is None else self._conversation_body(row[0])
