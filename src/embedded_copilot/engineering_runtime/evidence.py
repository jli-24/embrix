"""Canonical receipt-to-evidence qualification at the Runtime boundary."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import field_validator

if TYPE_CHECKING:
    from embedded_copilot.execution_worker.contracts import OperationReceipt

from .common import (
    RuntimeContract,
    normalize_text,
    validate_fingerprint,
    validate_identifier,
    validate_revision,
    validate_utc_datetime,
)


class EvidenceReferenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    FAKE_UNVERIFIED = "FAKE_UNVERIFIED"


class EvidenceReceiptReference(RuntimeContract):
    """Narrow identity/summary reference to an existing Evidence/Receipt record."""

    reference_id: str
    receipt_id: str
    tenant_id: str
    project_id: str
    source: str
    tool: str
    scope: str
    result_summary: str
    occurred_at: datetime
    source_fingerprint: str
    verification_status: EvidenceReferenceStatus = EvidenceReferenceStatus.UNVERIFIED
    receipt_fingerprint: str | None = None
    artifact_ref: str | None = None
    artifact_fingerprint: str | None = None
    schematic_revision: str | None = None
    tool_version: str | None = None
    execution_result: str | None = None

    @field_validator("reference_id", "receipt_id", "tenant_id", "project_id")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        if info.field_name in {"reference_id", "receipt_id"}:
            from embedded_copilot.execution_worker.contracts import (
                identifier as worker_identifier,
            )

            return worker_identifier(value, field="evidence_reference")
        return validate_identifier(value, field="evidence_reference")

    _texts = field_validator("source", "tool", "scope", "result_summary")(
        lambda value: normalize_text(value, field="evidence_reference_text")
    )
    _occurred_at = field_validator("occurred_at")(validate_utc_datetime)
    _source_fingerprint = field_validator("source_fingerprint")(validate_fingerprint)
    _optional_fingerprint = field_validator(
        "receipt_fingerprint", "artifact_fingerprint"
    )(
        lambda value, info: (
            None
            if value is None
            else validate_fingerprint(value, field=info.field_name)
        )
    )
    _optional_revision = field_validator("schematic_revision")(
        lambda value: None if value is None else validate_revision(value)
    )
    _optional_text = field_validator(
        "artifact_ref", "tool_version", "execution_result"
    )(
        lambda value, info: (
            None
            if value is None
            else normalize_text(value, field=info.field_name)
        )
    )


@runtime_checkable
class EvidenceResolutionRequest(Protocol):
    tenant_id: str
    project_id: str


class EvidenceReceiptResolutionError(ValueError):
    """Canonical Worker receipt could not qualify the supplied reference."""


class CanonicalOperationReceiptEvidenceResolver:
    """Resolve evidence only from a canonical KiCad ERC Worker receipt."""

    __slots__ = ("_source",)

    def __init__(self, source: object) -> None:
        if not callable(getattr(source, "get_receipt", None)) or not callable(
            getattr(source, "get_operation", None)
        ):
            raise TypeError("execution receipt source is invalid")
        self._source = source

    def _receipt(
        self, *, tenant_id: str, project_id: str, operation_id: str
    ) -> OperationReceipt | None:
        from embedded_copilot.execution_worker.contracts import OperationReceipt

        value = self._source.get_receipt(
            tenant_id=tenant_id, project_id=project_id, operation_id=operation_id
        )
        return value if isinstance(value, OperationReceipt) else None

    def _operation(
        self, *, tenant_id: str, project_id: str, operation_id: str
    ) -> object | None:
        return self._source.get_operation(
            tenant_id=tenant_id, project_id=project_id, operation_id=operation_id
        )

    @staticmethod
    def _reject(message: str) -> EvidenceReceiptResolutionError:
        return EvidenceReceiptResolutionError(message)

    def resolve(
        self,
        request: EvidenceResolutionRequest,
        reference: EvidenceReceiptReference,
    ) -> EvidenceReceiptReference:
        from embedded_copilot.execution_worker.contracts import (
            OperationStatus,
            OperationType,
            SchematicErcOperation,
        )

        if reference.verification_status is EvidenceReferenceStatus.VERIFIED:
            raise self._reject("caller cannot self-report VERIFIED evidence")
        if reference.project_id != request.project_id or reference.tenant_id != request.tenant_id:
            raise self._reject("evidence reference identity does not match request")
        if reference.receipt_fingerprint is None:
            raise self._reject("receipt fingerprint is required")
        if reference.artifact_ref is None or reference.artifact_fingerprint is None:
            raise self._reject("schematic artifact identity is required")
        if reference.schematic_revision is None or reference.tool_version is None:
            raise self._reject("schematic revision and tool version are required")
        if reference.execution_result is None:
            raise self._reject("execution result is required")
        if reference.scope != "SCHEMATIC_ERC":
            raise self._reject("evidence scope is not a schematic ERC scope")

        receipt = self._receipt(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            operation_id=reference.receipt_id,
        )
        if receipt is None:
            raise self._reject("canonical operation receipt is unavailable")
        if (
            receipt.operation_id != reference.receipt_id
            or receipt.tenant_id != request.tenant_id
            or receipt.project_id != request.project_id
            or receipt.fingerprint != reference.receipt_fingerprint
            or receipt.operation_type is not OperationType.KICAD_SCHEMATIC_ERC
            or receipt.status is not OperationStatus.SUCCEEDED
            or receipt.tool_execution_status != "SUCCEEDED"
            or reference.reference_id not in receipt.evidence_references
        ):
            raise self._reject("canonical receipt identity or execution binding mismatch")

        operation = self._operation(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            operation_id=receipt.operation_id,
        )
        if not isinstance(operation, SchematicErcOperation):
            raise self._reject("canonical ERC operation is unavailable")
        result = receipt.schematic_erc_result
        if result is None or receipt.schematic_erc_result_fingerprint != result.fingerprint:
            raise self._reject("canonical ERC result is unavailable or tampered")
        if (
            result.operation_id != operation.operation_id
            or result.schematic_artifact_ref != operation.target_relative_path
            or result.schematic_artifact_ref != reference.artifact_ref
            or result.schematic_content_fingerprint != reference.artifact_fingerprint
            or result.schematic_content_fingerprint != reference.source_fingerprint
            or result.receipt_ref != operation.source_operation_receipt_fingerprint
            or result.kicad_version != reference.tool_version
            or result.status.value != reference.execution_result
            or result.status.value not in {"ERC_PASS", "ERC_FAIL", "ERC_WARNINGS"}
            or operation.schematic_revision != reference.schematic_revision
        ):
            raise self._reject("canonical ERC result binding mismatch")
        if not result.kicad_version or result.kicad_version in {"UNKNOWN", "UNAVAILABLE"}:
            raise self._reject("KiCad tool version is unavailable")

        source_operation = self._operation(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            operation_id=operation.source_operation_id,
        )
        source_receipt = self._receipt(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            operation_id=operation.source_operation_id,
        )
        if (
            source_operation is None
            or source_receipt is None
            or getattr(source_operation, "operation_type", None) is not OperationType.KICAD_SCHEMATIC_WRITE
            or getattr(source_operation, "operation_id", None) != operation.source_operation_id
            or getattr(source_operation, "tenant_id", None) != request.tenant_id
            or getattr(source_operation, "project_id", None) != request.project_id
            or getattr(source_operation, "target_relative_path", None) != operation.target_relative_path
            or getattr(source_operation, "content_fingerprint", None) != operation.schematic_artifact_fingerprint
            or source_receipt.operation_type is not OperationType.KICAD_SCHEMATIC_WRITE
            or source_receipt.operation_id != operation.source_operation_id
            or source_receipt.tenant_id != request.tenant_id
            or source_receipt.project_id != request.project_id
            or source_receipt.fingerprint != operation.source_operation_receipt_fingerprint
            or source_receipt.status is not OperationStatus.SUCCEEDED
            or source_receipt.tool_execution_status != "SUCCEEDED"
            or not any(
                item.relative_path == operation.target_relative_path
                and item.content_fingerprint == operation.schematic_artifact_fingerprint
                for item in source_receipt.workspace_mutations
            )
        ):
            raise self._reject("source schematic receipt is stale or mismatched")

        return reference.model_copy(
            update={
                "source": "KICAD_EXECUTION_WORKER",
                "tool": result.command_identity,
                "result_summary": receipt.safe_diagnostics_summary,
                "occurred_at": receipt.created_at,
                "source_fingerprint": result.schematic_content_fingerprint,
                "verification_status": EvidenceReferenceStatus.VERIFIED,
            }
        )
