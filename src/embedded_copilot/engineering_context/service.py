from __future__ import annotations

import copy

from .contracts import (
    EngineeringContextProviderPort,
    EngineeringContextSourcePort,
    GraphProjection,
)
from .exceptions import EngineeringContextRejected, EngineeringContextUnavailable
from .fusion import fuse_projections
from .models import (
    EngineeringContextQuery,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)


class EngineeringContextService(EngineeringContextProviderPort):
    """Read-only deterministic context assembly Service."""

    __slots__ = ("_source",)

    def __init__(self, *, source_port: EngineeringContextSourcePort) -> None:
        if not isinstance(source_port, EngineeringContextSourcePort):
            raise TypeError("engineering context source port is invalid")
        self._source = source_port

    @staticmethod
    def _request(value: object) -> EngineeringContextRequest:
        if type(value) is EngineeringContextRequest:
            return EngineeringContextRequest.model_validate(
                copy.deepcopy(value.model_dump(mode="python"))
            )
        if type(value) is EngineeringContextQuery:
            return value.to_request()
        raise TypeError("context request must be a typed projection")

    def get_context(
        self, request: EngineeringContextRequest | EngineeringContextQuery
    ) -> EngineeringContextSnapshot | None:
        checked = self._request(request)
        try:
            memory = self._source.read_memory(copy.deepcopy(checked))
            graph = self._source.read_graph(copy.deepcopy(checked))
            if type(graph) is not GraphProjection:
                raise TypeError("graph context projection is invalid")
            knowledge = self._source.read_knowledge(copy.deepcopy(checked))
            multimodal = self._source.read_multimodal(copy.deepcopy(checked))
            return fuse_projections(
                request=checked,
                memory=memory,
                graph=graph.evidence,
                knowledge=knowledge,
                multimodal=multimodal,
                graph_conflicts=graph.conflicts,
            )
        except EngineeringContextRejected:
            raise
        except EngineeringContextUnavailable:
            raise
        except (TypeError, ValueError) as error:
            raise EngineeringContextRejected() from error
        except Exception as error:
            raise EngineeringContextUnavailable() from error


__all__ = ("EngineeringContextService",)
