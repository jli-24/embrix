from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings as ChromaSettings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from embedded_copilot.agents.debug import DebugAgent
from embedded_copilot.agents.firmware import FirmwareAgent
from embedded_copilot.agents.knowledge import KnowledgeAgent
from embedded_copilot.agents.supervisor import supervisor_node
from embedded_copilot.agents.workflow import build_workflow
from embedded_copilot.rag.embedding import (
    EmbeddingProvider,
    EmbeddingProviderError,
    HashEmbedding,
    OllamaEmbeddingAdapter,
)
from embedded_copilot.rag.hybrid_retriever import HybridRetriever
from embedded_copilot.rag.index import index_chunks
from embedded_copilot.rag.loader import DocumentLoadError, LoadedDocument, load_document
from embedded_copilot.rag.retriever import ChromaRetriever
from embedded_copilot.rag.splitter import split_documents
from embedded_copilot.services.config import Settings
from embedded_copilot.services.copilot import CopilotService
from embedded_copilot.services.llm import (
    LLMService,
    LangChainLLMService,
    OfflineLLMService,
)
from embedded_copilot.tools.code_tool import CodeAnalysisTool
from embedded_copilot.tools.debug_tool import DebugLogTool
from embedded_copilot.tools.document_tool import DocumentSearchTool


class RuntimeInitializationError(RuntimeError):
    """Raised when core runtime dependencies cannot be composed."""


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    service: CopilotService
    health_status: Literal["ok", "degraded"]
    ingestion_errors: list[str]


class _UnavailableRetriever:
    """Fail-closed retriever used when the optional embedding capability is down."""

    def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float,
    ) -> list[object]:
        del query, top_k, score_threshold
        raise RuntimeError("knowledge embedding is unavailable")


# This is a whitelist for the Canonical Runtime, not a repository-wide registry.
CANONICAL_RUNTIME_AGENT_TYPES = (
    KnowledgeAgent,
    FirmwareAgent,
    DebugAgent,
)
CANONICAL_RUNTIME_AGENT_NAMES = ("supervisor", "knowledge", "firmware", "debug")
CANONICAL_RUNTIME_ROUTER = supervisor_node


def _build_llm(settings: Settings) -> LLMService:
    if settings.runtime_mode == "offline":
        return OfflineLLMService()
    if settings.chat_model is None or settings.openai_api_key is None:
        raise RuntimeInitializationError(
            "LLM mode requires chat_model and openai_api_key."
        )
    model = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.openai_api_key,
        base_url=str(settings.openai_base_url) if settings.openai_base_url else None,
        timeout=settings.request_timeout_seconds,
    )
    return LangChainLLMService(model)


def _build_embedding(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "local_hash":
        return HashEmbedding(dimension=settings.embedding_dimension)
    if settings.embedding_provider == "ollama":
        if settings.embedding_model is None:
            raise RuntimeInitializationError(
                "Ollama embeddings require embedding_model."
            )
        adapter = OllamaEmbeddingAdapter(
            model=settings.embedding_model,
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.request_timeout_seconds,
        )
        adapter.probe()
        return adapter
    if settings.embedding_model is None or settings.openai_api_key is None:
        raise RuntimeInitializationError(
            "OpenAI-compatible embeddings require embedding_model and openai_api_key."
        )
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
        base_url=str(settings.openai_base_url) if settings.openai_base_url else None,
    )


def _knowledge_paths(knowledge_dir: Path) -> list[Path]:
    if not knowledge_dir.is_dir():
        return []
    suffixes = {".pdf", ".md", ".markdown"}
    return sorted(
        path
        for path in knowledge_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes
    )


def build_canonical_runtime(
    settings: Settings,
    *,
    chroma_client: ClientAPI | None = None,
    allow_unavailable_embedding: bool = False,
) -> RuntimeComponents:
    client = chroma_client or chromadb.PersistentClient(
        path=str(settings.chroma_path),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=settings.collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    documents: list[LoadedDocument] = []
    ingestion_errors: list[str] = []
    for path in _knowledge_paths(settings.knowledge_dir):
        try:
            documents.extend(load_document(path, source_root=settings.knowledge_dir))
        except DocumentLoadError:
            ingestion_errors.append(f"{path.name}: document_load_error")
    if not documents and not (
        allow_unavailable_embedding and settings.embedding_provider == "ollama"
    ):
        raise RuntimeInitializationError("No knowledge document could be loaded.")

    embedding: EmbeddingProvider | None
    try:
        embedding = _build_embedding(settings)
    except (EmbeddingProviderError, RuntimeInitializationError, OSError, ValueError):
        if not (
            allow_unavailable_embedding and settings.embedding_provider == "ollama"
        ):
            raise
        embedding = None
        ingestion_errors.append("knowledge_embedding_unavailable")

    if embedding is not None:
        chunks = split_documents(
            documents,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        active_sources = {document.source for document in documents}
        index_chunks(
            chunks,
            collection,
            embedding,
            active_sources=active_sources,
        )
        vector_retriever = ChromaRetriever(collection=collection, embedding=embedding)
    else:
        vector_retriever = _UnavailableRetriever()
    retriever = HybridRetriever(retriever=vector_retriever)
    llm = _build_llm(settings)
    document_tool = DocumentSearchTool(
        retriever=retriever,
        llm=llm,
        timeout_seconds=settings.request_timeout_seconds,
    )
    code_tool = CodeAnalysisTool(
        llm=llm,
        timeout_seconds=settings.request_timeout_seconds,
    )
    debug_tool = DebugLogTool(
        llm=llm,
        timeout_seconds=settings.request_timeout_seconds,
    )
    graph = build_workflow(
        knowledge_agent=KnowledgeAgent(
            tool=document_tool,
            top_k=settings.retrieval_top_k,
            score_threshold=settings.retrieval_score_threshold,
        ),
        firmware_agent=FirmwareAgent(tool=code_tool),
        debug_agent=DebugAgent(tool=debug_tool),
    )
    return RuntimeComponents(
        service=CopilotService(graph),
        health_status="degraded" if ingestion_errors else "ok",
        ingestion_errors=ingestion_errors,
    )


__all__ = (
    "CANONICAL_RUNTIME_AGENT_NAMES",
    "CANONICAL_RUNTIME_AGENT_TYPES",
    "CANONICAL_RUNTIME_ROUTER",
    "RuntimeComponents",
    "RuntimeInitializationError",
    "build_canonical_runtime",
)
