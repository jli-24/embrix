from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol

from .facts import authoritative_facts
from .models import (
    DesktopBoundedPage,
    DesktopCapabilityProjection,
    DesktopEvidencePage,
    DesktopEvidenceProjection,
    DesktopProjectionIdentity,
    DesktopProjectProjection,
    DesktopSafeItem,
    DesktopWorkflowSummary,
    DesktopWorkspaceProjection,
    ProjectionUnavailable,
    WorkbenchChange,
    WorkbenchFinding,
    WorkbenchProjection,
    WorkbenchRepairAlternative,
    WorkbenchResourceNode,
)


class ProjectCatalogProjectionPort(Protocol):
    def get_project_for_requester(self, project_id: str, *, tenant_id: str, requester: str) -> Any: ...
    def context(self, project_id: str, *, tenant_id: str) -> Any: ...
    def conversations(self, project_id: str, *, tenant_id: str, requester: str, cursor: str | None = None, limit: int = 20) -> Any: ...
    def conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str) -> Any: ...
    def current_circuit_intents(self, *, tenant_id: str, requester: str, project_id: str) -> Any: ...
    def workbench_resources(self, project_id: str, *, tenant_id: str, limit: int = 500) -> Any: ...


def _safe(value: object, *, field: str, maximum: int = 512) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        return None
    lowered = value.casefold()
    if any(token in lowered for token in ("password", "secret", "credential", "api_key", "token", "provider_payload")):
        return None
    return value


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _value(value: object) -> str:
    return str(getattr(value, "value", value)) if value is not None else ""


def _item(value: object, *, kind: str, reference_id: str | None = None) -> DesktopSafeItem:
    data = value.model_dump(mode="json") if hasattr(value, "model_dump") else (value if isinstance(value, dict) else {})
    identifier = reference_id or data.get("memory_id") or data.get("knowledge_id") or data.get("review_id") or data.get("validation_id") or data.get("finding_id") or data.get("proposal_id") or data.get("changeset_id") or data.get("operation_id") or data.get("event_id")
    if not isinstance(identifier, str) or not identifier:
        identifier = _fingerprint(data)
    summary = data.get("summary") or data.get("statement") or data.get("subject") or data.get("title") or data.get("detail")
    refs = data.get("source_references") or data.get("provenance_references") or data.get("evidence_ids") or ()
    return DesktopSafeItem(
        reference_id=identifier,
        kind=kind,
        status=_safe(getattr(getattr(value, "status", None), "value", data.get("status")), field="status", maximum=96),
        summary=_safe(summary, field="summary"),
        fingerprint=_safe(data.get("fingerprint") or data.get("memory_fingerprint") or data.get("knowledge_fingerprint") or data.get("proposal_fingerprint") or data.get("changeset_fingerprint"), field="fingerprint"),
        source_references=tuple(item for item in refs if isinstance(item, str) and len(item) <= 256)[:20],
        limitations=tuple(item for item in data.get("limitations", ()) if isinstance(item, str) and len(item) <= 256)[:20],
        confidence=data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else None,
    )


def _page(values: tuple[object, ...] | list[object], *, kind: str, limit: int, cursor: str | None = None) -> DesktopBoundedPage:
    bounded = tuple(_item(value, kind=kind) for value in values[:limit])
    return DesktopBoundedPage(items=bounded, next_cursor=cursor, limit=limit, total_available=None)


class DesktopWorkspaceProjectionService:
    """Read-only aggregation of persisted Desktop projections.

    This service intentionally exposes no mutation dependency. Its methods only
    call read ports and never create lifecycle objects or refresh stores.
    """

    __slots__ = ("_audit", "_capabilities", "_catalog", "_execution", "_memory", "_validation", "_workspace")

    def __init__(self, *, catalog: ProjectCatalogProjectionPort, memory: object | None = None, validation: object | None = None, workspace: object | None = None, execution: object | None = None, capabilities: object | None = None, audit: object | None = None) -> None:
        if not callable(getattr(catalog, "get_project_for_requester", None)) or not callable(getattr(catalog, "context", None)):
            raise TypeError("project catalog projection port is invalid")
        self._catalog = catalog
        self._memory = memory
        self._validation = validation
        self._workspace = workspace
        self._execution = execution
        self._capabilities = capabilities
        self._audit = audit

    def project(self, *, tenant_id: str, requester_id: str, project_id: str, limit: int = 20, cursor: str | None = None, conversation_id: str | None = None, task_id: str | None = None) -> DesktopWorkspaceProjection:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("projection limit is invalid")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 256):
            raise ValueError("projection cursor is invalid")
        if conversation_id is not None and (not isinstance(conversation_id, str) or not conversation_id.strip() or len(conversation_id) > 160):
            raise ValueError("selected conversation is invalid")
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 160):
            raise ValueError("selected task is invalid")
        project = self._catalog.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester_id)
        context = self._catalog.context(project_id, tenant_id=tenant_id)
        conversations = self._catalog.conversations(project_id, tenant_id=tenant_id, requester=requester_id, cursor=cursor, limit=limit)
        conversation_next_cursor = getattr(conversations, "next_cursor", None)
        project_data = project.model_dump(mode="json")
        project_fp = str(getattr(project, "fingerprint", project_data.get("fingerprint", "")))
        identity = DesktopProjectionIdentity(tenant_id=tenant_id, requester_id=requester_id, project_id=project_id, project_fingerprint=project_fp)
        context_item = _item(context, kind="ENGINEERING_CONTEXT", reference_id=f"context:{project_id}")

        memories = self._read_memory(tenant_id, project_id, limit)
        knowledge = self._read_knowledge(tenant_id, project_id, limit)
        reviews = self._read_reviews(tenant_id, project_id, limit)
        validations = self._read_validation("list_validations", tenant_id, project_id, limit)
        findings = self._read_validation("list_findings", tenant_id, project_id, limit)
        proposals = self._read_validation("list_proposals", tenant_id, project_id, limit)
        changes = self._read_workspace("get_project_items", tenant_id, project_id, limit)
        operations, operation_values, operations_complete = self._read_execution(tenant_id, project_id, limit)
        audit = self._read_audit(tenant_id, project_id, limit)
        capabilities = self._read_capabilities(project_id)

        conversation_values = tuple(getattr(conversations, "items", conversations if isinstance(conversations, (tuple, list)) else ()))
        conversation_reader = getattr(self._catalog, "conversation", None)
        if conversation_id is not None:
            if not callable(conversation_reader):
                raise ProjectionUnavailable("selected conversation Owner is unavailable")
            details = (conversation_reader(project_id, conversation_id, tenant_id=tenant_id, requester=requester_id),)
        elif conversation_values:
            if not callable(conversation_reader):
                raise ProjectionUnavailable("conversation Owner is unavailable")
            details = tuple(conversation_reader(project_id, value.conversation_id, tenant_id=tenant_id, requester=requester_id) for value in conversation_values)
        else:
            details = ()
        evidence = self._evidence_page(context, details, limit)
        circuit_reader = getattr(self._catalog, "current_circuit_intents", None)
        circuits = circuit_reader(tenant_id=tenant_id, requester=requester_id, project_id=project_id) if callable(circuit_reader) else None
        source_checker = getattr(self._catalog, "circuit_source_current", None)
        source_currentness = (
            {item.object_id: source_checker(
                project_id, item.object_id, item.revision,
                tenant_id=tenant_id, requester=requester_id,
            ) for item in circuits}
            if circuits is not None and callable(source_checker) else None
        )
        facts = authoritative_facts(
            project_id=project_id,
            conversations=details,
            circuits=circuits,
            source_currentness=source_currentness,
            evidence=evidence,
            page_incomplete=conversation_id is None and conversation_next_cursor is not None,
        )
        workflow = self._workflow_summary(
            evidence, memories, reviews, validations, findings, proposals, changes,
            operations, operation_values=operation_values,
            operations_complete=operations_complete, tenant_id=tenant_id,
            project_id=project_id, task_id=task_id,
        )
        generated_at = datetime.now(UTC)
        project_view = DesktopProjectProjection(
            project_id=project_id,
            tenant_id=tenant_id,
            name=str(project_data.get("name", project_data.get("project_name_reference", project_id))),
            relative_path=str(project_data.get("relative_path", project_data.get("directory_name", ""))),
            status=str(project_data.get("status", project_data.get("initialization_status", "UNKNOWN"))),
            fingerprint=project_fp,
            initialization_status=str(project_data.get("initialization_status", "UNKNOWN")),
        )
        payload = {
            "identity": identity.model_dump(mode="json"),
            "project": project_view.model_dump(mode="json"),
            "context": context_item.model_dump(mode="json"),
            "conversations": _page(conversation_values, kind="CONVERSATION", limit=limit, cursor=conversation_next_cursor).model_dump(mode="json"),
            "evidence": evidence.model_dump(mode="json"),
            "memory": memories.model_dump(mode="json"),
            "knowledge": knowledge.model_dump(mode="json"),
            "reviews": reviews.model_dump(mode="json"),
            "validations": validations.model_dump(mode="json"),
            "findings": findings.model_dump(mode="json"),
            "proposals": proposals.model_dump(mode="json"),
            "changes": changes.model_dump(mode="json"),
            "operations": operations.model_dump(mode="json"),
            "audit": audit.model_dump(mode="json"),
            "capabilities": [item.model_dump(mode="json") for item in capabilities],
            "workflow_summary": workflow.model_dump(mode="json"),
            "authoritative_facts": [item.model_dump(mode="json") for item in facts],
        }
        return DesktopWorkspaceProjection(
            identity=identity,
            project=project_view,
            context=context_item,
            conversations=_page(conversation_values, kind="CONVERSATION", limit=limit, cursor=conversation_next_cursor),
            evidence=evidence,
            memory=memories,
            knowledge=knowledge,
            reviews=reviews,
            validations=validations,
            findings=findings,
            proposals=proposals,
            changes=changes,
            operations=operations,
            audit=audit,
            capabilities=capabilities,
            workflow_summary=workflow,
            authoritative_facts=facts,
            generated_at=generated_at,
            projection_fingerprint=_fingerprint({**payload, "generated_at": generated_at.isoformat()}),
        )

    def workbench(self, *, tenant_id: str, requester_id: str, project_id: str, limit: int = 500) -> WorkbenchProjection:
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise ValueError("workbench limit is invalid")
        project = self._catalog.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester_id)
        project_data = project.model_dump(mode="json") if hasattr(project, "model_dump") else {}
        project_fp = str(getattr(project, "fingerprint", project_data.get("fingerprint", "")))
        resource_method = getattr(self._catalog, "workbench_resources", None)
        resources_raw, truncated = (resource_method(project_id, tenant_id=tenant_id, limit=limit) if callable(resource_method) else ((), False))
        resources = tuple(WorkbenchResourceNode.model_validate(item) for item in tuple(resources_raw or ()))
        validation_limit = min(limit, 100)
        findings_raw = self._validation_values("list_findings", tenant_id, project_id, validation_limit)
        proposals_raw = self._validation_values("list_proposals", tenant_id, project_id, validation_limit)
        findings = tuple(self._finding(item) for item in findings_raw)
        proposals = tuple(self._repair(item) for item in proposals_raw)
        generated_at = datetime.now(UTC)
        payload = {
            "project_id": project_id,
            "project_fingerprint": project_fp,
            "resources": [item.model_dump(mode="json") for item in resources],
            "findings": [item.model_dump(mode="json") for item in findings],
            "repair_alternatives": [item.model_dump(mode="json") for item in proposals],
            "truncated": truncated,
        }
        return WorkbenchProjection(
            project_id=project_id,
            project_fingerprint=project_fp,
            resources=resources,
            findings=findings,
            repair_alternatives=proposals,
            truncated=truncated,
            generated_at=generated_at,
            projection_fingerprint=_fingerprint({**payload, "generated_at": generated_at.isoformat()}),
        )

    def _validation_values(self, method_name: str, tenant_id: str, project_id: str, limit: int) -> tuple[object, ...]:
        if self._validation is None:
            return ()
        method = getattr(self._validation, method_name, None)
        if not callable(method):
            return ()
        return tuple(method(tenant_id, project_id, limit=limit))[:limit]

    @staticmethod
    def _finding(value: object) -> WorkbenchFinding:
        data = value.model_dump(mode="python") if hasattr(value, "model_dump") else (value if isinstance(value, dict) else {})
        return WorkbenchFinding(
            finding_id=str(data.get("finding_id", "unknown-finding")),
            finding_type=str(data.get("finding_type", "UNKNOWN")),
            severity=str(getattr(getattr(value, "severity", None), "value", data.get("severity", "UNKNOWN"))),
            subject=_safe(data.get("subject"), field="subject") or "工程发现",
            summary=_safe(data.get("summary"), field="summary") or "暂无摘要",
            evidence_ids=tuple(item for item in data.get("evidence_ids", ()) if isinstance(item, str))[:32],
            validation_id=data.get("validation_id") if isinstance(data.get("validation_id"), str) else None,
            confidence=data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else None,
            limitations=tuple(item for item in data.get("limitations", ()) if isinstance(item, str))[:20],
            target_identity=data.get("target_identity") if isinstance(data.get("target_identity"), str) else None,
            target_fingerprint=data.get("target_fingerprint") if isinstance(data.get("target_fingerprint"), str) else None,
            finding_fingerprint=data.get("finding_fingerprint") if isinstance(data.get("finding_fingerprint"), str) else None,
        )

    @staticmethod
    def _repair(value: object) -> WorkbenchRepairAlternative:
        data = value.model_dump(mode="python") if hasattr(value, "model_dump") else (value if isinstance(value, dict) else {})
        changes: list[WorkbenchChange] = []
        for change in data.get("proposed_changes", ()):
            change_data = change.model_dump(mode="python") if hasattr(change, "model_dump") else (change if isinstance(change, dict) else {})
            changes.append(WorkbenchChange(
                file_reference=_safe(change_data.get("file_reference"), field="file_reference"),
                symbol=_safe(change_data.get("symbol"), field="symbol"),
                logical_change=_safe(change_data.get("logical_change"), field="logical_change") or "变更细节不可用",
            ))
        return WorkbenchRepairAlternative(
            proposal_id=str(data.get("proposal_id", "unknown-proposal")),
            proposal_type=str(data.get("proposal_type", data.get("type", "UNKNOWN"))),
            subject=_safe(data.get("subject"), field="subject") or "修复方案",
            summary=_safe(data.get("summary"), field="summary") or "暂无摘要",
            rationale=_safe(data.get("rationale"), field="rationale"),
            finding_ids=tuple(item for item in data.get("finding_ids", ()) if isinstance(item, str))[:32],
            evidence_ids=tuple(item for item in data.get("evidence_ids", ()) if isinstance(item, str))[:32],
            proposed_changes=tuple(changes[:32]),
            risk=_safe(data.get("risk"), field="risk"),
            confidence=data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else None,
            limitations=tuple(item for item in data.get("limitations", ()) if isinstance(item, str))[:20],
            review_status=str(getattr(getattr(value, "status", None), "value", data.get("status"))) if data.get("status") is not None or getattr(value, "status", None) is not None else None,
        )

    def _read_memory(self, tenant_id: str, project_id: str, limit: int) -> DesktopBoundedPage:
        if self._memory is None:
            return DesktopBoundedPage(items=(), limit=limit)
        method = getattr(self._memory, "project_memories", None)
        if not callable(method):
            return DesktopBoundedPage(items=(), limit=limit)
        return _page(tuple(method(tenant_id, project_id, limit=limit)), kind="MEMORY", limit=limit)

    def _read_knowledge(self, tenant_id: str, project_id: str, limit: int) -> DesktopBoundedPage:
        if self._memory is None:
            return DesktopBoundedPage(items=(), limit=limit)
        method = getattr(self._memory, "project_knowledge", None)
        if not callable(method):
            return DesktopBoundedPage(items=(), limit=limit)
        return _page(tuple(method(tenant_id, project_id, limit=limit)), kind="KNOWLEDGE", limit=limit)

    def _read_reviews(self, tenant_id: str, project_id: str, limit: int) -> DesktopBoundedPage:
        values: list[object] = []
        if self._memory is not None and callable(getattr(self._memory, "list_reviews", None)):
            values.extend(self._memory.list_reviews(tenant_id, project_id, limit=limit))
        if self._validation is not None and callable(getattr(self._validation, "list_reviews", None)):
            values.extend(self._validation.list_reviews(tenant_id, project_id, limit=limit))
        return _page(values, kind="REVIEW", limit=limit)

    def _read_validation(self, method_name: str, tenant_id: str, project_id: str, limit: int) -> DesktopBoundedPage:
        if self._validation is None:
            return DesktopBoundedPage(items=(), limit=limit)
        method = getattr(self._validation, method_name, None)
        if not callable(method):
            return DesktopBoundedPage(items=(), limit=limit)
        return _page(tuple(method(tenant_id, project_id, limit=limit)), kind=method_name.removeprefix("list_").upper(), limit=limit)

    def _read_workspace(self, method_name: str, tenant_id: str, project_id: str, limit: int) -> DesktopBoundedPage:
        if self._workspace is None:
            return DesktopBoundedPage(items=(), limit=limit)
        method = getattr(self._workspace, method_name, None)
        if not callable(method):
            return DesktopBoundedPage(items=(), limit=limit)
        return _page(tuple(method(tenant_id=tenant_id, project_id=project_id, limit=limit)), kind="CHANGESET", limit=limit)

    def _read_execution(self, tenant_id: str, project_id: str, limit: int) -> tuple[DesktopBoundedPage, tuple[object, ...], bool]:
        if self._execution is None:
            return DesktopBoundedPage(items=(), limit=limit), (), False
        method = getattr(self._execution, "list_operations", None)
        if not callable(method):
            return DesktopBoundedPage(items=(), limit=limit), (), False
        values = tuple(method(tenant_id=tenant_id, project_id=project_id, limit=100))
        return _page(values, kind="OPERATION", limit=limit), values, len(values) < 100

    def _read_audit(self, tenant_id: str, project_id: str, limit: int) -> DesktopBoundedPage:
        if self._audit is None:
            return DesktopBoundedPage(items=(), limit=limit)
        method = getattr(self._audit, "list_events", None)
        if not callable(method):
            return DesktopBoundedPage(items=(), limit=limit)
        return _page(tuple(method(tenant_id=tenant_id, project_id=project_id, limit=limit)), kind="AUDIT", limit=limit)

    def _read_capabilities(self, project_id: str) -> tuple[DesktopCapabilityProjection, ...]:
        if self._capabilities is None:
            return ()
        values = self._capabilities(project_id) if callable(self._capabilities) else self._capabilities
        result: list[DesktopCapabilityProjection] = []
        for value in tuple(values):
            data = value.model_dump(mode="python") if hasattr(value, "model_dump") else value
            if not isinstance(data, dict):
                continue
            status = str(getattr(getattr(value, "status", None), "value", data.get("status", "UNAVAILABLE")))
            if status not in {"AVAILABLE", "DEGRADED", "UNAVAILABLE", "UNVERIFIED"}:
                status = "UNAVAILABLE"
            result.append(DesktopCapabilityProjection(capability=str(data.get("capability", "UNKNOWN")), status=status, reason_code=str(data.get("reason_code", "UNAVAILABLE")), detail=str(data.get("detail", "Capability is unavailable."))))
        return tuple(result)

    @staticmethod
    def _evidence_page(context: object, conversations: tuple[object, ...], limit: int) -> DesktopEvidencePage:
        result: list[DesktopEvidenceProjection] = []
        context_data = context.model_dump(mode="python") if hasattr(context, "model_dump") else {}
        for group in context_data.get("groups", ()):
            for reference in group.get("references", ()):
                source_fp = str(reference.get("source_fingerprint", ""))
                if not source_fp:
                    continue
                result.append(DesktopEvidenceProjection(evidence_id=str(reference.get("reference_id", source_fp)), source_type=str(group.get("category", "UNKNOWN")), subject=str(reference.get("display_label", "Engineering evidence")), summary=_safe(reference.get("display_label"), field="summary"), source_reference=None, fingerprint=source_fp, verification_status="OBSERVED", limitations=()))
        for conversation in conversations:
            data = conversation.model_dump(mode="python") if hasattr(conversation, "model_dump") else {}
            for turn in data.get("turns", ())[-limit:]:
                for item in turn.get("evidence", ()):
                    if isinstance(item, dict) and item.get("source_fingerprint"):
                        result.append(DesktopEvidenceProjection(evidence_id=str(item.get("reference_id", item["source_fingerprint"])), source_type=str(item.get("source_type", "UNKNOWN")), subject=str(item.get("label", "Engineering evidence")), summary=_safe(item.get("summary"), field="summary"), source_reference=_safe(item.get("source_uri"), field="source_reference"), fingerprint=str(item["source_fingerprint"]), verification_status=str(item.get("status", "OBSERVED")), confidence=_safe(item.get("confidence"), field="confidence"), limitations=()))
        unique = {item.evidence_id: item for item in result}
        return DesktopEvidencePage(items=tuple(unique.values())[:limit], limit=limit, total_available=None)

    def _workflow_summary(
        self, *pages: object, operation_values: tuple[object, ...],
        operations_complete: bool, tenant_id: str, project_id: str,
        task_id: str | None,
    ) -> DesktopWorkflowSummary:
        evidence, _memories, reviews, validations, findings, proposals, changes, _operations = pages
        def count(value: object) -> int:
            return len(getattr(value, "items", ()))
        stages: list[str] = []
        if count(evidence):
            stages.append("EVIDENCE_COLLECTED")
        if count(validations):
            stages.append("VALIDATION_FAILED" if any(getattr(item, "status", "") == "FAILED" for item in getattr(validations, "items", ())) else "VALIDATION_RECORDED")
        if count(findings):
            stages.append("FINDING_CREATED")
        if count(reviews):
            stages.append("WAIT_REVIEW")
        if count(proposals):
            stages.append("APPROVED" if any(getattr(item, "status", "") == "APPROVED" for item in getattr(proposals, "items", ())) else "WAIT_REVIEW")
        if count(changes):
            stages.append("CHANGE_PREVIEW_READY")
        current_reference = None
        source_references: tuple[str, ...] = ()
        execution_state = "UNAVAILABLE"
        if task_id is not None and operations_complete and self._execution is not None:
            matched = tuple(item for item in operation_values if (
                getattr(item, "tenant_id", None) == tenant_id
                and getattr(item, "project_id", None) == project_id
                and getattr(getattr(item, "correlation", None), "task_id", None) == task_id
                and getattr(getattr(item, "correlation", None), "project_id", None) == project_id
                and getattr(getattr(item, "correlation", None), "operation_id", None) == getattr(item, "operation_id", None)
            ))
            if len(matched) == 1:
                operation = matched[0]
                operation_id = operation.operation_id
                current_reference = operation_id
                status_reader = getattr(self._execution, "get_status", None)
                receipt_reader = getattr(self._execution, "get_receipt", None)
                if callable(status_reader) and callable(receipt_reader):
                    from embedded_copilot.execution_worker.service import (
                        ExecutionRejected,
                        ExecutionUnavailable,
                    )
                    try:
                        status = status_reader(tenant_id=tenant_id, project_id=project_id, operation_id=operation_id)
                        receipt = receipt_reader(tenant_id=tenant_id, project_id=project_id, operation_id=operation_id)
                    except (ExecutionRejected, ExecutionUnavailable):
                        status = receipt = None
                    correlation = getattr(operation, "correlation", None)
                    if (
                        status is not None and receipt is not None
                        and getattr(status, "operation_id", None) == operation_id
                        and getattr(receipt, "operation_id", None) == operation_id
                        and getattr(status, "tenant_id", None) == tenant_id
                        and getattr(receipt, "tenant_id", None) == tenant_id
                        and getattr(status, "project_id", None) == project_id
                        and getattr(receipt, "project_id", None) == project_id
                        and getattr(status, "operation_type", None) == getattr(operation, "operation_type", None)
                        and getattr(receipt, "operation_type", None) == getattr(operation, "operation_type", None)
                        and getattr(status, "correlation", None) == correlation
                        and getattr(receipt, "correlation", None) == correlation
                        and _value(getattr(status, "status", None)) == "SUCCEEDED"
                        and _value(getattr(receipt, "status", None)) == "SUCCEEDED"
                        and getattr(receipt, "tool_execution_status", None) == "SUCCEEDED"
                        and _value(getattr(status, "recovery_state", None)) == "NONE"
                        and not getattr(status, "cancellation_requested", True)
                        and getattr(status, "receipt_fingerprint", None) == getattr(receipt, "fingerprint", None)
                        and getattr(receipt, "fingerprint", None) is not None
                    ):
                        execution_state = "APPLIED"
                        source_references = (operation_id, receipt.fingerprint)
                    else:
                        execution_state = "EXECUTION_UNAVAILABLE"
        stages.append(execution_state)
        if not stages:
            stages.append("UNAVAILABLE")
        status = stages[-1]
        allowed = {"EVIDENCE_COLLECTED", "VALIDATION_FAILED", "VALIDATION_RECORDED", "FINDING_CREATED", "WAIT_REVIEW", "APPROVED", "CHANGE_PREVIEW_READY", "APPLY_APPROVAL_REQUIRED", "APPLIED", "UNAVAILABLE"}
        return DesktopWorkflowSummary(status=status if status in allowed or status == "EXECUTION_UNAVAILABLE" else "UNAVAILABLE", current_reference=current_reference, stages=tuple(stages), source_references=source_references)
