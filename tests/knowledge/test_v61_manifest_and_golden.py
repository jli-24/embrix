from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import chromadb

from embedded_copilot.agents.knowledge import KnowledgeAgent
from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationErrorCode,
    ApplicationRuntime,
    CanonicalSupervisorBinding,
    FakeApplicationAuditSink,
    FakeApplicationModelGateway,
    FixedApplicationClock,
    ModelPolicy,
)
from embedded_copilot.engineering_context import (
    EngineeringContextService,
    ReadOnlyProjectionSource,
)
from embedded_copilot.engineering_context.adapters import ProjectKnowledgeEvidenceAdapter
from embedded_copilot.knowledge.manifest import (
    InMemoryKnowledgeManifestStore,
    KnowledgeManifestRecord,
    SQLiteKnowledgeManifestStore,
)
from embedded_copilot.knowledge.project_search import ProjectKnowledgeSearchRuntime
from embedded_copilot.rag.embedding import HashEmbedding

from tests.application.fixtures import NOW, request


def _record(*, tenant: str = "tenant-1", project_id: str = "project-1") -> KnowledgeManifestRecord:
    return KnowledgeManifestRecord(
        document_id="document-1",
        project_id=project_id,
        tenant=tenant,
        requester="engineer-1",
        source_reference="docs/i2c.md",
        document_fingerprint="sha256:" + "a" * 64,
        content_fingerprint="sha256:" + "a" * 64,
        parser_version="loader-v2",
        chunker_version="splitter-v1",
        embedding_provider="test",
        embedding_model="hash-test",
        embedding_dimension=64,
        ingestion_status="READY",
        chunk_count=1,
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        ingestion_revision="sha256:" + "b" * 64,
        embedding_identity="test|hash-test|64",
    )


def test_manifest_migration_and_restart_preserve_authoritative_identity(tmp_path: Path) -> None:
    database = tmp_path / "desktop.sqlite3"
    first = SQLiteKnowledgeManifestStore(database)
    stored = first.upsert(_record())

    second = SQLiteKnowledgeManifestStore(database)
    assert second.get(tenant="tenant-1", project_id="project-1", document_id="document-1") == stored
    assert second.list_ready(tenant="tenant-1", project_id="project-1") == (stored,)


def test_project_search_requires_explicit_import_and_rejects_stale_or_tombstoned_index(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "i2c.md").write_text(
        "# I2C\nESP32-S3 I2C1 uses SDA and SCL.", encoding="utf-8"
    )
    manifest = InMemoryKnowledgeManifestStore()
    runtime = ProjectKnowledgeSearchRuntime(
        project_roots={("tenant-golden", "project-golden"): root},
        chroma_client=chromadb.EphemeralClient(),
        embedding=HashEmbedding(dimension=64),
        manifest_store=manifest,
        embedding_provider_name="test",
        embedding_model="hash-test",
    )

    assert runtime.search(
        tenant_id="tenant-golden", project_id="project-golden", query="I2C", sources=(), top_k=4
    ) == ()
    document_id = hashlib.sha256("tenant-golden|project-golden|docs/i2c.md".encode()).hexdigest()[:32]
    runtime.import_document(
        tenant_id="tenant-golden",
        project_id="project-golden",
        requester="engineer-1",
        source_reference="docs/i2c.md",
        document_id=document_id,
    )
    assert runtime.search(
        tenant_id="tenant-golden", project_id="project-golden", query="I2C", sources=(), top_k=4
    )

    manifest.tombstone_project(tenant="tenant-golden", project_id="project-golden", requester="engineer-1")
    assert runtime.search(
        tenant_id="tenant-golden", project_id="project-golden", query="I2C", sources=(), top_k=4
    ) == ()


class _EmptyRetrievalPort:
    def search(self, *, tenant_id: str, project_id: str, query: str, sources: tuple[object, ...], top_k: int):
        assert tenant_id == "default-tenant"
        assert project_id == "project-1"
        return ()


def test_golden_case_3_zero_evidence_is_hard_model_gate() -> None:
    knowledge_agent = KnowledgeAgent(retrieval_port=_EmptyRetrievalPort())
    knowledge = ProjectKnowledgeEvidenceAdapter(lambda _project_id: Path("."))
    knowledge.bind_knowledge_agent(knowledge_agent)
    context = EngineeringContextService(
        source_port=ReadOnlyProjectionSource(knowledge_port=knowledge)
    )
    gateway = FakeApplicationModelGateway()
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(),
            context_provider=context,
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(datetime(2026, 8, 13, tzinfo=UTC)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )

    result = runtime.answer(request(question="What is the undocumented I2C register value?"))

    assert result.error_code is ApplicationErrorCode.INSUFFICIENT_EVIDENCE
    assert result.answer is None
    assert gateway.call_count == 0
