from __future__ import annotations

import copy
from collections.abc import Mapping

from ..exceptions import PCBArtifactNotFound
from ..models import PCBArtifactReference, PCBBOMArtifact
from ..parser import parse_csv_bom


class CSVPCBParserAdapter:
    def __init__(self, content: Mapping[str, bytes]) -> None:
        self._content = copy.deepcopy(dict(content))

    def parse(self, artifact: PCBArtifactReference) -> PCBBOMArtifact:
        try:
            content = self._content[artifact.artifact_id]
        except KeyError as exc:
            raise PCBArtifactNotFound("BOM content is unavailable") from exc
        return parse_csv_bom(artifact, copy.deepcopy(content))
