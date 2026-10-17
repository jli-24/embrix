from __future__ import annotations

import copy
import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    confidence,
    finalize,
    fp,
    ident,
    text,
    tuple_values,
)
from embedded_copilot.workspace_runtime import FrozenWorkspaceSnapshot

from .contracts import ExecutionCapability, ExecutionRequest

_CONFIG_PATH = re.compile(r"^[^\x00\r\n]{1,512}$")


class ExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    REJECTED = "REJECTED"


class DebugSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


def _configured_path(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    candidate = value.strip()
    if not _CONFIG_PATH.fullmatch(candidate):
        raise ValueError(f"{field} is invalid")
    return candidate


def _relative_path(value: object, *, field: str) -> str:
    candidate = _configured_path(value, field=field).replace("\\", "/")
    path = PurePosixPath(candidate)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{field} is invalid")
    return path.as_posix()


class ToolConfiguration(Fingerprinted):
    configuration_id: str
    project_id: str
    workspace_id: str
    target_id: str

    @field_validator(
        "configuration_id", "project_id", "workspace_id", "target_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @classmethod
    def create(cls, **values: object):
        return finalize(cls, values)


class ESPIDFToolConfiguration(ToolConfiguration):
    idf_executable: str
    esptool_executable: str
    artifact_relative_path: str
    chip: str = "esp32"
    flash_offset: str = "0x10000"
    serial_port: str
    baud_rate: int = 115200

    @field_validator("idf_executable", "esptool_executable", mode="before")
    @classmethod
    def _executables(cls, value: object, info) -> str:
        return _configured_path(value, field=info.field_name)

    @field_validator("artifact_relative_path", mode="before")
    @classmethod
    def _artifact_path(cls, value: object) -> str:
        return _relative_path(value, field="artifact_relative_path")

    @field_validator("chip", "flash_offset", "serial_port", mode="before")
    @classmethod
    def _settings(cls, value: object, info) -> str:
        return _configured_path(value, field=info.field_name)

    @field_validator("baud_rate", mode="before")
    @classmethod
    def _baud_rate(cls, value: object) -> int:
        if type(value) is not int or not 1200 <= value <= 4_000_000:
            raise ValueError("baud_rate is invalid")
        return value


class SerialToolConfiguration(ToolConfiguration):
    serial_port: str
    baud_rate: int = 115200
    read_timeout_seconds: float = 0.1
    max_lines: int = 32

    @field_validator("serial_port", mode="before")
    @classmethod
    def _serial_port(cls, value: object) -> str:
        return _configured_path(value, field="serial_port")

    @field_validator("baud_rate", mode="before")
    @classmethod
    def _baud_rate(cls, value: object) -> int:
        if type(value) is not int or not 1200 <= value <= 4_000_000:
            raise ValueError("baud_rate is invalid")
        return value

    @field_validator("read_timeout_seconds", mode="before")
    @classmethod
    def _read_timeout(cls, value: object) -> float:
        if type(value) not in (int, float) or not 0.01 <= value <= 5.0:
            raise ValueError("read_timeout_seconds is invalid")
        return float(value)

    @field_validator("max_lines", mode="before")
    @classmethod
    def _max_lines(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 128:
            raise ValueError("max_lines is invalid")
        return value


class BuildArtifactReference(Fingerprinted):
    artifact_id: str
    project_id: str
    workspace_id: str
    target_id: str
    firmware_fingerprint: str
    size_bytes: int

    @field_validator(
        "artifact_id", "project_id", "workspace_id", "target_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("firmware_fingerprint", mode="before")
    @classmethod
    def _firmware_fingerprint(cls, value: object) -> str:
        return fp(value, "firmware_fingerprint")

    @field_validator("size_bytes", mode="before")
    @classmethod
    def _size(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 64 * 1024 * 1024:
            raise ValueError("size_bytes is invalid")
        return value

    @classmethod
    def from_bytes(
        cls,
        *,
        project_id: str,
        workspace_id: str,
        target_id: str,
        content: bytes,
    ) -> BuildArtifactReference:
        digest = hashlib.sha256(content).hexdigest()
        return cls.create(
            artifact_id=f"artifact-{digest[:32]}",
            project_id=project_id,
            workspace_id=workspace_id,
            target_id=target_id,
            firmware_fingerprint="sha256:" + digest,
            size_bytes=len(content),
        )

    @classmethod
    def create(cls, **values: object):
        return finalize(cls, values)


class BuildRequest(Fingerprinted):
    request: ExecutionRequest
    workspace_snapshot: FrozenWorkspaceSnapshot

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> ExecutionRequest:
        return ExecutionRequest.model_validate(copy.deepcopy(value))

    @field_validator("workspace_snapshot", mode="before")
    @classmethod
    def _snapshot(cls, value: object) -> FrozenWorkspaceSnapshot:
        return FrozenWorkspaceSnapshot.model_validate(copy.deepcopy(value))

    @model_validator(mode="after")
    def _bound(self) -> BuildRequest:
        if self.request.capability is not ExecutionCapability.BUILD:
            raise ValueError("build request capability is invalid")
        return self

    @classmethod
    def create(cls, **values: object):
        return finalize(cls, values)


class FlashRequest(Fingerprinted):
    request: ExecutionRequest
    workspace_snapshot: FrozenWorkspaceSnapshot
    artifact: BuildArtifactReference

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> ExecutionRequest:
        return ExecutionRequest.model_validate(copy.deepcopy(value))

    @field_validator("workspace_snapshot", mode="before")
    @classmethod
    def _snapshot(cls, value: object) -> FrozenWorkspaceSnapshot:
        return FrozenWorkspaceSnapshot.model_validate(copy.deepcopy(value))

    @field_validator("artifact", mode="before")
    @classmethod
    def _artifact(cls, value: object) -> BuildArtifactReference:
        return BuildArtifactReference.model_validate(copy.deepcopy(value))

    @model_validator(mode="after")
    def _bound(self) -> FlashRequest:
        if self.request.capability is not ExecutionCapability.FLASH:
            raise ValueError("flash request capability is invalid")
        if self.request.project_id != self.artifact.project_id:
            raise ValueError("flash artifact project mismatch")
        if self.workspace_snapshot.workspace_id != self.artifact.workspace_id:
            raise ValueError("flash artifact workspace mismatch")
        if self.request.target_id != self.artifact.target_id:
            raise ValueError("flash artifact target mismatch")
        return self

    @classmethod
    def create(cls, **values: object):
        return finalize(cls, values)


class DebugRequest(Fingerprinted):
    request: ExecutionRequest

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> ExecutionRequest:
        return ExecutionRequest.model_validate(copy.deepcopy(value))

    @model_validator(mode="after")
    def _bound(self) -> DebugRequest:
        if self.request.capability is not ExecutionCapability.DEBUG_LOGS:
            raise ValueError("debug request capability is invalid")
        return self

    @classmethod
    def create(cls, **values: object):
        return finalize(cls, values)


class BuildResult(Fingerprinted):
    request_id: str
    status: ExecutionStatus
    exit_code: int | None = None
    artifact: BuildArtifactReference | None = None
    logs_summary: str

    @field_validator("request_id", mode="before")
    @classmethod
    def _request_id(cls, value: object) -> str:
        return ident(value, "request_id")

    @field_validator("logs_summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return text(value, "logs_summary", 512)

    @model_validator(mode="after")
    def _result(self) -> BuildResult:
        if self.status is ExecutionStatus.SUCCEEDED:
            if self.exit_code != 0 or self.artifact is None:
                raise ValueError("successful build result is invalid")
        elif self.artifact is not None:
            raise ValueError("failed build cannot expose an artifact")
        return self

    @classmethod
    def create(cls, **values: object):
        return finalize(cls, values)


class FlashResult(Fingerprinted):
    request_id: str
    status: ExecutionStatus
    exit_code: int | None = None
    firmware_fingerprint: str
    logs_summary: str

    @field_validator("request_id", mode="before")
    @classmethod
    def _request_id(cls, value: object) -> str:
        return ident(value, "request_id")

    @field_validator("firmware_fingerprint", mode="before")
    @classmethod
    def _firmware_fingerprint(cls, value: object) -> str:
        return fp(value, "firmware_fingerprint")

    @field_validator("logs_summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return text(value, "logs_summary", 512)

    @model_validator(mode="after")
    def _result(self) -> FlashResult:
        if self.status is ExecutionStatus.SUCCEEDED and self.exit_code != 0:
            raise ValueError("successful flash result is invalid")
        return self

    @classmethod
    def create(cls, **values: object):
        return finalize(cls, values)


class DebugObservation(Fingerprinted):
    observation_id: str
    request_id: str
    project_id: str
    target_id: str
    timestamp: datetime
    source: str = "UART"
    severity: DebugSeverity
    log_lines: tuple[str, ...]
    confidence: float

    @field_validator(
        "observation_id", "request_id", "project_id", "target_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return ident(value, "source")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("timestamp is invalid")
        return value.astimezone(UTC)

    @field_validator("log_lines", mode="before")
    @classmethod
    def _lines(cls, value: object) -> tuple[str, ...]:
        lines = tuple_values(value, "log_lines")
        if not 1 <= len(lines) <= 128:
            raise ValueError("log_lines are invalid")
        return tuple(text(line, "log_line", 256) for line in lines)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        return confidence(value)

    @classmethod
    def create(cls, **values: object):
        return finalize(cls, values)


__all__ = (
    "BuildArtifactReference",
    "BuildRequest",
    "BuildResult",
    "DebugObservation",
    "DebugRequest",
    "DebugSeverity",
    "ESPIDFToolConfiguration",
    "ExecutionStatus",
    "FlashRequest",
    "FlashResult",
    "SerialToolConfiguration",
    "ToolConfiguration",
)
