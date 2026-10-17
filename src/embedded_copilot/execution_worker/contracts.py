from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.engineering_workspace_apply import ApplyApprovalGrant, ChangeSet, WorkspaceSnapshot, ChangeType
from embedded_copilot.engineering_copilot import SchematicErcResult
from embedded_copilot.engineering_copilot.schematic_evidence import LibraryTerminalBindingV2
from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration, FirmwareBuildRequest, FirmwareBuildResult, FirmwareSourceArtifact
from embedded_copilot.pcb_production import PCBArtifactRecord, PCBDrcResult

_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}$")
_AUTO = "sha256:" + "0" * 64


class ExecutionContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


def identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    value = unicodedata.normalize("NFC", value).strip()
    if _ID.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def safe_text(value: object, *, field: str, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field} is invalid")
    return value


def utc_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{field} must be timezone-aware") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class FingerprintedContract(ExecutionContract):
    fingerprint: str = _AUTO

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected and self.fingerprint not in self._legacy_fingerprint_candidates():
            raise ValueError("fingerprint mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        return ()


def _legacy_without_fields(value: FingerprintedContract, *fields: str) -> tuple[str, ...]:
    payload = value.model_dump(mode="json", exclude={"fingerprint", *fields})
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return ("sha256:" + hashlib.sha256(encoded).hexdigest(),)


class OperationType(StrEnum):
    BUILD = "BUILD"
    TEST = "TEST"
    SIMULATION = "SIMULATION"
    FLASH = "FLASH"
    UART_OBSERVATION = "UART_OBSERVATION"
    HIL = "HIL"
    FIRMWARE_SOURCE_WRITE = "FIRMWARE_SOURCE_WRITE"
    KICAD_SCHEMATIC_WRITE = "KICAD_SCHEMATIC_WRITE"
    KICAD_SCHEMATIC_PARSE = "KICAD_SCHEMATIC_PARSE"
    KICAD_SCHEMATIC_READ_BACK = "KICAD_SCHEMATIC_READ_BACK"
    KICAD_SCHEMATIC_ERC = "KICAD_SCHEMATIC_ERC"
    KICAD_PCB_WRITE = "KICAD_PCB_WRITE"
    KICAD_NATIVE_FOOTPRINT_MOVE = "KICAD_NATIVE_FOOTPRINT_MOVE"
    KICAD_PCB_DRC = "KICAD_PCB_DRC"
    KICAD_PCB_DRC_BASELINE = "KICAD_PCB_DRC_BASELINE"
    KICAD_GERBER_EXPORT = "KICAD_GERBER_EXPORT"
    KICAD_DRILL_EXPORT = "KICAD_DRILL_EXPORT"
    KICAD_POSITION_EXPORT = "KICAD_POSITION_EXPORT"
    MANUFACTURING_PACKAGE_BUILD = "MANUFACTURING_PACKAGE_BUILD"
    HARDWARE_BRINGUP = "HARDWARE_BRINGUP"


class ApprovalPurpose(StrEnum):
    WORKSPACE_APPLY = "WORKSPACE_APPLY"
    PROPOSAL = "PROPOSAL"
    BUILD = "BUILD"
    TEST = "TEST"
    SIMULATION = "SIMULATION"
    FLASH = "FLASH"
    UART_OBSERVATION = "UART_OBSERVATION"
    HIL = "HIL"
    MANUFACTURING = "MANUFACTURING"
    HARDWARE_BRINGUP = "HARDWARE_BRINGUP"


_APPROVAL_PURPOSE_BY_OPERATION = {
    OperationType.BUILD: ApprovalPurpose.BUILD,
    OperationType.TEST: ApprovalPurpose.TEST,
    OperationType.SIMULATION: ApprovalPurpose.SIMULATION,
    OperationType.FLASH: ApprovalPurpose.FLASH,
    OperationType.UART_OBSERVATION: ApprovalPurpose.UART_OBSERVATION,
    OperationType.HIL: ApprovalPurpose.HIL,
    OperationType.FIRMWARE_SOURCE_WRITE: ApprovalPurpose.WORKSPACE_APPLY,
    OperationType.KICAD_SCHEMATIC_WRITE: ApprovalPurpose.WORKSPACE_APPLY,
    OperationType.KICAD_SCHEMATIC_ERC: ApprovalPurpose.PROPOSAL,
    OperationType.KICAD_SCHEMATIC_PARSE: ApprovalPurpose.PROPOSAL,
    OperationType.KICAD_SCHEMATIC_READ_BACK: ApprovalPurpose.PROPOSAL,
    OperationType.KICAD_PCB_WRITE: ApprovalPurpose.WORKSPACE_APPLY,
    OperationType.KICAD_NATIVE_FOOTPRINT_MOVE: ApprovalPurpose.WORKSPACE_APPLY,
    OperationType.KICAD_PCB_DRC: ApprovalPurpose.PROPOSAL,
    OperationType.KICAD_PCB_DRC_BASELINE: ApprovalPurpose.PROPOSAL,
    OperationType.KICAD_GERBER_EXPORT: ApprovalPurpose.MANUFACTURING,
    OperationType.KICAD_DRILL_EXPORT: ApprovalPurpose.MANUFACTURING,
    OperationType.KICAD_POSITION_EXPORT: ApprovalPurpose.MANUFACTURING,
    OperationType.MANUFACTURING_PACKAGE_BUILD: ApprovalPurpose.MANUFACTURING,
    OperationType.HARDWARE_BRINGUP: ApprovalPurpose.HARDWARE_BRINGUP,
}


def approval_purpose_for_operation(operation_type: OperationType) -> ApprovalPurpose:
    try:
        return _APPROVAL_PURPOSE_BY_OPERATION[operation_type]
    except KeyError:
        raise ValueError("operation approval purpose is unavailable") from None


class ApprovalStatus(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class OperationStatus(StrEnum):
    CREATED = "CREATED"
    WAIT_APPROVAL = "WAIT_APPROVAL"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class RecoveryState(StrEnum):
    NONE = "NONE"
    INTERRUPTED = "INTERRUPTED"
    TARGET_STATE_UNKNOWN = "TARGET_STATE_UNKNOWN"
    OPERATOR_DECISION_REQUIRED = "OPERATOR_DECISION_REQUIRED"
    RECOVERED = "RECOVERED"


class OperationEventType(StrEnum):
    OPERATION_CREATED = "OPERATION_CREATED"
    APPROVAL_ISSUED = "APPROVAL_ISSUED"
    OPERATION_QUEUED = "OPERATION_QUEUED"
    OPERATION_CLAIMED = "OPERATION_CLAIMED"
    OPERATION_STARTED = "OPERATION_STARTED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLATION_PENDING = "CANCELLATION_PENDING"
    OPERATION_SUCCEEDED = "OPERATION_SUCCEEDED"
    OPERATION_FAILED = "OPERATION_FAILED"
    OPERATION_CANCELLED = "OPERATION_CANCELLED"
    OPERATION_TIMED_OUT = "OPERATION_TIMED_OUT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECOVERY_RESOLVED = "RECOVERY_RESOLVED"


class TargetBindingStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    CANDIDATE = "CANDIDATE"
    BOUND = "BOUND"
    REVOKED = "REVOKED"


class TargetDiscoveryCandidate(FingerprintedContract):
    candidate_id: str
    tenant_id: str
    project_id: str
    target_kind: str
    model: str
    observed_identity_hash: str
    discovered_at: datetime

    _ids = field_validator("candidate_id", "tenant_id", "project_id", "target_kind", "model", "observed_identity_hash", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _time = field_validator("discovered_at", mode="before")(lambda value: utc_datetime(value, field="discovered_at"))


class ExecutionTarget(FingerprintedContract):
    target_id: str
    tenant_id: str
    project_id: str
    target_kind: str
    board_family: str
    mcu_family: str
    stable_identity_hash: str
    candidate_id: str
    binding_status: TargetBindingStatus
    bound_by: str
    bound_at: datetime

    _ids = field_validator("target_id", "tenant_id", "project_id", "target_kind", "board_family", "mcu_family", "stable_identity_hash", "candidate_id", "bound_by", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _time = field_validator("bound_at", mode="before")(lambda value: utc_datetime(value, field="bound_at"))

    @model_validator(mode="after")
    def _must_be_bound(self):
        if self.binding_status is not TargetBindingStatus.BOUND:
            raise ValueError("operation target must be explicitly bound")
        return self


class ToolchainIdentity(FingerprintedContract):
    toolchain_id: str
    tool_version: str
    tool_family: str
    supported_operation: OperationType
    supported_target: str

    _ids = field_validator("toolchain_id", "tool_version", "tool_family", "supported_target", mode="before")(lambda value, info: identifier(value, field=info.field_name))


class ArtifactIdentity(FingerprintedContract):
    artifact_id: str
    tenant_id: str
    project_id: str
    target_id: str
    workspace_fingerprint: str
    build_operation_id: str
    toolchain_id: str
    toolchain_version: str
    build_configuration: str
    artifact_type: Literal[
        "ELF", "BIN", "HEX", "MAP", "LOG", "GERBER", "DRILL", "CPL",
        "BOM", "MANUFACTURING_NOTES", "MANIFEST", "MANUFACTURING_PACKAGE", "NETLIST",
    ]
    artifact_sha256: str
    size_bytes: int = Field(ge=0, le=512 * 1024 * 1024)
    created_at: datetime

    _ids = field_validator("artifact_id", "tenant_id", "project_id", "target_id", "build_operation_id", "toolchain_id", "toolchain_version", "build_configuration", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("workspace_fingerprint", "artifact_sha256", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))
    _time = field_validator("created_at", mode="before")(lambda value: utc_datetime(value, field="created_at"))

class IndependentLibraryMapObservationV2(FingerprintedContract):
    schema_version: Literal[2]
    source_evidence_id: str
    source_evidence_fingerprint: str
    symbol_sha256: str
    footprint_sha256: str
    observed_symbol_pins: tuple[str, ...]
    observed_physical_pad_ids: tuple[str, ...]
    observed_net_bindings: tuple[LibraryTerminalBindingV2, ...]
    artifact: ArtifactIdentity

    _source_id = field_validator("source_evidence_id", mode="before")(
        lambda value: identifier(value, field="source_evidence_id"))
    _fps = field_validator("source_evidence_fingerprint", "symbol_sha256", "footprint_sha256", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name))

    @field_validator("observed_symbol_pins", "observed_physical_pad_ids", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 128:
            raise ValueError("observed identities must be bounded and nonempty")
        refs = tuple(identifier(item, field=info.field_name) for item in value)
        if len(set(refs)) != len(refs):
            raise ValueError("observed identity repeats")
        return refs

    @field_validator("observed_net_bindings", mode="before")
    @classmethod
    def _bindings(cls, value: object) -> tuple[LibraryTerminalBindingV2, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 128:
            raise ValueError("observed bindings must be bounded and nonempty")
        return tuple(value)

    @model_validator(mode="after")
    def _coverage(self):
        manufacturers = tuple(item.manufacturer_terminal for item in self.observed_net_bindings)
        logical = tuple(item.logical_symbol_pin for item in self.observed_net_bindings)
        pads = tuple(pad for item in self.observed_net_bindings for pad in item.physical_pad_ids)
        if (len(set(manufacturers)) != len(manufacturers) or len(set(logical)) != len(logical)
            or len(set(pads)) != len(pads) or self.artifact.artifact_type != "MANIFEST"):
            raise ValueError("library observation coverage or artifact kind invalid")
        return self


def parse_library_observation_payload(content: bytes, artifact: ArtifactIdentity) -> IndependentLibraryMapObservationV2:
    """Reconstruct a typed observation from actual payload and registered identity.

    Membership in the canonical operation/receipt remains the consuming Owner's
    responsibility. Identity/fingerprint never appear in these raw bytes.
    """
    artifact = ArtifactIdentity.model_validate(artifact.model_dump(mode="python"))
    if (type(content) is not bytes or not content or len(content) > 128 * 1024
        or artifact.size_bytes != len(content)
        or artifact.artifact_sha256 != "sha256:" + hashlib.sha256(content).hexdigest()):
        raise ValueError("library observation content identity invalid")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("library observation JSON key repeats")
            value[key] = item
        return value
    try:
        body = json.loads(content, object_pairs_hook=pairs)
    except (UnicodeError, ValueError):
        raise ValueError("library observation JSON invalid") from None
    expected = set(IndependentLibraryMapObservationV2.model_fields) - {"artifact", "fingerprint"}
    if type(body) is not dict or set(body) != expected:
        raise ValueError("library observation raw fields invalid")
    return IndependentLibraryMapObservationV2.model_validate({**body, "artifact": artifact})


class BuildProvenanceType(StrEnum):
    EXISTING_SNAPSHOT = "EXISTING_SNAPSHOT"
    APPLIED_CHANGE = "APPLIED_CHANGE"


class ExecutionProposal(FingerprintedContract):
    proposal_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    approved_workspace_operation_id: str | None = None
    approved_workspace_receipt_fingerprint: str | None = None
    operation_type: OperationType
    toolchain_id: str
    toolchain_version: str
    build_configuration: str
    created_at: datetime
    source_revision: str | None = None
    firmware_build_request_fingerprint: str | None = None
    provenance: BuildProvenanceType = BuildProvenanceType.APPLIED_CHANGE
    plan_id: str | None = None
    plan_fingerprint: str | None = None
    build_configuration_fingerprint: str | None = None
    catalog_context_fingerprint: str | None = None

    _ids = field_validator("proposal_id", "tenant_id", "project_id", "requester_id", "target_id", "workspace_snapshot_id", "toolchain_id", "toolchain_version", "build_configuration", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _receipt_id = field_validator("approved_workspace_operation_id", mode="before")(
        lambda value: None if value is None else identifier(value, field="approved_workspace_operation_id")
    )
    _fps = field_validator("target_fingerprint", "workspace_fingerprint", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))
    _receipt_fp = field_validator("approved_workspace_receipt_fingerprint", mode="before")(
        lambda value: None if value is None else fingerprint(value, field="approved_workspace_receipt_fingerprint")
    )
    _plan_id = field_validator("plan_id", mode="before")(
        lambda value: None if value is None else identifier(value, field="plan_id")
    )
    _context_fps = field_validator("plan_fingerprint", "build_configuration_fingerprint", "catalog_context_fingerprint", mode="before")(
        lambda value, info: None if value is None else fingerprint(value, field=info.field_name)
    )
    _build_fps = field_validator("source_revision", "firmware_build_request_fingerprint", mode="before")(
        lambda value, info: None if value is None else fingerprint(value, field=info.field_name)
    )
    _time = field_validator("created_at", mode="before")(lambda value: utc_datetime(value, field="created_at"))

    @model_validator(mode="after")
    def _provenance_binding(self):
        has_operation = self.approved_workspace_operation_id is not None
        has_receipt = self.approved_workspace_receipt_fingerprint is not None
        if self.provenance is BuildProvenanceType.APPLIED_CHANGE and not (has_operation and has_receipt):
            raise ValueError("applied build provenance requires workspace receipt")
        if self.provenance is BuildProvenanceType.EXISTING_SNAPSHOT and (has_operation or has_receipt):
            raise ValueError("snapshot build provenance cannot claim workspace apply")
        if self.provenance is BuildProvenanceType.EXISTING_SNAPSHOT and (
            self.plan_id is None
            or self.plan_fingerprint is None
            or self.build_configuration_fingerprint is None
            or self.catalog_context_fingerprint is None
        ):
            raise ValueError("snapshot build provenance requires confirmed catalog context")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if self.provenance is not BuildProvenanceType.APPLIED_CHANGE:
            return ()
        legacy_fields = (
            "provenance", "plan_id", "plan_fingerprint",
            "build_configuration_fingerprint", "catalog_context_fingerprint",
        )
        candidates = _legacy_without_fields(self, *legacy_fields)
        if self.source_revision is None and self.firmware_build_request_fingerprint is None:
            candidates += _legacy_without_fields(
                self, *legacy_fields, "source_revision", "firmware_build_request_fingerprint"
            )
        return candidates


class OperationBase(FingerprintedContract):
    operation_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    operation_type: OperationType
    created_at: datetime
    expires_at: datetime
    nonce: str
    correlation: CorrelationContext | None = None

    _ids = field_validator("operation_id", "tenant_id", "project_id", "requester_id", "target_id", "workspace_snapshot_id", "proposal_id", "nonce", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("target_fingerprint", "workspace_fingerprint", "proposal_fingerprint", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))
    _times = field_validator("created_at", "expires_at", mode="before")(lambda value, info: utc_datetime(value, field=info.field_name))

    @model_validator(mode="after")
    def _correlation_binding(self):
        if self.correlation is not None and (
            self.correlation.project_id != self.project_id
            or self.correlation.operation_id != self.operation_id
        ):
            raise ValueError("operation correlation binding mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        return _legacy_without_fields(self, "correlation") if self.correlation is None else ()


class BuildOperation(OperationBase):
    operation_type: Literal[OperationType.BUILD] = OperationType.BUILD
    toolchain: ToolchainIdentity
    configuration: str
    expected_artifact_type: Literal["ELF", "BIN", "HEX", "MAP"]
    firmware_build_request_id: str | None = None
    firmware_build_request_fingerprint: str | None = None
    firmware_spec_id: str | None = None
    firmware_spec_fingerprint: str | None = None
    implementation_id: str | None = None
    implementation_fingerprint: str | None = None
    source_artifacts: tuple[FirmwareSourceArtifact, ...] = ()
    build_system: str | None = None
    build_directory: str = "."
    firmware_build_request: FirmwareBuildRequest | None = None

    _configuration = field_validator("configuration", mode="before")(lambda value: identifier(value, field="configuration"))

    @field_validator(
        "firmware_build_request_id", "firmware_spec_id", "implementation_id", "build_system",
        mode="before",
    )
    @classmethod
    def _optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator(
        "firmware_build_request_fingerprint", "firmware_spec_fingerprint", "implementation_fingerprint",
        mode="before",
    )
    @classmethod
    def _optional_fingerprints(cls, value: object, info) -> str | None:
        return None if value is None else fingerprint(value, field=info.field_name)

    @field_validator("source_artifacts", mode="before")
    @classmethod
    def _source_artifacts(cls, value: object) -> tuple[FirmwareSourceArtifact, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("source_artifacts is invalid")
        return tuple(
            item if isinstance(item, FirmwareSourceArtifact)
            else FirmwareSourceArtifact.model_validate(item)
            for item in value
        )

    @field_validator("build_directory", mode="before")
    @classmethod
    def _build_directory(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("build_directory is invalid")
        normalized = value.replace("\\", "/")
        if normalized != "." and (
            not normalized
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError("build_directory is invalid")
        return normalized

    @model_validator(mode="after")
    def _firmware_build_binding(self):
        values = (
            self.firmware_build_request_id,
            self.firmware_build_request_fingerprint,
            self.firmware_spec_id,
            self.firmware_spec_fingerprint,
            self.implementation_id,
            self.implementation_fingerprint,
        )
        if self.firmware_build_request is not None and any(value is None for value in values):
            raise ValueError("firmware build binding is required for typed request")
        if any(value is not None for value in values) and any(value is None for value in values):
            raise ValueError("firmware build binding is incomplete")
        if self.build_system is not None and not (
            self.toolchain.tool_family == self.build_system
            or (
                self.build_system == "CMAKE"
                and self.toolchain.tool_family == "ARM_NONE_EABI_GCC"
            )
        ):
            raise ValueError("firmware build system and tool family mismatch")
        if self.firmware_build_request is not None:
            request = self.firmware_build_request
            if (
                request.project_id != self.project_id
                or request.fingerprint != self.firmware_build_request_fingerprint
                or request.target_kind != self.toolchain.supported_target
                or request.toolchain_id != self.toolchain.toolchain_id
            ):
                raise ValueError("firmware build request binding mismatch")
        return self


class BuildEvidence(FingerprintedContract):
    operation_id: str
    request_fingerprint: str
    result_status: Literal["BUILD_PASS", "BUILD_FAIL", "BUILD_BLOCKED", "BUILD_TIMEOUT", "BUILD_UNAVAILABLE"]
    toolchain_id: str
    toolchain_version: str
    source_fingerprints: tuple[str, ...] = ()
    output_artifact_ids: tuple[str, ...] = ()
    log_artifact_id: str | None = None
    raw_evidence_artifact_id: str | None = None
    diagnostics: tuple[str, ...] = ()
    tool_observed: bool = False
    build_system: str | None = None
    cmake_configure_exit_code: int | None = Field(default=None, ge=0)
    compiler_exit_code: int | None = Field(default=None, ge=0)
    assembler_exit_code: int | None = Field(default=None, ge=0)
    linker_exit_code: int | None = Field(default=None, ge=0)
    objcopy_exit_code: int | None = Field(default=None, ge=0)
    elf_verified: bool = False
    bin_verified: bool = False
    created_at: datetime

    _ids = field_validator("operation_id", "toolchain_id", "toolchain_version", "log_artifact_id", "raw_evidence_artifact_id", mode="before")(
        lambda value, info: None if value is None else identifier(value, field=info.field_name)
    )
    _fps = field_validator("request_fingerprint", mode="before")(
        lambda value: fingerprint(value, field="request_fingerprint")
    )
    _source_fps = field_validator("source_fingerprints", mode="before")(
        lambda value: tuple(fingerprint(item, field="source_fingerprint") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("source_fingerprints is invalid"))
    )
    _outputs = field_validator("output_artifact_ids", mode="before")(
        lambda value: tuple(identifier(item, field="output_artifact_id") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("output_artifact_ids is invalid"))
    )
    _diagnostics = field_validator("diagnostics", mode="before")(
        lambda value: tuple(safe_text(item, field="diagnostic", maximum=512) for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("diagnostics is invalid"))
    )
    _build_system = field_validator("build_system", mode="before")(
        lambda value: None if value is None else identifier(value, field="build_system")
    )
    _time = field_validator("created_at", mode="before")(lambda value: utc_datetime(value, field="created_at"))

    @model_validator(mode="after")
    def _raw_evidence_binding(self) -> "BuildEvidence":
        if self.raw_evidence_artifact_id is not None and self.raw_evidence_artifact_id not in self.output_artifact_ids:
            raise ValueError("raw build evidence artifact binding mismatch")
        return self


class FlashOperation(OperationBase):
    operation_type: Literal[OperationType.FLASH] = OperationType.FLASH
    artifact: ArtifactIdentity
    tool_id: str
    tool_version: str

    _ids = field_validator("tool_id", "tool_version", mode="before")(lambda value, info: identifier(value, field=info.field_name))

    @model_validator(mode="after")
    def _artifact_binding(self):
        if (
            self.artifact.project_id != self.project_id
            or self.artifact.target_id != self.target_id
            or self.artifact.workspace_fingerprint != self.workspace_fingerprint
        ):
            raise ValueError("artifact target binding mismatch")
        return self


class HardwareBringupOperation(OperationBase):
    """One approval-bound STM32F103C8T6 bring-up action for ExecutionWorker."""

    operation_type: Literal[OperationType.HARDWARE_BRINGUP] = OperationType.HARDWARE_BRINGUP
    action: Literal["CONNECT", "FLASH", "BOOT", "UART"]
    tool_id: str
    probe_type: Literal["ST_LINK", "OPENOCD", "PYOCD"]
    probe_serial: str
    target_mcu: Literal["STM32F103C8T6"] = "STM32F103C8T6"
    connection_id: str
    hardware_session_id: str
    firmware_sha256: str
    pcb_sha256: str
    schematic_sha256: str
    elf_sha256: str
    build_id: str
    design_truth_id: str
    artifact: ArtifactIdentity | None = None
    uart_port_binding: str | None = None
    baud_rate: int | None = Field(default=None, ge=1200, le=4_000_000)
    expected_patterns: tuple[str, ...] = ()

    _ids = field_validator(
        "tool_id", "probe_serial", "connection_id", "hardware_session_id", "build_id", "design_truth_id",
        mode="before",
    )(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator(
        "firmware_sha256", "pcb_sha256", "schematic_sha256", "elf_sha256", mode="before"
    )(lambda value, info: fingerprint(value, field=info.field_name))

    @field_validator("expected_patterns", mode="before")
    @classmethod
    def _patterns(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or len(value) > 32:
            raise ValueError("expected_patterns is invalid")
        return tuple(safe_text(item, field="expected_pattern", maximum=128) for item in value)

    @model_validator(mode="after")
    def _binding(self):
        expected_tool = {
            "ST_LINK": "STLINK_FLASH",
            "OPENOCD": "OPENOCD_FLASH",
            "PYOCD": "PYOCD_FLASH",
        }[self.probe_type]
        if self.tool_id != expected_tool:
            raise ValueError("hardware probe and tool binding mismatch")
        if self.action == "FLASH" and self.artifact is None:
            raise ValueError("hardware flash requires firmware artifact")
        if self.artifact is not None:
            if self.artifact.project_id != self.project_id or self.artifact.target_id != self.target_id:
                raise ValueError("hardware artifact target binding mismatch")
            if self.artifact.artifact_type not in {"ELF", "BIN"}:
                raise ValueError("hardware bring-up accepts ELF or BIN only")
            expected_artifact_hash = self.elf_sha256 if self.artifact.artifact_type == "ELF" else self.firmware_sha256
            if self.artifact.artifact_sha256 != expected_artifact_hash:
                raise ValueError("firmware artifact hash mismatch")
        if self.action == "UART" and self.baud_rate is None:
            raise ValueError("UART bring-up requires baud rate")
        if self.action == "UART" and self.uart_port_binding is None:
            raise ValueError("UART bring-up requires port binding")
        return self


class TestOperation(OperationBase):
    """A typed request to execute one validation action.

    This contract participates in the controlled operation lifecycle only. It
    does not define a test runner, test framework, or hardware test platform.
    """

    operation_type: Literal[OperationType.TEST] = OperationType.TEST
    test_suite_id: str
    test_profile_id: str
    tool_id: Literal["FIRMWARE_TEST"] = "FIRMWARE_TEST"
    artifact: ArtifactIdentity | None = None

    _ids = field_validator("test_suite_id", "test_profile_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )

    @model_validator(mode="after")
    def _artifact_binding(self):
        if self.artifact is not None and (
            self.artifact.project_id != self.project_id
            or self.artifact.target_id != self.target_id
            or self.artifact.workspace_fingerprint != self.workspace_fingerprint
        ):
            raise ValueError("artifact target binding mismatch")
        return self


class SimulationOperation(OperationBase):
    """First-class controlled operation for a local virtual-hardware run."""

    operation_type: Literal[OperationType.SIMULATION] = OperationType.SIMULATION
    operation_family: Literal["SIMULATION"] = "SIMULATION"
    simulation_id: str
    scenario_id: str
    scenario_fingerprint: str
    backend: str
    backend_version: str
    platform_descriptor: str
    prepared_id: str
    request_fingerprint: str
    timeout_seconds: int = Field(ge=1, le=900)
    artifact: ArtifactIdentity

    _ids = field_validator(
        "simulation_id",
        "scenario_id",
        "backend",
        "backend_version",
        "platform_descriptor",
        "prepared_id",
        mode="before",
    )(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("scenario_fingerprint", "request_fingerprint", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name)
    )

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _timeout(cls, value: object) -> int:
        if type(value) is not int:
            raise TypeError("timeout_seconds is invalid")
        return value

    @model_validator(mode="after")
    def _artifact_binding(self):
        if (
            self.artifact.project_id != self.project_id
            or self.artifact.target_id != self.target_id
            or self.artifact.workspace_fingerprint != self.workspace_fingerprint
        ):
            raise ValueError("artifact target binding mismatch")
        return self


class UartObservationOperation(OperationBase):
    operation_type: Literal[OperationType.UART_OBSERVATION] = OperationType.UART_OBSERVATION
    port_binding_id: str
    baud_rate: Literal[9600, 19200, 38400, 57600, 115200, 230400]
    duration_seconds: int = Field(ge=1, le=300)
    max_bytes: int = Field(ge=1, le=1024 * 1024)
    expected_patterns: tuple[str, ...] = ()

    _port = field_validator("port_binding_id", mode="before")(lambda value: identifier(value, field="port_binding_id"))

    @field_validator("expected_patterns", mode="before")
    @classmethod
    def _patterns(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or len(value) > 32:
            raise ValueError("expected_patterns is invalid")
        return tuple(safe_text(item, field="expected_pattern", maximum=128) for item in value)


class HilStepKind(StrEnum):
    WAIT_FOR_UART_PATTERN = "WAIT_FOR_UART_PATTERN"
    ASSERT_UART_PATTERN = "ASSERT_UART_PATTERN"
    ASSERT_NO_PANIC = "ASSERT_NO_PANIC"
    WAIT_DURATION = "WAIT_DURATION"
    POWER_CYCLE = "POWER_CYCLE"
    RESET_TARGET = "RESET_TARGET"


class HilStep(ExecutionContract):
    kind: HilStepKind
    pattern: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1, le=300)

    @field_validator("pattern", mode="before")
    @classmethod
    def _pattern(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="pattern", maximum=128)

    @model_validator(mode="after")
    def _shape(self):
        needs_pattern = self.kind in {HilStepKind.WAIT_FOR_UART_PATTERN, HilStepKind.ASSERT_UART_PATTERN}
        needs_duration = self.kind is HilStepKind.WAIT_DURATION
        if needs_pattern != (self.pattern is not None) or needs_duration != (self.duration_seconds is not None):
            raise ValueError("HIL step parameters do not match step kind")
        return self


class HilScenario(FingerprintedContract):
    scenario_id: str
    project_id: str
    target_id: str
    artifact_id: str
    steps: tuple[HilStep, ...]
    timeout_seconds: int = Field(ge=1, le=900)

    _ids = field_validator("scenario_id", "project_id", "target_id", "artifact_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, value: object) -> tuple[HilStep, ...]:
        if not isinstance(value, (tuple, list)) or not value or len(value) > 64:
            raise ValueError("HIL steps are invalid")
        return tuple(HilStep.model_validate(item) for item in value)


class HilOperation(OperationBase):
    operation_type: Literal[OperationType.HIL] = OperationType.HIL
    scenario: HilScenario
    flash_operation_id: str

    _flash = field_validator("flash_operation_id", mode="before")(lambda value: identifier(value, field="flash_operation_id"))

    @model_validator(mode="after")
    def _scenario_binding(self):
        if self.scenario.project_id != self.project_id or self.scenario.target_id != self.target_id:
            raise ValueError("HIL scenario binding mismatch")
        return self


class AuthorizedExecutionBinding(FingerprintedContract):
    capability_id: Literal["FIRMWARE_CODE_GENERATION"] = "FIRMWARE_CODE_GENERATION"
    project_id: str
    conversation_id: str
    implementation_id: str
    implementation_fingerprint: str
    task_id: str
    task_fingerprint: str
    intent_id: str
    intent_fingerprint: str
    request_id: str
    request_fingerprint: str
    decision_id: str
    decision_fingerprint: str
    authorization_id: str
    authorization_fingerprint: str
    revision: str

    _ids = field_validator(
        "project_id", "conversation_id", "implementation_id", "task_id", "intent_id",
        "request_id", "decision_id", "authorization_id", "revision", mode="before",
    )(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator(
        "implementation_fingerprint", "task_fingerprint", "intent_fingerprint",
        "request_fingerprint", "decision_fingerprint", "authorization_fingerprint",
        mode="before",
    )(lambda value, info: fingerprint(value, field=info.field_name))


class FirmwareSourceWriteFile(FingerprintedContract):
    relative_path: str
    content: str
    content_fingerprint: str
    size_bytes: int = Field(ge=1, le=64 * 1024)

    @field_validator("relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("relative_path is invalid")
        normalized = value.replace("\\", "/")
        windows_reserved = {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{index}" for index in range(1, 10)),
            *(f"LPT{index}" for index in range(1, 10)),
        }
        if (
            not normalized.startswith("generated/")
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
            or normalized.rsplit(".", 1)[-1].casefold() not in {"c", "h"}
            or any(part.split(".", 1)[0].upper() in windows_reserved for part in normalized.split("/"))
        ):
            raise ValueError("relative_path is invalid")
        return normalized

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> str:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("source content is invalid")
        if len(value.encode("utf-8")) > 64 * 1024:
            raise ValueError("source content exceeds limit")
        return value

    _content_fp = field_validator("content_fingerprint", mode="before")(
        lambda value: fingerprint(value, field="content_fingerprint")
    )

    @model_validator(mode="after")
    def _binding(self):
        encoded = self.content.encode("utf-8")
        expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if self.content_fingerprint != expected or self.size_bytes != len(encoded):
            raise ValueError("source content binding mismatch")
        return self


class FirmwareSourceWriteOperation(OperationBase):
    operation_type: Literal[OperationType.FIRMWARE_SOURCE_WRITE] = OperationType.FIRMWARE_SOURCE_WRITE
    authorized_execution: AuthorizedExecutionBinding
    files: tuple[FirmwareSourceWriteFile, ...]
    changeset: ChangeSet
    workspace_approval: ApplyApprovalGrant

    @field_validator("authorized_execution", mode="before")
    @classmethod
    def _authorization(cls, value: object) -> AuthorizedExecutionBinding:
        return AuthorizedExecutionBinding.model_validate(
            value.model_dump(mode="python") if isinstance(value, AuthorizedExecutionBinding) else value,
            strict=False,
        )

    @field_validator("files", mode="before")
    @classmethod
    def _files(cls, value: object) -> tuple[FirmwareSourceWriteFile, ...]:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise ValueError("firmware source operation requires two files")
        return tuple(
            FirmwareSourceWriteFile.model_validate(
                item.model_dump(mode="python") if isinstance(item, FirmwareSourceWriteFile) else item,
                strict=False,
            )
            for item in value
        )

    @field_validator("changeset", mode="before")
    @classmethod
    def _changeset(cls, value: object) -> ChangeSet:
        return ChangeSet.model_validate(
            value.model_dump(mode="python") if isinstance(value, ChangeSet) else value,
            strict=False,
        )

    @field_validator("workspace_approval", mode="before")
    @classmethod
    def _workspace_approval(cls, value: object) -> ApplyApprovalGrant:
        return ApplyApprovalGrant.model_validate(
            value.model_dump(mode="python") if isinstance(value, ApplyApprovalGrant) else value,
            strict=False,
        )

    @model_validator(mode="after")
    def _source_binding(self):
        binding = self.authorized_execution
        change = self.changeset
        grant = self.workspace_approval
        if (
            binding.project_id != self.project_id
            or self.proposal_id != change.proposal_id
            or self.proposal_fingerprint != change.proposal_fingerprint
            or self.target_id != change.target.target_id
            or self.target_fingerprint != change.target_fingerprint
            or self.workspace_fingerprint != change.base_project_fingerprint
            or grant.changeset_id != change.changeset_id
            or grant.changeset_fingerprint != change.fingerprint
            or grant.proposal_fingerprint != self.proposal_fingerprint
        ):
            raise ValueError("firmware source operation binding mismatch")
        paths = tuple(item.relative_path.casefold() for item in self.files)
        if len(paths) != len(set(paths)) or sum(item.size_bytes for item in self.files) > 128 * 1024:
            raise ValueError("firmware source file boundary mismatch")
        entries = tuple(
            (item.relative_file_reference.casefold(), item.after_fingerprint, item.after_content)
            for item in change.entries
        )
        expected = tuple(
            (item.relative_path.casefold(), item.content_fingerprint, item.content)
            for item in self.files
        )
        if entries != expected:
            raise ValueError("firmware source changeset content mismatch")
        return self


class SchematicWriteOperation(OperationBase):
    operation_type: Literal[OperationType.KICAD_SCHEMATIC_WRITE] = OperationType.KICAD_SCHEMATIC_WRITE
    conversation_id: str
    implementation_id: str
    implementation_task_id: str
    capability_id: Literal["ARTIFACT_CREATION"] = "ARTIFACT_CREATION"
    authorization_id: str
    authorization_request_id: str
    proposal_decision_id: str
    proposal_decision_fingerprint: str
    schematic_request_id: str
    schematic_request_fingerprint: str
    design_truth_fingerprint: str
    target_relative_path: str
    content: str
    content_fingerprint: str
    changeset: ChangeSet
    workspace_approval: ApplyApprovalGrant

    _ids = field_validator("conversation_id", "implementation_id", "implementation_task_id", "authorization_id", "authorization_request_id", "proposal_decision_id", "schematic_request_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fps = field_validator(
        "schematic_request_fingerprint", "design_truth_fingerprint", "proposal_decision_fingerprint", "content_fingerprint", mode="before"
    )(lambda value, info: fingerprint(value, field=info.field_name))

    @field_validator("target_relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("target_relative_path is invalid")
        normalized = value.replace("\\", "/")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if normalized.startswith("/") or normalized.startswith("//") or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_sch") or any(part.split(".", 1)[0].upper() in reserved for part in normalized.split("/")):
            raise ValueError("target_relative_path is invalid")
        return normalized

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> str:
        if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("schematic content is invalid")
        return value

    @field_validator("changeset", mode="before")
    @classmethod
    def _changeset(cls, value: object) -> ChangeSet:
        return ChangeSet.model_validate(value.model_dump(mode="python") if isinstance(value, ChangeSet) else value, strict=False)

    @field_validator("workspace_approval", mode="before")
    @classmethod
    def _approval(cls, value: object) -> ApplyApprovalGrant:
        return ApplyApprovalGrant.model_validate(value.model_dump(mode="python") if isinstance(value, ApplyApprovalGrant) else value, strict=False)

    @model_validator(mode="after")
    def _binding(self):
        expected = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_fingerprint != expected or self.changeset.proposal_fingerprint != self.proposal_fingerprint or self.changeset.base_project_fingerprint != self.workspace_fingerprint or self.changeset.target.target_id != self.target_id or self.workspace_approval.changeset_id != self.changeset.changeset_id or self.workspace_approval.changeset_fingerprint != self.changeset.fingerprint:
            raise ValueError("schematic write operation binding mismatch")
        entries = tuple(item for item in self.changeset.entries if item.relative_file_reference.casefold() == self.target_relative_path.casefold())
        if len(entries) != 1 or entries[0].after_content != self.content or entries[0].after_fingerprint != self.content_fingerprint:
            raise ValueError("schematic changeset content mismatch")
        return self


class SchematicParseOperation(OperationBase):
    """A separately approved read of the exact generated schematic."""

    operation_type: Literal[OperationType.KICAD_SCHEMATIC_PARSE] = OperationType.KICAD_SCHEMATIC_PARSE
    conversation_id: str
    source_write_operation_id: str
    source_write_receipt_fingerprint: str
    schematic_plan_id: str
    schematic_plan_fingerprint: str
    artifact_path: Literal["hardware/main.kicad_sch"] = "hardware/main.kicad_sch"
    artifact_fingerprint: str
    parse_format: Literal["KICAD_XML_NETLIST"] = "KICAD_XML_NETLIST"

    _source_ids = field_validator("conversation_id", "source_write_operation_id", "schematic_plan_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _source_fps = field_validator("source_write_receipt_fingerprint", "schematic_plan_fingerprint", "artifact_fingerprint", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name)
    )

    @model_validator(mode="after")
    def _plan_binding(self):
        if self.proposal_id != self.schematic_plan_id or self.proposal_fingerprint != self.schematic_plan_fingerprint:
            raise ValueError("schematic evidence plan binding mismatch")
        return self


class SchematicReadBackOperation(SchematicParseOperation):
    operation_type: Literal[OperationType.KICAD_SCHEMATIC_READ_BACK] = OperationType.KICAD_SCHEMATIC_READ_BACK
    library_map_evidence_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    library_map_evidence_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)

    _library_id = field_validator("library_map_evidence_id", mode="before")(
        lambda value: None if value is None else identifier(value, field="library_map_evidence_id"))
    _library_fp = field_validator("library_map_evidence_fingerprint", mode="before")(
        lambda value: None if value is None else fingerprint(value, field="library_map_evidence_fingerprint"))

    @model_validator(mode="after")
    def _library_pair(self):
        if (self.library_map_evidence_id is None) != (self.library_map_evidence_fingerprint is None):
            raise ValueError("library map selector pair incomplete")
        return self


class SchematicXmlResult(FingerprintedContract):
    """Canonical tool evidence; PASS alone is not a domain claim."""

    operation_id: str
    source_write_operation_id: str
    source_write_receipt_fingerprint: str
    input_artifact_path: Literal["hardware/main.kicad_sch"]
    fingerprint_before: str
    fingerprint_after: str
    tool_executable_sha256: str
    tool_version: str
    command_identity: Literal["kicad-cli sch export netlist --format kicadxml"]
    netlist_artifact: ArtifactIdentity
    coverage: tuple[Literal["references", "part_properties", "physical_pin_numbers", "nets"], ...]
    loss_or_unknown: tuple[str, ...] = ()
    exit_code: int
    parse_status: Literal["PASS", "FAIL", "UNKNOWN"]
    created_at: datetime

    _ids = field_validator("operation_id", "source_write_operation_id", mode="before")(lambda v, info: identifier(v, field=info.field_name))
    _fps = field_validator("source_write_receipt_fingerprint", "fingerprint_before", "fingerprint_after", "tool_executable_sha256", mode="before")(lambda v, info: fingerprint(v, field=info.field_name))
    _version = field_validator("tool_version", mode="before")(lambda v: safe_text(v, field="tool_version", maximum=80))
    _time = field_validator("created_at", mode="before")(lambda v: utc_datetime(v, field="created_at"))
    _tuples = field_validator("coverage", "loss_or_unknown", mode="before")(lambda v: tuple(v) if isinstance(v, (tuple, list)) else v)

    @model_validator(mode="after")
    def _result_binding(self):
        if len(set(self.coverage)) != len(self.coverage):
            raise ValueError("duplicate XML coverage")
        if (self.netlist_artifact.artifact_type != "NETLIST" or self.netlist_artifact.build_operation_id != self.operation_id
            or self.netlist_artifact.toolchain_id != "KICAD_XML_NETLIST" or self.netlist_artifact.toolchain_version != self.tool_version
            or self.netlist_artifact.created_at != self.created_at):
            raise ValueError("XML artifact provenance mismatch")
        if self.parse_status == "PASS" and (self.exit_code != 0 or self.fingerprint_before != self.fingerprint_after or not self.coverage or self.loss_or_unknown):
            raise ValueError("XML PASS requires unchanged complete declared input evidence")
        return self


class SchematicErcOperation(OperationBase):
    operation_type: Literal[OperationType.KICAD_SCHEMATIC_ERC] = OperationType.KICAD_SCHEMATIC_ERC
    conversation_id: str
    schematic_artifact_ref: str
    schematic_artifact_fingerprint: str
    source_operation_id: str
    source_operation_receipt_fingerprint: str
    target_relative_path: str
    schematic_revision: str = "r1"
    source_parse_operation_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_parse_receipt_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _parse_binding(self):
        if (self.source_parse_operation_id is None) != (self.source_parse_receipt_fingerprint is None):
            raise ValueError("ERC parse binding is incomplete")
        if self.source_parse_operation_id is not None:
            identifier(self.source_parse_operation_id, field="source_parse_operation_id")
            fingerprint(self.source_parse_receipt_fingerprint)
        return self

    _ids = field_validator("conversation_id", "schematic_artifact_ref", "source_operation_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fps = field_validator("schematic_artifact_fingerprint", "source_operation_receipt_fingerprint", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name)
    )

    @field_validator("schematic_revision", mode="before")
    @classmethod
    def _revision(cls, value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"r[1-9][0-9]*", value.strip()) is None:
            raise ValueError("schematic_revision is invalid")
        return value.strip()

    @field_validator("target_relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("target_relative_path is invalid")
        normalized = value.replace("\\", "/")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if normalized.startswith("/") or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_sch") or any(part.split(".", 1)[0].upper() in reserved for part in normalized.split("/")):
            raise ValueError("target_relative_path is invalid")
        return normalized


class PCBWriteOperation(OperationBase):
    operation_type: Literal[OperationType.KICAD_PCB_WRITE] = OperationType.KICAD_PCB_WRITE
    conversation_id: str
    implementation_id: str
    implementation_task_id: str
    capability_id: Literal["ARTIFACT_CREATION"] = "ARTIFACT_CREATION"
    authorization_id: str
    authorization_request_id: str
    pcb_request_id: str
    pcb_request_fingerprint: str
    pcb_proposal_id: str
    pcb_proposal_fingerprint: str
    schematic_artifact_id: str
    schematic_fingerprint: str
    erc_result_id: str
    erc_fingerprint: str
    design_truth_fingerprint: str
    target_relative_path: str
    content: str
    content_fingerprint: str
    changeset: ChangeSet
    workspace_approval: ApplyApprovalGrant

    _ids = field_validator("conversation_id", "implementation_id", "implementation_task_id", "authorization_id", "authorization_request_id", "pcb_request_id", "pcb_proposal_id", "schematic_artifact_id", "erc_result_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fps = field_validator("pcb_request_fingerprint", "pcb_proposal_fingerprint", "schematic_fingerprint", "erc_fingerprint", "design_truth_fingerprint", "content_fingerprint", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name)
    )

    @field_validator("target_relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("target_relative_path is invalid")
        normalized = value.replace("\\", "/")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_pcb") or any(part.split(".", 1)[0].upper() in reserved for part in normalized.split("/")):
            raise ValueError("target_relative_path is invalid")
        return normalized

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> str:
        if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("PCB content is invalid")
        return value

    @field_validator("changeset", mode="before")
    @classmethod
    def _changeset(cls, value: object) -> ChangeSet:
        return ChangeSet.model_validate(value.model_dump(mode="python") if isinstance(value, ChangeSet) else value, strict=False)

    @field_validator("workspace_approval", mode="before")
    @classmethod
    def _approval(cls, value: object) -> ApplyApprovalGrant:
        return ApplyApprovalGrant.model_validate(value.model_dump(mode="python") if isinstance(value, ApplyApprovalGrant) else value, strict=False)

    @model_validator(mode="after")
    def _binding(self):
        expected = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_fingerprint != expected or self.changeset.proposal_fingerprint != self.pcb_proposal_fingerprint or self.changeset.base_project_fingerprint != self.workspace_fingerprint or self.changeset.target.target_id != self.target_id or self.workspace_approval.changeset_id != self.changeset.changeset_id or self.workspace_approval.changeset_fingerprint != self.changeset.fingerprint:
            raise ValueError("PCB write operation binding mismatch")
        entries = tuple(item for item in self.changeset.entries if item.relative_file_reference.casefold() == self.target_relative_path.casefold())
        if len(entries) != 1 or entries[0].after_content != self.content or entries[0].after_fingerprint != self.content_fingerprint:
            raise ValueError("PCB changeset content mismatch")
        return self


def native_move_operation_id(
    tenant_id: str, project_id: str, candidate_id: str, candidate_fingerprint: str,
    mapping_review_id: str, mapping_review_fingerprint: str,
    production_context_fingerprint: str | None = None,
) -> str:
    values = (
        identifier(tenant_id, field="tenant_id"), identifier(project_id, field="project_id"),
        identifier(candidate_id, field="candidate_id"), fingerprint(candidate_fingerprint),
        identifier(mapping_review_id, field="mapping_review_id"), fingerprint(mapping_review_fingerprint),
    )
    if production_context_fingerprint is not None:
        values += (fingerprint(production_context_fingerprint),)
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "native-move-" + hashlib.sha256(encoded).hexdigest()[:32]


def native_move_operation_nonce(mapping_review_id: str, mapping_review_fingerprint: str, production_context_fingerprint: str | None = None) -> str:
    material = identifier(mapping_review_id, field="mapping_review_id") + "|" + fingerprint(mapping_review_fingerprint)
    if production_context_fingerprint is not None:
        material += "|" + fingerprint(production_context_fingerprint)
    encoded = material.encode("utf-8")
    return "native-move-nonce-" + hashlib.sha256(encoded).hexdigest()[:24]


class NativeMoveReaderPolicy(ExecutionContract):
    reader_id: str
    reader_version: str
    executable_sha256: str
    timeout_seconds: int = Field(ge=1, le=600)

    _ids = field_validator("reader_id", "reader_version", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fp = field_validator("executable_sha256", mode="before")(lambda value: fingerprint(value))


class PcbDrcBaselineCollectorPolicy(FingerprintedContract):
    """Input-only identity for the independent C1 baseline observation."""
    collector_id: str
    collector_version: str
    tool_id: str
    tool_version: str
    executable_sha256: str
    tool_bundle_manifest_fingerprint: str
    environment_content_fingerprint: str
    rules_manifest_fingerprint: str
    dependency_manifest_fingerprint: str
    timeout_seconds: int = Field(ge=1, le=600)
    normalizer_id: Literal["KICAD_DRC_JSON_V1"] = "KICAD_DRC_JSON_V1"
    normalizer_version: Literal["10.0.5-r1"] = "10.0.5-r1"
    raw_schema: Literal["https://schemas.kicad.org/drc.v1.json"] = "https://schemas.kicad.org/drc.v1.json"
    raw_version: Literal["10.0.5"] = "10.0.5"
    comparison_scope: Literal["BASELINE_COMPARISON_ONLY"] = "BASELINE_COMPARISON_ONLY"
    included_severities: tuple[Literal["error"], Literal["warning"]] = ("error", "warning")
    ignored_checks: tuple[str, ...] = ()
    zone_fill_policy: Literal["NO_REFILL"] = "NO_REFILL"
    schematic_parity: Literal["NOT_RUN"] = "NOT_RUN"
    prl_policy: Literal["UNCHANGED"] = "UNCHANGED"

    _ids = field_validator("collector_id", "collector_version", "tool_id", "tool_version", mode="before")(
        lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("executable_sha256", "tool_bundle_manifest_fingerprint", "environment_content_fingerprint",
        "rules_manifest_fingerprint", "dependency_manifest_fingerprint", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name))

    @model_validator(mode="after")
    def _no_ignored_checks(self):
        if self.ignored_checks:
            raise ValueError("baseline ignored checks are forbidden")
        return self


class NativeMoveDrcPolicy(ExecutionContract):
    tool_id: str
    tool_version: str
    executable_sha256: str
    timeout_seconds: int = Field(ge=1, le=600)
    baseline_report: ArtifactIdentity
    comparison_scope: Literal["BASELINE_COMPARISON_ONLY"] = "BASELINE_COMPARISON_ONLY"
    included_severities: tuple[Literal["error", "warning"], ...] = ("error", "warning")
    ignored_checks: tuple[str, ...]
    zone_fill_policy: Literal["NO_REFILL"] = "NO_REFILL"
    schematic_parity: Literal["NOT_RUN"] = "NOT_RUN"

    _ids = field_validator("tool_id", "tool_version", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fp = field_validator("executable_sha256", mode="before")(lambda value: fingerprint(value))

    @model_validator(mode="after")
    def _fixed_scope(self):
        if self.included_severities != ("error", "warning") or len(set(self.ignored_checks)) != len(self.ignored_checks):
            raise ValueError("native DRC scope is invalid")
        if any(not item or "\x00" in item for item in self.ignored_checks):
            raise ValueError("native DRC ignored check is invalid")
        if self.baseline_report.artifact_type != "LOG":
            raise ValueError("native DRC baseline report type is invalid")
        return self


class NativeMoveSideEffectPolicy(FingerprintedContract):
    prl_relative_path: str
    prl_before_sha256: str | None
    prl_policy: Literal["REJECT", "ALLOW_REVIEWED_KEYS"]
    allowed_root_keys: tuple[str, ...] = ()

    _before = field_validator("prl_before_sha256", mode="before")(lambda value: None if value is None else fingerprint(value))

    @model_validator(mode="after")
    def _fixed_path_and_keys(self):
        if (not self.prl_relative_path.endswith(".kicad_prl") or self.prl_relative_path.startswith("/")
            or "\\" in self.prl_relative_path or ":" in self.prl_relative_path or "\x00" in self.prl_relative_path
            or self.prl_relative_path != unicodedata.normalize("NFC", self.prl_relative_path)
            or any(part in {"", ".", ".."} for part in self.prl_relative_path.split("/"))):
            raise ValueError("native PRL path is invalid")
        allowed = {"board", "net_inspector_panel", "schematic", "project", "open_jobsets", "meta", "git"}
        if self.prl_policy == "REJECT" and self.allowed_root_keys:
            raise ValueError("rejected native PRL cannot allow keys")
        if self.prl_policy == "ALLOW_REVIEWED_KEYS" and (not self.allowed_root_keys or not set(self.allowed_root_keys) <= allowed or len(set(self.allowed_root_keys)) != len(self.allowed_root_keys)):
            raise ValueError("native PRL key policy is invalid")
        return self


class NativeMoveProductionContextV1(FingerprintedContract):
    schema_version: Literal[1] = 1
    workspace_snapshot: WorkspaceSnapshot
    changeset: ChangeSet
    native_reader_policy: NativeMoveReaderPolicy
    drc_policy: NativeMoveDrcPolicy
    side_effect_policy: NativeMoveSideEffectPolicy

    @model_validator(mode="after")
    def _single_pcb_change(self):
        snapshot, change = self.workspace_snapshot, self.changeset
        if (snapshot.tenant_id, snapshot.project_id, snapshot.target_id, snapshot.project_fingerprint, snapshot.target_fingerprint) != (
            change.tenant_id, change.project_id, change.target.target_id, change.base_project_fingerprint, change.target_fingerprint
        ):
            raise ValueError("native workspace context mismatch")
        if len(change.entries) != 1 or change.entries[0].change_type is not ChangeType.MODIFY_FILE or not change.entries[0].relative_file_reference.endswith(".kicad_pcb"):
            raise ValueError("native changeset must modify exactly one PCB")
        if change.entries[0].after_content is None:
            raise ValueError("native changeset has no material")
        baseline = self.drc_policy.baseline_report
        if (baseline.tenant_id, baseline.project_id, baseline.target_id, baseline.workspace_fingerprint) != (
            snapshot.tenant_id, snapshot.project_id, snapshot.target_id, snapshot.project_fingerprint
        ):
            raise ValueError("native DRC baseline belongs to another Workspace")
        expected_prl = change.entries[0].relative_file_reference[:-len(".kicad_pcb")] + ".kicad_prl"
        if self.side_effect_policy.prl_relative_path != expected_prl:
            raise ValueError("native PRL path does not match the PCB")
        return self


class KiCadNativeFootprintMoveOperation(OperationBase):
    """C2-I identity remains valid; production context adds a separate authority binding."""

    operation_type: Literal[OperationType.KICAD_NATIVE_FOOTPRINT_MOVE] = OperationType.KICAD_NATIVE_FOOTPRINT_MOVE
    mapping_review_id: str
    mapping_review_fingerprint: str
    mapping_reviewed_at: datetime
    import_revision_id: str
    import_revision_fingerprint: str
    source_set_fingerprint: str
    manifest_fingerprint: str
    parser_ruleset_id: Literal["kicad-pcb-footprint-20240108-r1", "kicad-pcb-footprint-20240108-r2"]
    parser_ruleset_fingerprint: str
    supported_subset_id: Literal["kicad-footprint-placement-r1"]
    supported_subset_fingerprint: str
    native_entry_relative_path: str
    pcb_relative_path: str
    pcb_sha256: str
    native_uuid: str
    native_locator: str
    before_x_mm: float
    before_y_mm: float
    after_x_mm: float
    after_y_mm: float
    rotation_degrees: float
    diff_fingerprint: str
    production_context: NativeMoveProductionContextV1 | None = Field(default=None, exclude_if=lambda value: value is None)

    _ids = field_validator("mapping_review_id", "import_revision_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fps = field_validator(
        "mapping_review_fingerprint", "import_revision_fingerprint", "source_set_fingerprint",
        "manifest_fingerprint", "parser_ruleset_fingerprint", "supported_subset_fingerprint",
        "pcb_sha256", "diff_fingerprint", mode="before",
    )(lambda value, info: fingerprint(value, field=info.field_name))
    _review_time = field_validator("mapping_reviewed_at", mode="before")(
        lambda value: utc_datetime(value, field="mapping_reviewed_at")
    )

    @field_validator("native_entry_relative_path", "pcb_relative_path", mode="before")
    @classmethod
    def _native_path(cls, value: object, info) -> str:
        if not isinstance(value, str) or not value or value != unicodedata.normalize("NFC", value):
            raise ValueError("native path is invalid")
        if value.startswith("/") or "\\" in value or ":" in value or "\x00" in value or any(
            part in ("", ".", "..") for part in value.split("/")
        ):
            raise ValueError("native path is invalid")
        suffixes = (".kicad_pro", ".kicad_pcb") if info.field_name == "native_entry_relative_path" else (".kicad_pcb",)
        if not value.endswith(suffixes):
            raise ValueError("native path is invalid")
        return value

    @field_validator("native_uuid", mode="before")
    @classmethod
    def _uuid(cls, value: object) -> str:
        try:
            canonical = str(UUID(value))
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError("native UUID is invalid") from error
        if value != canonical:
            raise ValueError("native UUID is invalid")
        return canonical

    @field_validator("native_locator", mode="before")
    @classmethod
    def _locator(cls, value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"/kicad_pcb/footprint\[[0-9]+\]", value) is None:
            raise ValueError("native locator is invalid")
        return value

    @field_validator("before_x_mm", "before_y_mm", "after_x_mm", "after_y_mm", "rotation_degrees")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value) or abs(value) > 10_000:
            raise ValueError("native placement is invalid")
        return value

    @model_validator(mode="after")
    def _native_binding(self):
        context_fp = None if self.production_context is None else self.production_context.fingerprint
        if self.operation_id != native_move_operation_id(
            self.tenant_id, self.project_id, self.proposal_id, self.proposal_fingerprint,
            self.mapping_review_id, self.mapping_review_fingerprint, context_fp,
        ) or self.nonce != native_move_operation_nonce(self.mapping_review_id, self.mapping_review_fingerprint, context_fp):
            raise ValueError("native operation identity mismatch")
        if self.created_at != self.mapping_reviewed_at or self.expires_at != self.mapping_reviewed_at + timedelta(minutes=10):
            raise ValueError("native operation review lifetime mismatch")
        if (self.before_x_mm, self.before_y_mm) == (self.after_x_mm, self.after_y_mm):
            raise ValueError("native placement is unchanged")
        if self.production_context is not None:
            context = self.production_context
            snapshot, change = context.workspace_snapshot, context.changeset
            if (
                (self.tenant_id, self.project_id, self.target_id, self.workspace_fingerprint, self.target_fingerprint)
                != (snapshot.tenant_id, snapshot.project_id, snapshot.target_id, snapshot.project_fingerprint, snapshot.target_fingerprint)
                or self.workspace_snapshot_id != "workspace-snapshot-" + hashlib.sha256(snapshot.fingerprint.encode("utf-8")).hexdigest()[:24]
                or (change.proposal_id, change.proposal_fingerprint, change.requester_id) != (self.proposal_id, self.proposal_fingerprint, self.requester_id)
                or change.entries[0].relative_file_reference != self.pcb_relative_path
                or change.entries[0].before_fingerprint != self.pcb_sha256
                or change.expires_at < self.expires_at
            ):
                raise ValueError("native production context binding mismatch")
        return self


def drc_baseline_proposal_fingerprint(values: dict) -> str:
    fields = ("operation_type", "tenant_id", "project_id", "requester_id", "target_id",
        "target_fingerprint", "workspace_snapshot_id", "workspace_fingerprint", "import_revision_id",
        "import_revision_fingerprint", "source_set_fingerprint", "manifest_fingerprint", "parser_ruleset_id",
        "parser_ruleset_fingerprint", "supported_subset_id", "supported_subset_fingerprint",
        "native_entry_relative_path", "pcb_relative_path", "pcb_sha256", "prl_relative_path", "prl_before_sha256")
    material = {key: values[key] for key in fields}
    policy = values["collector_policy"]
    if not isinstance(policy, PcbDrcBaselineCollectorPolicy):
        policy = PcbDrcBaselineCollectorPolicy.model_validate(policy)
    material.update(schema_version=1, collector_policy_fingerprint=policy.fingerprint)
    return "sha256:" + hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class PcbDrcBaselineOperation(OperationBase):
    """Read-only C1 baseline without a write source or output identity."""
    operation_type: Literal[OperationType.KICAD_PCB_DRC_BASELINE] = OperationType.KICAD_PCB_DRC_BASELINE
    import_revision_id: str
    import_revision_fingerprint: str
    source_set_fingerprint: str
    manifest_fingerprint: str
    parser_ruleset_id: str
    parser_ruleset_fingerprint: str
    supported_subset_id: str
    supported_subset_fingerprint: str
    native_entry_relative_path: str
    pcb_relative_path: str
    pcb_sha256: str
    collector_policy: PcbDrcBaselineCollectorPolicy
    prl_relative_path: str
    prl_before_sha256: str | None

    _ids = field_validator("import_revision_id", "parser_ruleset_id", "supported_subset_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("import_revision_fingerprint", "source_set_fingerprint", "manifest_fingerprint",
        "parser_ruleset_fingerprint", "supported_subset_fingerprint", "pcb_sha256", "prl_before_sha256", mode="before")(
        lambda value, info: None if value is None and info.field_name == "prl_before_sha256"
        else fingerprint(value, field=info.field_name))

    @field_validator("native_entry_relative_path", "pcb_relative_path", "prl_relative_path", mode="before")
    @classmethod
    def _paths(cls, value: object, info) -> str:
        if not isinstance(value, str):
            raise ValueError("baseline path is invalid")
        if (not value or len(value) > 1024 or "\x00" in value or "\\" in value or ":" in value
            or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/"))
            or (info.field_name == "pcb_relative_path" and not value.casefold().endswith(".kicad_pcb"))
            or (info.field_name == "prl_relative_path" and not value.casefold().endswith(".kicad_prl"))):
            raise ValueError("baseline path is invalid")
        return value

    @model_validator(mode="after")
    def _proposal_binding(self):
        if self.proposal_fingerprint != drc_baseline_proposal_fingerprint(self.model_dump(mode="python")):
            raise ValueError("baseline proposal binding mismatch")
        return self


class PcbDrcOperation(OperationBase):
    operation_type: Literal[OperationType.KICAD_PCB_DRC] = OperationType.KICAD_PCB_DRC
    conversation_id: str
    pcb_artifact_ref: str
    pcb_content_fingerprint: str
    source_write_operation_id: str
    source_write_receipt_fingerprint: str
    target_relative_path: str

    _ids = field_validator("conversation_id", "pcb_artifact_ref", "source_write_operation_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fps = field_validator("pcb_content_fingerprint", "source_write_receipt_fingerprint", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name)
    )

    @field_validator("target_relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("target_relative_path is invalid")
        normalized = value.replace("\\", "/")
        if normalized != value or normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_pcb"):
            raise ValueError("target_relative_path is invalid")
        return normalized


class KiCadManufacturingOperation(OperationBase):
    """Typed, read-only KiCad manufacturing export operation.

    The operation writes only to a caller-bound manufacturing workspace.  The
    actual executable remains resolved by the canonical Worker registry; no
    user text is accepted as an argv fragment.
    """

    operation_type: Literal[
        OperationType.KICAD_GERBER_EXPORT,
        OperationType.KICAD_DRILL_EXPORT,
        OperationType.KICAD_POSITION_EXPORT,
        OperationType.MANUFACTURING_PACKAGE_BUILD,
    ]
    conversation_id: str
    tool_id: str
    tool_version: str
    export_kind: Literal["GERBER", "DRILL", "POSITION", "PACKAGE"]
    project_revision: str
    pcb_artifact_id: str
    pcb_sha256: str
    manufacturing_specification_id: str
    manufacturing_specification_fingerprint: str
    pcb_state_fingerprint: str | None = None
    g7_summary_fingerprint: str | None = None
    ready_for_fab_fingerprint: str | None = None
    ready_for_fab_target_fingerprint: str | None = None
    human_approval_fingerprint: str | None = None
    manufacturer_profile_fingerprint: str | None = None
    dfm_fingerprint: str | None = None
    source_pcb_relative_path: str
    output_workspace_relative: str
    layer_list: tuple[str, ...] = ()

    _ids = field_validator(
        "conversation_id", "tool_id", "tool_version", "project_revision",
        "pcb_artifact_id", "manufacturing_specification_id", mode="before",
    )(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator(
        "pcb_sha256", "manufacturing_specification_fingerprint", mode="before",
    )(lambda value, info: fingerprint(value, field=info.field_name))
    _gate_fps = field_validator(
        "pcb_state_fingerprint",
        "g7_summary_fingerprint",
        "ready_for_fab_fingerprint",
        "ready_for_fab_target_fingerprint",
        "human_approval_fingerprint",
        "manufacturer_profile_fingerprint",
        "dfm_fingerprint",
        mode="before",
    )(
        lambda value, info: None if value is None else fingerprint(value, field=info.field_name)
    )
    _tuple = field_validator("layer_list", mode="before")(
        lambda value: tuple(identifier(item, field="layer") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("layer_list is invalid"))
    )

    @field_validator("source_pcb_relative_path", "output_workspace_relative", mode="before")
    @classmethod
    def _relative_path(cls, value: object, info) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{info.field_name} is invalid")
        normalized = value.replace("\\", "/")
        if (
            normalized != value
            or normalized.startswith(("/", "//"))
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError(f"{info.field_name} is invalid")
        if info.field_name == "source_pcb_relative_path" and not normalized.casefold().endswith(".kicad_pcb"):
            raise ValueError("source_pcb_relative_path is invalid")
        return normalized

    @model_validator(mode="after")
    def _kind_binding(self):
        expected = {
            OperationType.KICAD_GERBER_EXPORT: "GERBER",
            OperationType.KICAD_DRILL_EXPORT: "DRILL",
            OperationType.KICAD_POSITION_EXPORT: "POSITION",
            OperationType.MANUFACTURING_PACKAGE_BUILD: "PACKAGE",
        }[self.operation_type]
        if self.export_kind != expected:
            raise ValueError("manufacturing operation kind mismatch")
        gate_fingerprints = (
            self.pcb_state_fingerprint,
            self.g7_summary_fingerprint,
            self.ready_for_fab_fingerprint,
            self.ready_for_fab_target_fingerprint,
            self.human_approval_fingerprint,
            self.manufacturer_profile_fingerprint,
            self.dfm_fingerprint,
        )
        if any(value is None for value in gate_fingerprints) and any(value is not None for value in gate_fingerprints):
            raise ValueError("manufacturing operation G8 gate binding is incomplete")
        return self


class OperationRequest(ExecutionContract):
    operation: Annotated[
        Union[BuildOperation, TestOperation, SimulationOperation, FlashOperation, HardwareBringupOperation, HilOperation, FirmwareSourceWriteOperation, SchematicWriteOperation, SchematicParseOperation, SchematicReadBackOperation, SchematicErcOperation, PCBWriteOperation, KiCadNativeFootprintMoveOperation, PcbDrcOperation, PcbDrcBaselineOperation, KiCadManufacturingOperation, UartObservationOperation],
        Field(discriminator="operation_type"),
    ]


class OperationApprovalGrant(FingerprintedContract):
    grant_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    approval_purpose: ApprovalPurpose
    operation_id: str
    operation_type: OperationType
    operation_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    artifact_id: str | None = None
    artifact_sha256: str | None = None
    reviewer_id: str
    status: ApprovalStatus
    expires_at: datetime
    nonce: str
    issued_at: datetime

    _ids = field_validator("grant_id", "tenant_id", "project_id", "requester_id", "operation_id", "proposal_id", "target_id", "workspace_snapshot_id", "reviewer_id", "nonce", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("operation_fingerprint", "proposal_fingerprint", "target_fingerprint", "workspace_fingerprint", "artifact_sha256", mode="before")(lambda value, info: None if value is None else fingerprint(value, field=info.field_name))
    _optional_artifact = field_validator("artifact_id", mode="before")(lambda value: None if value is None else identifier(value, field="artifact_id"))
    _times = field_validator("expires_at", "issued_at", mode="before")(lambda value, info: utc_datetime(value, field=info.field_name))

    @model_validator(mode="after")
    def _purpose_binding(self):
        expected = approval_purpose_for_operation(self.operation_type)
        if self.approval_purpose is not expected:
            raise ValueError("approval purpose does not match operation type")
        if (self.artifact_id is None) != (self.artifact_sha256 is None):
            raise ValueError("artifact approval binding is incomplete")
        return self


class OperationStatusView(FingerprintedContract):
    operation_id: str
    tenant_id: str
    project_id: str
    operation_type: OperationType
    status: OperationStatus
    approval_id: str | None
    worker_id: str | None
    recovery_state: RecoveryState
    cancellation_requested: bool
    updated_at: datetime
    receipt_fingerprint: str | None = None
    safe_detail: str = ""
    correlation: CorrelationContext | None = None

    _ids = field_validator("operation_id", "tenant_id", "project_id", "worker_id", "approval_id", mode="before")(lambda value, info: None if value is None else identifier(value, field=info.field_name))
    _fps = field_validator("receipt_fingerprint", mode="before")(lambda value: None if value is None else fingerprint(value, field="receipt_fingerprint"))
    _time = field_validator("updated_at", mode="before")(lambda value: utc_datetime(value, field="updated_at"))
    _detail = field_validator("safe_detail", mode="before")(lambda value: safe_text(value, field="safe_detail", maximum=512) if value else "")

    @model_validator(mode="after")
    def _correlation_binding(self):
        if self.correlation is not None and (
            self.correlation.project_id != self.project_id
            or self.correlation.operation_id != self.operation_id
            or self.correlation.approval_id != self.approval_id
        ):
            raise ValueError("operation status correlation binding mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        return _legacy_without_fields(self, "correlation") if self.correlation is None else ()


class OperationEvent(FingerprintedContract):
    """Immutable lifecycle observation for one controlled operation."""

    event_id: str
    sequence: int = Field(ge=1)
    tenant_id: str
    project_id: str
    operation_id: str
    operation_fingerprint: str
    operation_type: OperationType
    event_type: OperationEventType
    status: OperationStatus
    recovery_state: RecoveryState
    approval_id: str | None = None
    receipt_fingerprint: str | None = None
    reason_code: str
    occurred_at: datetime
    correlation: CorrelationContext | None = None

    _ids = field_validator(
        "event_id",
        "tenant_id",
        "project_id",
        "operation_id",
        "approval_id",
        "reason_code",
        mode="before",
    )(
        lambda value, info: None
        if value is None
        else identifier(value, field=info.field_name)
    )
    _fps = field_validator(
        "operation_fingerprint", "receipt_fingerprint", mode="before"
    )(
        lambda value, info: None
        if value is None
        else fingerprint(value, field=info.field_name)
    )
    _time = field_validator("occurred_at", mode="before")(
        lambda value: utc_datetime(value, field="occurred_at")
    )

    @model_validator(mode="after")
    def _correlation_binding(self):
        if self.correlation is not None and (
            self.correlation.project_id != self.project_id
            or self.correlation.operation_id != self.operation_id
            or self.correlation.approval_id != self.approval_id
        ):
            raise ValueError("operation event correlation binding mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        return _legacy_without_fields(self, "correlation") if self.correlation is None else ()


class WorkspaceMutationResult(FingerprintedContract):
    relative_path: str
    content_fingerprint: str
    status: Literal["CREATED"] = "CREATED"
    recovery_state: Literal["AVAILABLE", "RECOVERED", "RECOVERY_REQUIRED"]

    @field_validator("relative_path", mode="before")
    @classmethod
    def _relative_path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("workspace mutation path is invalid")
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/") or ":" in normalized:
            raise ValueError("workspace mutation path is invalid")
        return normalized

    _content_fp = field_validator("content_fingerprint", mode="before")(
        lambda value: fingerprint(value, field="content_fingerprint")
    )


class NativeMoveOfflineResult(ExecutionContract):
    candidate_id: str
    candidate_fingerprint: str
    mapping_review_id: str
    mapping_review_fingerprint: str
    import_revision_id: str
    import_revision_fingerprint: str
    source_set_fingerprint: str
    diff_fingerprint: str
    fake_result_status: Literal["SUCCEEDED", "FAILED", "UNAVAILABLE", "CANCELLED", "TIMED_OUT"]
    workspace_apply_status: Literal["NOT_RUN"] = "NOT_RUN"
    native_read_back_status: Literal["NOT_RUN"] = "NOT_RUN"
    drc_status: Literal["NOT_RUN"] = "NOT_RUN"
    circuit_verified: Literal[False] = False

    _ids = field_validator("candidate_id", "mapping_review_id", "import_revision_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fps = field_validator(
        "candidate_fingerprint", "mapping_review_fingerprint", "import_revision_fingerprint",
        "source_set_fingerprint", "diff_fingerprint", mode="before",
    )(lambda value, info: fingerprint(value, field=info.field_name))


class NativeMoveReadBackV1(FingerprintedContract):
    status: Literal["PASS", "FAIL", "UNAVAILABLE", "UNKNOWN"]
    reader_id: str
    reader_version: str
    executable_sha256: str
    pcb_sha256: str | None = None
    native_uuid: str | None = None
    x_mm: float | None = None
    y_mm: float | None = None
    rotation_degrees: float | None = None
    pad_net_fingerprint: str | None = None
    non_target_summary_fingerprint: str | None = None
    observation_artifact: ArtifactIdentity | None = None

    _ids = field_validator("reader_id", "reader_version", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("executable_sha256", "pcb_sha256", "pad_net_fingerprint", "non_target_summary_fingerprint", mode="before")(
        lambda value, info: None if value is None else fingerprint(value, field=info.field_name)
    )

    @model_validator(mode="after")
    def _observed(self):
        coords = (self.x_mm, self.y_mm, self.rotation_degrees)
        if any(value is not None and (not math.isfinite(value) or abs(value) > 10_000) for value in coords):
            raise ValueError("native read-back placement is invalid")
        if self.status == "PASS" and (
            any(value is None for value in (self.pcb_sha256, self.native_uuid, *coords, self.pad_net_fingerprint, self.non_target_summary_fingerprint, self.observation_artifact))
        ):
            raise ValueError("native read-back PASS requires complete observation")
        return self


class NativeMoveDrcResultV1(FingerprintedContract):
    status: Literal["NO_NEW_KEYS", "NEW_KEYS", "SEVERITY_INCREASED", "COMPARABILITY_UNKNOWN", "NOT_RUN", "FAILED"]
    report: ArtifactIdentity | None = None
    tool_id: str
    tool_version: str
    executable_sha256: str
    comparison_context_fingerprint: str
    baseline_report_sha256: str
    error_count: int | None = Field(default=None, ge=0)
    warning_count: int | None = Field(default=None, ge=0)
    new_key_count: int | None = Field(default=None, ge=0)
    severity_increase_count: int | None = Field(default=None, ge=0)

    _ids = field_validator("tool_id", "tool_version", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("executable_sha256", "comparison_context_fingerprint", "baseline_report_sha256", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))

    @model_validator(mode="after")
    def _comparison(self):
        if self.status == "NO_NEW_KEYS" and (
            self.report is None or self.error_count is None or self.warning_count is None
            or self.new_key_count != 0 or self.severity_increase_count != 0
        ):
            raise ValueError("native DRC comparison is incomplete")
        if self.status == "NOT_RUN" and self.report is not None:
            raise ValueError("unrun native DRC cannot cite a report")
        return self


class NativeMoveProductionResultV1(ExecutionContract):
    schema_version: Literal[1] = 1
    candidate_id: str
    candidate_fingerprint: str
    mapping_review_id: str
    mapping_review_fingerprint: str
    import_revision_id: str
    import_revision_fingerprint: str
    production_context_fingerprint: str
    before_manifest_fingerprint: str
    after_manifest_fingerprint: str | None = None
    workspace_apply_receipt_fingerprint: str | None = None
    workspace_approval_fingerprint: str | None = None
    workspace_apply_status: Literal["NOT_RUN", "APPLIED", "FAILED", "UNKNOWN"] = "NOT_RUN"
    native_read_back: NativeMoveReadBackV1 | None = None
    drc_result: NativeMoveDrcResultV1 | None = None
    recovery_state: RecoveryState = RecoveryState.NONE
    validation_scope: Literal["BASELINE_COMPARISON_ONLY"] = "BASELINE_COMPARISON_ONLY"
    drc_board_pass: Literal[False] = False
    circuit_verified: Literal[False] = False

    _ids = field_validator("candidate_id", "mapping_review_id", "import_revision_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator(
        "candidate_fingerprint", "mapping_review_fingerprint", "import_revision_fingerprint",
        "production_context_fingerprint", "before_manifest_fingerprint", "after_manifest_fingerprint",
        "workspace_apply_receipt_fingerprint", "workspace_approval_fingerprint", mode="before",
    )(lambda value, info: None if value is None else fingerprint(value, field=info.field_name))

    @model_validator(mode="after")
    def _phases(self):
        if self.workspace_apply_status == "APPLIED" and (
            self.workspace_apply_receipt_fingerprint is None or self.workspace_approval_fingerprint is None
        ):
            raise ValueError("native Workspace apply evidence is incomplete")
        if self.workspace_apply_status == "APPLIED" and self.after_manifest_fingerprint is None and (
            self.recovery_state is RecoveryState.NONE
            or (self.native_read_back is not None and self.native_read_back.status == "PASS")
            or (self.drc_result is not None and self.drc_result.status == "NO_NEW_KEYS")
        ):
            raise ValueError("unknown native post-apply manifest requires recovery")
        if self.native_read_back is not None and self.native_read_back.status == "PASS" and self.workspace_apply_status != "APPLIED":
            raise ValueError("native read-back PASS requires applied Workspace")
        if self.drc_result is not None and self.drc_result.status == "NO_NEW_KEYS" and (
            self.native_read_back is None or self.native_read_back.status != "PASS"
        ):
            raise ValueError("native DRC comparison requires independent read-back")
        return self


class OperationReceipt(FingerprintedContract):
    operation_id: str
    tenant_id: str
    project_id: str
    operation_type: OperationType
    status: OperationStatus
    tool_execution_status: Literal["SUCCEEDED", "FAILED", "UNAVAILABLE", "CANCELLED", "TIMED_OUT"]
    flash_verification_status: Literal["NOT_APPLICABLE", "NOT_REPORTED", "VERIFIED", "FAILED"]
    target_runtime_verification_status: Literal["NOT_APPLICABLE", "NOT_VERIFIED"]
    artifact: ArtifactIdentity | None = None
    evidence_references: tuple[str, ...] = ()
    safe_diagnostics_summary: str = ""
    recovery_state: RecoveryState = RecoveryState.NONE
    workspace_mutations: tuple[WorkspaceMutationResult, ...] = ()
    workspace_apply_receipt_fingerprint: str | None = None
    firmware_evidence_parse: Literal["NOT_APPLICABLE", "UNAVAILABLE"] = "NOT_APPLICABLE"
    schematic_erc_status: Literal["ERC_PASS", "ERC_FAIL", "ERC_WARNINGS", "ERC_BLOCKED", "ERC_UNAVAILABLE"] | None = None
    schematic_erc_result_fingerprint: str | None = None
    schematic_erc_result: SchematicErcResult | None = None
    pcb_artifact: PCBArtifactRecord | None = None
    pcb_drc_result: PCBDrcResult | None = None
    build_result: FirmwareBuildResult | None = None
    build_evidence: BuildEvidence | None = None
    native_move_offline: NativeMoveOfflineResult | None = None
    native_move_production: NativeMoveProductionResultV1 | None = Field(default=None, exclude_if=lambda value: value is None)
    schematic_parse_result: SchematicXmlResult | None = Field(default=None, exclude_if=lambda value: value is None)
    schematic_readback_result: SchematicXmlResult | None = Field(default=None, exclude_if=lambda value: value is None)
    created_at: datetime
    correlation: CorrelationContext | None = None

    _ids = field_validator("operation_id", "tenant_id", "project_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _refs = field_validator("evidence_references", mode="before")(lambda value: tuple(identifier(item, field="evidence_reference") for item in value) if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("evidence_references is invalid")))
    _detail = field_validator("safe_diagnostics_summary", mode="before")(lambda value: safe_text(value, field="safe_diagnostics_summary", maximum=1024) if value else "")
    _time = field_validator("created_at", mode="before")(lambda value: utc_datetime(value, field="created_at"))

    @model_validator(mode="after")
    def _correlation_binding(self):
        if self.correlation is not None and (
            self.correlation.project_id != self.project_id
            or self.correlation.operation_id != self.operation_id
        ):
            raise ValueError("operation receipt correlation binding mismatch")
        return self

    @field_validator("workspace_mutations", mode="before")
    @classmethod
    def _workspace_mutations(cls, value: object) -> tuple[WorkspaceMutationResult, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("workspace_mutations is invalid")
        return tuple(
            WorkspaceMutationResult.model_validate(
                item.model_dump(mode="python") if isinstance(item, WorkspaceMutationResult) else item,
                strict=False,
            )
            for item in value
        )

    _workspace_receipt_fp = field_validator("workspace_apply_receipt_fingerprint", mode="before")(
        lambda value: None if value is None else fingerprint(value, field="workspace_apply_receipt_fingerprint")
    )
    _schematic_erc_fp = field_validator("schematic_erc_result_fingerprint", mode="before")(
        lambda value: None if value is None else fingerprint(value, field="schematic_erc_result_fingerprint")
    )

    _pcb_artifact = field_validator("pcb_artifact", mode="before")(
        lambda value: None if value is None else PCBArtifactRecord.model_validate(value.model_dump(mode="python") if isinstance(value, PCBArtifactRecord) else value, strict=False)
    )
    _pcb_drc = field_validator("pcb_drc_result", mode="before")(
        lambda value: None if value is None else PCBDrcResult.model_validate(value.model_dump(mode="python") if isinstance(value, PCBDrcResult) else value, strict=False)
    )
    _build_result = field_validator("build_result", mode="before")(
        lambda value: None if value is None else FirmwareBuildResult.model_validate(
            value.model_dump(mode="python") if isinstance(value, FirmwareBuildResult) else value,
            strict=False,
        )
    )
    _build_evidence = field_validator("build_evidence", mode="before")(
        lambda value: None if value is None else BuildEvidence.model_validate(
            value.model_dump(mode="python") if isinstance(value, BuildEvidence) else value,
            strict=False,
        )
    )
    _native_move_offline = field_validator("native_move_offline", mode="before")(
        lambda value: None if value is None else NativeMoveOfflineResult.model_validate(
            value.model_dump(mode="python") if isinstance(value, NativeMoveOfflineResult) else value,
            strict=False,
        )
    )
    _native_move_production = field_validator("native_move_production", mode="before")(
        lambda value: None if value is None else NativeMoveProductionResultV1.model_validate(
            value.model_dump(mode="python") if isinstance(value, NativeMoveProductionResultV1) else value,
            strict=False,
        )
    )

    @model_validator(mode="after")
    def _workspace_result_binding(self):
        xml_result = self.schematic_parse_result or self.schematic_readback_result
        expected_field = {
            OperationType.KICAD_SCHEMATIC_PARSE: self.schematic_parse_result,
            OperationType.KICAD_SCHEMATIC_READ_BACK: self.schematic_readback_result,
        }
        if self.operation_type in expected_field:
            if xml_result is None or expected_field[self.operation_type] != xml_result or (self.schematic_parse_result is not None and self.schematic_readback_result is not None):
                raise ValueError("schematic XML receipt requires operation-specific result")
            if xml_result.operation_id != self.operation_id or xml_result.netlist_artifact.project_id != self.project_id:
                raise ValueError("schematic XML receipt identity mismatch")
            if self.status is OperationStatus.SUCCEEDED and (xml_result.parse_status != "PASS" or self.tool_execution_status != "SUCCEEDED"):
                raise ValueError("schematic XML receipt success mismatch")
        elif xml_result is not None:
            raise ValueError("schematic XML evidence is operation-specific")
        if self.operation_type is OperationType.KICAD_NATIVE_FOOTPRINT_MOVE:
            if (self.native_move_offline is None) == (self.native_move_production is None):
                raise ValueError("exactly one native move result is required")
            if self.native_move_offline is not None:
                if self.native_move_offline.fake_result_status != self.tool_execution_status:
                    raise ValueError("native move offline status mismatch")
                if self.workspace_mutations or self.workspace_apply_receipt_fingerprint is not None or self.evidence_references:
                    raise ValueError("native move offline receipt cannot contain production evidence")
            else:
                production = self.native_move_production
                assert production is not None
                if production.recovery_state != self.recovery_state or production.workspace_apply_receipt_fingerprint != self.workspace_apply_receipt_fingerprint:
                    raise ValueError("native move production recovery/apply mismatch")
                artifacts = (
                    None if production.native_read_back is None else production.native_read_back.observation_artifact,
                    None if production.drc_result is None else production.drc_result.report,
                )
                if any(artifact is not None and (
                    artifact.tenant_id != self.tenant_id or artifact.project_id != self.project_id
                    or artifact.build_operation_id != self.operation_id
                ) for artifact in artifacts):
                    raise ValueError("native move observation/report identity mismatch")
                if self.status is OperationStatus.SUCCEEDED and (
                    self.recovery_state is not RecoveryState.NONE
                    or self.tool_execution_status != "SUCCEEDED"
                    or production.workspace_apply_status != "APPLIED"
                    or production.native_read_back is None or production.native_read_back.status != "PASS"
                    or production.drc_result is None or production.drc_result.status != "NO_NEW_KEYS"
                ):
                    raise ValueError("native move limited success evidence is incomplete")
        elif self.native_move_offline is not None or self.native_move_production is not None:
            raise ValueError("native move evidence is operation-specific")
        if self.operation_type in {OperationType.FIRMWARE_SOURCE_WRITE, OperationType.KICAD_SCHEMATIC_WRITE, OperationType.KICAD_PCB_WRITE}:
            if self.firmware_evidence_parse != "UNAVAILABLE":
                if self.operation_type is OperationType.FIRMWARE_SOURCE_WRITE:
                    raise ValueError("firmware evidence parse must be unavailable")
            if self.status is OperationStatus.SUCCEEDED and (
                not self.workspace_mutations or self.workspace_apply_receipt_fingerprint is None
            ):
                raise ValueError("workspace write receipt requires workspace mutation evidence")
        elif self.operation_type is not OperationType.KICAD_NATIVE_FOOTPRINT_MOVE and (self.workspace_mutations or self.workspace_apply_receipt_fingerprint is not None):
            raise ValueError("workspace mutation evidence is operation-specific")
        if self.operation_type is OperationType.KICAD_SCHEMATIC_ERC and (
            self.schematic_erc_status is None
            or self.schematic_erc_result_fingerprint is None
            or self.schematic_erc_result is None
        ):
            raise ValueError("schematic ERC receipt requires typed result")
        if self.operation_type is OperationType.KICAD_SCHEMATIC_ERC:
            result = self.schematic_erc_result
            if (
                result is None
                or result.operation_id != self.operation_id
                or result.fingerprint != self.schematic_erc_result_fingerprint
                or result.status.value != self.schematic_erc_status
            ):
                raise ValueError("schematic ERC result binding mismatch")
        if self.operation_type is not OperationType.KICAD_SCHEMATIC_ERC and (
            self.schematic_erc_status is not None
            or self.schematic_erc_result_fingerprint is not None
            or self.schematic_erc_result is not None
        ):
            raise ValueError("schematic ERC evidence is operation-specific")
        if self.operation_type is OperationType.KICAD_PCB_WRITE:
            if self.status is OperationStatus.SUCCEEDED and (not self.workspace_mutations or self.workspace_apply_receipt_fingerprint is None or self.pcb_artifact is None):
                raise ValueError("PCB write receipt requires workspace and artifact evidence")
            if self.pcb_drc_result is not None:
                raise ValueError("PCB DRC evidence is operation-specific")
        elif self.operation_type is OperationType.KICAD_PCB_DRC:
            if self.pcb_drc_result is None or self.pcb_drc_result.operation_id != self.operation_id:
                raise ValueError("PCB DRC receipt requires typed result")
            if self.pcb_artifact is not None or self.workspace_mutations or self.workspace_apply_receipt_fingerprint is not None:
                raise ValueError("PCB DRC must be read-only")
        elif self.operation_type is OperationType.KICAD_PCB_DRC_BASELINE:
            if self.pcb_artifact is not None or self.pcb_drc_result is not None or self.workspace_mutations or self.workspace_apply_receipt_fingerprint is not None:
                raise ValueError("DRC baseline must remain an observation")
            if self.status is OperationStatus.SUCCEEDED and (self.artifact is None or self.artifact.artifact_type != "LOG" or not self.evidence_references):
                raise ValueError("DRC baseline context evidence is unavailable")
        elif self.pcb_artifact is not None or self.pcb_drc_result is not None:
            raise ValueError("PCB evidence is operation-specific")
        if self.operation_type is OperationType.BUILD:
            if self.build_evidence is not None and self.build_evidence.operation_id != self.operation_id:
                raise ValueError("build evidence operation binding mismatch")
        elif self.build_result is not None or self.build_evidence is not None:
            raise ValueError("build evidence is operation-specific")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        if self.native_move_offline is not None:
            return ()
        candidates: list[str] = []
        variants = [payload, {key: value for key, value in payload.items() if key != "native_move_offline"}]
        if self.correlation is None:
            variants.extend({key: value for key, value in material.items() if key != "correlation"} for material in tuple(variants))
        for material in variants:
            encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        result = self.pcb_drc_result or self.schematic_erc_result
        if result is None or result.fingerprint not in result._legacy_fingerprint_candidates():
            return tuple(candidates)
        nested_field = "pcb_drc_result" if self.pcb_drc_result is not None else "schematic_erc_result"
        nested_fields = (
            (
                "project_id", "conversation_id", "kicad_cli_path", "command_identity",
                "raw_report_fingerprint", "tool_execution_status", "engineering_status",
                "parse_validation", "issues",
            )
            if nested_field == "pcb_drc_result"
            else (
                "issues", "correlations", "raw_evidence", "raw_evidence_fingerprint",
                "command_identity", "engineering_status",
            )
        )
        for field in nested_fields:
            payload[nested_field].pop(field, None)
        for material in variants:
            encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        return tuple(candidates)


class CancellationRequest(FingerprintedContract):
    request_id: str
    tenant_id: str
    project_id: str
    operation_id: str
    requester_id: str
    reason: str
    requested_at: datetime

    _ids = field_validator("request_id", "tenant_id", "project_id", "operation_id", "requester_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _reason = field_validator("reason", mode="before")(lambda value: safe_text(value, field="reason", maximum=256))
    _time = field_validator("requested_at", mode="before")(lambda value: utc_datetime(value, field="requested_at"))


class RecoveryDecision(FingerprintedContract):
    decision_id: str
    tenant_id: str
    project_id: str
    operation_id: str
    reviewer_id: str
    decision: Literal["MARK_FAILED", "MARK_TARGET_SAFE", "REQUIRES_MANUAL_INTERVENTION"]
    decided_at: datetime

    _ids = field_validator("decision_id", "tenant_id", "project_id", "operation_id", "reviewer_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _time = field_validator("decided_at", mode="before")(lambda value: utc_datetime(value, field="decided_at"))


__all__ = tuple(name for name in globals() if not name.startswith("_") and name not in {"Annotated", "Union", "Literal"})
