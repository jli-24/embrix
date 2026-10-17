from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from collections.abc import Mapping
from pathlib import Path

from chromadb.api import ClientAPI

from .models import KnowledgeResult, KnowledgeSource
from .manifest import InMemoryKnowledgeManifestStore, KnowledgeManifestRecord, SQLiteKnowledgeManifestStore
from embedded_copilot.rag.embedding import EmbeddingProvider, HashEmbedding
from embedded_copilot.rag.index import IngestionReport, index_chunks
from embedded_copilot.rag.loader import DocumentLoadError, load_document
from embedded_copilot.rag.retriever import ChromaRetriever
from embedded_copilot.rag.splitter import split_documents


class ProjectKnowledgeSearchRuntime:
    """Read-only, tenant/project-bound adapter over the existing Chroma RAG path."""

    _suffixes = frozenset({".pdf", ".md", ".markdown", ".json", ".txt"})

    def __init__(
        self,
        *,
        project_roots: Mapping[tuple[str, str], str | Path],
        chroma_client: ClientAPI,
        embedding: EmbeddingProvider | None = None,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        top_k: int = 4,
        score_threshold: float = 0.15,
        manifest_store: SQLiteKnowledgeManifestStore | None = None,
        embedding_provider_name: str = "configured",
        embedding_model: str = "configured",
        embedding_identity: str | None = None,
        parser_version: str = "loader-v2",
        chunker_version: str = "splitter-v1",
        allow_implicit_refresh: bool = False,
    ) -> None:
        if not project_roots:
            raise ValueError("project_roots must not be empty")
        if chunk_size <= 0 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("invalid chunk window")
        if top_k < 1 or top_k > 20 or score_threshold < 0 or score_threshold > 1:
            raise ValueError("invalid retrieval configuration")
        self._roots = {identity: self._safe_root(root) for identity, root in project_roots.items()}
        self._client = chroma_client
        if embedding is None:
            raise ValueError("a configured embedding provider is required")
        self._embedding = embedding
        if manifest_store is None and isinstance(embedding, HashEmbedding):
            manifest_store = InMemoryKnowledgeManifestStore()
        self._manifest = manifest_store
        self._embedding_provider_name = embedding_provider_name
        self._embedding_model = embedding_model
        self._embedding_identity = embedding_identity or str(
            getattr(embedding, "identity", f"{embedding_provider_name}|{embedding_model}")
        )
        self._parser_version = parser_version
        self._chunker_version = chunker_version
        self._allow_implicit_refresh = allow_implicit_refresh
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._top_k = top_k
        self._score_threshold = score_threshold
        self._refreshed: set[tuple[str, str]] = set()

    @staticmethod
    def _safe_root(value: str | Path) -> Path:
        root = Path(value)
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError("project knowledge root is unavailable")
        return root.resolve(strict=True)

    def _root(self, *, tenant_id: str, project_id: str) -> Path:
        try:
            return self._roots[(tenant_id, project_id)]
        except KeyError as error:
            raise ValueError("project knowledge identity mismatch") from error

    @staticmethod
    def _collection_name(tenant_id: str, project_id: str, embedding_identity: str) -> str:
        digest = hashlib.sha256(
            f"{tenant_id}|{project_id}|{embedding_identity}".encode()
        ).hexdigest()[:32]
        return f"project_knowledge_{digest}"

    def _collection(self, *, tenant_id: str, project_id: str):
        return self._client.get_or_create_collection(
            name=self._collection_name(tenant_id, project_id, self._embedding_identity),
            metadata={
                "hnsw:space": "cosine",
                "tenant_id": tenant_id,
                "project_id": project_id,
                "embedding_identity": self._embedding_identity,
            },
        )

    @classmethod
    def _files(cls, root: Path, source_reference: str | None = None) -> list[Path]:
        if source_reference is not None:
            relative = Path(source_reference)
            if (
                relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
                or relative.suffix.lower() not in cls._suffixes
            ):
                raise ValueError("knowledge source reference is invalid")
            candidate = root.joinpath(*relative.parts)
            if not candidate.is_file() or candidate.is_symlink():
                raise ValueError("knowledge source reference is unavailable")
            try:
                if not candidate.resolve(strict=True).is_relative_to(root):
                    raise ValueError("knowledge source reference escapes project root")
            except OSError as error:
                raise ValueError("knowledge source reference is unavailable") from error
            return [candidate]
        files: list[Path] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in cls._suffixes:
                continue
            try:
                path.resolve(strict=True).relative_to(root)
            except (OSError, ValueError) as error:
                raise ValueError("project knowledge path escapes root") from error
            files.append(path)
        return files

    def refresh(
        self,
        *,
        tenant_id: str,
        project_id: str,
        requester: str = "system-import",
        source_reference: str | None = None,
    ) -> IngestionReport:
        if self._manifest is None:
            raise ValueError("knowledge manifest is required")
        if getattr(self._manifest, "is_project_tombstoned", lambda **_: False)(tenant=tenant_id, project_id=project_id):
            raise ValueError("project knowledge is tombstoned")
        root = self._root(tenant_id=tenant_id, project_id=project_id)
        documents = []
        errors: list[str] = []
        for path in self._files(root, source_reference):
            try:
                documents.extend(load_document(path, source_root=root))
            except DocumentLoadError:
                errors.append(path.name)
        if errors:
            raise ValueError("project knowledge ingestion failed")
        chunks = split_documents(documents, chunk_size=self._chunk_size, chunk_overlap=self._chunk_overlap)
        by_source = {document.source: document for document in documents}
        now = datetime.now(UTC).isoformat()
        for source, document in by_source.items():
            document_id = hashlib.sha256(f"{tenant_id}|{project_id}|{source}".encode()).hexdigest()[:32]
            existing = self._manifest.get(tenant=tenant_id, project_id=project_id, document_id=document_id)
            revision = hashlib.sha256(
                f"{project_id}|{document.checksum}|{self._parser_version}|{self._chunker_version}|{self._embedding_identity}".encode()
            ).hexdigest()
            document_chunks = tuple(item for item in chunks if item.source == source)
            record = KnowledgeManifestRecord(
                document_id=document_id,
                project_id=project_id,
                tenant=tenant_id,
                requester=requester,
                source_reference=source,
                document_fingerprint=f"sha256:{document.checksum}",
                content_fingerprint=f"sha256:{document.checksum}",
                parser_version=self._parser_version,
                chunker_version=self._chunker_version,
                embedding_provider=self._embedding_provider_name,
                embedding_model=self._embedding_model,
                embedding_dimension=len(self._embedding.embed_query(document.text)),
                ingestion_status="READY",
                chunk_count=len(document_chunks),
                created_at=existing.created_at if existing else now,
                updated_at=now,
                ingestion_revision=f"sha256:{revision}",
                embedding_identity=self._embedding_identity,
            )
            self._manifest.upsert(record)
        records = self._manifest.list_ready(tenant=tenant_id, project_id=project_id)
        record_by_source = {item.source_reference: item for item in records}
        report = index_chunks(
            chunks,
            self._collection(tenant_id=tenant_id, project_id=project_id),
            self._embedding,
            active_sources=(
                {item.source for item in documents}
                if source_reference is None
                else None
            ),
            tenant_id=tenant_id,
            project_id=project_id,
            document_id_by_source={source: item.document_id for source, item in record_by_source.items()},
            document_fingerprint_by_source={source: item.document_fingerprint for source, item in record_by_source.items()},
            ingestion_revision_by_source={source: item.ingestion_revision for source, item in record_by_source.items()},
            embedding_identity=self._embedding_identity,
        )
        self._refreshed.add((tenant_id, project_id))
        return report

    def import_document(
        self,
        *,
        tenant_id: str,
        project_id: str,
        requester: str,
        source_reference: str,
        document_id: str,
    ) -> IngestionReport:
        root = self._root(tenant_id=tenant_id, project_id=project_id)
        if self._manifest is not None and getattr(self._manifest, "is_project_tombstoned", lambda **_: False)(tenant=tenant_id, project_id=project_id):
            raise ValueError("project knowledge is tombstoned")
        files = self._files(root, source_reference)
        if len(files) != 1 or files[0].name != Path(source_reference).name:
            raise ValueError("knowledge source reference is invalid")
        expected_document_id = hashlib.sha256(
            f"{tenant_id}|{project_id}|{source_reference}".encode()
        ).hexdigest()[:32]
        if document_id != expected_document_id:
            raise ValueError("document identity does not match source reference")
        return self.refresh(
            tenant_id=tenant_id,
            project_id=project_id,
            requester=requester,
            source_reference=source_reference,
        )

    def search(self, *, tenant_id: str, project_id: str, query: str, sources: tuple[object, ...], top_k: int) -> tuple[KnowledgeResult, ...]:
        self._root(tenant_id=tenant_id, project_id=project_id)
        if self._manifest is None:
            raise ValueError("knowledge manifest is required")
        if getattr(self._manifest, "is_project_tombstoned", lambda **_: False)(tenant=tenant_id, project_id=project_id):
            return ()
        if (tenant_id, project_id) not in self._refreshed and self._allow_implicit_refresh:
            self.refresh(tenant_id=tenant_id, project_id=project_id)
        if not self._manifest.list_ready(tenant=tenant_id, project_id=project_id):
            return ()
        if sources and "LOCAL" not in {str(source) for source in sources}:
            return ()
        retriever = ChromaRetriever(
            collection=self._collection(tenant_id=tenant_id, project_id=project_id),
            embedding=self._embedding,
        )
        results = retriever.retrieve_filtered(
            query,
            top_k=min(top_k, self._top_k),
            score_threshold=self._score_threshold,
            metadata_filter={"$and": [{"tenant_id": tenant_id}, {"project_id": project_id}]},
        )
        ready = {item.source_reference: item for item in self._manifest.list_ready(tenant=tenant_id, project_id=project_id)}
        values: list[KnowledgeResult] = []
        for result in results:
            record = ready.get(result.citation.source)
            raw = result.raw_metadata
            if record is None:
                continue
            if (
                raw.get("tenant_id") != tenant_id
                or raw.get("project_id") != project_id
                or raw.get("document_id") != record.document_id
                or raw.get("document_fingerprint") != record.document_fingerprint
                or raw.get("ingestion_revision") != record.ingestion_revision
                or raw.get("embedding_identity") != record.embedding_identity
                or not isinstance(raw.get("chunk_fingerprint"), str)
            ):
                continue
            values.append(KnowledgeResult(
                id=result.chunk_id,
                title=result.citation.filename,
                content=result.text,
                source=KnowledgeSource.LOCAL,
                score=result.citation.score,
                metadata={
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "document_id": record.document_id,
                    "document_fingerprint": record.document_fingerprint,
                    "chunk_id": result.chunk_id,
                    "source_reference": result.citation.source,
                    "chunk_fingerprint": raw["chunk_fingerprint"],
                    "ingestion_revision": record.ingestion_revision,
                    "chip": result.metadata.chip,
                    "chapter": result.metadata.chapter,
                    "page": result.metadata.page,
                    "document_type": result.metadata.document_type,
                },
            ))
        return tuple(values)


class ProjectCatalogKnowledgeSearchPort:
    def __init__(self, *, project_catalog: object, chroma_client: ClientAPI, embedding: EmbeddingProvider | None = None, manifest_store: SQLiteKnowledgeManifestStore | None = None, embedding_provider_name: str = "configured", embedding_model: str = "configured", embedding_identity: str | None = None, chunk_size: int = 800, chunk_overlap: int = 100, top_k: int = 4, score_threshold: float = 0.15) -> None:
        self._catalog = project_catalog
        self._client = chroma_client
        self._embedding = embedding
        self._manifest = manifest_store
        self._embedding_provider_name = embedding_provider_name
        self._embedding_model = embedding_model
        self._embedding_identity = embedding_identity or str(
            getattr(embedding, "identity", f"{embedding_provider_name}|{embedding_model}")
        )
        self._configuration = {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap, "top_k": top_k, "score_threshold": score_threshold}
        self._runtimes: dict[tuple[str, str], ProjectKnowledgeSearchRuntime] = {}

    def search(self, **kwargs: object) -> tuple[KnowledgeResult, ...]:
        tenant_id, project_id = kwargs.get("tenant_id"), kwargs.get("project_id")
        query, sources, top_k = kwargs.get("query"), kwargs.get("sources"), kwargs.get("top_k")
        if not all(isinstance(value, str) for value in (tenant_id, project_id, query)) or not isinstance(sources, tuple) or not isinstance(top_k, int):
            raise ValueError("project knowledge request is invalid")
        key = (tenant_id, project_id)
        if self._manifest is not None and getattr(self._manifest, "is_project_tombstoned", lambda **_: False)(tenant=tenant_id, project_id=project_id):
            return ()
        root = self._catalog.knowledge_snapshot_root(project_id, tenant_id=tenant_id)
        runtime = self._runtimes.get(key)
        if runtime is None:
            runtime = ProjectKnowledgeSearchRuntime(project_roots={key: root}, chroma_client=self._client, embedding=self._embedding, manifest_store=self._manifest, embedding_provider_name=self._embedding_provider_name, embedding_model=self._embedding_model, embedding_identity=self._embedding_identity, **self._configuration)
            self._runtimes[key] = runtime
        return runtime.search(tenant_id=tenant_id, project_id=project_id, query=query, sources=sources, top_k=top_k)

    def import_document(
        self,
        *,
        tenant_id: str,
        project_id: str,
        requester: str,
        document_id: str,
        source_reference: str,
    ) -> IngestionReport:
        if not all(isinstance(value, str) and value.strip() for value in (tenant_id, project_id, requester, document_id, source_reference)):
            raise ValueError("project knowledge import request is invalid")
        if self._manifest is not None and getattr(self._manifest, "is_project_tombstoned", lambda **_: False)(tenant=tenant_id, project_id=project_id):
            raise ValueError("project knowledge is tombstoned")
        key = (tenant_id, project_id)
        root = self._catalog.knowledge_snapshot_root(project_id, tenant_id=tenant_id)
        runtime = self._runtimes.get(key)
        if runtime is None:
            runtime = ProjectKnowledgeSearchRuntime(
                project_roots={key: root},
                chroma_client=self._client,
                embedding=self._embedding,
                manifest_store=self._manifest,
                embedding_provider_name=self._embedding_provider_name,
                embedding_model=self._embedding_model,
                embedding_identity=self._embedding_identity,
                **self._configuration,
            )
            self._runtimes[key] = runtime
        return runtime.import_document(
            tenant_id=tenant_id,
            project_id=project_id,
            requester=requester,
            document_id=document_id,
            source_reference=source_reference,
        )

    def status(self, *, tenant_id: str, project_id: str) -> tuple[KnowledgeManifestRecord, ...]:
        if self._manifest is None:
            raise ValueError("knowledge manifest is required")
        if getattr(self._manifest, "is_project_tombstoned", lambda **_: False)(tenant=tenant_id, project_id=project_id):
            return ()
        self._catalog.knowledge_snapshot_root(project_id, tenant_id=tenant_id)
        return self._manifest.list_ready(tenant=tenant_id, project_id=project_id)

    def tombstone_project(self, *, tenant_id: str, project_id: str, requester: str) -> int:
        if self._manifest is None:
            raise ValueError("knowledge manifest is required")
        return self._manifest.tombstone_project(tenant=tenant_id, project_id=project_id, requester=requester)


__all__ = ("ProjectCatalogKnowledgeSearchPort", "ProjectKnowledgeSearchRuntime")
