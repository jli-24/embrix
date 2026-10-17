"""Idempotent migration helpers for persisted conversation turns."""

from embedded_copilot.desktop_copilot_chat.response_contract import (
    extract_artifact_refs,
    migrate_conversation_payload,
    resolve_response_type,
)

__all__ = (
    "extract_artifact_refs",
    "migrate_conversation_payload",
    "resolve_response_type",
)
