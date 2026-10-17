from __future__ import annotations

import copy
from collections.abc import Mapping

from ..exceptions import PCBArtifactNotFound
from ..models import PCBArtifactReference, PCBNetlistArtifact
from ..parser import parse_kicad_xml_netlist


class KiCadXMLNetlistParserAdapter:
    def __init__(self, content: Mapping[str, bytes]) -> None:
        self._content = copy.deepcopy(dict(content))

    def parse(self, artifact: PCBArtifactReference) -> PCBNetlistArtifact:
        try:
            content = self._content[artifact.artifact_id]
        except KeyError as exc:
            raise PCBArtifactNotFound("netlist content is unavailable") from exc
        return parse_kicad_xml_netlist(artifact, copy.deepcopy(content))
