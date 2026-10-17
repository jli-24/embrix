from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from embedded_copilot.desktop_workspace_projection import (
    DesktopWorkspaceProjectionService,
)


class _Catalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def get_project(self, project_id: str, *, tenant_id: str):
        self.calls.append(("project", None))
        return SimpleNamespace(
            fingerprint="sha256:" + "1" * 64,
            model_dump=lambda mode="json": {
                "project_id": project_id,
                "tenant_id": tenant_id,
                "name": "Synthetic A",
                "relative_path": "project-a",
                "status": "READY",
                "initialization_status": "READY",
                "fingerprint": "sha256:" + "1" * 64,
            },
        )

    def get_project_for_requester(self, project_id: str, *, tenant_id: str, requester: str):
        return self.get_project(project_id, tenant_id=tenant_id)

    def context(self, project_id: str, *, tenant_id: str):
        self.calls.append(("context", None))
        return SimpleNamespace(
            model_dump=lambda mode="python": {
                "groups": ({
                    "category": "Firmware",
                    "references": ({
                        "reference_id": "evidence-a",
                        "display_label": "Synthetic firmware evidence",
                        "source_fingerprint": "sha256:" + "2" * 64,
                    },),
                },),
            }
        )

    def conversations(self, project_id: str, *, tenant_id: str, requester: str, cursor=None, limit: int = 20):
        self.calls.append(("conversations", limit))
        return SimpleNamespace(items=())


def test_projection_requires_requester_bound_project_lookup() -> None:
    class TenantOnlyCatalog:
        def get_project(self, project_id: str, *, tenant_id: str):
            return SimpleNamespace()

        def context(self, project_id: str, *, tenant_id: str):
            return SimpleNamespace()

    with pytest.raises(TypeError, match="project catalog projection port is invalid"):
        DesktopWorkspaceProjectionService(catalog=TenantOnlyCatalog())


class _ReadOnlyMemory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def project_memories(self, tenant_id: str, project_id: str, *, limit: int):
        self.calls.append(("memory", limit))
        return ()

    def project_knowledge(self, tenant_id: str, project_id: str, *, limit: int):
        self.calls.append(("knowledge", limit))
        return ()

    def list_reviews(self, tenant_id: str, project_id: str, *, limit: int):
        self.calls.append(("reviews", limit))
        return ()


class _ReadOnlyValidation:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def list_validations(self, tenant_id: str, project_id: str, *, limit: int):
        self.calls.append(("validations", limit))
        return ()

    def list_findings(self, tenant_id: str, project_id: str, *, limit: int):
        self.calls.append(("findings", limit))
        return ()

    def list_proposals(self, tenant_id: str, project_id: str, *, limit: int):
        self.calls.append(("proposals", limit))
        return ()

    def list_reviews(self, tenant_id: str, project_id: str, *, limit: int):
        self.calls.append(("proposal_reviews", limit))
        return ()


def test_projection_is_read_only_and_all_history_reads_are_bounded() -> None:
    catalog = _Catalog()
    memory = _ReadOnlyMemory()
    validation = _ReadOnlyValidation()
    service = DesktopWorkspaceProjectionService(
        catalog=catalog,
        memory=memory,
        validation=validation,
        workspace=None,
        execution=None,
        capabilities=(),
    )

    result = service.project(
        tenant_id="tenant-a",
        requester_id="requester-a",
        project_id="project-a",
        limit=7,
    )

    assert result.identity.project_id == "project-a"
    assert result.evidence.items[0].evidence_id == "evidence-a"
    assert result.workflow_summary.status == "UNAVAILABLE"
    assert "EVIDENCE_COLLECTED" in result.workflow_summary.stages
    assert ("conversations", 7) in catalog.calls
    assert memory.calls == [("memory", 7), ("knowledge", 7), ("reviews", 7)]
    assert sorted(validation.calls) == sorted([("validations", 7), ("findings", 7), ("proposals", 7), ("proposal_reviews", 7)])
    assert result.model_dump_json().find("raw") == -1


def test_queued_operation_does_not_claim_applied_workflow() -> None:
    class QueuedExecution:
        def list_operations(self, *, tenant_id: str, project_id: str, limit: int):
            return (SimpleNamespace(
                model_dump=lambda mode="json": {
                    "operation_id": "operation-queued", "project_id": project_id,
                    "tenant_id": tenant_id,
                },
            ),)

    result = DesktopWorkspaceProjectionService(
        catalog=_Catalog(), execution=QueuedExecution(),
    ).project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-a")
    assert result.operations.items[0].reference_id == "operation-queued"
    assert result.workflow_summary.status != "APPLIED"


@pytest.mark.parametrize("status", ["CREATED", "QUEUED", "RUNNING", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"])
def test_selected_task_without_successful_canonical_receipt_is_unavailable(status: str) -> None:
    correlation = SimpleNamespace(task_id="task-current", project_id="project-a", operation_id="operation-current")
    operation = SimpleNamespace(
        operation_id="operation-current", tenant_id="tenant-a", project_id="project-a",
        operation_type="BUILD", correlation=correlation,
        model_dump=lambda mode="json": {"operation_id": "operation-current"},
    )

    class Execution:
        def list_operations(self, *, tenant_id, project_id, limit):
            return (operation,)

        def get_status(self, *, tenant_id, project_id, operation_id):
            return SimpleNamespace(
                operation_id=operation_id, tenant_id=tenant_id, project_id=project_id,
                operation_type="BUILD", status=status, recovery_state="REQUIRED" if status == "RECOVERY_REQUIRED" else "NONE",
                cancellation_requested=False, receipt_fingerprint=None, correlation=correlation,
            )

        def get_receipt(self, *, tenant_id, project_id, operation_id):
            return None

    result = DesktopWorkspaceProjectionService(catalog=_Catalog(), execution=Execution()).project(
        tenant_id="tenant-a", requester_id="requester-a", project_id="project-a", task_id="task-current",
    )
    assert result.workflow_summary.status != "APPLIED"


def test_selected_task_requires_unique_complete_matching_success_evidence() -> None:
    def operation(task_id: str, operation_id: str):
        correlation = SimpleNamespace(task_id=task_id, project_id="project-a", operation_id=operation_id)
        return SimpleNamespace(
            operation_id=operation_id, tenant_id="tenant-a", project_id="project-a",
            operation_type="BUILD", correlation=correlation,
            updated_at=datetime(2026, 9, 29, tzinfo=UTC),
            model_dump=lambda mode="json": {"operation_id": operation_id},
        )

    class Execution:
        def __init__(self):
            self.operations = [operation("task-other", "operation-other"), operation("task-current", "operation-current")]
            self.receipt_fingerprint = "sha256:" + "a" * 64

        def list_operations(self, *, tenant_id, project_id, limit):
            return tuple(self.operations[:limit])

        def get_status(self, *, tenant_id, project_id, operation_id):
            selected = next(item for item in self.operations if item.operation_id == operation_id)
            return SimpleNamespace(
                operation_id=operation_id, tenant_id=tenant_id, project_id=project_id,
                operation_type="BUILD", status="SUCCEEDED", recovery_state="NONE",
                cancellation_requested=False, receipt_fingerprint=self.receipt_fingerprint,
                correlation=selected.correlation,
            )

        def get_receipt(self, *, tenant_id, project_id, operation_id):
            selected = next(item for item in self.operations if item.operation_id == operation_id)
            return SimpleNamespace(
                operation_id=operation_id, tenant_id=tenant_id, project_id=project_id,
                operation_type="BUILD", status="SUCCEEDED", tool_execution_status="SUCCEEDED",
                fingerprint="sha256:" + "a" * 64, correlation=selected.correlation,
            )

    execution = Execution()
    service = DesktopWorkspaceProjectionService(catalog=_Catalog(), execution=execution)
    current = service.project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-a", task_id="task-current")
    assert current.workflow_summary.status == "APPLIED"
    assert current.workflow_summary.current_reference == "operation-current"
    unselected = service.project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-a")
    assert unselected.workflow_summary.status == "UNAVAILABLE"
    execution.receipt_fingerprint = "sha256:" + "b" * 64
    assert service.project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-a", task_id="task-current").workflow_summary.status != "APPLIED"
    execution.receipt_fingerprint = "sha256:" + "a" * 64
    original_status = execution.get_status
    execution.get_status = lambda **kwargs: SimpleNamespace(**{
        **vars(original_status(**kwargs)),
        "status": "QUEUED" if kwargs["operation_id"] == "operation-current" else "SUCCEEDED",
    })
    assert service.project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-a", task_id="task-current").workflow_summary.status != "APPLIED"
    execution.get_status = original_status
    execution.operations.append(operation("task-current", "operation-duplicate"))
    assert service.project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-a", task_id="task-current").workflow_summary.status == "UNAVAILABLE"
    execution.operations = [operation("task-other", f"operation-{index}") for index in range(99)] + [operation("task-current", "operation-current")]
    assert service.project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-a", task_id="task-current").workflow_summary.status == "UNAVAILABLE"


def test_capability_projection_uses_current_project_for_each_read() -> None:
    projects: list[str] = []

    def capabilities(project_id: str):
        projects.append(project_id)
        return ({"capability": "CHAT_MODEL", "status": "AVAILABLE" if project_id == "project-a" else "UNAVAILABLE", "reason_code": "GOVERNED_SELECTION_READY", "detail": "Read-only policy projection."},)

    service = DesktopWorkspaceProjectionService(catalog=_Catalog(), capabilities=capabilities)
    first = service.project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-a")
    second = service.project(tenant_id="tenant-a", requester_id="requester-a", project_id="project-b")
    assert projects == ["project-a", "project-b"]
    assert first.capabilities[0].status == "AVAILABLE"
    assert second.capabilities[0].status == "UNAVAILABLE"
