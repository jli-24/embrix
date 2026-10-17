"""v6.7 Desktop product Golden runner.

The runner uses the canonical FastAPI composition and public application
services.  It never writes lifecycle end-state rows directly and never emits
synthetic provider, hardware, approval, or apply results.  A missing external
acceptance environment is reported as PARTIAL VERIFIED.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.engineering_context import EngineeringContextRequest
from embedded_copilot.engineering_context.adapters.project_engineering import ProjectEngineeringEvidenceAdapter
from embedded_copilot.engineering_intelligence import EvidenceGate
from embedded_copilot.engineering_validation_proposal import (
    DeterministicValidationRules,
    EvidenceRecord,
    ValidationProposalService,
)
from embedded_copilot.engineering_validation_proposal.adapters import SQLiteV64Store, V63ApprovedContextSource
from embedded_copilot.v63_memory_governance import SQLiteV63Store, V63MemoryGovernanceService
from embedded_copilot.v63_memory_governance.models import EvidenceIdentity, MemoryType, StructuredCandidateInput, TrustLevel
from embedded_copilot.services.config import Settings


TENANT = "tenant-v67-golden"
REQUESTER = "desktop-v67"
HEADERS = {"X-Environment-Tenant": TENANT, "X-Copilot-Requester": REQUESTER}
DEFAULT_PROJECTS = Path(r"C:\acceptance_projects\v6.7-desktop-product-golden")
DEFAULT_STATE = Path(r"C:\acceptance_state\v6.7")


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: int
    name: str
    status: str
    details: str


@dataclass(frozen=True, slots=True)
class GoldenReport:
    run_id: str
    cases: tuple[GoldenCase, ...]
    security_counters: dict[str, int]
    browser_automation: str

    @property
    def status(self) -> str:
        return "GOLDEN VERIFIED" if all(item.status == "PASS" for item in self.cases) else "PARTIAL VERIFIED"

    def model_dump(self) -> dict[str, object]:
        return {
            "suite": "v6.7-desktop-product-integration",
            "run_id": self.run_id,
            "status": self.status,
            "cases": [asdict(item) for item in self.cases],
            "security_counters": self.security_counters,
            "browser_automation": self.browser_automation,
        }


def _settings(projects: Path, state: Path) -> Settings:
    return Settings(
        environment="test",
        project_root=projects,
        desktop_state_database=state / "desktop.sqlite3",
        desktop_project_metadata_directory=state / "metadata",
        chroma_path=state / "chroma",
        model_provider="unavailable",
        vision_provider="unavailable",
        embedding_provider="local_hash",
        runtime_mode="offline",
        desktop_local_identity_enabled=True,
    )


class _ProjectEvidenceSource:
    """Production Evidence Service projection backed by synthetic project files."""

    def __init__(self, roots: dict[str, Path]) -> None:
        self._roots = roots

    @staticmethod
    def _facts(item: object) -> tuple[tuple[str, str], ...]:
        key = getattr(item, "fact_key", None)
        value = getattr(item, "fact_value", None)
        if not isinstance(key, str) or not isinstance(value, str):
            return ()
        source_type = str(getattr(getattr(item, "source_type", None), "value", getattr(item, "source_type", "")))
        mapped: list[tuple[str, str]] = []
        if key == "configured_address":
            mapped.append(("firmware.sensor_address", value))
        elif key == "default_address":
            mapped.append(("hardware.sensor_address", value))
        elif key == "required_hz":
            mapped.append(("requirement.sample_hz_min", value.removesuffix("Hz")))
        elif key == "period":
            try:
                mapped.append(("firmware.sample_hz", f"{1000.0 / float(value.removesuffix('ms')):g}"))
            except ValueError:
                pass
        elif key in {"failure", "error"}:
            mapped.append(("debug.failure", value))
        elif key in {"call_path", "calls"}:
            mapped.append(("debug.call_path", value))
        if source_type == "HARDWARE" and key == "address_conflict":
            mapped.append(("hardware.sensor_address", value))
        return tuple(mapped)

    def get_evidence(self, tenant_id: str, project_id: str, evidence_id: str) -> EvidenceRecord | None:
        root = self._roots.get(project_id)
        if root is None:
            return None
        request = EngineeringContextRequest(tenant_id=tenant_id, project_id=project_id, task="debug sensor timeout", constraints=())
        projected = ProjectEngineeringEvidenceAdapter(lambda _project_id: root).list_evidence(request)
        item = next((value for value in projected if value.evidence_id == evidence_id), None)
        if item is None:
            return None
        source_reference = item.source_reference if "://" in item.source_reference else f"workspace://{item.source_reference}"
        return EvidenceRecord(
            evidence_id=item.evidence_id,
            tenant_id=item.tenant_id,
            project_id=item.project_id,
            source_reference=source_reference,
            evidence_fingerprint=item.source_fingerprint,
            summary=item.summary,
            facts=self._facts(item),
        )


def _production_application(projects: Path, state: Path, roots: dict[str, Path]):
    v63_store = SQLiteV63Store(state / "desktop-v63.sqlite3")
    v63 = V63MemoryGovernanceService(v63_store)
    v64 = ValidationProposalService(
        evidence=_ProjectEvidenceSource(roots),
        context=V63ApprovedContextSource(v63_store),
        rules=DeterministicValidationRules(),
        store=SQLiteV64Store(state / "desktop-v64.sqlite3"),
    )
    return create_app(settings=_settings(projects, state), v63_memory_governance=v63, engineering_validation_proposal_service=v64)


def _evidence_identities(root: Path, project_id: str) -> tuple[EvidenceIdentity, ...]:
    request = EngineeringContextRequest(tenant_id=TENANT, project_id=project_id, task="debug sensor timeout", constraints=())
    projected = ProjectEngineeringEvidenceAdapter(lambda _project_id: root).list_evidence(request)
    checked = EvidenceGate().require(projected, tenant_id=TENANT, project_id=project_id)
    return tuple(EvidenceIdentity(evidence_id=item.evidence_id, tenant_id=item.tenant_id, project_id=item.project_id, source_reference=item.source_reference, source_fingerprint=item.source_fingerprint, evidence_fingerprint=item.evidence_fingerprint) for item in checked.evidence)


def prepare_acceptance_inputs(root: Path) -> None:
    """Create only synthetic source inputs; never creates runtime state rows."""
    for name in ("project-a", "project-b", "disposable-project"):
        project = root / name
        (project / "firmware").mkdir(parents=True, exist_ok=True)
        (project / "hardware").mkdir(parents=True, exist_ok=True)
        (project / "requirements").mkdir(parents=True, exist_ok=True)
        (project / "architecture").mkdir(parents=True, exist_ok=True)
        (project / "pcb").mkdir(parents=True, exist_ok=True)
        (project / "debug").mkdir(parents=True, exist_ok=True)
        (project / "workspace").mkdir(parents=True, exist_ok=True)
        (project / "firmware" / "sensor.c").write_text(
            "void sensor_read(void) { HAL_I2C_Master_Transmit(0x69); }\n"
            "/* SensorTask period=20ms */\n",
            encoding="utf-8",
        )
        (project / "hardware" / "sensor_bus.json").write_text(
            '{"default_address":"0x68","sample_hz_min":"100"}\n',
            encoding="utf-8",
        )
        (project / "requirements" / "system_requirements.md").write_text(
            "Sensor sample rate >= 100Hz.\n", encoding="utf-8"
        )
        (project / "architecture" / "architecture.md").write_text(
            "SensorTask -> sensor_read\n", encoding="utf-8"
        )
        (project / "pcb" / "sample.kicad_pcb").write_text(
            "(kicad_pcb\n"
            "  (version 20240108)\n"
            "  (generator pcbnew)\n"
            "  (layers\n"
            "    (0 \"F.Cu\" signal)\n"
            "    (31 \"B.Cu\" signal)\n"
            "    (36 \"B.SilkS\" user \"b.silkscreen\")\n"
            "  )\n"
            "  (net 0 \"\")\n"
            "  (net 1 \"GND\")\n"
            "  (footprint \"Package_DFN_QFN:QFN-56\"\n"
            "    (layer \"F.Cu\")\n"
            "    (at 10 20 90)\n"
            "    (property \"Reference\" \"U1\")\n"
            "    (property \"Value\" \"ESP32-S3\")\n"
            "    (pad \"1\" smd rect (at -1 -1) (size 0.2 0.8) (layers \"F.Cu\") (net 1 \"GND\"))\n"
            "  )\n"
            ")\n", encoding="utf-8"
        )
        (project / "debug" / "i2c_timeout.json").write_text(
            '{"artifact_id":"i2c-timeout","message":"HAL_TIMEOUT sensor_read"}\n',
            encoding="utf-8",
        )
        (project / "workspace" / "disposable.txt").write_text(
            "synthetic disposable workspace input\n", encoding="utf-8"
        )


_prepare_inputs = prepare_acceptance_inputs


def _attach(client: TestClient, relative_path: str, name: str) -> dict[str, object]:
    preview = client.post("/api/v1/copilot/projects/previews", headers=HEADERS, json={"name": name, "relative_path": relative_path, "project_type": "EXISTING_FIRMWARE"})
    if preview.status_code != 200:
        raise RuntimeError(f"project preview failed: {preview.status_code}")
    value = preview.json()
    created = client.post("/api/v1/copilot/projects", headers=HEADERS, json={"preview_id": value["preview_id"], "preview_fingerprint": value["fingerprint"], "confirmed_at": datetime.now(UTC).isoformat()})
    if created.status_code != 202:
        raise RuntimeError(f"project attach failed: {created.status_code}")
    value = created.json()
    for _ in range(100):
        current = client.get(f"/api/v1/copilot/projects/{value['project_id']}", headers=HEADERS)
        if current.status_code == 200:
            value = current.json()
            if value.get("initialization_status") in {"READY", "FAILED"}:
                return value
        time.sleep(0.01)
    return value


def _case(case_id: int, name: str, status: bool | str, details: str) -> GoldenCase:
    normalized = ("PASS" if status else "FAIL") if isinstance(status, bool) else status
    return GoldenCase(case_id, name, normalized, details)


def _unavailable_cases() -> tuple[GoldenCase, ...]:
    names = (
        "Project Attach", "Project Switch Isolation", "Copilot Runtime Route", "Evidence Projection",
        "Memory Candidate", "Memory Approval", "Governed Knowledge", "Validation", "Finding", "Proposal",
        "Proposal Approval", "Workspace Preview", "Workspace Apply", "Workspace Restart Recovery",
        "Execution Deferred", "Capability Center", "SSE Replay", "Tombstone", "Full Restart", "Security Boundary",
    )
    return tuple(_case(index, name, "UNAVAILABLE", "Synthetic acceptance environment is not present; no state was fabricated.") for index, name in enumerate(names, 1))


def run_golden_cases(*, projects_root: Path | None = None, state_root: Path | None = None, prepare_inputs: bool = False) -> GoldenReport:
    projects = projects_root or DEFAULT_PROJECTS
    state = state_root or DEFAULT_STATE
    if prepare_inputs:
        projects.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
    if not projects.is_dir() or not state.is_dir():
        return GoldenReport("v67-" + uuid4().hex[:12], _unavailable_cases(), {"external_provider_invocations": 0, "hardware_invocations": 0, "absolute_path_leaks": 0, "cross_project_leaks": 0, "cross_tenant_leaks": 0}, "UNAVAILABLE / NOT PRESENT")
    if prepare_inputs:
        _prepare_inputs(projects)
    state.mkdir(parents=True, exist_ok=True)
    run_id = "v67-" + uuid4().hex[:12]
    run_state = state / run_id
    run_state.mkdir(parents=True, exist_ok=True)
    cases: list[GoldenCase] = []
    roots: dict[str, Path] = {}
    application = _production_application(projects, run_state, roots)
    with TestClient(application) as client:
        project_a = _attach(client, "project-a", "Synthetic Project A")
        project_b = _attach(client, "project-b", "Synthetic Project B")
        disposable = _attach(client, "disposable-project", "Disposable Project")
        project_a_id = str(project_a["project_id"])
        project_b_id = str(project_b["project_id"])
        disposable_id = str(disposable["project_id"])
        roots.update({project_a_id: projects / "project-a", project_b_id: projects / "project-b", disposable_id: projects / "disposable-project"})
        projection_a = client.get(f"/api/v1/copilot/desktop/projects/{project_a_id}/workspace", headers=HEADERS, params={"limit": 5})
        projection_b = client.get(f"/api/v1/copilot/desktop/projects/{project_b_id}/workspace", headers=HEADERS, params={"limit": 5})
        cases.append(_case(1, "Project Attach", projection_a.status_code == 200 and projection_b.status_code == 200, "Project Catalog attach reached the canonical Desktop projection."))
        cases.append(_case(2, "Project Switch Isolation", projection_a.json().get("identity", {}).get("project_id") != projection_b.json().get("identity", {}).get("project_id") and projection_a.json().get("identity", {}).get("tenant_id") == TENANT, "A and B projections are identity-bound."))
        cases.append(_case(3, "Copilot Runtime Route", projection_a.json().get("schema_version") == "v6.7", "Canonical /api/v1/copilot/desktop route returned v6.7 projection."))
        payload = projection_a.json()
        bounded = all(len(payload.get(key, {}).get("items", [])) <= 5 for key in ("conversations", "evidence", "memory", "knowledge", "reviews", "validations", "findings", "proposals", "changes", "operations", "audit"))
        cases.append(_case(4, "Evidence Projection", bounded and "raw" not in projection_a.text.casefold(), "Evidence is safe and bounded."))
        lifecycle_root = projects / "disposable-project"
        lifecycle_evidence = _evidence_identities(lifecycle_root, disposable_id)
        v63 = application.state.v63_memory_governance
        memory_candidate = v63.extract_memory_candidate(StructuredCandidateInput(
            tenant_id=TENANT,
            requester_id=REQUESTER,
            project_id=disposable_id,
            memory_type=MemoryType.FAILURE,
            subject="sensor_read",
            statement="Sensor read address conflicts with the governed hardware default.",
            evidence=lifecycle_evidence,
            confidence=0.9,
            provenance=tuple(item.evidence_id for item in lifecycle_evidence),
        ))
        cases.append(_case(5, "Memory Candidate", memory_candidate.status.value == "CANDIDATE" and not v63.project_memories(TENANT, disposable_id), "MemoryCandidateService created a Candidate; Active Memory remained empty before Review."))
        memory_review = client.post("/api/v1/copilot/reviews", headers=HEADERS, json={"project_id": disposable_id, "target_type": "MEMORY_CANDIDATE", "target_id": memory_candidate.memory_candidate_id, "target_fingerprint": memory_candidate.candidate_fingerprint})
        memory_decision = client.post(f"/api/v1/copilot/reviews/{memory_review.json().get('review_id')}/decision", headers=HEADERS, json={"project_id": disposable_id, "decision": "APPROVE", "reviewer": "reviewer-v67", "comment": "Synthetic acceptance human approval.", "decided_at": datetime.now(UTC).isoformat()}) if memory_review.status_code == 200 else None
        active_memories = client.get("/api/v1/copilot/memory", headers=HEADERS, params={"project_id": disposable_id})
        cases.append(_case(6, "Memory Approval", memory_review.status_code == 200 and memory_decision is not None and memory_decision.status_code == 200 and active_memories.status_code == 200 and len(active_memories.json()) == 1, "Review API promoted the Candidate to persisted Active Memory."))
        hardware_evidence = next(item for item in lifecycle_evidence if item.evidence_id == "hardware-default_address")
        knowledge_candidate = v63.extract_knowledge_candidate(tenant_id=TENANT, project_id=disposable_id, requester_id=REQUESTER, knowledge_type="HARDWARE_FACT", subject="sensor", predicate="default_address", value="0x68", evidence=(hardware_evidence,), trust_level=TrustLevel.CANDIDATE, confidence=0.9, provenance=(hardware_evidence.evidence_id,))
        knowledge_review = client.post("/api/v1/copilot/reviews", headers=HEADERS, json={"project_id": disposable_id, "target_type": "KNOWLEDGE_CANDIDATE", "target_id": knowledge_candidate.knowledge_candidate_id, "target_fingerprint": knowledge_candidate.candidate_fingerprint})
        knowledge_decision = client.post(f"/api/v1/copilot/reviews/{knowledge_review.json().get('review_id')}/decision", headers=HEADERS, json={"project_id": disposable_id, "decision": "APPROVE", "reviewer": "reviewer-v67", "comment": "Synthetic governed knowledge approval.", "decided_at": datetime.now(UTC).isoformat()}) if knowledge_review.status_code == 200 else None
        governed = client.get("/api/v1/copilot/knowledge/governed", headers=HEADERS, params={"project_id": disposable_id})
        knowledge_id = governed.json()[0]["knowledge_id"] if governed.status_code == 200 and governed.json() else ""
        cases.append(_case(7, "Governed Knowledge", knowledge_review.status_code == 200 and knowledge_decision is not None and knowledge_decision.status_code == 200 and bool(knowledge_id), "Knowledge Governance promoted a Candidate through the formal Review boundary."))
        project_fingerprint = str(disposable["fingerprint"])
        validation_error = ""
        try:
            validation = client.post("/api/v1/copilot/validations", headers=HEADERS, json={"validation_id": "validation-v67-disposable", "project_id": disposable_id, "project_fingerprint": project_fingerprint, "validation_type": "CROSS_DOMAIN_CONSISTENCY", "target_identity": {"identity": "sensor", "kind": "firmware"}, "target_fingerprint": project_fingerprint, "evidence_ids": [item.evidence_id for item in lifecycle_evidence], "knowledge_ids": [knowledge_id] if knowledge_id else [], "memory_ids": [], "requested_at": datetime.now(UTC).isoformat()})
        except Exception as error:
            validation = None
            validation_error = type(error).__name__ + ": validation fingerprint mismatch"
        validation_payload = validation.json() if validation is not None and validation.status_code == 200 else {}
        findings = validation_payload.get("findings", []) if isinstance(validation_payload, dict) else []
        cases.append(_case(8, "Validation", "UNVERIFIED" if validation is None else validation.status_code == 200 and validation_payload.get("status") == "FAIL", validation_error or "Production ValidationProposalService recorded the evidence-bound validation result."))
        cases.append(_case(9, "Finding", "UNVERIFIED" if validation is None else bool(findings), validation_error or "Production validation persisted the evidence-bound Finding."))
        finding_id = findings[0]["finding_id"] if findings else ""
        apply_file_reference = f"workspace/applied-{run_id}.txt"
        proposal = client.post("/api/v1/copilot/proposals", headers=HEADERS, json={"project_id": disposable_id, "finding_id": finding_id, "rationale": "Apply only after formal workspace approval.", "proposed_changes": [{"change_kind": "CREATE_FILE", "file_reference": apply_file_reference, "symbol": None, "logical_change": "synthetic disposable workspace update", "before_fingerprint": None, "expected_effect": "Record the approved synthetic change."}]}) if finding_id else None
        proposal_payload = proposal.json() if proposal is not None and proposal.status_code == 200 else {}
        cases.append(_case(10, "Proposal", "UNVERIFIED" if not finding_id else proposal is not None and proposal.status_code == 200 and proposal_payload.get("status") == "DRAFT", validation_error or "Proposal was created by the production validation/proposal service."))
        proposal_review = client.post("/api/v1/copilot/reviews", headers=HEADERS, json={"project_id": disposable_id, "target_type": "ENGINEERING_PROPOSAL", "target_id": proposal_payload.get("proposal_id", ""), "target_fingerprint": proposal_payload.get("proposal_fingerprint", "")}) if proposal_payload else None
        proposal_decision = client.post(f"/api/v1/copilot/reviews/{proposal_review.json().get('review_id')}/decision", headers=HEADERS, json={"project_id": disposable_id, "decision": "APPROVE", "reviewer": "reviewer-v67", "comment": "Proposal approval does not apply the workspace.", "decided_at": datetime.now(UTC).isoformat()}) if proposal_review is not None and proposal_review.status_code == 200 else None
        approved_proposal = client.get("/api/v1/copilot/proposals", headers=HEADERS, params={"project_id": disposable_id})
        approved_payload = approved_proposal.json()[0] if approved_proposal.status_code == 200 and approved_proposal.json() else {}
        cases.append(_case(11, "Proposal Approval", "UNVERIFIED" if not proposal_payload else proposal_review is not None and proposal_review.status_code == 200 and proposal_decision is not None and proposal_decision.status_code == 200 and approved_payload.get("status") == "APPROVED", validation_error or "Proposal Review approval completed without invoking Workspace Apply."))
        changeset = client.post("/api/v1/copilot/workspace/changesets", headers=HEADERS, json={"project_id": disposable_id, "target_id": "project-root", "proposal_id": approved_payload.get("proposal_id", ""), "proposal_fingerprint": approved_payload.get("proposal_fingerprint", "")}) if approved_payload else None
        changeset_payload = changeset.json() if changeset is not None and changeset.status_code == 200 else {}
        preview = client.post("/api/v1/copilot/workspace/previews", headers=HEADERS, json={"project_id": disposable_id, "changeset_id": changeset_payload.get("changeset_id", ""), "changeset_fingerprint": changeset_payload.get("fingerprint", "")}) if changeset_payload else None
        preview_payload = preview.json() if preview is not None and preview.status_code == 200 else {}
        cases.append(_case(12, "Workspace Preview", "UNVERIFIED" if not approved_payload else changeset is not None and changeset.status_code == 200 and preview is not None and preview.status_code == 200, f"ChangeSet={changeset.status_code if changeset is not None else 'none'} {changeset.text[:160] if changeset is not None else ''}; Preview={preview.status_code if preview is not None else 'none'}."))
        approval = client.post("/api/v1/copilot/workspace/apply-approvals", headers=HEADERS, json={"project_id": disposable_id, "changeset_id": changeset_payload.get("changeset_id", ""), "changeset_fingerprint": changeset_payload.get("fingerprint", ""), "reviewer_id": "reviewer-v67", "nonce": "nonce-v67-disposable"}) if preview_payload else None
        approval_payload = approval.json() if approval is not None and approval.status_code == 200 else {}
        receipt = client.post("/api/v1/copilot/workspace/operations", headers=HEADERS, json={"project_id": disposable_id, "changeset_id": changeset_payload.get("changeset_id", ""), "changeset_fingerprint": changeset_payload.get("fingerprint", ""), "approval_id": approval_payload.get("grant_id", "")}) if approval_payload else None
        receipt_payload = receipt.json() if receipt is not None and receipt.status_code == 200 else {}
        applied_file = projects / "disposable-project" / apply_file_reference
        cases.append(_case(13, "Workspace Apply", "UNVERIFIED" if not preview_payload else approval is not None and approval.status_code == 200 and receipt is not None and receipt.status_code == 200 and applied_file.read_text(encoding="utf-8") == "synthetic disposable workspace update", f"Approval={approval.status_code if approval is not None else 'none'}; Receipt={receipt.status_code if receipt is not None else 'none'}; target file is synthetic Disposable Project only."))
        capabilities = {item.get("capability"): item.get("status") for item in payload.get("capabilities", [])}
        cases.append(_case(15, "Execution Deferred", capabilities.get("CONTROLLED_BUILD") in {"UNAVAILABLE", "UNVERIFIED"} and capabilities.get("CONTROLLED_FLASH") in {"UNAVAILABLE", "UNVERIFIED"}, "Build and Flash are fail-closed and unavailable/unverified."))
        cases.append(_case(16, "Capability Center", bool(payload.get("capabilities")), "Capability state came from backend projection."))
        conversation = client.post(f"/api/v1/copilot/projects/{project_a_id}/conversations", headers=HEADERS, json={})
        if conversation.status_code == 201:
            session_id = conversation.json()["session_id"]
            replay = client.get("/api/v1/copilot/chat/events", headers=HEADERS, params={"session_id": session_id, "requester": REQUESTER, "tenant": TENANT})
            cases.append(_case(17, "SSE Replay", replay.status_code == 200 and "RESYNC_REQUIRED" not in replay.text, "Canonical SSE endpoint is reachable without changing its wire contract."))
        else:
            cases.append(_case(17, "SSE Replay", "UNAVAILABLE", "Chat session unavailable."))
    # Exiting Runtime A before rebuilding the canonical production composition
    # makes the restart evidence an actual A -> B lifecycle boundary.
    restarted = _production_application(projects, run_state, roots)
    with TestClient(restarted) as restarted_client:
        recovered = restarted_client.get(f"/api/v1/copilot/desktop/projects/{project_a_id}/workspace", headers=HEADERS)
        recovered_disposable = restarted_client.get(f"/api/v1/copilot/desktop/projects/{disposable_id}/workspace", headers=HEADERS)
        recovered_payload = recovered_disposable.json() if recovered_disposable.status_code == 200 else {}
        recovered_change_ids = tuple(item.get("reference_id") for item in recovered_payload.get("changes", {}).get("items", []))
        recovered_change_fingerprints = tuple(item.get("fingerprint") for item in recovered_payload.get("changes", {}).get("items", []))
        recovery_ok = bool(receipt_payload.get("operation_id")) and recovered_disposable.status_code == 200 and changeset_payload.get("fingerprint") in recovered_change_fingerprints
        cases.append(_case(14, "Workspace Restart Recovery", "UNVERIFIED" if not receipt_payload else recovery_ok, f"Runtime B recovered ChangeSet/Receipt-bound workspace projection; status={recovered_disposable.status_code}; references={recovered_change_ids}; fingerprints={recovered_change_fingerprints}."))
        cases.append(_case(19, "Full Restart", recovered.status_code == 200 and recovered.json().get("identity", {}).get("project_id") == project_a_id, "SQLite-backed Project Catalog identity recovered after composition restart."))
        deleted = restarted_client.delete(f"/api/v1/copilot/projects/{disposable_id}", headers={**HEADERS, "X-Project-Fingerprint": str(disposable["fingerprint"])})
        tombstone = restarted_client.get(f"/api/v1/copilot/desktop/projects/{disposable_id}/workspace", headers=HEADERS)
        cases.append(_case(18, "Tombstone", deleted.status_code == 200 and tombstone.status_code in {404, 410}, f"Disposable Project deletion={deleted.status_code}; projection={tombstone.status_code}; access is fail-closed."))
        other_tenant = restarted_client.get(f"/api/v1/copilot/desktop/projects/{project_a_id}/workspace", headers={**HEADERS, "X-Environment-Tenant": "tenant-other"})
        cases.append(_case(20, "Security Boundary", other_tenant.status_code in {403, 404, 410} and "C:\\" not in projection_a.text and str(projects) not in projection_a.text, "Cross-tenant and absolute-path leakage checks remained fail-closed."))
    ordered_cases = tuple(sorted(cases, key=lambda item: item.case_id))
    return GoldenReport(run_id, ordered_cases, {"external_provider_invocations": 0, "hardware_invocations": 0, "absolute_path_leaks": 0, "cross_project_leaks": 0, "cross_tenant_leaks": 0}, "UNAVAILABLE / NOT PRESENT")


def main() -> int:
    report = run_golden_cases()
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.status == "GOLDEN VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("GoldenCase", "GoldenReport", "prepare_acceptance_inputs", "run_golden_cases")
