from __future__ import annotations

from .ports import EngineeringContextSourcePort
from .projection import ReadOnlyProjectionSource
from .service import EngineeringContextService


def create_engineering_context_service(
    *,
    source_port: EngineeringContextSourcePort | None = None,
    memory_port: object | None = None,
    graph_port: object | None = None,
    knowledge_port: object | None = None,
    multimodal_port: object | None = None,
) -> EngineeringContextService:
    if source_port is None:
        source_port = ReadOnlyProjectionSource(
            memory_port=memory_port,
            graph_port=graph_port,
            knowledge_port=knowledge_port,
            multimodal_port=multimodal_port,
        )
    return EngineeringContextService(source_port=source_port)


__all__ = ("create_engineering_context_service",)
