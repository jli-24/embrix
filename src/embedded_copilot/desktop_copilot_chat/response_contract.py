from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence

from .contracts import ResponseType, identifier


_DOCUMENT_REQUEST = re.compile(
    r"(?:生成|输出|制作|撰写|编写|导出|generate|write|export)"
    r"[^\n]{0,40}(?:报告|文档|说明书|方案|规格书|report|document|specification|design\s+plan)",
    re.IGNORECASE,
)
_ARTIFACT_REQUEST = re.compile(
    r"(?:生成|创建|编写|输出|写入|produce|create|generate)"
    r"[^\n]{0,40}(?:工程文件|文件|代码|源码|artifact|"
    r"[A-Za-z0-9_.-]+\.(?:c|h|cc|cpp|hpp|py|json|ya?ml|toml|ini|md|kicad_sch|kicad_pcb))",
    re.IGNORECASE,
)
_EXPLICIT_DOCUMENT_CONFIRMATIONS = frozenset(
    {
        "是",
        "确认",
        "确认生成",
        "确认生成报告",
        "开始生成",
        "开始生成报告",
        "生成吧",
        "请生成报告",
    }
)


def _metadata_response_type(metadata: Mapping[str, object] | None) -> ResponseType | None:
    if metadata is None:
        return None
    candidates: list[object] = [metadata.get("response_type")]
    nested = metadata.get("metadata")
    if isinstance(nested, Mapping):
        candidates.append(nested.get("response_type"))
    for value in candidates:
        if isinstance(value, ResponseType):
            return value
        if isinstance(value, str):
            try:
                return ResponseType(value.upper())
            except ValueError:
                continue
    if metadata.get("document_generation") is True or metadata.get("document_requested") is True:
        return ResponseType.DOCUMENT
    return None


def extract_artifact_refs(metadata: Mapping[str, object] | None) -> tuple[str, ...]:
    """Extract only legacy artifact identities; never copy arbitrary content."""

    if metadata is None:
        return ()
    values: list[object] = []
    raw_refs = metadata.get("artifact_refs")
    if isinstance(raw_refs, Sequence) and not isinstance(raw_refs, (str, bytes, bytearray)):
        values.extend(raw_refs)
    for key in ("artifact_id", "file_id", "doc_id"):
        if key in metadata:
            values.append(metadata[key])
    refs: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("artifact_id") or value.get("file_id") or value.get("doc_id")
        if not isinstance(value, str):
            continue
        try:
            checked = identifier(value, field="artifact_ref")
        except (TypeError, ValueError):
            continue
        if checked.casefold() not in {item.casefold() for item in refs}:
            refs.append(checked)
    return tuple(refs)


def resolve_response_type(
    question: str,
    *,
    metadata: Mapping[str, object] | None = None,
    artifact_refs: Sequence[str] = (),
    report_confirmation_allowed: bool = False,
) -> ResponseType:
    """Resolve a durable type at the backend boundary.

    Markdown is deliberately absent from this decision. Existing explicit
    metadata wins, followed by explicit user intent and real artifact identity.
    """

    explicit = _metadata_response_type(metadata)
    if explicit is not None:
        return explicit
    if _DOCUMENT_REQUEST.search(question if isinstance(question, str) else ""):
        return ResponseType.DOCUMENT
    normalized_question = " ".join(question.strip().casefold().rstrip("。！？!?，,；;、").split()) if isinstance(question, str) else ""
    if report_confirmation_allowed and normalized_question in _EXPLICIT_DOCUMENT_CONFIRMATIONS:
        return ResponseType.DOCUMENT
    if artifact_refs or _ARTIFACT_REQUEST.search(question if isinstance(question, str) else ""):
        return ResponseType.ARTIFACT
    return ResponseType.CHAT


def migrate_conversation_payload(value: Mapping[str, object]) -> dict[str, object]:
    """Backfill response metadata for legacy catalog rows, idempotently."""

    material = copy.deepcopy(dict(value))
    turns = material.get("turns")
    if not isinstance(turns, Sequence) or isinstance(turns, (str, bytes, bytearray)):
        return material
    changed = False
    migrated_turns: list[object] = []
    for raw_turn in turns:
        if not isinstance(raw_turn, Mapping):
            migrated_turns.append(raw_turn)
            continue
        turn = copy.deepcopy(dict(raw_turn))
        turn_changed = False
        refs = extract_artifact_refs(turn)
        resolved = resolve_response_type(
            str(turn.get("question", "")), metadata=turn, artifact_refs=refs
        )
        if turn.get("response_type") != resolved.value:
            turn["response_type"] = resolved.value
            turn_changed = True
        if turn.get("artifact_refs") != list(refs):
            turn["artifact_refs"] = list(refs)
            turn_changed = True
        if turn_changed:
            turn["fingerprint"] = "sha256:" + "0" * 64
            changed = True
        migrated_turns.append(turn)
    if changed:
        material["turns"] = migrated_turns
        material["fingerprint"] = "sha256:" + "0" * 64
    return material


__all__ = (
    "ResponseType",
    "extract_artifact_refs",
    "migrate_conversation_payload",
    "resolve_response_type",
)
