from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping

from chromadb.api.models.Collection import Collection

from embedded_copilot.knowledge.models import DocumentMetadata
from embedded_copilot.rag.embedding import EmbeddingProvider
from embedded_copilot.rag.metadata_filter import GENERIC_CHIP
from embedded_copilot.schemas.result import SourceCitation


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    citation: SourceCitation
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    raw_metadata: Mapping[str, object] = field(default_factory=dict)


class ChromaRetriever:
    def __init__(
        self,
        *,
        collection: Collection,
        embedding: EmbeddingProvider,
    ) -> None:
        self._collection = collection
        self._embedding = embedding

    def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]:
        return self.retrieve_filtered(
            query,
            top_k=top_k,
            score_threshold=score_threshold,
            metadata_filter=None,
        )

    def retrieve_filtered(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float,
        metadata_filter: dict[str, object] | None,
    ) -> list[RetrievedChunk]:
        if not query.strip() or self._collection.count() == 0:
            return []
        result = self._collection.query(
            query_embeddings=[self._embedding.embed_query(query)],
            n_results=min(top_k, self._collection.count()),
            where=metadata_filter,
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        retrieved: list[RetrievedChunk] = []
        for chunk_id, text, metadata, distance in zip(
            ids,
            documents,
            metadatas,
            distances,
            strict=True,
        ):
            score = max(0.0, min(1.0, 1.0 - float(distance)))
            if score < score_threshold:
                continue
            source_metadata = metadata or {}
            if not isinstance(source_metadata, dict):
                continue
            chip = source_metadata.get("chip")
            document_metadata = DocumentMetadata(
                chip=None if chip == GENERIC_CHIP else chip,
                manufacturer=source_metadata.get("manufacturer"),
                category=source_metadata.get("category"),
                chapter=source_metadata.get("chapter")
                or source_metadata.get("section"),
                page=source_metadata.get("page"),
                document_type=source_metadata.get("document_type"),
            )
            citation = SourceCitation(
                source=str(source_metadata["source"]),
                filename=str(source_metadata["filename"]),
                page=source_metadata.get("page"),
                chunk_id=chunk_id,
                score=score,
            )
            retrieved.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    text=text or "",
                    citation=citation,
                    metadata=document_metadata,
                    raw_metadata=dict(source_metadata),
                )
            )
        return retrieved
