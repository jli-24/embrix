from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from embedded_copilot.desktop_copilot_chat.response_contract import (
    ResponseType,
    migrate_conversation_payload,
    resolve_response_type,
)
from embedded_copilot.project_catalog import (
    ConversationDetail,
    ConversationTurn,
    ConversationTurnStatus,
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
    SQLiteProjectCatalogRepository,
)


def test_response_type_is_not_inferred_from_markdown() -> None:
    legacy = {
        "turns": [
            {
                "question": "普通工程讨论",
                "answer_text": "# STM32 设计方案\n\n## 系统架构\n\n正文",
                "status": "COMPLETED",
                "response_type": "CHAT",
            }
        ]
    }

    migrated = migrate_conversation_payload(legacy)

    assert migrated["turns"][0]["response_type"] == ResponseType.CHAT.value
    assert migrated["turns"][0]["artifact_refs"] == []


def test_legacy_migration_prefers_metadata_then_intent_and_artifact_identity() -> None:
    payload = {
        "turns": [
            {"question": "生成需求分析报告", "response_type": "CHAT"},
            {"question": "生成 STM32 小车需求文档"},
            {"question": "生成 main.c"},
            {"question": "导出设计方案", "artifact_id": "artifact-1"},
        ]
    }

    turns = migrate_conversation_payload(payload)["turns"]

    assert [item["response_type"] for item in turns] == [
        "CHAT",
        "DOCUMENT",
        "ARTIFACT",
        "DOCUMENT",
    ]
    assert turns[2]["artifact_refs"] == []
    assert turns[3]["artifact_refs"] == ["artifact-1"]


def test_legacy_migration_uses_artifact_relation_when_no_explicit_intent() -> None:
    migrated = migrate_conversation_payload({
        "turns": [{"question": "查看这个工程资源", "file_id": "file-1"}],
    })

    assert migrated["turns"][0]["response_type"] == ResponseType.ARTIFACT.value
    assert migrated["turns"][0]["artifact_refs"] == ["file-1"]


def test_backend_resolver_defaults_to_chat_without_response_metadata() -> None:
    assert resolve_response_type("继续") is ResponseType.CHAT
    assert resolve_response_type("生成需求分析报告") is ResponseType.DOCUMENT
    assert resolve_response_type("生成 main.c") is ResponseType.ARTIFACT
    assert resolve_response_type("确认生成") is ResponseType.CHAT
    assert resolve_response_type("确认生成", report_confirmation_allowed=True) is ResponseType.DOCUMENT


def test_sqlite_repository_migrates_legacy_turns_on_startup(tmp_path) -> None:
    database = tmp_path / "catalog.sqlite3"
    repository = SQLiteProjectCatalogRepository(database)
    now = datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
    project = ProjectView(
        project_id="project-1",
        tenant_id="tenant-1",
        name="STM32 Smart Car",
        location_id="location-1",
        directory_name="smart-car",
        creation_mode=ProjectCreationMode.CREATE_NEW,
        relative_path="smart-car",
        project_type=ProjectType.STM32,
        initialization_status=ProjectInitializationStatus.READY,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )
    repository.save_project(project)
    detail = ConversationDetail(
        conversation_id="conversation-1",
        project_id=project.project_id,
        tenant_id=project.tenant_id,
        requester="engineer-1",
        title="旧会话",
        turns=(ConversationTurn(
            turn_id="turn-1",
            request_id="request-1",
            status=ConversationTurnStatus.COMPLETED,
            question="普通工程讨论",
            answer_text="# STM32 设计方案",
            created_at=now,
            completed_at=now,
        ),),
        created_at=now,
        updated_at=now,
    )
    repository.save_conversation(detail)
    with sqlite3.connect(database) as connection:
        material = json.loads(detail.model_dump_json())
        legacy_payload = detail.model_dump(
            mode="json",
            exclude={"fingerprint", "hardware_design_spec"},
        )
        encoded = json.dumps(
            legacy_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        material.pop("hardware_design_spec", None)
        material["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
        material["turns"][0].pop("response_type", None)
        material["turns"][0].pop("artifact_refs", None)
        connection.execute(
            "UPDATE conversations SET body=? WHERE conversation_id=?",
            (json.dumps(material, ensure_ascii=False), detail.conversation_id),
        )

    migrated = SQLiteProjectCatalogRepository(database).conversation(detail.conversation_id)

    assert migrated is not None
    assert migrated.turns[0].response_type is ResponseType.CHAT
    with sqlite3.connect(database) as connection:
        stored = json.loads(connection.execute("SELECT body FROM conversations WHERE conversation_id=?", (detail.conversation_id,)).fetchone()[0])
    assert stored["turns"][0]["response_type"] == "CHAT"


def test_resumed_session_keeps_original_trace_session_binding_after_repository_restart(tmp_path) -> None:
    database = tmp_path / "catalog.sqlite3"
    repository = SQLiteProjectCatalogRepository(database)
    now = datetime(2026, 9, 29, tzinfo=UTC)
    project = ProjectView(
        project_id="project-1", tenant_id="tenant-1", name="Trace project",
        location_id="location-1", directory_name="trace-project",
        creation_mode=ProjectCreationMode.CREATE_NEW, relative_path="trace-project",
        project_type=ProjectType.STM32, initialization_status=ProjectInitializationStatus.READY,
        created_at=now, updated_at=now, last_activity_at=now,
    )
    repository.save_project(project)
    conversation = ConversationDetail(
        conversation_id="conversation-1", project_id=project.project_id,
        tenant_id=project.tenant_id, requester="engineer-1", title="Trace history",
        turns=(ConversationTurn(
            turn_id="turn-1", request_id="request-1", trace_id="trace-1",
            status=ConversationTurnStatus.COMPLETED, question="Question",
            answer_text="Answer", created_at=now, completed_at=now,
        ),), created_at=now, updated_at=now,
    )
    repository.save_conversation(conversation)
    repository.bind_session("session-original", conversation.conversation_id)
    repository.bind_session("session-resumed", conversation.conversation_id)

    restarted = SQLiteProjectCatalogRepository(database)
    assert restarted.conversation_for_session("session-original") == conversation
    assert restarted.conversation_for_session("session-resumed") == conversation
