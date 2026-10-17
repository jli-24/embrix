from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Any, ClassVar, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.engineering_optimization_loop import OptimizationProposal
from embedded_copilot.engineering_validation import ValidationFinding

_AUTO = "sha256:" + "0" * 64
_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,127}$")
_LOGICAL_REF = re.compile(
    r"^(?:artifact|knowledge|pcb)://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,255}$"
)
_UNSAFE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret)\s*[:=]?|"
    r"raw\s+(?:model\s+)?output|hidden\s+reasoning|chain\s+of\s+thought|"
    r"(?:\A[A-Za-z]:[\\/]|\\\\|file://|https?://|data:|base64|\x00|\r|\n)",
    re.IGNORECASE,
)


def canonical_fingerprint(value: BaseModel, *, exclude: set[str] | None = None) -> str:
    payload = value.model_dump(mode="json", exclude={"fingerprint", *(exclude or set())})
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def content_fingerprint(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def deterministic_id(prefix: str, *parts: str) -> str:
    return (
        f"{prefix}-" + hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:48]
    )


def _checked_id(value: str, field: str) -> str:
    if _ID.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _sanitize_boundary(value: object, field: str) -> object:
    if isinstance(value, StrEnum):
        return value
    if isinstance(value, str):
        checked = unicodedata.normalize("NFC", value).strip()
        if not checked:
            raise ValueError(f"{field} is empty")
        if field != "fingerprint" and _UNSAFE.search(checked):
            raise ValueError(f"{field} is unsafe")
        return checked
    if isinstance(value, tuple):
        return tuple(_sanitize_boundary(item, field) for item in value)
    if isinstance(value, list):
        return [_sanitize_boundary(item, field) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_boundary(item, str(key)) for key, item in value.items()}
    return value


class PCBContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )
    fingerprint: str = _AUTO
    _fingerprint_exempt: ClassVar[bool] = False
    _legacy_fingerprint_excludes: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def copy_and_require_tuples(cls, value: object) -> object:
        if not isinstance(value, dict):
            if isinstance(value, cls):
                value = value.model_dump(mode="python")
            else:
                return value
        material = copy.deepcopy(value)
        for name, field in cls.model_fields.items():
            if (
                name in material
                and get_origin(field.annotation) is tuple
                and type(material[name]) is not tuple
            ):
                raise TypeError(f"{name} must be a tuple")
            annotation = field.annotation
            if (
                name in material
                and isinstance(annotation, type)
                and issubclass(annotation, StrEnum)
                and isinstance(material[name], str)
            ):
                material[name] = annotation(material[name])
            if (
                name in material
                and name.endswith("_fingerprint")
                and (
                    material[name] is not None
                    and (
                        not isinstance(material[name], str)
                        or _FP.fullmatch(material[name]) is None
                    )
                )
            ):
                raise ValueError(f"{name} is invalid")
            if name in material and name.endswith("_fingerprints"):
                values = material[name]
                if type(values) is not tuple or any(
                    not isinstance(item, str) or _FP.fullmatch(item) is None
                    for item in values
                ):
                    raise ValueError(f"{name} is invalid")
        return {name: _sanitize_boundary(item, name) for name, item in material.items()}

    @field_validator("*", mode="before")
    @classmethod
    def normalize_boundary_values(cls, value: object, info: Any) -> object:
        if isinstance(value, StrEnum):
            return value
        if isinstance(value, str):
            checked = unicodedata.normalize("NFC", value).strip()
            if not checked:
                raise ValueError(f"{info.field_name} is empty")
            if info.field_name != "fingerprint" and _UNSAFE.search(checked):
                raise ValueError(f"{info.field_name} is unsafe")
            return checked
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                raise ValueError(f"{info.field_name} must be UTC")
            return value.astimezone(UTC)
        return value

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint_shape(cls, value: object) -> str:
        if not isinstance(value, str) or _FP.fullmatch(value) is None:
            raise ValueError("fingerprint is invalid")
        return value

    @model_validator(mode="after")
    def bind_fingerprint(self) -> PCBContract:
        expected = canonical_fingerprint(self)
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            legacy_excludes = set(self._legacy_fingerprint_excludes)
            if not legacy_excludes or self.fingerprint != canonical_fingerprint(self, exclude=legacy_excludes):
                raise ValueError("fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object):
        return cls.model_validate(values)


class PCBArtifactType(StrEnum):
    BOARD = "BOARD"
    SCHEMATIC = "SCHEMATIC"
    BOM = "BOM"
    NETLIST = "NETLIST"


class PCBSourceFormat(StrEnum):
    KICAD_PCB = "KICAD_PCB"
    KICAD_SCH = "KICAD_SCH"
    CSV_BOM = "CSV_BOM"
    KICAD_XML_NETLIST = "KICAD_XML_NETLIST"


class PCBEvidenceClaim(StrEnum):
    OBSERVED = "OBSERVED"
    CITED = "CITED"
    INFERRED = "INFERRED"


class PCBEvidenceSource(StrEnum):
    ARTIFACT = "ARTIFACT"
    DATASHEET = "DATASHEET"
    CONTEXT = "CONTEXT"
    RULE = "RULE"


class PCBPopulation(StrEnum):
    POPULATED = "POPULATED"
    DNP = "DNP"
    MECHANICAL = "MECHANICAL"


class PCBNetType(StrEnum):
    POWER = "POWER"
    GROUND = "GROUND"
    SIGNAL = "SIGNAL"
    CLOCK = "CLOCK"
    UNKNOWN = "UNKNOWN"


class PCBGraphNodeType(StrEnum):
    COMPONENT = "COMPONENT"
    PIN = "PIN"
    NET = "NET"
    INTERFACE = "INTERFACE"
    BOM_ITEM = "BOM_ITEM"
    DATASHEET = "DATASHEET"


class PCBGraphEdgeType(StrEnum):
    HAS_PIN = "HAS_PIN"
    CONNECTED_TO = "CONNECTED_TO"
    PART_OF_NET = "PART_OF_NET"
    IMPLEMENTS_INTERFACE = "IMPLEMENTS_INTERFACE"
    MATCHES_BOM = "MATCHES_BOM"
    DESCRIBED_BY = "DESCRIBED_BY"


class PCBFindingSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class PCBFindingCategory(StrEnum):
    STRUCTURE = "STRUCTURE"
    CONNECTIVITY = "CONNECTIVITY"
    POWER = "POWER"
    INTERFACE = "INTERFACE"
    BOM_CONSISTENCY = "BOM_CONSISTENCY"
    DATASHEET_CONSTRAINT = "DATASHEET_CONSTRAINT"
    CROSS_ARTIFACT_CONSISTENCY = "CROSS_ARTIFACT_CONSISTENCY"


class PCBProposalType(StrEnum):
    LAYOUT_REVIEW = "LAYOUT_REVIEW"
    SCHEMATIC_CHANGE = "SCHEMATIC_CHANGE"
    BOM_CHANGE = "BOM_CHANGE"
    NETLIST_REVIEW = "NETLIST_REVIEW"
    COMPONENT_REVIEW = "COMPONENT_REVIEW"


class PCBProposalRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PCBAnalysisStatus(StrEnum):
    COMPLETE = "COMPLETE"


class PCBRepairHandoffStatus(StrEnum):
    RECORDED = "RECORDED"


class PCBArtifactReference(PCBContract):
    project_id: str
    artifact_id: str
    artifact_type: PCBArtifactType
    source_format: PCBSourceFormat
    source_reference: str
    filename: str
    byte_size: int = Field(gt=0, le=16_777_216)
    content_fingerprint: str
    captured_at: datetime

    @model_validator(mode="after")
    def verify_binding(self) -> PCBArtifactReference:
        _checked_id(self.project_id, "project_id")
        _checked_id(self.artifact_id, "artifact_id")
        if _LOGICAL_REF.fullmatch(self.source_reference) is None:
            raise ValueError("source_reference is invalid")
        if (
            PurePath(self.filename).name != self.filename
            or "/" in self.filename
            or "\\" in self.filename
        ):
            raise ValueError("filename must be a basename")
        expected = {
            PCBArtifactType.BOARD: (PCBSourceFormat.KICAD_PCB, ".kicad_pcb"),
            PCBArtifactType.SCHEMATIC: (PCBSourceFormat.KICAD_SCH, ".kicad_sch"),
            PCBArtifactType.BOM: (PCBSourceFormat.CSV_BOM, ".csv"),
            PCBArtifactType.NETLIST: (PCBSourceFormat.KICAD_XML_NETLIST, ".xml"),
        }[self.artifact_type]
        if self.source_format != expected[0] or not self.filename.casefold().endswith(
            expected[1]
        ):
            raise ValueError("artifact type, format, and filename mismatch")
        if _FP.fullmatch(self.content_fingerprint) is None:
            raise ValueError("content_fingerprint is invalid")
        return self


class PCBEvidenceReference(PCBContract):
    evidence_id: str
    project_id: str
    source_type: PCBEvidenceSource
    source_id: str
    source_fingerprint: str
    claim: PCBEvidenceClaim
    locator: str

    @model_validator(mode="after")
    def verify_fields(self) -> PCBEvidenceReference:
        for field in ("evidence_id", "project_id", "source_id"):
            _checked_id(getattr(self, field), field)
        if (
            _FP.fullmatch(self.source_fingerprint) is None
            or _LOGICAL_REF.fullmatch(self.locator) is None
        ):
            raise ValueError("evidence source binding is invalid")
        return self


class PCBPin(PCBContract):
    component_reference: str
    pin_number: str
    pin_name: str | None = None
    electrical_type: str | None = None
    no_connect: bool = False
    evidence_refs: tuple[PCBEvidenceReference, ...]


class PCBComponent(PCBContract):
    reference: str
    value: str | None = None
    # KiCad schematic symbols commonly have no footprint assigned yet.  Keep
    # that observed state distinct from an invented footprint binding.
    footprint: str | None = None
    manufacturer: str | None = None
    manufacturer_part_number: str | None = None
    population: PCBPopulation
    pins: tuple[PCBPin, ...] = ()
    datasheet_evidence_refs: tuple[PCBEvidenceReference, ...] = ()
    evidence_refs: tuple[PCBEvidenceReference, ...]

    @model_validator(mode="after")
    def verify_component(self) -> PCBComponent:
        pins = tuple(pin.pin_number for pin in self.pins)
        if len(pins) != len(set(pins)) or any(
            pin.component_reference != self.reference for pin in self.pins
        ):
            raise ValueError("component pin binding is invalid")
        return self


class PCBNet(PCBContract):
    net_id: str
    name: str
    net_type: PCBNetType
    evidence_refs: tuple[PCBEvidenceReference, ...]


class PCBConnection(PCBContract):
    component_reference: str
    pin_number: str
    net_id: str
    connection_kind: str
    artifact_fingerprints: tuple[str, ...]
    evidence_refs: tuple[PCBEvidenceReference, ...]


class PCBBOMItem(PCBContract):
    item_id: str
    references: tuple[str, ...]
    quantity: int = Field(gt=0, le=10_000)
    value: str
    footprint: str | None = None
    manufacturer: str | None = None
    manufacturer_part_number: str | None = None
    symbol: str | None = None
    description: str | None = None
    source_reference: str | None = None
    population: PCBPopulation
    evidence_refs: tuple[PCBEvidenceReference, ...]

    @model_validator(mode="after")
    def verify_quantity(self) -> PCBBOMItem:
        if (
            not self.references
            or len(self.references) != len(set(self.references))
            or self.quantity != len(self.references)
        ):
            raise ValueError("BOM references and quantity mismatch")
        return self


class PCBInterface(PCBContract):
    interface_id: str
    protocol: str
    signal_nets: tuple[str, ...]
    claim: PCBEvidenceClaim
    evidence_refs: tuple[PCBEvidenceReference, ...]


class PCBBoardArtifact(PCBContract):
    artifact: PCBArtifactReference
    components: tuple[PCBComponent, ...]
    nets: tuple[PCBNet, ...]
    connections: tuple[PCBConnection, ...]

    @model_validator(mode="after")
    def verify_type(self) -> PCBBoardArtifact:
        if self.artifact.artifact_type is not PCBArtifactType.BOARD:
            raise ValueError("board artifact type mismatch")
        return self


class PCBSchematicArtifact(PCBContract):
    artifact: PCBArtifactReference
    components: tuple[PCBComponent, ...]
    connections: tuple[PCBConnection, ...] = ()

    @model_validator(mode="after")
    def verify_type(self) -> PCBSchematicArtifact:
        if self.artifact.artifact_type is not PCBArtifactType.SCHEMATIC:
            raise ValueError("schematic artifact type mismatch")
        return self


class PCBBOMArtifact(PCBContract):
    artifact: PCBArtifactReference
    items: tuple[PCBBOMItem, ...]

    @model_validator(mode="after")
    def verify_type(self) -> PCBBOMArtifact:
        if self.artifact.artifact_type is not PCBArtifactType.BOM:
            raise ValueError("BOM artifact type mismatch")
        return self


class PCBNetlistArtifact(PCBContract):
    artifact: PCBArtifactReference
    components: tuple[PCBComponent, ...]
    nets: tuple[PCBNet, ...]
    connections: tuple[PCBConnection, ...]

    @model_validator(mode="after")
    def verify_type(self) -> PCBNetlistArtifact:
        if self.artifact.artifact_type is not PCBArtifactType.NETLIST:
            raise ValueError("netlist artifact type mismatch")
        component_refs = {item.reference for item in self.components}
        net_ids = {item.net_id for item in self.nets}
        pin_ids = {
            (item.reference, pin.pin_number)
            for item in self.components
            for pin in item.pins
        }
        if any(
            item.component_reference not in component_refs
            or item.net_id not in net_ids
            or (item.component_reference, item.pin_number) not in pin_ids
            for item in self.connections
        ):
            raise ValueError("netlist connection identity mismatch")
        return self


ParsedPCBArtifact = (
    PCBBoardArtifact | PCBSchematicArtifact | PCBBOMArtifact | PCBNetlistArtifact
)


class PCBDatasheetConstraint(PCBContract):
    constraint_id: str
    name: str
    value: str
    unit: str
    evidence_refs: tuple[PCBEvidenceReference, ...]


class PCBDatasheetEvidence(PCBContract):
    project_id: str
    component_reference: str
    component_part_number: str
    constraints: tuple[PCBDatasheetConstraint, ...]
    evidence_refs: tuple[PCBEvidenceReference, ...]

    @model_validator(mode="after")
    def verify_citations(self) -> PCBDatasheetEvidence:
        if not self.evidence_refs or any(
            item.project_id != self.project_id
            or item.source_type is not PCBEvidenceSource.DATASHEET
            or item.claim is not PCBEvidenceClaim.CITED
            for item in self.evidence_refs
        ):
            raise ValueError("datasheet evidence must be cited and project-bound")
        return self


class PCBContextRequest(PCBContract):
    request_id: str
    project_id: str
    artifacts: tuple[PCBArtifactReference, ...]
    datasheet_scope: tuple[str, ...] = ()
    engineering_task: str
    requested_at: datetime

    @model_validator(mode="after")
    def verify_request(self) -> PCBContextRequest:
        _checked_id(self.request_id, "request_id")
        _checked_id(self.project_id, "project_id")
        types = tuple(item.artifact_type for item in self.artifacts)
        if (
            len(self.artifacts) != 4
            or set(types) != set(PCBArtifactType)
            or len(types) != len(set(types))
        ):
            raise ValueError("exactly one artifact of each type is required")
        if any(item.project_id != self.project_id for item in self.artifacts):
            raise ValueError("artifact project binding mismatch")
        if len(self.datasheet_scope) != len(set(self.datasheet_scope)):
            raise ValueError("datasheet_scope must be unique")
        return self


class PCBContext(PCBContract):
    request_id: str
    project_id: str
    artifacts: tuple[PCBArtifactReference, ...]
    board: PCBBoardArtifact
    schematic: PCBSchematicArtifact
    bom: PCBBOMArtifact
    netlist: PCBNetlistArtifact
    components: tuple[PCBComponent, ...]
    nets: tuple[PCBNet, ...]
    connections: tuple[PCBConnection, ...]
    bom_items: tuple[PCBBOMItem, ...]
    interfaces: tuple[PCBInterface, ...]
    datasheets: tuple[PCBDatasheetEvidence, ...]
    engineering_task: str
    engineering_context_fingerprint: str
    provenance: tuple[PCBEvidenceReference, ...]

    @model_validator(mode="after")
    def verify_context_binding(self) -> PCBContext:
        expected = {
            PCBArtifactType.BOARD: self.board.artifact,
            PCBArtifactType.SCHEMATIC: self.schematic.artifact,
            PCBArtifactType.BOM: self.bom.artifact,
            PCBArtifactType.NETLIST: self.netlist.artifact,
        }
        if self.request_id == "" or any(
            item.project_id != self.project_id
            or expected.get(item.artifact_type) != item
            for item in self.artifacts
        ):
            raise ValueError("PCB context artifact binding mismatch")
        if (
            len(self.artifacts) != 4
            or len({item.artifact_type for item in self.artifacts}) != 4
        ):
            raise ValueError("PCB context requires four unique artifacts")
        if any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("PCB context provenance project mismatch")
        if _FP.fullmatch(self.engineering_context_fingerprint) is None:
            raise ValueError("engineering context fingerprint is invalid")
        return self


class PCBGraphNode(PCBContract):
    node_id: str
    node_type: PCBGraphNodeType
    identity: str
    source_fingerprint: str
    evidence_refs: tuple[PCBEvidenceReference, ...]


class PCBGraphEdge(PCBContract):
    edge_id: str
    edge_type: PCBGraphEdgeType
    source_node_id: str
    target_node_id: str
    evidence_refs: tuple[PCBEvidenceReference, ...]


class EngineeringPCBGraph(PCBContract):
    graph_id: str
    project_id: str
    context_fingerprint: str
    nodes: tuple[PCBGraphNode, ...]
    edges: tuple[PCBGraphEdge, ...]
    evidence_refs: tuple[PCBEvidenceReference, ...]

    @model_validator(mode="after")
    def verify_graph(self) -> EngineeringPCBGraph:
        node_ids = {item.node_id for item in self.nodes}
        edge_ids = tuple(item.edge_id for item in self.edges)
        if len(node_ids) != len(self.nodes) or len(edge_ids) != len(set(edge_ids)):
            raise ValueError("PCB graph identities must be unique")
        if any(
            item.source_node_id not in node_ids or item.target_node_id not in node_ids
            for item in self.edges
        ):
            raise ValueError("PCB graph edge references an unknown node")
        return self


class PCBAnalysisRequest(PCBContract):
    analysis_id: str
    project_id: str
    context_fingerprint: str
    rule_scope: tuple[str, ...]
    requester: str
    requested_at: datetime


class PCBObservation(PCBContract):
    observation_id: str
    rule_id: str
    claim: PCBEvidenceClaim
    message: str
    affected_ids: tuple[str, ...]
    evidence_refs: tuple[PCBEvidenceReference, ...]


class PCBFinding(PCBContract):
    finding_id: str
    project_id: str
    rule_id: str
    category: PCBFindingCategory
    severity: PCBFindingSeverity
    message: str
    affected_ids: tuple[str, ...]
    evidence_refs: tuple[PCBEvidenceReference, ...]


class PCBProposal(PCBContract):
    proposal_id: str
    project_id: str
    finding_fingerprints: tuple[str, ...]
    target_artifact_fingerprints: tuple[str, ...]
    proposal_type: PCBProposalType
    recommended_changes: tuple[str, ...]
    rationale: str
    risk: PCBProposalRisk
    evidence_refs: tuple[PCBEvidenceReference, ...]

    @model_validator(mode="after")
    def forbid_action_payloads(self) -> PCBProposal:
        joined = " ".join((*self.recommended_changes, self.rationale)).casefold()
        forbidden = (
            "diff --git",
            "@@ -",
            "git apply",
            "subprocess",
            ".kicad_pcb/",
            ".kicad_sch/",
        )
        if any(item in joined for item in forbidden):
            raise ValueError("proposal contains executable or source content")
        return self


class PCBAnalysis(PCBContract):
    analysis_id: str
    project_id: str
    context_fingerprint: str
    graph_fingerprint: str
    engineering_context_fingerprint: str
    observations: tuple[PCBObservation, ...]
    findings: tuple[PCBFinding, ...]
    proposals: tuple[PCBProposal, ...]
    evidence_refs: tuple[PCBEvidenceReference, ...]
    status: PCBAnalysisStatus

    @model_validator(mode="after")
    def verify_analysis_binding(self) -> PCBAnalysis:
        if any(
            item.project_id != self.project_id
            for item in (*self.findings, *self.proposals)
        ):
            raise ValueError("PCB analysis project binding mismatch")
        finding_fingerprints = {item.fingerprint for item in self.findings}
        if any(
            not set(item.finding_fingerprints).issubset(finding_fingerprints)
            for item in self.proposals
        ):
            raise ValueError("PCB proposal finding binding mismatch")
        return self


class PCBGovernanceBinding(PCBContract):
    pcb_proposal_fingerprint: str
    validation_finding_fingerprints: tuple[str, ...]
    optimization_proposal_id: str
    optimization_proposal_fingerprint: str


class PCBGovernanceBundle(PCBContract):
    bundle_id: str
    project_id: str
    analysis: PCBAnalysis
    validation_findings: tuple[ValidationFinding, ...]
    optimization_proposals: tuple[OptimizationProposal, ...]
    bindings: tuple[PCBGovernanceBinding, ...]

    @model_validator(mode="after")
    def verify_bundle_binding(self) -> PCBGovernanceBundle:
        if self.analysis.project_id != self.project_id or any(
            item.project_id != self.project_id
            for item in (*self.validation_findings, *self.optimization_proposals)
        ):
            raise ValueError("governance bundle project binding mismatch")
        pcb_fps = {item.fingerprint for item in self.analysis.proposals}
        finding_fps = {item.fingerprint for item in self.validation_findings}
        optimization = {
            item.proposal_id: item.fingerprint for item in self.optimization_proposals
        }
        if any(
            item.pcb_proposal_fingerprint not in pcb_fps
            or not set(item.validation_finding_fingerprints).issubset(finding_fps)
            or optimization.get(item.optimization_proposal_id)
            != item.optimization_proposal_fingerprint
            for item in self.bindings
        ):
            raise ValueError("governance binding is invalid")
        return self


class PCBRepairHandoff(PCBContract):
    handoff_id: str
    project_id: str
    pcb_proposal_fingerprint: str
    optimization_proposal_id: str
    optimization_proposal_fingerprint: str
    review_id: str
    review_record_fingerprint: str
    artifact_fingerprints: tuple[str, ...]
    recorded_at: datetime


class PCBRepairHandoffReceipt(PCBContract):
    receipt_id: str
    project_id: str
    handoff_fingerprint: str
    status: PCBRepairHandoffStatus
    recorded_at: datetime


__all__ = tuple(
    name
    for name in globals()
    if name.startswith("PCB")
    or name
    in {
        "EngineeringPCBGraph",
        "ParsedPCBArtifact",
        "canonical_fingerprint",
        "content_fingerprint",
        "deterministic_id",
    }
)
