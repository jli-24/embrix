from __future__ import annotations

from typing import Any, Protocol

from embedded_copilot.agents._common import (
    failure_update,
    success_update,
    tool_call_finished,
    tool_call_started,
)
from embedded_copilot.schemas.result import ErrorCode, ErrorDetail, KnowledgeResult, ToolResult, ToolStatus
from embedded_copilot.schemas.state import AgentState
from embedded_copilot.tools.document_tool import (
    DocumentSearchInput,
    DocumentSearchOutput,
)


class DocumentTool(Protocol):
    async def invoke(
        self,
        payload: DocumentSearchInput,
    ) -> ToolResult[DocumentSearchOutput]: ...


class ProjectKnowledgeRetrievalPort(Protocol):
    def search(
        self,
        *,
        tenant_id: str,
        project_id: str,
        query: str,
        sources: tuple[Any, ...],
        top_k: int,
    ) -> tuple[Any, ...]: ...


class KnowledgeAgent:
    def __init__(
        self,
        *,
        tool: DocumentTool | None = None,
        retrieval_port: ProjectKnowledgeRetrievalPort | None = None,
        top_k: int = 4,
        score_threshold: float = 0.15,
    ) -> None:
        self._tool = tool
        self._retrieval_port = retrieval_port
        self._top_k = top_k
        self._score_threshold = score_threshold

    def retrieve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        query: str,
        top_k: int | None = None,
    ) -> tuple[KnowledgeResult, ...]:
        """Run the formal Knowledge Agent retrieval step without synthesis.

        Final answer generation remains owned by ApplicationRuntime and its
        ModelGateway. This method exists for the production Application path.
        """
        if self._retrieval_port is None:
            raise RuntimeError("knowledge retrieval port is unavailable")
        values = self._retrieval_port.search(
            tenant_id=tenant_id,
            project_id=project_id,
            query=query,
            sources=(),
            top_k=top_k or self._top_k,
        )
        if not isinstance(values, tuple):
            raise TypeError("knowledge retrieval result is invalid")
        checked: list[KnowledgeResult] = []
        for value in values:
            item = KnowledgeResult.model_validate(value.model_dump(mode="python"))
            if (
                item.metadata.get("tenant_id") != tenant_id
                or item.metadata.get("project_id") != project_id
            ):
                raise ValueError("knowledge retrieval identity mismatch")
            checked.append(item)
        return tuple(checked)

    async def run(self, state: AgentState) -> dict[str, object]:
        if self._tool is None:
            return failure_update(
                state,
                error=ErrorDetail(
                    code=ErrorCode.RETRIEVAL_ERROR,
                    message="Knowledge retrieval is unavailable.",
                ),
            )
        started_at = tool_call_started(state, tool_name="document_search_tool")
        tool_result = await self._tool.invoke(
            DocumentSearchInput(
                query=state["user_input"],
                top_k=self._top_k,
                score_threshold=self._score_threshold,
            )
        )
        tool_call_finished(
            state,
            tool_name="document_search_tool",
            started_at=started_at,
            error=tool_result.error,
        )
        if tool_result.status is ToolStatus.ERROR:
            assert tool_result.error is not None
            return failure_update(state, error=tool_result.error)

        assert tool_result.data is not None
        citations = [item.citation for item in tool_result.data.items]
        answer = tool_result.data.answer
        if citations:
            source_lines: list[str] = []
            for item in tool_result.data.items:
                source_line = f"- {item.citation.filename}"
                if item.chapter is not None:
                    source_line += f", chapter {item.chapter}"
                if item.citation.page is not None:
                    source_line += f", page {item.citation.page}"
                source_lines.append(source_line)
            answer = f"{answer}\n\nSources:\n" + "\n".join(source_lines)
        result = KnowledgeResult(
            answer=answer,
            sources=citations,
            insufficient_context=tool_result.data.insufficient_context,
        )
        return success_update(
            state,
            result=result,
            final_answer=answer,
            sources=citations,
        )
