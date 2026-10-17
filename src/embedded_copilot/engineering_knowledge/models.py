from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,255}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_UNSAFE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|https?://|\x00|\r|\n|"
    r"credential|password|secret|token|provider|runtime|prompt|cot|"
    r"stdout|stderr|raw\s+log)",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}")


class EngineeringKnowledgeContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class NodeType(StrEnum):
    PROJECT = "PROJECT"
    COMPONENT = "COMPONENT"
    MCU = "MCU"
    SENSOR = "SENSOR"
    INTERFACE = "INTERFACE"
    DECISION = "DECISION"
    PROBLEM = "PROBLEM"
    SOLUTION = "SOLUTION"
    CONSTRAINT = "CONSTRAINT"
    REQUIREMENT = "REQUIREMENT"
    MEMORY = "MEMORY"


class RelationType(StrEnum):
    USES = "USES"
    DEPENDS_ON = "DEPENDS_ON"
    CONFLICTS_WITH = "CONFLICTS_WITH"
    SOLVED_BY = "SOLVED_BY"
    DERIVED_FROM = "DERIVED_FROM"
    VERIFIED_BY = "VERIFIED_BY"
    OPTIMIZED_BY = "OPTIMIZED_BY"
    RELATED_TO = "RELATED_TO"


class EngineeringVerificationStatus(StrEnum):
    APPROVED = "APPROVED"


def _text(value: object, *, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} is invalid")  # noqa: TRY004
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str, maximum: int = 128) -> str:
    checked = _text(value, field=field, maximum=maximum)
    if not _IDENTIFIER.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def reference(value: object, *, field: str) -> str:
    checked = _text(value, field=field, maximum=256)
    if not _REFERENCE.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def tuple_only(value: object, *, field: str) -> object:
    if type(value) is not tuple:
        raise ValueError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value.strip()):
        raise ValueError(f"{field} is invalid")
    return value.strip()


def canonical_fingerprint(
    value: BaseModel, *, exclude: set[str] | frozenset[str] = frozenset()
) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, float):
        raise TypeError("confidence must be a float")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("confidence is invalid")
    return value


class EngineeringKnowledgeNode(EngineeringKnowledgeContract):
    node_id: str
    project_id: str
    node_type: NodeType
    entity_name: str
    summary: str
    source_memory_id: str
    source_reference: str
    confidence: float
    verification_status: Literal["APPROVED"] = "APPROVED"
    fingerprint: str

    @field_validator("node_id", "project_id", "source_memory_id", mode="before")
    @classmethod
    def validate_identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("entity_name", "summary", mode="before")
    @classmethod
    def validate_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("source_reference", mode="before")
    @classmethod
    def validate_source_reference(cls, value: object) -> str:
        return reference(value, field="source_reference")

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float:
        return _confidence(value)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> EngineeringKnowledgeNode:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("knowledge node fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringKnowledgeNode:
        material = dict(values)
        material["node_id"] = identifier(material["node_id"], field="node_id")
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["entity_name"] = _text(material["entity_name"], field="entity_name")
        material["summary"] = _text(material["summary"], field="summary")
        material["source_memory_id"] = identifier(
            material["source_memory_id"], field="source_memory_id"
        )
        material["source_reference"] = reference(
            material["source_reference"], field="source_reference"
        )
        material["node_type"] = NodeType(material["node_type"])
        material["confidence"] = _confidence(material["confidence"])
        material.setdefault("verification_status", "APPROVED")
        provisional = cls.model_construct(
            **{**material, "fingerprint": "sha256:" + "0" * 64}
        )
        material["fingerprint"] = canonical_fingerprint(
            provisional, exclude={"fingerprint"}
        )
        return cls.model_validate(material)


class EngineeringRelation(EngineeringKnowledgeContract):
    relation_id: str
    source_node_id: str
    target_node_id: str
    relation_type: RelationType
    confidence: float
    source_memory_id: str
    provenance: str = "memory"
    evidence: tuple[str, ...] = ()
    verification_status: Literal["APPROVED"] = "APPROVED"
    fingerprint: str

    @field_validator(
        "relation_id", "source_node_id", "target_node_id", "source_memory_id",
        mode="before",
    )
    @classmethod
    def validate_identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float:
        return _confidence(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def validate_provenance(cls, value: object) -> str:
        return reference(value, field="provenance")

    @field_validator("evidence", mode="before")
    @classmethod
    def validate_evidence(cls, value: object) -> object:
        return tuple_only(value, field="evidence")

    @field_validator("evidence")
    @classmethod
    def validate_evidence_values(cls, value: tuple[object, ...]) -> tuple[str, ...]:
        checked = tuple(reference(item, field="evidence") for item in value)
        if len(checked) != len(set(checked)):
            raise ValueError("relation evidence must be unique")
        return tuple(sorted(checked))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> EngineeringRelation:
        if self.source_node_id == self.target_node_id:
            raise ValueError("self relations are not allowed")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("knowledge relation fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringRelation:
        material = dict(values)
        for field in (
            "relation_id",
            "source_node_id",
            "target_node_id",
            "source_memory_id",
        ):
            material[field] = identifier(material[field], field=field)
        material["relation_type"] = RelationType(material["relation_type"])
        material["confidence"] = _confidence(material["confidence"])
        material["provenance"] = reference(
            material.get("provenance", "memory"), field="provenance"
        )
        material["evidence"] = tuple(
            sorted(
                reference(item, field="evidence")
                for item in tuple_only(
                    material.get("evidence", ()), field="evidence"
                )
            )
        )
        material.setdefault("verification_status", "APPROVED")
        provisional = cls.model_construct(
            **{**material, "fingerprint": "sha256:" + "0" * 64}
        )
        material["fingerprint"] = canonical_fingerprint(
            provisional, exclude={"fingerprint"}
        )
        return cls.model_validate(material)


class EngineeringGraphSnapshot(EngineeringKnowledgeContract):
    project_id: str
    nodes: tuple[EngineeringKnowledgeNode, ...] = Field(max_length=1024)
    relations: tuple[EngineeringRelation, ...] = Field(max_length=2048)
    fingerprint: str

    @field_validator("project_id", mode="before")
    @classmethod
    def validate_project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("nodes", "relations", mode="before")
    @classmethod
    def validate_collections(cls, value: object, info) -> object:
        return tuple_only(value, field=info.field_name)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> EngineeringGraphSnapshot:
        node_ids = tuple(node.node_id for node in self.nodes)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("knowledge node ids must be unique")
        if any(node.project_id != self.project_id for node in self.nodes):
            raise ValueError("knowledge node project binding mismatch")
        relation_ids = tuple(relation.relation_id for relation in self.relations)
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("knowledge relation ids must be unique")
        node_set = set(node_ids)
        if any(
            relation.source_node_id not in node_set
            or relation.target_node_id not in node_set
            for relation in self.relations
        ):
            raise ValueError("knowledge relation references unknown node")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("knowledge graph fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringGraphSnapshot:
        material = dict(values)
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["nodes"] = tuple(
            EngineeringKnowledgeNode.model_validate(copy.deepcopy(item))
            for item in tuple_only(material["nodes"], field="nodes")
        )
        material["relations"] = tuple(
            EngineeringRelation.model_validate(copy.deepcopy(item))
            for item in tuple_only(material["relations"], field="relations")
        )
        provisional = cls.model_construct(
            **{**material, "fingerprint": "sha256:" + "0" * 64}
        )
        material["fingerprint"] = canonical_fingerprint(
            provisional, exclude={"fingerprint"}
        )
        return cls.model_validate(material)


class EngineeringContextQuery(EngineeringKnowledgeContract):
    project_id: str
    query: str
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("project_id", mode="before")
    @classmethod
    def validate_project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("query", mode="before")
    @classmethod
    def validate_query(cls, value: object) -> str:
        return _text(value, field="query", maximum=512)


class EngineeringContextSnapshot(EngineeringKnowledgeContract):
    project_id: str
    query: str
    related_nodes: tuple[EngineeringKnowledgeNode, ...]
    related_relations: tuple[EngineeringRelation, ...]
    historical_decisions: tuple[EngineeringKnowledgeNode, ...]
    known_problems: tuple[EngineeringKnowledgeNode, ...]
    solutions: tuple[EngineeringKnowledgeNode, ...]
    constraints: tuple[EngineeringKnowledgeNode, ...]
    graph_fingerprint: str
    fingerprint: str

    @field_validator("project_id", mode="before")
    @classmethod
    def validate_project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("query", mode="before")
    @classmethod
    def validate_query(cls, value: object) -> str:
        return _text(value, field="query", maximum=512)

    @field_validator(
        "related_nodes", "related_relations", "historical_decisions",
        "known_problems", "solutions", "constraints", mode="before",
    )
    @classmethod
    def validate_collections(cls, value: object, info) -> object:
        return tuple_only(value, field=info.field_name)

    @field_validator("graph_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @model_validator(mode="after")
    def verify(self) -> EngineeringContextSnapshot:
        related_ids = {node.node_id for node in self.related_nodes}
        if any(
            node.node_id not in related_ids
            for group in (
                self.historical_decisions,
                self.known_problems,
                self.solutions,
                self.constraints,
            )
            for node in group
        ):
            raise ValueError("context category is not related to context")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("context fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringContextSnapshot:
        material = dict(values)
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["query"] = _text(material["query"], field="query", maximum=512)
        for field, model in (
            ("related_nodes", EngineeringKnowledgeNode),
            ("historical_decisions", EngineeringKnowledgeNode),
            ("known_problems", EngineeringKnowledgeNode),
            ("solutions", EngineeringKnowledgeNode),
            ("constraints", EngineeringKnowledgeNode),
            ("related_relations", EngineeringRelation),
        ):
            material[field] = tuple(
                model.model_validate(copy.deepcopy(item))
                for item in tuple_only(material[field], field=field)
            )
        material["graph_fingerprint"] = fingerprint(
            material["graph_fingerprint"], field="graph_fingerprint"
        )
        provisional = cls.model_construct(
            **{**material, "fingerprint": "sha256:" + "0" * 64}
        )
        material["fingerprint"] = canonical_fingerprint(
            provisional, exclude={"fingerprint"}
        )
        return cls.model_validate(material)


def validate_graph_snapshot(value: object) -> EngineeringGraphSnapshot:
    if type(value) is not EngineeringGraphSnapshot:
        raise TypeError("engineering graph snapshot is invalid")
    return EngineeringGraphSnapshot.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def tokens(value: str) -> frozenset[str]:
    return frozenset(item.casefold() for item in _TOKEN.findall(value))


__all__ = (
    "EngineeringContextQuery",
    "EngineeringContextSnapshot",
    "EngineeringGraphSnapshot",
    "EngineeringKnowledgeContract",
    "EngineeringKnowledgeNode",
    "EngineeringRelation",
    "EngineeringVerificationStatus",
    "NodeType",
    "RelationType",
    "canonical_fingerprint",
    "fingerprint",
    "identifier",
    "reference",
    "tokens",
    "tuple_only",
    "validate_graph_snapshot",
)
