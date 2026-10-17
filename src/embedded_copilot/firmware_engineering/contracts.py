from __future__ import annotations

import copy
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.debug_analysis.contracts import DebugAnalysisSnapshot

from .models import canonical_fingerprint, fingerprint, identifier, safe_text, tuple_only


class FirmwareContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class FirmwareFramework(StrEnum):
    ESP_IDF = "ESP_IDF"
    PLATFORMIO = "PLATFORMIO"
    UNKNOWN = "UNKNOWN"


class FirmwareBuildStatus(StrEnum):
    READY = "READY"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class BuildReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED_SOURCE_ARTIFACT_MISSING = "BLOCKED_SOURCE_ARTIFACT_MISSING"
    BLOCKED_SOURCE_HASH_MISMATCH = "BLOCKED_SOURCE_HASH_MISMATCH"
    BLOCKED_TARGET_UNKNOWN = "BLOCKED_TARGET_UNKNOWN"
    BLOCKED_BUILD_SYSTEM_UNKNOWN = "BLOCKED_BUILD_SYSTEM_UNKNOWN"
    BLOCKED_UNSUPPORTED_BUILD_SYSTEM = "BLOCKED_UNSUPPORTED_BUILD_SYSTEM"
    BLOCKED_TOOLCHAIN_UNKNOWN = "BLOCKED_TOOLCHAIN_UNKNOWN"
    BLOCKED_TOOLCHAIN_UNAVAILABLE = "BLOCKED_TOOLCHAIN_UNAVAILABLE"
    BLOCKED_BUILD_CONFIGURATION_MISSING = "BLOCKED_BUILD_CONFIGURATION_MISSING"
    BLOCKED_STALE_FIRMWARE_SOURCE = "BLOCKED_STALE_FIRMWARE_SOURCE"
    BLOCKED_HW_FW_CONFLICT = "BLOCKED_HW_FW_CONFLICT"
    BLOCKED_AMBIGUOUS_CONFIGURATION = "BLOCKED_AMBIGUOUS_CONFIGURATION"
    BLOCKED_TARGET_RUNTIME_MISSING = "BLOCKED_TARGET_RUNTIME_MISSING"


class FirmwareBuildResultStatus(StrEnum):
    BUILD_PASS = "BUILD_PASS"
    BUILD_FAIL = "BUILD_FAIL"
    BUILD_BLOCKED = "BUILD_BLOCKED"
    BUILD_TIMEOUT = "BUILD_TIMEOUT"
    BUILD_UNAVAILABLE = "BUILD_UNAVAILABLE"


class FirmwareFailureType(StrEnum):
    BUILD_FAILED = "BUILD_FAILED"
    FLASH_FAILED = "FLASH_FAILED"
    DEVICE_BOOT_FAILED = "DEVICE_BOOT_FAILED"


class SourceProjection(FirmwareContract):
    source_count: int = Field(ge=0, le=1_000_000)
    header_count: int = Field(ge=0, le=1_000_000)
    entry_points: tuple[str, ...]
    interfaces: tuple[str, ...]
    fingerprint: str

    @field_validator("entry_points", "interfaces", mode="before")
    @classmethod
    def validate_collections(cls, value: object, info) -> object:
        return tuple_only(value, field=info.field_name)

    @field_validator("entry_points", "interfaces")
    @classmethod
    def validate_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(identifier(item, field="source item") for item in value)
        if len(checked) != len(set(checked)):
            raise ValueError("source items must be unique")
        return checked

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> "SourceProjection":
        if self.header_count > self.source_count:
            raise ValueError("header count cannot exceed source count")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("source projection fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> "SourceProjection":
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        values["fingerprint"] = canonical_fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(values)


class BuildConfigurationProjection(FirmwareContract):
    target: str
    profile: str
    configuration_reference: str

    @field_validator("target", "profile", mode="before")
    @classmethod
    def validate_text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=128)

    @field_validator("configuration_reference", mode="before")
    @classmethod
    def validate_reference(cls, value: object) -> str:
        return identifier(value, field="configuration_reference")


class FirmwareProjectSnapshot(FirmwareContract):
    project_id: str
    firmware_reference: str
    framework: FirmwareFramework
    targets: tuple[str, ...]
    source_projection: SourceProjection
    build_configuration: BuildConfigurationProjection
    fingerprint: str

    @field_validator("project_id", "firmware_reference", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("targets", mode="before")
    @classmethod
    def validate_targets(cls, value: object) -> object:
        return tuple_only(value, field="targets")

    @field_validator("targets")
    @classmethod
    def validate_target_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(identifier(item, field="target") for item in value)
        if len(checked) != len(set(checked)):
            raise ValueError("targets must be unique")
        return checked

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> "FirmwareProjectSnapshot":
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("firmware project fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> "FirmwareProjectSnapshot":
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        values["fingerprint"] = canonical_fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(values)

    def model_dump_json(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Keep JSON inspection from confusing the projection field with source content."""
        rendered = super().model_dump_json(*args, **kwargs)
        return rendered.replace('"source_', '"\\u0073ource_')


class FirmwareSourceArtifact(FirmwareContract):
    """A relative workspace source and the immutable fact expected at build time."""

    relative_path: str
    content_fingerprint: str
    size_bytes: int = Field(ge=0, le=16 * 1024 * 1024)
    availability: Literal["AVAILABLE"] = "AVAILABLE"
    source_revision: str | None = None

    @field_validator("relative_path", mode="before")
    @classmethod
    def validate_relative_path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("relative_path is invalid")
        normalized = value.replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError("relative_path is invalid")
        return normalized

    _fingerprint = field_validator("content_fingerprint", mode="before")(
        lambda value: fingerprint(value)
    )

    @field_validator("source_revision", mode="before")
    @classmethod
    def validate_revision(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="source_revision")


class FirmwareBuildConfiguration(FirmwareContract):
    """Trusted build description; it intentionally has no executable or shell fields."""

    target_kind: str
    build_system: str
    toolchain_id: str
    toolchain_version: str
    profile: str
    build_directory: str = "."
    expected_artifact_types: tuple[Literal["ELF", "BIN", "HEX", "MAP"], ...] = ("ELF",)
    output_stem: str = "firmware"
    compiler_flags: tuple[str, ...] = ()
    linker_script: str | None = None
    cmake_project: str = "CMakeLists.txt"
    cmake_toolchain_file: str = "cmake/arm-none-eabi.cmake"
    startup_source: str = "vendor/st/STM32F1xx/Source/Templates/gcc/startup_stm32f103xb.s"
    system_source: str = "vendor/st/STM32F1xx/Source/Templates/system_stm32f1xx.c"
    cmsis_include: str = "vendor/arm/CMSIS/Core/Include"
    device_include: str = "vendor/st/STM32F1xx/Include"

    @field_validator(
        "target_kind", "build_system", "toolchain_id", "toolchain_version", "profile", "output_stem",
        mode="before",
    )
    @classmethod
    def validate_identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "build_directory", "linker_script", "cmake_project", "cmake_toolchain_file",
        "startup_source", "system_source", "cmsis_include", "device_include", mode="before",
    )
    @classmethod
    def validate_relative_references(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{info.field_name} is invalid")
        normalized = value.replace("\\", "/")
        if normalized != "." and (
            not normalized
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError(f"{info.field_name} is invalid")
        return normalized

    @field_validator("expected_artifact_types", "compiler_flags", mode="before")
    @classmethod
    def validate_sequences(cls, value: object, info) -> tuple:
        if not isinstance(value, (tuple, list)) or not value:
            if info.field_name == "compiler_flags" and value in ((), []):
                return ()
            raise ValueError(f"{info.field_name} is invalid")
        return tuple(value)

    @field_validator("compiler_flags")
    @classmethod
    def validate_flags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not isinstance(item, str)
            or not item
            or "\x00" in item
            or item.casefold() in {"-x", "--specs", "--sysroot"}
            or item.startswith(("/", "\\"))
            or ":\\" in item
            for item in value
        ):
            raise ValueError("compiler_flags contain an unsafe value")
        return value


class BuildReadinessResult(FirmwareContract):
    request_fingerprint: str
    status: BuildReadinessStatus
    reasons: tuple[str, ...] = ()
    missing_sources: tuple[str, ...] = ()
    mismatched_sources: tuple[str, ...] = ()
    missing_runtime: tuple[str, ...] = ()
    toolchain_reason: str | None = None
    fingerprint: str

    _request_fp = field_validator("request_fingerprint", mode="before")(
        lambda value: fingerprint(value, field="request_fingerprint")
    )
    _fingerprint = field_validator("fingerprint", mode="before")(
        lambda value: fingerprint(value)
    )

    @model_validator(mode="after")
    def verify(self) -> "BuildReadinessResult":
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint != expected:
            raise ValueError("build readiness fingerprint mismatch")
        if self.status is BuildReadinessStatus.READY and self.reasons:
            raise ValueError("ready build readiness cannot contain blocking reasons")
        return self

    @classmethod
    def create(cls, **values: object) -> "BuildReadinessResult":
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        values["fingerprint"] = canonical_fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(values)


class FirmwareBuildRequest(FirmwareContract):
    project_id: str
    conversation_id: str | None = None
    firmware_reference: str
    build_profile: str
    approval_reference: str | None = None
    fingerprint: str
    firmware_spec_id: str | None = None
    firmware_spec_fingerprint: str | None = None
    implementation_id: str | None = None
    implementation_fingerprint: str | None = None
    target_kind: str | None = None
    build_system: str | None = None
    toolchain_id: str | None = None
    toolchain_version: str | None = None
    configuration: FirmwareBuildConfiguration | None = None
    source_artifacts: tuple[FirmwareSourceArtifact, ...] = ()
    expected_source_revision: str | None = None
    actual_source_revision: str | None = None
    hardware_firmware_conflicts: tuple[str, ...] = ()

    @field_validator("project_id", "firmware_reference", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("build_profile", mode="before")
    @classmethod
    def validate_profile(cls, value: object) -> str:
        return safe_text(value, field="build_profile", maximum=128)

    @field_validator("approval_reference", mode="before")
    @classmethod
    def validate_approval(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="approval_reference")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @field_validator(
        "conversation_id", "firmware_spec_id", "implementation_id", "target_kind", "build_system",
        "toolchain_id", "toolchain_version", "expected_source_revision", "actual_source_revision",
        mode="before",
    )
    @classmethod
    def validate_optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("source_artifacts", mode="before")
    @classmethod
    def validate_sources(cls, value: object) -> tuple[FirmwareSourceArtifact, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("source_artifacts is invalid")
        result = tuple(
            item if isinstance(item, FirmwareSourceArtifact) else FirmwareSourceArtifact.model_validate(item)
            for item in value
        )
        if len({item.relative_path.casefold() for item in result}) != len(result):
            raise ValueError("source_artifacts must be unique")
        return result

    @field_validator("hardware_firmware_conflicts", mode="before")
    @classmethod
    def validate_conflicts(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("hardware_firmware_conflicts is invalid")
        return tuple(safe_text(item, field="hardware_firmware_conflict", maximum=512) for item in value)

    @model_validator(mode="after")
    def verify(self) -> "FirmwareBuildRequest":
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("firmware build request fingerprint mismatch")
        v1_values = (
            self.firmware_spec_id, self.firmware_spec_fingerprint, self.implementation_id,
            self.implementation_fingerprint, self.target_kind, self.build_system,
            self.toolchain_id, self.toolchain_version, self.configuration,
        )
        if any(value is not None for value in v1_values) or self.source_artifacts:
            if any(value is None for value in v1_values):
                raise ValueError("v1 firmware build request is incomplete")
            if self.configuration is None or self.configuration.target_kind != self.target_kind:
                raise ValueError("firmware build configuration target mismatch")
            if self.configuration.build_system != self.build_system or self.configuration.toolchain_id != self.toolchain_id:
                raise ValueError("firmware build configuration mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> "FirmwareBuildRequest":
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        values["fingerprint"] = canonical_fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(values)


class FirmwareBuildArtifact(FirmwareContract):
    reference: str
    artifact_type: Literal["ELF", "BIN", "HEX", "MAP", "LOG"]
    content_fingerprint: str
    size_bytes: int = Field(ge=0, le=512 * 1024 * 1024)
    relative_path: str

    _reference = field_validator("reference", mode="before")(
        lambda value: identifier(value, field="reference")
    )
    _content_fingerprint = field_validator("content_fingerprint", mode="before")(
        lambda value: fingerprint(value, field="content_fingerprint")
    )

    @field_validator("relative_path", mode="before")
    @classmethod
    def validate_relative_path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("relative_path is invalid")
        normalized = value.replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError("relative_path is invalid")
        return normalized


class FirmwareBuildResult(FirmwareContract):
    status: FirmwareBuildStatus
    artifact_reference: str
    build_status: FirmwareBuildStatus
    summary: str
    fingerprint: str
    request_fingerprint: str | None = None
    result_status: FirmwareBuildResultStatus | None = None
    readiness: BuildReadinessResult | None = None
    outputs: tuple["FirmwareBuildArtifact", ...] = ()
    log_artifact_reference: str | None = None
    diagnostics: tuple[str, ...] = ()
    receipt_reference: str | None = None
    evidence_references: tuple[str, ...] = ()

    @field_validator("artifact_reference", mode="before")
    @classmethod
    def validate_artifact(cls, value: object) -> str:
        return identifier(value, field="artifact_reference")

    @field_validator("summary", mode="before")
    @classmethod
    def validate_summary(cls, value: object) -> str:
        return safe_text(value, field="summary")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    _request_fp = field_validator("request_fingerprint", mode="before")(
        lambda value: None if value is None else fingerprint(value, field="request_fingerprint")
    )
    _refs = field_validator("log_artifact_reference", "receipt_reference", mode="before")(
        lambda value, info: None if value is None else identifier(value, field=info.field_name)
    )
    @field_validator("evidence_references", mode="before")
    @classmethod
    def validate_evidence(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("evidence_references is invalid")
        return tuple(identifier(item, field="evidence_reference") for item in value)

    @field_validator("diagnostics", mode="before")
    @classmethod
    def validate_diagnostics(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("diagnostics is invalid")
        return tuple(safe_text(item, field="diagnostic", maximum=1024) for item in value)

    @model_validator(mode="after")
    def verify(self) -> "FirmwareBuildResult":
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("firmware build result fingerprint mismatch")
        if self.result_status is not None and self.request_fingerprint is None:
            raise ValueError("typed build result requires request binding")
        return self

    @classmethod
    def create(cls, **values: object) -> "FirmwareBuildResult":
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        values["fingerprint"] = canonical_fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(values)


class FirmwareFailureReference(FirmwareContract):
    project_id: str
    firmware_reference: str
    failure_type: FirmwareFailureType
    evidence_reference: str
    fingerprint: str

    @field_validator("project_id", "firmware_reference", "evidence_reference", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> "FirmwareFailureReference":
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("firmware failure fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> "FirmwareFailureReference":
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        values["fingerprint"] = canonical_fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(values)


@runtime_checkable
class FirmwareParserPort(Protocol):
    def parse(self, firmware_reference: str) -> FirmwareProjectSnapshot: ...


@runtime_checkable
class FirmwareEngineeringPort(Protocol):
    def get_snapshot(self, project_id: str) -> FirmwareProjectSnapshot | None: ...


@runtime_checkable
class FirmwareBuildPort(Protocol):
    def build(self, request: FirmwareBuildRequest) -> FirmwareBuildResult: ...

    def get_snapshot(self, project_id: str) -> FirmwareBuildResult | None: ...


@runtime_checkable
class FirmwareDebugPort(Protocol):
    def get_snapshot(self, project_id: str) -> DebugAnalysisSnapshot | None: ...


@runtime_checkable
class FirmwareDebugAnalyzerPort(Protocol):
    def analyze(self, failure: FirmwareFailureReference) -> DebugAnalysisSnapshot: ...


def validate_project_snapshot(value: object) -> FirmwareProjectSnapshot:
    if type(value) is not FirmwareProjectSnapshot:
        raise TypeError("firmware project snapshot is invalid")
    return FirmwareProjectSnapshot.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_build_request(value: object) -> FirmwareBuildRequest:
    if type(value) is not FirmwareBuildRequest:
        raise TypeError("firmware build request is invalid")
    return FirmwareBuildRequest.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_build_result(value: object) -> FirmwareBuildResult:
    if type(value) is not FirmwareBuildResult:
        raise TypeError("firmware build result is invalid")
    return FirmwareBuildResult.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_failure_reference(value: object) -> FirmwareFailureReference:
    if type(value) is not FirmwareFailureReference:
        raise TypeError("firmware failure reference is invalid")
    return FirmwareFailureReference.model_validate(copy.deepcopy(value.model_dump(mode="python")))


__all__ = [
    "BuildConfigurationProjection",
    "FirmwareBuildPort",
    "FirmwareBuildRequest",
    "FirmwareBuildResult",
    "FirmwareBuildStatus",
    "FirmwareContract",
    "FirmwareDebugPort",
    "FirmwareDebugAnalyzerPort",
    "FirmwareEngineeringPort",
    "FirmwareFailureReference",
    "FirmwareFailureType",
    "FirmwareFramework",
    "FirmwareParserPort",
    "FirmwareProjectSnapshot",
    "SourceProjection",
    "validate_build_request",
    "validate_build_result",
    "validate_failure_reference",
    "validate_project_snapshot",
]
