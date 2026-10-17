from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from chromadb.api.models.Collection import Collection

from embedded_copilot.rag.embedding import EmbeddingProvider
from embedded_copilot.rag.metadata_filter import GENERIC_CHIP
from embedded_copilot.rag.splitter import DocumentChunk


@dataclass(frozen=True, slots=True)
class IngestionReport:
    inserted: int
    updated: int
    unchanged: int
    deleted: int = 0


def _metadata(
    chunk: DocumentChunk,
    *,
    tenant_id: str | None = None,
    project_id: str | None = None,
    document_id: str | None = None,
    document_fingerprint: str | None = None,
    ingestion_revision: str | None = None,
    embedding_identity: str | None = None,
) -> dict[str, str | int]:
    metadata: dict[str, str | int] = {
        "source": chunk.source,
        "filename": chunk.filename,
        "chunk_index": chunk.chunk_index,
        "content_hash": chunk.content_hash,
        "source_checksum": chunk.source_checksum,
        "chip": chunk.metadata.chip or GENERIC_CHIP,
    }
    metadata.update(
        chunk.metadata.model_dump(
            exclude={"chip", "page"},
            exclude_none=True,
        )
    )
    if chunk.page is not None:
        metadata["page"] = chunk.page
    if chunk.section is not None:
        metadata["section"] = chunk.section
    if chunk.metadata.chapter is None and chunk.section is not None:
        metadata["chapter"] = chunk.section
    if tenant_id is not None:
        metadata["tenant_id"] = tenant_id
    if project_id is not None:
        metadata["project_id"] = project_id
    if document_id is not None:
        metadata["document_id"] = document_id
    if document_fingerprint is not None:
        metadata["document_fingerprint"] = document_fingerprint
    if ingestion_revision is not None:
        metadata["ingestion_revision"] = ingestion_revision
    if embedding_identity is not None:
        metadata["embedding_identity"] = embedding_identity
    metadata["chunk_fingerprint"] = f"sha256:{chunk.content_hash}"
    return metadata


def index_chunks(
    chunks: Sequence[DocumentChunk],
    collection: Collection,
    embedding: EmbeddingProvider,
    *,
    active_sources: Sequence[str] | None = None,
    tenant_id: str | None = None,
    project_id: str | None = None,
    document_id_by_source: dict[str, str] | None = None,
    document_fingerprint_by_source: dict[str, str] | None = None,
    ingestion_revision_by_source: dict[str, str] | None = None,
    embedding_identity: str | None = None,
) -> IngestionReport:
    if not chunks and active_sources is None:
        return IngestionReport(inserted=0, updated=0, unchanged=0, deleted=0)

    ids = [chunk.chunk_id for chunk in chunks]
    existing: dict[str, dict[str, Any]] = {}
    existing_result = (
        collection.get(ids=ids, include=["metadatas"])
        if ids
        else {"ids": [], "metadatas": []}
    )
    for item_id, metadata in zip(
        existing_result.get("ids", []),
        existing_result.get("metadatas", []),
        strict=True,
    ):
        existing[item_id] = metadata or {}

    existing_all = collection.get(include=["metadatas"])
    existing_metadata = {
        item_id: metadata or {}
        for item_id, metadata in zip(
            existing_all.get("ids", []),
            existing_all.get("metadatas", []),
            strict=True,
        )
    }
    incoming_ids = set(ids)
    incoming_sources = {chunk.source for chunk in chunks}
    source_scope = (
        set(active_sources) if active_sources is not None else incoming_sources
    )
    if active_sources is not None:
        stale_ids = [
            item_id
            for item_id, metadata in existing_metadata.items()
            if (
                metadata.get("source") not in source_scope
                or item_id not in incoming_ids
            )
        ]
    else:
        stale_ids = [
            item_id
            for item_id, metadata in existing_metadata.items()
            if metadata.get("source") in source_scope and item_id not in incoming_ids
        ]
    if stale_ids:
        collection.delete(ids=stale_ids)

    incoming_metadata = {
        chunk.chunk_id: _metadata(
            chunk,
            tenant_id=tenant_id,
            project_id=project_id,
            document_id=(document_id_by_source or {}).get(chunk.source),
            document_fingerprint=(document_fingerprint_by_source or {}).get(chunk.source),
            ingestion_revision=(ingestion_revision_by_source or {}).get(chunk.source),
            embedding_identity=embedding_identity,
        )
        for chunk in chunks
    }
    changed = [
        chunk
        for chunk in chunks
        if existing.get(chunk.chunk_id) != incoming_metadata[chunk.chunk_id]
    ]
    inserted = sum(chunk.chunk_id not in existing for chunk in changed)
    updated = len(changed) - inserted
    unchanged = len(chunks) - len(changed)

    if changed:
        collection.upsert(
            ids=[chunk.chunk_id for chunk in changed],
            documents=[chunk.text for chunk in changed],
            metadatas=[incoming_metadata[chunk.chunk_id] for chunk in changed],
            embeddings=embedding.embed_documents([chunk.text for chunk in changed]),
        )
    return IngestionReport(
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        deleted=len(stale_ids),
    )
