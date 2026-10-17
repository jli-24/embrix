from __future__ import annotations

import errno
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
    ProjectCatalogRejected,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectDirectoryReference,
    ProjectInitializationStatus,
    ProjectLocationReference,
    ProjectTargetNotWritable,
    ProjectType,
)
from embedded_copilot.v63_memory_governance import SQLiteV63Store, V63MemoryGovernanceService
from embedded_copilot.engineering_requirements import merge_requirement_context

NOW = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def runtime(tmp_path: Path, *, initialize_async: bool = False) -> ProjectCatalogRuntime:
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    return ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=Clock(),
        initialize_async=initialize_async,
    )


def request(**updates: object) -> ProjectCreationRequest:
    values: dict[str, object] = {
        "name": "Sensor Gateway",
        "relative_path": "sensor-gateway",
        "project_type": ProjectType.ESP_IDF,
    }
    values.update(updates)
    return ProjectCreationRequest.model_validate(values)


def test_preview_and_confirm_create_empty_project_without_source_scaffold(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    target = tmp_path / "projects" / "sensor-gateway"
    assert project.initialization_status is ProjectInitializationStatus.READY
    assert target.is_dir()
    assert list(target.iterdir()) == []
    assert project.relative_path == "sensor-gateway"
    assert str(tmp_path) not in project.model_dump_json()
    assert not (target / ".copilot").exists()
    assert not (target / ".embedded-copilot").exists()


def test_new_contract_creates_safe_unicode_project_from_controlled_location(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = service.preview(
        ProjectCreationRequest(
            name="循迹智能小车",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint),
        ),
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    assert preview.directory_name == "循迹智能小车"
    assert preview.location.display_label == "Embedded Projects"
    assert str(tmp_path) not in preview.model_dump_json()
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    assert (tmp_path / "projects" / "循迹智能小车").is_dir()
    assert project.creation_mode is ProjectCreationMode.CREATE_NEW


def _create_diagnostic_records(caplog: pytest.LogCaptureFixture) -> dict[str, logging.LogRecord]:
    return {
        record.stage: record
        for record in caplog.records
        if record.name == "embedded_copilot.project_catalog.runtime"
        and record.event_name == "project_catalog_create_diagnostic"
    }


def test_create_records_redacted_c0_to_c5_success_diagnostics(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")

    with caplog.at_level(logging.INFO, logger="embedded_copilot.project_catalog.runtime"):
        service.create(
            preview.preview_id,
            preview.fingerprint,
            confirmed_at=NOW,
            tenant_id="tenant-1",
            requester="engineer-1",
        )

    records = _create_diagnostic_records(caplog)
    assert set(records) == {
        "C0_CREATE_ENTER",
        "C1_TARGET_PREPARE",
        "C2_TARGET_DIR_CREATE",
        "C3_SIDECAR_WRITE",
        "C4_PROJECT_RECORD_WRITE",
        "C5_COMMIT_COMPLETE",
    }
    assert all(record.status == "PASS" for record in records.values())
    assert all(record.exception_type == "-" for record in records.values())
    assert all(record.reason_code == "-" for record in records.values())
    assert records["C1_TARGET_PREPARE"].project_root_writable is True
    assert records["C2_TARGET_DIR_CREATE"].target_exists is True
    assert records["C2_TARGET_DIR_CREATE"].project_root_writable is True
    assert records["C2_TARGET_DIR_CREATE"].write_attempted is True
    assert str(tmp_path) not in caplog.text


def test_create_rejects_non_writable_project_root_before_mkdir(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    monkeypatch.setattr(service, "_project_root_writable", lambda _root: False)

    with caplog.at_level(logging.INFO, logger="embedded_copilot.project_catalog.runtime"):
        with pytest.raises(ProjectTargetNotWritable):
            service.create(
                preview.preview_id,
                preview.fingerprint,
                confirmed_at=NOW,
                tenant_id="tenant-1",
                requester="engineer-1",
            )

    records = _create_diagnostic_records(caplog)
    assert records["C1_TARGET_PREPARE"].status == "PASS"
    assert records["C1_TARGET_PREPARE"].project_root_writable is False
    assert records["C2_TARGET_DIR_CREATE"].status == "FAIL"
    assert records["C2_TARGET_DIR_CREATE"].exception_type == "ProjectTargetNotWritable"
    assert records["C2_TARGET_DIR_CREATE"].reason_code == "PROJECT_TARGET_NOT_WRITABLE"
    assert records["C2_TARGET_DIR_CREATE"].project_root_writable is False
    assert records["C2_TARGET_DIR_CREATE"].write_attempted is False
    assert not (tmp_path / "projects" / "sensor-gateway").exists()
    assert str(tmp_path) not in caplog.text


def test_create_records_target_directory_failure_without_sensitive_detail(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    target = Mock()
    target.exists.return_value = False
    target.mkdir.side_effect = PermissionError(errno.EACCES, "private target path")
    monkeypatch.setattr(service, "_relative", lambda _value, root=None: ("sensor-gateway", target))

    with caplog.at_level(logging.INFO, logger="embedded_copilot.project_catalog.runtime"):
        with pytest.raises(ProjectTargetNotWritable):
            service.create(
                preview.preview_id,
                preview.fingerprint,
                confirmed_at=NOW,
                tenant_id="tenant-1",
                requester="engineer-1",
            )

    records = _create_diagnostic_records(caplog)
    assert records["C1_TARGET_PREPARE"].status == "PASS"
    assert records["C1_TARGET_PREPARE"].project_root_writable is True
    assert records["C2_TARGET_DIR_CREATE"].status == "FAIL"
    assert records["C2_TARGET_DIR_CREATE"].exception_type == "ProjectTargetNotWritable"
    assert records["C2_TARGET_DIR_CREATE"].reason_code == "PROJECT_TARGET_NOT_WRITABLE"
    assert records["C2_TARGET_DIR_CREATE"].project_root_writable is True
    assert records["C2_TARGET_DIR_CREATE"].write_attempted is True
    assert "private target path" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_create_records_sidecar_failure_without_sensitive_detail(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    monkeypatch.setattr(
        service,
        "_write_sidecar",
        Mock(side_effect=OSError(errno.ENOENT, "private sidecar path")),
    )

    with caplog.at_level(logging.INFO, logger="embedded_copilot.project_catalog.runtime"):
        with pytest.raises(FileNotFoundError):
            service.create(
                preview.preview_id,
                preview.fingerprint,
                confirmed_at=NOW,
                tenant_id="tenant-1",
                requester="engineer-1",
            )

    records = _create_diagnostic_records(caplog)
    assert records["C2_TARGET_DIR_CREATE"].status == "PASS"
    assert records["C3_SIDECAR_WRITE"].status == "FAIL"
    assert records["C3_SIDECAR_WRITE"].exception_type == "FileNotFoundError"
    assert records["C3_SIDECAR_WRITE"].reason_code == "OS_ERROR_ENOENT"
    assert "private sidecar path" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_create_records_project_commit_failure_and_c5_incomplete(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")

    def fail_commit(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOENT, "private database path")

    monkeypatch.setattr(service._repository, "commit_project", fail_commit)

    with caplog.at_level(logging.INFO, logger="embedded_copilot.project_catalog.runtime"):
        with pytest.raises(FileNotFoundError):
            service.create(
                preview.preview_id,
                preview.fingerprint,
                confirmed_at=NOW,
                tenant_id="tenant-1",
                requester="engineer-1",
            )

    records = _create_diagnostic_records(caplog)
    assert records["C3_SIDECAR_WRITE"].status == "PASS"
    assert records["C4_PROJECT_RECORD_WRITE"].status == "FAIL"
    assert records["C4_PROJECT_RECORD_WRITE"].exception_type == "FileNotFoundError"
    assert records["C4_PROJECT_RECORD_WRITE"].reason_code == "OS_ERROR_ENOENT"
    assert records["C5_COMMIT_COMPLETE"].status == "FAIL"
    assert records["C5_COMMIT_COMPLETE"].target_exists is True
    assert "private database path" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_new_contract_accepts_parent_location_alias(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = service.preview(
        ProjectCreationRequest(
            name="循迹智能小车",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            parent_location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint),
        ),
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    assert project.directory_name == "循迹智能小车"
    assert (tmp_path / "projects" / "循迹智能小车").is_dir()


def test_attach_uses_directory_reference_and_revalidates_fingerprint(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    source = tmp_path / "projects" / "existing"
    source.mkdir()
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    directory = service.directories(location.location_id, tenant_id="tenant-1", requester="engineer-1").items[0]
    preview = service.preview(
        ProjectCreationRequest(
            name="Existing Controller",
            project_type=ProjectType.GENERIC_FIRMWARE,
            creation_mode=ProjectCreationMode.ATTACH_EXISTING,
            location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint),
            existing_directory=ProjectDirectoryReference(directory_id=directory.directory_id, fingerprint=directory.fingerprint),
        ),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    source.rename(tmp_path / "projects" / "replaced")
    with pytest.raises(ProjectCatalogRejected):
        service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")


@pytest.mark.parametrize(
    "relative_path",
    ("../outside", "/absolute", "C:/absolute", ".embedded-copilot/private"),
)
def test_preview_rejects_unsafe_project_paths(tmp_path: Path, relative_path: str) -> None:
    service = runtime(tmp_path)
    with pytest.raises(ProjectCatalogRejected):
        service.preview(
            request(relative_path=relative_path),
            tenant_id="tenant-1",
            requester="engineer-1",
        )


def test_existing_firmware_is_attached_without_modifying_source_tree(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    source = tmp_path / "projects" / "existing"
    source.mkdir()
    (source / "main.c").write_text("int main(void) { return 0; }", encoding="utf-8")
    before = {item.name for item in source.iterdir()}

    preview = service.preview(
        request(relative_path="existing", project_type=ProjectType.EXISTING_FIRMWARE),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    assert {item.name for item in source.iterdir()} == before
    assert project.initialization_status is ProjectInitializationStatus.READY
    context = service.context(project.project_id, tenant_id="tenant-1")
    assert [item.display_label for group in context.groups for item in group.references] == ["main.c"]
    references = service.references("tenant-1", project.project_id)
    assert references[0].display_label == "Firmware · main.c"
    assert "int main" not in context.model_dump_json()


def test_preview_is_identity_bound_single_use_and_expires(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")

    with pytest.raises(ProjectCatalogForbidden):
        service.create(
            preview.preview_id,
            preview.fingerprint,
            confirmed_at=NOW,
            tenant_id="tenant-2",
            requester="engineer-1",
        )

    service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    with pytest.raises(ProjectCatalogConflict):
        service.create(
            preview.preview_id,
            preview.fingerprint,
            confirmed_at=NOW,
            tenant_id="tenant-1",
            requester="engineer-1",
        )

    expired = service.preview(
        request(name="Other", relative_path="other"),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    with pytest.raises(ProjectCatalogRejected):
        service.create(
            expired.preview_id,
            expired.fingerprint,
            confirmed_at=NOW + timedelta(minutes=11),
            tenant_id="tenant-1",
            requester="engineer-1",
        )


def test_projects_and_private_conversations_survive_repository_restart(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    conversation = service.create_conversation(
        project.project_id, tenant_id="tenant-1", requester="engineer-1"
    )
    service.begin_turn(
        conversation.session_id,
        request_id="request-1",
        question="为什么 I2C 读取失败？",
        created_at=NOW,
    )
    service.fail_turn(conversation.session_id, "request-1", "MODEL_UNAVAILABLE", NOW)

    reopened = runtime(tmp_path)
    assert [item.project_id for item in reopened.projects("tenant-1")] == [project.project_id]
    detail = reopened.conversation(
        project.project_id,
        conversation.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert detail.title == "为什么 I2C 读取失败？"
    assert detail.turns[0].error_code == "MODEL_UNAVAILABLE"
    with pytest.raises(ProjectCatalogForbidden):
        reopened.conversations(project.project_id, tenant_id="tenant-1", requester="engineer-2")


def test_non_owner_cannot_create_conversation_in_same_tenant_project(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")

    with pytest.raises(ProjectCatalogForbidden):
        service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-2")

    assert service.conversations(project.project_id, tenant_id="tenant-1", requester="engineer-1").items == ()


def test_non_owner_cannot_read_historical_own_conversation(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    owner_conversation = service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    historical = type(owner_conversation.conversation).model_validate({
        **owner_conversation.conversation.model_dump(mode="python", exclude={"fingerprint"}),
        "conversation_id": "conversation-historical-non-owner",
        "requester": "engineer-2",
        "requirement_context": None,
    })
    service._repository.save_conversation(historical)

    reopened = runtime(tmp_path)
    with pytest.raises(ProjectCatalogForbidden):
        reopened.conversations(project.project_id, tenant_id="tenant-1", requester="engineer-2")
    with pytest.raises(ProjectCatalogForbidden):
        reopened.conversation(project.project_id, historical.conversation_id, tenant_id="tenant-1", requester="engineer-2")


def test_delete_conversation_is_persistent_and_does_not_delete_project_files(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    first = service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    second = service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    project_directory = tmp_path / "projects" / project.directory_name
    assert project_directory.is_dir()

    deleted = service.delete_conversation(project.project_id, first.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    assert deleted.conversation_id == first.conversation.conversation_id
    assert [item.conversation_id for item in service.conversations(project.project_id, tenant_id="tenant-1", requester="engineer-1").items] == [second.conversation.conversation_id]

    reopened = runtime(tmp_path)
    assert [item.conversation_id for item in reopened.conversations(project.project_id, tenant_id="tenant-1", requester="engineer-1").items] == [second.conversation.conversation_id]
    assert project_directory.is_dir()


def test_clarification_turn_and_structured_continuation_survive_repository_restart(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    conversation = service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    service.begin_turn(
        conversation.session_id,
        request_id="request-clarification",
        trace_id="trace-clarification",
        question="帮我看看这个",
        created_at=NOW,
    )
    service.fail_turn(
        conversation.session_id,
        "request-clarification",
        None,
        NOW,
        analysis_state="WAITING_FOR_CLARIFICATION",
        analysis_reason_code="CLARIFICATION_REQUIRED",
        route="CLARIFICATION_REQUIRED",
        trace_id="trace-clarification",
        terminal_event="ANSWER_FAILED",
        answer_text="我还不能确定你希望分析哪一部分。",
    )

    reopened = runtime(tmp_path)
    first = reopened.conversation(project.project_id, conversation.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    assert first.turns[-1].status.value == "WAITING_FOR_CLARIFICATION"
    assert first.turns[-1].analysis_state.value == "WAITING_FOR_CLARIFICATION"
    assert first.turns[-1].analysis_reason_code == "CLARIFICATION_REQUIRED"
    assert first.turns[-1].error_code is None
    assert first.turns[-1].terminal_event == "ANSWER_FAILED"

    reopened.begin_turn(
        conversation.session_id,
        request_id="request-continuation",
        trace_id="trace-continuation",
        question="我要从零设计 STM32 启动架构",
        created_at=NOW,
    )
    continued = reopened.conversation(project.project_id, conversation.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    turn = continued.turns[-1]
    assert turn.question == "我要从零设计 STM32 启动架构"
    assert turn.continuation_of_turn_id == continued.turns[-2].turn_id
    assert turn.previous_analysis_state == "WAITING_FOR_CLARIFICATION"
    assert turn.previous_route == "CLARIFICATION_REQUIRED"
    assert turn.previous_intent == "UNKNOWN"


def test_greenfield_requirement_context_and_partial_lifecycle_survive_restart(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = service.preview(
        request(project_type=ProjectType.STM32, creation_mode=ProjectCreationMode.CREATE_NEW, location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint), relative_path=None),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    conversation = service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    initial = service.conversation(
        project.project_id,
        conversation.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert initial.requirement_context is not None
    assert initial.requirement_context.lifecycle.value == "DISCOVERY_STARTED"
    assert any(
        item.category == "PRODUCT_GOAL"
        and item.status.value == "UNKNOWN"
        for item in initial.requirement_context.standard_fields
    )
    routing = service.routing_context(conversation.session_id)
    assert routing is not None
    assert routing.requirement_context is not None
    assert routing.requirement_context.conversation_id == conversation.conversation.conversation_id
    service.begin_turn(conversation.session_id, request_id="request-1", trace_id="trace-1", question="stm32", created_at=NOW)
    context = merge_requirement_context(
        initial.requirement_context,
        project_id=project.project_id,
        conversation_id=conversation.conversation.conversation_id,
        turn_id="request-1",
        utterance="stm32",
        proposals=(
            {"category": "RTOS", "value": "FreeRTOS"},
        ),
        provider_questions=(
            {
                "category": "PRODUCT_GOAL",
                "question": "这个项目要实现什么产品目标？",
            },
        ),
    )
    service.complete_turn(
        conversation.session_id,
        "request-1",
        type("Answer", (), {"model_dump": lambda self, mode="json": {"answer_text": "开始收集需求", "evidence_details": (), "recommendations": ()}})(),
        NOW,
        route="GREENFIELD_DISCOVERY",
        trace_id="trace-1",
        analysis_state="REQUIREMENTS_PARTIAL",
        analysis_reason_code="ANSWER_COMPLETED",
        requirement_context=context,
    )

    reopened = runtime(tmp_path)
    detail = reopened.conversation(project.project_id, conversation.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    assert detail.requirement_context is not None
    assert detail.requirement_context.project_id == project.project_id
    assert detail.requirement_context.conversation_id == conversation.conversation.conversation_id
    assert detail.turns[-1].answer_text == "开始收集需求"
    assert any(
        item.category == "RTOS" and item.status.value == "PROPOSED"
        for item in detail.requirement_context.proposed_items
    )
    assert any(
        item.category == "PRODUCT_GOAL"
        for item in detail.requirement_context.open_questions
    )
    assert any(
        item.category == "TARGET_PLATFORM"
        and item.normalized_value == "STM32"
        for item in detail.requirement_context.requirements
    )
    assert detail.turns[-1].route == "GREENFIELD_DISCOVERY"
    assert detail.turns[-1].analysis_state.value == "REQUIREMENTS_PARTIAL"
    assert detail.turns[-1].terminal_event == "ANSWER_COMPLETED"
    routing = reopened.routing_context(conversation.session_id)
    assert routing is not None
    assert routing.requirement_context is not None
    assert routing.previous_route == "GREENFIELD_DISCOVERY"


def test_brownfield_conversation_does_not_initialize_requirement_context(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    source = tmp_path / "projects" / "existing"
    source.mkdir()
    (source / "main.c").write_text("int main(void) { return 0; }", encoding="utf-8")
    preview = service.preview(
        request(relative_path="existing", project_type=ProjectType.EXISTING_FIRMWARE),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    conversation = service.create_conversation(
        project.project_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    detail = service.conversation(
        project.project_id,
        conversation.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    assert detail.requirement_context is None


def test_delete_project_clears_copilot_data_but_preserves_disk_directory(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    conversation = service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    target = tmp_path / "projects" / "sensor-gateway"
    sidecar = tmp_path / ".embedded-copilot" / "projects" / f"{project.project_id}.json"

    result = service.delete_project(
        project.project_id,
        project_fingerprint=project.fingerprint,
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    assert result.operation == "PROJECT_DELETED"
    assert target.is_dir()
    assert list(target.iterdir()) == []
    assert not sidecar.exists()
    assert service.projects("tenant-1") == ()
    with pytest.raises(ProjectCatalogNotFound):
        service.get_project(project.project_id, tenant_id="tenant-1")
    with pytest.raises(ProjectCatalogNotFound):
        service.conversation(project.project_id, conversation.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")


def test_delete_project_rejects_wrong_identity_or_fingerprint(tmp_path: Path) -> None:
    service = runtime(tmp_path)
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")

    with pytest.raises(ProjectCatalogForbidden):
        service.delete_project(project.project_id, project_fingerprint=project.fingerprint, tenant_id="tenant-2", requester="engineer-2")
    with pytest.raises(ProjectCatalogConflict):
        service.delete_project(project.project_id, project_fingerprint="sha256:" + "0" * 64, tenant_id="tenant-1", requester="engineer-1")
    with pytest.raises(ProjectCatalogForbidden):
        service.delete_project(project.project_id, project_fingerprint=project.fingerprint, tenant_id="tenant-1", requester="engineer-2")


def test_delete_retries_after_catalog_failure_without_repeating_tombstone_promotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    v63 = V63MemoryGovernanceService(SQLiteV63Store(tmp_path / "v63.sqlite3"))
    service = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=Clock(),
        initialize_async=False,
        v63_memory_governance=v63,
    )
    preview = service.preview(request(), tenant_id="tenant-1", requester="engineer-1")
    project = service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    original_delete = service._repository.delete_project
    state = {"failed": False}

    def fail_once(*args, **kwargs):
        if not state["failed"]:
            state["failed"] = True
            raise OSError("catalog transaction unavailable")
        return original_delete(*args, **kwargs)

    monkeypatch.setattr(service._repository, "delete_project", fail_once)
    with pytest.raises(OSError):
        service.delete_project(
            project.project_id,
            project_fingerprint=project.fingerprint,
            tenant_id="tenant-1",
            requester="engineer-1",
        )
    assert v63.is_project_inaccessible("tenant-1", project.project_id)
    assert service.projects("tenant-1")

    service.delete_project(
        project.project_id,
        project_fingerprint=project.fingerprint,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert service.projects("tenant-1") == ()
    assert len(v63.store.list_audit("tenant-1", project.project_id)) == 1
    assert len(v63.store.list_status_history("tenant-1", project.project_id)) == 1


def test_registered_location_can_attach_project_with_location_scoped_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "simulated-home"))
    (tmp_path / "simulated-home").mkdir()
    second_root = tmp_path / "stm32-projects"
    second_root.mkdir()
    source = second_root / "car001"
    source.mkdir()
    (tmp_path / "projects").mkdir()
    service = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "state" / "desktop.sqlite3",
        metadata_directory=tmp_path / "metadata",
        location_allow_list=(second_root,),
        clock=Clock(),
        initialize_async=False,
    )

    registered = service.register_location(
        path=str(second_root),
        display_label="STM32 Projects",
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    directory = service.directories(registered.location_id, tenant_id="tenant-1", requester="engineer-1").items[0]
    preview = service.preview(
        ProjectCreationRequest(
            name="循迹智能小车",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.ATTACH_EXISTING,
            parent_location=ProjectLocationReference(location_id=registered.location_id, fingerprint=registered.fingerprint),
            existing_directory=ProjectDirectoryReference(directory_id=directory.directory_id, fingerprint=directory.fingerprint),
        ),
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    project = service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    assert project.location_id == registered.location_id
    assert project.directory_name == "car001"
    assert service.knowledge_snapshot_root(project.project_id) == source
