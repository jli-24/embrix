from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path

from embedded_copilot.knowledge import KnowledgeQuery, KnowledgeSource
from embedded_copilot.knowledge.providers.local import LocalKnowledgeProvider
from embedded_copilot.knowledge.source import project_result

_CONTENT_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.-]*")


def _content_matches(result: object, query: str) -> bool:
    """Require retrieval relevance from document content, never its filename."""
    content = getattr(result, "content", None)
    if not isinstance(content, str) or not content.strip():
        return False
    tokens = tuple(
        token.casefold() for token in _CONTENT_TOKEN.findall(query)
    )
    normalized_content = content.casefold()
    if tokens:
        return any(token in normalized_content for token in tokens)
    normalized_query = " ".join(query.split()).casefold()
    return bool(normalized_query) and normalized_query in normalized_content


class ProjectKnowledgeEvidenceAdapter:
    """Adapt an existing project-scoped snapshot to the Context evidence Port.

    The resolver is the only filesystem boundary. This adapter never walks a
    project root, enumerates arbitrary extensions, or infers evidence from
    filenames and metadata. LocalKnowledgeProvider owns the existing snapshot
    contract and loader validation.
    """

    __slots__ = ("_root_resolver", "_top_k", "_search_port", "_knowledge_agent")

    def __init__(
        self,
        root_resolver: Callable[[str], Path],
        *,
        top_k: int = 4,
        search_port: object | None = None,
    ) -> None:
        if not callable(root_resolver):
            raise TypeError("project snapshot resolver is invalid")
        if type(top_k) is not int or not 1 <= top_k <= 100:
            raise ValueError("project evidence top_k is invalid")
        self._root_resolver = root_resolver
        self._top_k = top_k
        self._search_port = search_port
        self._knowledge_agent = None

    def bind_search_port(self, value: object) -> None:
        if not callable(getattr(value, "search", None)):
            raise TypeError("knowledge search port is invalid")
        self._search_port = value

    def bind_knowledge_agent(self, value: object) -> None:
        if not callable(getattr(value, "retrieve", None)):
            raise TypeError("knowledge agent is invalid")
        self._knowledge_agent = value

    def list_evidence(
        self,
        project_id: str,
        task: str,
        constraints: tuple[str, ...],
        *,
        tenant_id: str = "default-tenant",
    ) -> tuple[object, ...]:
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project identity is invalid")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("evidence task is invalid")
        if type(constraints) is not tuple or any(
            not isinstance(value, str) for value in constraints
        ):
            raise ValueError("evidence constraints are invalid")

        if self._knowledge_agent is not None:
            query_text = " ".join((task.strip(), *(value.strip() for value in constraints if value.strip())))
            values = self._knowledge_agent.retrieve(
                tenant_id=tenant_id,
                project_id=project_id,
                query=query_text,
                top_k=self._top_k,
            )
            if not isinstance(values, tuple):
                raise TypeError("knowledge agent result is invalid")
            return tuple(project_result(copy.deepcopy(value), project_id=project_id, tenant_id=tenant_id) for value in values)

        if self._search_port is not None:
            query_text = " ".join((task.strip(), *(value.strip() for value in constraints if value.strip())))
            values = self._search_port.search(
                tenant_id=tenant_id,
                project_id=project_id,
                query=query_text,
                sources=(),
                top_k=self._top_k,
            )
            if not isinstance(values, tuple):
                raise TypeError("production knowledge search result is invalid")
            return tuple(project_result(copy.deepcopy(value), project_id=project_id, tenant_id=tenant_id) for value in values)

        # Legacy compatibility is retained only for direct tests/older callers.
        # Production composition binds the manifest-backed search port above.
        root = self._root_resolver(project_id)
        if not isinstance(root, Path):
            raise TypeError("project snapshot root is invalid")
        provider = LocalKnowledgeProvider(knowledge_root=root)
        query_text = " ".join(
            (task.strip(), *(value.strip() for value in constraints if value.strip()))
        )
        query = KnowledgeQuery(
            query=query_text,
            sources=[KnowledgeSource.LOCAL],
            top_k=self._top_k,
        )
        results = provider.search(query)
        if type(results) is not list:
            raise TypeError("local knowledge result is invalid")
        return tuple(
            project_result(copy.deepcopy(result))
            for result in results
            if _content_matches(result, query_text)
        )


__all__ = ("ProjectKnowledgeEvidenceAdapter",)
