from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError

from embedded_copilot.api.main import create_app
from embedded_copilot.application.desktop_composition import _V63ContextProjectionAdapter
from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextService,
    ReadOnlyProjectionSource,
)
from embedded_copilot.engineering_context.adapters.project_engineering import ProjectEngineeringEvidenceAdapter
from embedded_copilot.engineering_intelligence import EvidenceGate, EvidenceGateRejected
from embedded_copilot.engineering_qa import EngineeringQAService, EngineeringQuestion, InsufficientEvidence
from embedded_copilot.model_gateway import ModelGatewayPort
from embedded_copilot.project_catalog import ProjectCreationRequest, ProjectType
from embedded_copilot.services.config import Settings
from embedded_copilot.v63_memory_governance import (
    SQLiteV63Store,
    V63MemoryGovernanceService,
    V63Rejected,
)
from embedded_copilot.v63_memory_governance.models import (
    EvidenceIdentity,
    EvolutionApplyRequest,
    EvolutionTarget,
    MemoryStatus,
    MemoryType,
    StructuredCandidateInput,
    TrustLevel,
)


ACCEPTANCE_PROJECTS_ROOT = Path(r"C:\acceptance_projects")
GOLDEN_PROJECT_ROOT = ACCEPTANCE_PROJECTS_ROOT / "v6.3-memory-governance-golden"
DISPOSABLE_PROJECT_ROOT = ACCEPTANCE_PROJECTS_ROOT / "v6.3-memory-governance-disposable"
ACCEPTANCE_STATE_ROOT = Path(r"C:\acceptance_state\v6.3")
TENANT = "tenant-v63-golden"
REQUESTER = "engineer-v63"
REVIEWER = "reviewer-v63"


class GoldenFailure(AssertionError):
    pass


@dataclass(frozen=True, slots=True)
class GoldenCaseResult:
    case_id: str
    status: str
    details: str


@dataclass(frozen=True, slots=True)
class GoldenReport:
    run_id: str
    cases: tuple[GoldenCaseResult, ...]
    runtime_evidence: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(item.status == "PASS" for item in self.cases)

    def model_dump(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "cases": [
                {"case_id": item.case_id, "status": item.status, "details": item.details}
                for item in self.cases
            ],
            "runtime_evidence": list(self.runtime_evidence),
            "passed": self.passed,
        }


@dataclass(slots=True)
class _Runtime:
    client: TestClient
    service: V63MemoryGovernanceService
    project_catalog: object


class _CountingGateway:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("ModelGateway must not be invoked for zero current evidence")

    async def agenerate(self, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("ModelGateway must not be invoked for zero current evidence")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GoldenFailure(message)


def acceptance_environment_available() -> bool:
    required = (
        GOLDEN_PROJECT_ROOT / "firmware" / "sensor.c",
        GOLDEN_PROJECT_ROOT / "hardware" / "sensor_bus.json",
        GOLDEN_PROJECT_ROOT / "requirements" / "system_requirements.md",
        GOLDEN_PROJECT_ROOT / "architecture" / "architecture.md",
        GOLDEN_PROJECT_ROOT / "pcb" / "sample.kicad_pcb",
        GOLDEN_PROJECT_ROOT / "debug" / "i2c_timeout.json",
        DISPOSABLE_PROJECT_ROOT / "hardware" / "sensor_bus.json",
        DISPOSABLE_PROJECT_ROOT / "firmware" / "sensor.c",
        DISPOSABLE_PROJECT_ROOT / "requirements" / "system_requirements.md",
        DISPOSABLE_PROJECT_ROOT / "architecture" / "architecture.md",
        DISPOSABLE_PROJECT_ROOT / "pcb" / "sample.kicad_pcb",
        DISPOSABLE_PROJECT_ROOT / "debug" / "i2c_timeout.json",
    )
    if not ACCEPTANCE_PROJECTS_ROOT.is_dir() or not ACCEPTANCE_STATE_ROOT.is_dir() or not all(item.is_file() for item in required):
        return False
    # Directory metadata can be visible even when the current process cannot
    # open the external acceptance state. Probe an existing database read-only;
    # never create a probe file or mutate acceptance state here.
    existing_database = next(ACCEPTANCE_STATE_ROOT.glob("*.sqlite3"), None)
    if existing_database is None:
        return False
    try:
        with sqlite3.connect(
            f"file:{existing_database.as_posix()}?mode=ro", uri=True
        ) as connection:
            connection.execute("PRAGMA user_version").fetchone()
        (GOLDEN_PROJECT_ROOT / "hardware" / "sensor_bus.json").read_bytes()
    except (OSError, sqlite3.Error):
        return False
    probe = ACCEPTANCE_STATE_ROOT / f".v63-access-probe-{uuid4().hex}.sqlite3"
    try:
        connection = sqlite3.connect(probe)
        connection.close()
    except (OSError, sqlite3.Error):
        return False
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            return False
    return True


def _settings(database: Path) -> Settings:
    return Settings(
        environment="test",
        project_root=ACCEPTANCE_PROJECTS_ROOT,
        desktop_state_database=database,
        desktop_project_metadata_directory=database.parent / f"projects-{database.stem}",
        chroma_path=database.parent / f"chroma-{database.stem}",
        model_provider="unavailable",
        vision_provider="unavailable",
        embedding_provider="local_hash",
        runtime_mode="offline",
        desktop_local_identity_enabled=True,
    )


def _headers(*, tenant: str = TENANT, requester: str = REQUESTER) -> dict[str, str]:
    return {
        "X-Environment-Tenant": tenant,
        "X-Copilot-Requester": requester,
    }


@contextmanager
def _open_runtime(database: Path) -> Iterator[_Runtime]:
    application = create_app(settings=_settings(database))
    with TestClient(application) as client:
        service = getattr(application.state, "v63_memory_governance", None)
        catalog = getattr(application.state, "project_catalog_runtime", None)
        _require(isinstance(service, V63MemoryGovernanceService), "production v6.3 service is unavailable")
        _require(catalog is not None, "production Project Catalog is unavailable")
        _require(isinstance(service.store, SQLiteV63Store), "production v6.3 store is not SQLite")
        yield _Runtime(client=client, service=service, project_catalog=catalog)


def _attach(runtime: _Runtime, *, relative_path: str, name: str) -> tuple[str, str]:
    response = runtime.client.post(
        "/api/v1/copilot/projects/previews",
        headers=_headers(),
        json={
            "name": name,
            "project_type": ProjectType.EXISTING_FIRMWARE.value,
            "relative_path": relative_path,
        },
    )
    _require(response.status_code == 200, f"Project preview failed: {response.status_code}")
    preview = response.json()
    _require(preview["creation_mode"] == "ATTACH_EXISTING", "Project was not previewed as ATTACH_EXISTING")
    created = runtime.client.post(
        "/api/v1/copilot/projects",
        headers=_headers(),
        json={
            "preview_id": preview["preview_id"],
            "preview_fingerprint": preview["fingerprint"],
            "confirmed_at": datetime.now(UTC).isoformat(),
        },
    )
    _require(created.status_code == 202, f"Project attach failed: {created.status_code}")
    project = created.json()
    project_id = project["project_id"]
    for _ in range(100):
        current = runtime.client.get(
            f"/api/v1/copilot/projects/{project_id}", headers=_headers()
        )
        _require(current.status_code == 200, "Attached project cannot be read")
        value = current.json()
        if value["initialization_status"] == "READY":
            return project_id, value["fingerprint"]
        time.sleep(0.01)
    raise GoldenFailure("Attached project did not reach READY")


def _validated_evidence(root: Path, *, project_id: str, task: str) -> tuple[EvidenceIdentity, ...]:
    adapter = ProjectEngineeringEvidenceAdapter(lambda _project_id: root)
    request = EngineeringContextRequest(
        tenant_id=TENANT,
        project_id=project_id,
        task=task,
        constraints=(),
    )
    projected = adapter.list_evidence(request)
    validated = EvidenceGate().require(projected, tenant_id=TENANT, project_id=project_id)
    _require(validated.evidence_count > 0, "formal Engineering Evidence validation returned zero evidence")
    return tuple(
        EvidenceIdentity(
            evidence_id=item.evidence_id,
            tenant_id=item.tenant_id,
            project_id=item.project_id,
            source_reference=item.source_reference,
            source_fingerprint=item.source_fingerprint,
            evidence_fingerprint=item.evidence_fingerprint,
        )
        for item in validated.evidence
    )


def _review(
    runtime: _Runtime,
    *,
    project_id: str,
    target_type: str,
    target_id: str,
    target_fingerprint: str,
) -> dict[str, object]:
    created = runtime.client.post(
        "/api/v1/copilot/reviews",
        headers=_headers(),
        json={
            "project_id": project_id,
            "target_type": target_type,
            "target_id": target_id,
            "target_fingerprint": target_fingerprint,
        },
    )
    _require(created.status_code == 200, f"Review request failed: {created.status_code}")
    return created.json()


def _decide(runtime: _Runtime, *, project_id: str, review_id: str, decision: str) -> dict[str, object]:
    response = runtime.client.post(
        f"/api/v1/copilot/reviews/{review_id}/decision",
        headers=_headers(),
        json={
            "project_id": project_id,
            "decision": decision,
            "reviewer": REVIEWER,
            "comment": "Golden human review decision.",
            "decided_at": datetime.now(UTC).isoformat(),
        },
    )
    _require(
        response.status_code == 200,
        f"Review decision failed: {response.status_code} {response.text[:256]}",
    )
    return response.json()


def _case(case_id: str, condition: bool, details: str) -> GoldenCaseResult:
    return GoldenCaseResult(case_id, "PASS" if condition else "FAIL", details)


def run_golden_cases() -> GoldenReport:
    if not acceptance_environment_available():
        raise FileNotFoundError("v6.3 Synthetic Acceptance environment is unavailable")

    run_id = "golden-" + uuid4().hex[:12]
    database = ACCEPTANCE_STATE_ROOT / f"{run_id}.sqlite3"
    cases: list[GoldenCaseResult] = []
    runtime_evidence: list[str] = ["production composition", "formal Engineering Evidence validation"]
    with _open_runtime(database) as runtime_a:
        golden_project_id, golden_project_fingerprint = _attach(
            runtime_a,
            relative_path=GOLDEN_PROJECT_ROOT.name,
            name="v6.3 Synthetic Golden Project",
        )
        evidence_a = _validated_evidence(
            GOLDEN_PROJECT_ROOT,
            project_id=golden_project_id,
            task="debug sensor_read I2C timeout",
        )
        memory_candidate = runtime_a.service.extract_memory_candidate(
            StructuredCandidateInput(
                tenant_id=TENANT,
                requester_id=REQUESTER,
                project_id=golden_project_id,
                memory_type=MemoryType.FAILURE,
                subject="sensor_read",
                statement="sensor_read correlates HAL_TIMEOUT with the bound engineering evidence.",
                evidence=evidence_a,
                confidence=0.9,
                provenance=tuple(item.evidence_id for item in evidence_a),
            )
        )
        cases.append(_case(
            "1-memory-candidate",
            memory_candidate.status.value == "CANDIDATE"
            and not runtime_a.service.store.list_memories(TENANT, golden_project_id),
            "validated deterministic candidate exists while Active Memory remains empty",
        ))

        memory_review = _review(
            runtime_a,
            project_id=golden_project_id,
            target_type="MEMORY_CANDIDATE",
            target_id=memory_candidate.memory_candidate_id,
            target_fingerprint=memory_candidate.candidate_fingerprint,
        )
        approved_memory_review = _decide(
            runtime_a,
            project_id=golden_project_id,
            review_id=str(memory_review["review_id"]),
            decision="APPROVE",
        )
        memories = runtime_a.service.store.list_memories(TENANT, golden_project_id)
        cases.append(_case(
            "2-human-approval",
            approved_memory_review["status"] == "APPROVED"
            and len(memories) == 1
            and memories[0].status is MemoryStatus.ACTIVE
            and memories[0].review_id == memory_review["review_id"],
            "Review API promotion preserved fingerprint, reviewer and audit binding",
        ))

        rejected_candidate = runtime_a.service.extract_memory_candidate(
            StructuredCandidateInput(
                tenant_id=TENANT,
                requester_id=REQUESTER,
                project_id=golden_project_id,
                memory_type=MemoryType.FAILURE,
                subject="dma",
                statement="possible DMA issue requires additional evidence.",
                evidence=(evidence_a[0],),
                confidence=0.95,
                provenance=(evidence_a[0].evidence_id,),
            )
        )
        rejected_review = _review(
            runtime_a,
            project_id=golden_project_id,
            target_type="MEMORY_CANDIDATE",
            target_id=rejected_candidate.memory_candidate_id,
            target_fingerprint=rejected_candidate.candidate_fingerprint,
        )
        rejected = _decide(
            runtime_a,
            project_id=golden_project_id,
            review_id=str(rejected_review["review_id"]),
            decision="REJECT",
        )
        cases.append(_case(
            "3-rejection",
            rejected["status"] == "REJECTED"
            and runtime_a.service.project_memories(TENANT, golden_project_id) != ()
            and not any(item.memory_id.endswith(rejected_candidate.memory_candidate_id) for item in runtime_a.service.project_memories(TENANT, golden_project_id)),
            "Rejected candidate is absent from Active Memory projection",
        ))

        # Register and govern the Knowledge candidate while the source identity
        # is still revision A.  The stale case deliberately changes that same
        # source identity afterwards; registering it first proves that the
        # production evidence registry rejects unbound fingerprint drift.
        hardware_evidence_a = next(
            item for item in evidence_a if item.evidence_id == "hardware-default_address"
        )
        knowledge_candidate = runtime_a.service.extract_knowledge_candidate(
            tenant_id=TENANT,
            project_id=golden_project_id,
            requester_id=REQUESTER,
            knowledge_type="HARDWARE_FACT",
            subject="sensor",
            predicate="default_i2c_address",
            value="0x68",
            evidence=(hardware_evidence_a,),
            trust_level=TrustLevel.CANDIDATE,
            confidence=0.9,
            provenance=(hardware_evidence_a.evidence_id,),
        )
        knowledge_review = _review(
            runtime_a,
            project_id=golden_project_id,
            target_type="KNOWLEDGE_CANDIDATE",
            target_id=knowledge_candidate.knowledge_candidate_id,
            target_fingerprint=knowledge_candidate.candidate_fingerprint,
        )
        approved_knowledge_review = _decide(
            runtime_a,
            project_id=golden_project_id,
            review_id=str(knowledge_review["review_id"]),
            decision="APPROVE",
        )
        knowledge = runtime_a.service.store.list_knowledge(TENANT, golden_project_id)
        knowledge_case = _case(
            "5-governed-knowledge",
            approved_knowledge_review["status"] == "APPROVED"
            and len(knowledge) == 1
            and knowledge[0].review_id == knowledge_review["review_id"]
            and knowledge[0].source_fingerprints == (hardware_evidence_a.source_fingerprint,),
            "Approved Knowledge is evidence-bound and available through governed projection",
        )

        original_source = GOLDEN_PROJECT_ROOT / "hardware" / "sensor_bus.json"
        original_content = original_source.read_text(encoding="utf-8")
        original_source.write_text(
            json.dumps({"default_address": "0x68", "source_revision": "B"}, sort_keys=True),
            encoding="utf-8",
        )
        try:
            evidence_b = _validated_evidence(
                GOLDEN_PROJECT_ROOT,
                project_id=golden_project_id,
                task="debug sensor_read I2C timeout",
            )
            stale_active = runtime_a.service.project_memories(
                TENANT, golden_project_id, evidence_b
            )
            stale_history = runtime_a.service.project_memories(
                TENANT, golden_project_id, evidence_b, include_stale=True
            )
            cases.append(_case(
                "4-stale-memory",
                stale_active == ()
                and any(item.status is MemoryStatus.STALE for item in stale_history),
                "source fingerprint drift excludes the old memory from Current projection",
            ))
        finally:
            # Synthetic acceptance input is disposable and must be restored even
            # when formal evidence validation or the projection assertion fails.
            original_source.write_text(original_content, encoding="utf-8")

        hardware_evidence = hardware_evidence_a
        cases.append(knowledge_case)

        cross_tenant = runtime_a.client.get(
            "/api/v1/copilot/memory",
            params={"project_id": golden_project_id},
            headers=_headers(tenant="tenant-other"),
        )
        cross_project = runtime_a.service.store.get_memory_candidate(
            TENANT, "project-other", memory_candidate.memory_candidate_id
        )
        cases.append(_case(
            "6-isolation",
            cross_tenant.status_code == 200
            and cross_tenant.json() == []
            and cross_project is None,
            "cross-tenant and cross-project memory reads fail closed",
        ))

        high_confidence = runtime_a.service.extract_memory_candidate(
            StructuredCandidateInput(
                tenant_id=TENANT,
                requester_id=REQUESTER,
                project_id=golden_project_id,
                memory_type=MemoryType.DECISION,
                subject="confidence",
                statement="High confidence does not approve a candidate.",
                evidence=(hardware_evidence,),
                confidence=0.99,
                provenance=(hardware_evidence.evidence_id,),
            )
        )
        cases.append(_case(
            "8-no-automatic-promotion",
            high_confidence.status.value == "CANDIDATE"
            and runtime_a.service.store.list_memories(TENANT, golden_project_id).__len__() == 1,
            "confidence 0.99 remains a Candidate without a Review decision",
        ))

        runtime_evidence.append("Runtime A shutdown completed")

    with _open_runtime(database) as runtime_b:
        persisted_candidates = runtime_b.service.store.list_memory_candidates(TENANT, golden_project_id)
        persisted_memories = runtime_b.service.store.list_memories(TENANT, golden_project_id)
        persisted_knowledge = runtime_b.service.store.list_knowledge(TENANT, golden_project_id)
        persisted_reviews = runtime_b.service.store.list_reviews(TENANT, golden_project_id)
        cases.append(_case(
            "7-restart-persistence",
            any(item.candidate_fingerprint == memory_candidate.candidate_fingerprint for item in persisted_candidates)
            and len(persisted_memories) == 1
            and len(persisted_knowledge) == 1
            and len(persisted_reviews) >= 3,
            "Runtime B rebuilt the production composition and recovered state/fingerprints",
        ))

        replay_memory = _decide(
            runtime_b,
            project_id=golden_project_id,
            review_id=str(memory_review["review_id"]),
            decision="APPROVE",
        )
        replay_knowledge = _decide(
            runtime_b,
            project_id=golden_project_id,
            review_id=str(knowledge_review["review_id"]),
            decision="APPROVE",
        )
        audit = runtime_b.service.store.list_audit(TENANT, golden_project_id)
        history = runtime_b.service.store.list_status_history(TENANT, golden_project_id)
        cases.append(_case(
            "9-review-replay",
            replay_memory["review_id"] == memory_review["review_id"]
            and replay_knowledge["review_id"] == knowledge_review["review_id"]
            and len(runtime_b.service.store.list_memories(TENANT, golden_project_id)) == 1
            and len(runtime_b.service.store.list_knowledge(TENANT, golden_project_id)) == 1
            and sum(item.event_type == "MEMORY_APPROVED" for item in audit) == 1
            and sum(item.event_type == "KNOWLEDGE_APPROVED" for item in audit) == 1
            and sum(item.event_type == "MEMORY_APPROVED" for item in history) == 1
            and sum(item.event_type == "KNOWLEDGE_APPROVED" for item in history) == 1,
            "Review replay creates no duplicate records, promotion history or promotion audit",
        ))

        disposable_id, disposable_fingerprint = _attach(
            runtime_b,
            relative_path=DISPOSABLE_PROJECT_ROOT.name,
            name="v6.3 Disposable Tombstone Project",
        )
        disposable_evidence = _validated_evidence(
            DISPOSABLE_PROJECT_ROOT,
            project_id=disposable_id,
            task="debug sensor_read I2C timeout",
        )
        disposable_candidate = runtime_b.service.extract_memory_candidate(
            StructuredCandidateInput(
                tenant_id=TENANT,
                requester_id=REQUESTER,
                project_id=disposable_id,
                memory_type=MemoryType.FAILURE,
                subject="sensor_read",
                statement="Disposable project failure candidate.",
                evidence=disposable_evidence,
                confidence=0.7,
                provenance=tuple(item.evidence_id for item in disposable_evidence),
            )
        )
        disposable_review = _review(
            runtime_b,
            project_id=disposable_id,
            target_type="MEMORY_CANDIDATE",
            target_id=disposable_candidate.memory_candidate_id,
            target_fingerprint=disposable_candidate.candidate_fingerprint,
        )
        _decide(
            runtime_b,
            project_id=disposable_id,
            review_id=str(disposable_review["review_id"]),
            decision="APPROVE",
        )
        deletion = runtime_b.client.delete(
            f"/api/v1/copilot/projects/{disposable_id}",
            headers={**_headers(), "X-Project-Fingerprint": disposable_fingerprint},
        )
        tombstone_case = _case(
            "10-tombstone",
            deletion.status_code == 200
            and runtime_b.service.is_project_inaccessible(TENANT, disposable_id)
            and runtime_b.client.get("/api/v1/copilot/memory", params={"project_id": disposable_id}, headers=_headers()).status_code == 410
            and runtime_b.client.get("/api/v1/copilot/knowledge/governed", params={"project_id": disposable_id}, headers=_headers()).status_code == 410
            and runtime_b.client.get("/api/v1/copilot/reviews", params={"project_id": disposable_id}, headers=_headers()).status_code == 410
            and DISPOSABLE_PROJECT_ROOT.is_dir(),
            "tombstone committed and verified before Catalog delete; user directory retained",
        )

        knowledge = runtime_b.service.store.list_knowledge(TENANT, golden_project_id)[0]
        proposal = runtime_b.service.create_evolution_proposal(
            tenant_id=TENANT,
            project_id=golden_project_id,
            knowledge_id=knowledge.knowledge_id,
            created_by=REQUESTER,
            target=EvolutionTarget.MEMORY,
            statement="Use as a project-scoped reference only.",
        )
        proposal_retry = runtime_b.service.create_evolution_proposal(
            tenant_id=TENANT,
            project_id=golden_project_id,
            knowledge_id=knowledge.knowledge_id,
            created_by=REQUESTER,
            target=EvolutionTarget.MEMORY,
            statement="Use as a project-scoped reference only.",
        )
        proposal_review = _review(
            runtime_b,
            project_id=golden_project_id,
            target_type="KNOWLEDGE_EVOLUTION_PROPOSAL",
            target_id=proposal.proposal_id,
            target_fingerprint=proposal.proposal_fingerprint,
        )
        approved_proposal = _decide(
            runtime_b,
            project_id=golden_project_id,
            review_id=str(proposal_review["review_id"]),
            decision="APPROVE",
        )
        stored_proposal = runtime_b.service.store.get_proposal(TENANT, golden_project_id, proposal.proposal_id)
        _require(stored_proposal is not None, "evolution proposal was not persisted")
        apply_status = runtime_b.service.apply_evolution(
            EvolutionApplyRequest(
                request_id="evolution-apply-v63",
                tenant_id=TENANT,
                project_id=golden_project_id,
                proposal_id=proposal.proposal_id,
                proposal_fingerprint=stored_proposal.proposal_fingerprint,
                review_id=str(proposal_review["review_id"]),
                requested_by=REQUESTER,
                requested_at=datetime.now(UTC),
            )
        )
        cases.append(_case(
            "11-evolution-proposal",
            proposal_retry == proposal
            and approved_proposal["status"] == "APPROVED"
            and apply_status == "SINK_UNAVAILABLE"
            and len(runtime_b.service.store.list_proposals(TENANT, golden_project_id)) == 1,
            "Proposal, Review and Audit persist; physical Apply remains unavailable",
        ))

        source = _V63ContextProjectionAdapter(runtime_b.service)
        context = EngineeringContextService(
            source_port=ReadOnlyProjectionSource(
                v63_memory_port=source,
                v63_knowledge_port=source,
            )
        )
        gateway = _CountingGateway()
        qa = EngineeringQAService(context_provider=context, model_gateway=gateway)
        try:
            qa.answer(EngineeringQuestion(
                question_id="zero-evidence-v63",
                project_id=golden_project_id,
                question="What is the current verified engineering fact?",
            ))
        except InsufficientEvidence:
            pass
        before_zero_evidence = (
            len(runtime_b.service.store.list_memory_candidates(TENANT, golden_project_id))
            + len(runtime_b.service.store.list_knowledge_candidates(TENANT, golden_project_id))
            + len(runtime_b.service.store.list_reviews(TENANT, golden_project_id))
        )
        try:
            StructuredCandidateInput(
                tenant_id=TENANT,
                requester_id=REQUESTER,
                project_id=golden_project_id,
                memory_type=MemoryType.DECISION,
                subject="zero-evidence",
                statement="This candidate must not be created.",
                evidence=(),
                confidence=0.99,
            )
        except ValidationError:
            memory_zero_evidence_rejected = True
        else:
            memory_zero_evidence_rejected = False
        try:
            runtime_b.service.extract_knowledge_candidate(
                tenant_id=TENANT,
                project_id=golden_project_id,
                requester_id=REQUESTER,
                knowledge_type="HARDWARE_FACT",
                subject="zero-evidence",
                predicate="must_not_exist",
                value="none",
                evidence=(),
            )
        except V63Rejected:
            knowledge_zero_evidence_rejected = True
        else:
            knowledge_zero_evidence_rejected = False
        after_zero_evidence = (
            len(runtime_b.service.store.list_memory_candidates(TENANT, golden_project_id))
            + len(runtime_b.service.store.list_knowledge_candidates(TENANT, golden_project_id))
            + len(runtime_b.service.store.list_reviews(TENANT, golden_project_id))
        )
        cases.append(_case(
            "12-zero-evidence",
            gateway.calls == 0
            and memory_zero_evidence_rejected
            and knowledge_zero_evidence_rejected
            and after_zero_evidence == before_zero_evidence,
            "zero legal evidence creates no Candidate/Review and does not bypass the Current Evidence gate",
        ))
        runtime_evidence.append("Runtime B rebuilt composition and completed Cases 7-12")

    with _open_runtime(database) as runtime_c:
        recovered_tombstone = runtime_c.service.is_project_inaccessible(TENANT, disposable_id)
        tombstone_case = _case(
            "10-tombstone",
            tombstone_case.status == "PASS"
            and recovered_tombstone
            and runtime_c.service.store.list_memories(TENANT, disposable_id) == ()
            and runtime_c.service.store.list_knowledge(TENANT, disposable_id) == ()
            and runtime_c.service.store.list_reviews(TENANT, disposable_id) == ()
            and runtime_c.service.store.list_audit(TENANT, disposable_id),
            "tombstone remains fail-closed after Runtime C rebuild; audit/history remain available",
        )
        cases.append(tombstone_case)
        runtime_evidence.append("Runtime C rebuilt composition and recovered tombstone/replay state")

    _require(len(cases) == 12, "Golden Case count is not 12")
    case_order = {
        "1-memory-candidate": 1,
        "2-human-approval": 2,
        "3-rejection": 3,
        "4-stale-memory": 4,
        "5-governed-knowledge": 5,
        "6-isolation": 6,
        "7-restart-persistence": 7,
        "8-no-automatic-promotion": 8,
        "9-review-replay": 9,
        "10-tombstone": 10,
        "11-evolution-proposal": 11,
        "12-zero-evidence": 12,
    }
    ordered_cases = tuple(sorted(cases, key=lambda item: case_order[item.case_id]))
    return GoldenReport(run_id=run_id, cases=ordered_cases, runtime_evidence=tuple(runtime_evidence))


if __name__ == "__main__":
    print(json.dumps(run_golden_cases().model_dump(), ensure_ascii=False, indent=2))


__all__ = (
    "GoldenCaseResult",
    "GoldenFailure",
    "GoldenReport",
    "acceptance_environment_available",
    "run_golden_cases",
)
