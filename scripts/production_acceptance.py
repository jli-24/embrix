"""Offline gates and safe evidence projections for v5.0 production acceptance.

This module deliberately does not build, flash, change a device, or invoke a model.
An operator invokes preflight explicitly and supplies a new approval artifact before
handing a request to the existing bounded execution and HIL adapters.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,127}$")
_RUN_ID = re.compile(r"^RUN-ESP32S3-[0-9]{4,}$")
_USB_ID = re.compile(r"^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}$")
_WINDOWS_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
_UNIX_ABSOLUTE = re.compile(r"(?:^|\s)/(?:home|tmp|etc|var|usr|opt|private)(?:/|$)")
_SENSITIVE_TEXT = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret)\s*[:=]",
    re.IGNORECASE,
)
_SENSITIVE_KEY = re.compile(
    r"(?:token|secret|credential|password|command|argv|raw|uart|stdout|stderr|payload|"
    r"absolute_path|binary|waveform)",
    re.IGNORECASE,
)


class AcceptanceError(ValueError):
    """Stable fail-closed error for the operator-facing composition."""


class AcceptanceFinalState(StrEnum):
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    PARTIALLY_VALIDATED = "PARTIALLY_VALIDATED"
    UNVERIFIED = "UNVERIFIED"


class PreflightStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    MISMATCH = "MISMATCH"
    UNVERIFIED = "UNVERIFIED"


class AcceptanceCapability(StrEnum):
    BUILD = "BUILD"
    FLASH = "FLASH"
    HIL = "HIL"
    FAILURE_INJECTION = "FAILURE_INJECTION"
    RECOVERY_HIL = "RECOVERY_HIL"


class AcceptanceLineagePhase(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    KNOWLEDGE_ANALYSIS = "KNOWLEDGE_ANALYSIS"
    FIRMWARE_PROPOSAL = "FIRMWARE_PROPOSAL"
    EDA_VALIDATION = "EDA_VALIDATION"
    BUILD = "BUILD"
    FLASH = "FLASH"
    HIL_OBSERVATION = "HIL_OBSERVATION"
    FAILURE_OBSERVATION = "FAILURE_OBSERVATION"
    REPAIR_PROPOSAL = "REPAIR_PROPOSAL"
    OPTIMIZATION_PROPOSAL = "OPTIMIZATION_PROPOSAL"


class _Strict(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _fingerprint(value: object, field: str) -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{field} is invalid")
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"{field} is invalid")
    return candidate.as_posix()


def _utc(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} is invalid") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"{field} is invalid")
    return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


class AssetReference(_Strict):
    asset_id: str
    relative_path: str
    fingerprint: str

    @field_validator("asset_id", mode="before")
    @classmethod
    def _asset_id(cls, value: object) -> str:
        return _identifier(value, "asset_id")

    @field_validator("relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        return _relative_path(value, "relative_path")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _fingerprint(value, "fingerprint")


class ToolchainExpectation(_Strict):
    python_version: str
    esp_idf_version: str
    kicad_version: str
    pcbnew_version: str
    glm_provider_id: str
    glm_text_profile_id: str
    glm_vision_profile_id: str

    @field_validator(
        "python_version", "esp_idf_version", "kicad_version", "pcbnew_version"
    )
    @classmethod
    def _version(cls, value: object) -> str:
        if not isinstance(value, str) or not value or len(value) > 64:
            raise ValueError("toolchain version is invalid")
        return value

    @field_validator("glm_provider_id", "glm_text_profile_id", "glm_vision_profile_id")
    @classmethod
    def _provider(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)


class HardwareBinding(_Strict):
    board_model: str
    board_revision: str
    usb_vid_pid: str
    uart_port: str
    uart_framing: str
    i2c_sensor_model: str
    i2c_sensor_address: str
    oled_model: str
    oled_address: str
    gpio_fixture_id: str
    power_fixture_id: str

    @field_validator("board_model", "board_revision", "uart_port", "uart_framing")
    @classmethod
    def _board(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator(
        "i2c_sensor_model", "oled_model", "gpio_fixture_id", "power_fixture_id"
    )
    @classmethod
    def _peripheral(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator("usb_vid_pid", mode="before")
    @classmethod
    def _usb(cls, value: object) -> str:
        if not isinstance(value, str) or _USB_ID.fullmatch(value) is None:
            raise ValueError("usb_vid_pid is invalid")
        return value.upper()

    @field_validator("i2c_sensor_address", "oled_address", mode="before")
    @classmethod
    def _address(cls, value: object, info) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"0x[0-7][0-9A-Fa-f]", value) is None
        ):
            raise ValueError(f"{info.field_name} is invalid")
        return value.lower()


class AcceptanceManifest(_Strict):
    schema_version: int
    golden_project: str
    project_id: str
    workspace_id: str
    target_id: str
    firmware_project: AssetReference
    firmware_artifact_relative_path: str
    kicad_project: AssetReference
    kicad_schematic: AssetReference
    kicad_board: AssetReference
    toolchain: ToolchainExpectation
    hardware: HardwareBinding
    operator_id: str
    approver_id: str

    @field_validator("golden_project", mode="before")
    @classmethod
    def _golden(cls, value: object) -> str:
        if value != "ESP32-S3 Sensor Gateway":
            raise ValueError("golden_project is invalid")
        return value

    @field_validator(
        "project_id", "workspace_id", "target_id", "operator_id", "approver_id"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator("firmware_artifact_relative_path", mode="before")
    @classmethod
    def _artifact(cls, value: object) -> str:
        return _relative_path(value, "firmware_artifact_relative_path")

    @model_validator(mode="after")
    def _manifest(self) -> AcceptanceManifest:
        if self.schema_version != 1:
            raise ValueError("schema_version is unsupported")
        if self.target_id.casefold() != "esp32-s3":
            raise ValueError("target_id must bind ESP32-S3")
        asset_ids = {
            self.firmware_project.asset_id,
            self.kicad_project.asset_id,
            self.kicad_schematic.asset_id,
            self.kicad_board.asset_id,
        }
        paths = {
            self.firmware_project.relative_path,
            self.kicad_project.relative_path,
            self.kicad_schematic.relative_path,
            self.kicad_board.relative_path,
        }
        if len(asset_ids) != 4 or len(paths) != 4:
            raise ValueError("asset bindings must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        return _canonical_fingerprint(self.model_dump(mode="json"))


class AcceptanceRun(_Strict):
    run_id: str
    project_id: str
    manifest_fingerprint: str
    operator_id: str
    started_at: str
    toolchain: ToolchainExpectation

    @field_validator("run_id", mode="before")
    @classmethod
    def _run(cls, value: object) -> str:
        if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
            raise ValueError("run_id is invalid")
        return value

    @field_validator("project_id", "operator_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator("manifest_fingerprint", mode="before")
    @classmethod
    def _manifest_fp(cls, value: object) -> str:
        return _fingerprint(value, "manifest_fingerprint")

    @field_validator("started_at", mode="before")
    @classmethod
    def _started(cls, value: object) -> str:
        return _utc(value, "started_at")

    @classmethod
    def from_manifest(
        cls, *, run_id: str, manifest: AcceptanceManifest, started_at: str
    ) -> AcceptanceRun:
        return cls(
            run_id=run_id,
            project_id=manifest.project_id,
            manifest_fingerprint=manifest.fingerprint,
            operator_id=manifest.operator_id,
            started_at=started_at,
            toolchain=manifest.toolchain,
        )


class ApprovalArtifact(_Strict):
    approval_id: str
    acceptance_run_id: str
    project_id: str
    workspace_id: str
    target_id: str
    capability: AcceptanceCapability
    proposal_fingerprint: str
    artifact_fingerprint: str | None = None
    reviewer_id: str
    issued_at: str
    expires_at: str

    @field_validator(
        "approval_id", "project_id", "workspace_id", "target_id", "reviewer_id"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator("acceptance_run_id", mode="before")
    @classmethod
    def _run(cls, value: object) -> str:
        if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
            raise ValueError("acceptance_run_id is invalid")
        return value

    @field_validator("proposal_fingerprint", "artifact_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str | None:
        return None if value is None else _fingerprint(value, info.field_name)

    @field_validator("issued_at", "expires_at", mode="before")
    @classmethod
    def _dates(cls, value: object, info) -> str:
        return _utc(value, info.field_name)

    @model_validator(mode="after")
    def _binding(self) -> ApprovalArtifact:
        if (
            self.capability is AcceptanceCapability.FLASH
            and self.artifact_fingerprint is None
        ):
            raise ValueError("flash approval requires artifact_fingerprint")
        if self.issued_at >= self.expires_at:
            raise ValueError("approval window is invalid")
        return self


class FirmwareArtifactReference(_Strict):
    artifact_id: str
    acceptance_run_id: str
    project_id: str
    workspace_id: str
    target_id: str
    firmware_fingerprint: str
    size_bytes: int
    build_receipt_fingerprint: str

    @field_validator(
        "artifact_id",
        "project_id",
        "workspace_id",
        "target_id",
        "build_receipt_fingerprint",
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator("acceptance_run_id", mode="before")
    @classmethod
    def _run(cls, value: object) -> str:
        if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
            raise ValueError("acceptance_run_id is invalid")
        return value

    @field_validator("firmware_fingerprint", mode="before")
    @classmethod
    def _firmware(cls, value: object) -> str:
        return _fingerprint(value, "firmware_fingerprint")

    @field_validator("size_bytes", mode="before")
    @classmethod
    def _size(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 64 * 1024 * 1024:
            raise ValueError("size_bytes is invalid")
        return value


class BuildReceipt(_Strict):
    receipt_id: str
    acceptance_run_id: str
    project_id: str
    workspace_id: str
    target_id: str
    proposal_fingerprint: str
    status: str
    artifact_fingerprint: str | None = None
    completed_at: str
    provenance_fingerprint: str
    receipt_fingerprint: str

    @field_validator("receipt_id", "project_id", "workspace_id", "target_id")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator(
        "proposal_fingerprint",
        "artifact_fingerprint",
        "provenance_fingerprint",
        "receipt_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str | None:
        return None if value is None else _fingerprint(value, info.field_name)

    @field_validator("acceptance_run_id", mode="before")
    @classmethod
    def _run(cls, value: object) -> str:
        if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
            raise ValueError("acceptance_run_id is invalid")
        return value

    @field_validator("completed_at", mode="before")
    @classmethod
    def _completed(cls, value: object) -> str:
        return _utc(value, "completed_at")

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> str:
        if value not in {"SUCCEEDED", "FAILED", "UNAVAILABLE", "TIMEOUT", "REJECTED"}:
            raise ValueError("build receipt status is invalid")
        return value

    @model_validator(mode="after")
    def _receipt(self) -> BuildReceipt:
        if self.status == "SUCCEEDED" and self.artifact_fingerprint is None:
            raise ValueError("successful build receipt requires artifact_fingerprint")
        if self.status != "SUCCEEDED" and self.artifact_fingerprint is not None:
            raise ValueError("failed build receipt cannot promote an artifact")
        return self


class AcceptanceLineageEvent(_Strict):
    event_id: str
    acceptance_run_id: str
    project_id: str
    phase: AcceptanceLineagePhase
    event_fingerprint: str
    parent_fingerprints: tuple[str, ...] = ()
    recorded_at: str

    @field_validator("event_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, info.field_name)

    @field_validator("acceptance_run_id", mode="before")
    @classmethod
    def _run(cls, value: object) -> str:
        if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
            raise ValueError("acceptance_run_id is invalid")
        return value

    @field_validator("event_fingerprint", mode="before")
    @classmethod
    def _event_fp(cls, value: object) -> str:
        return _fingerprint(value, "event_fingerprint")

    @field_validator("parent_fingerprints", mode="before")
    @classmethod
    def _parents(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise TypeError("parent_fingerprints are invalid")
        parents = tuple(_fingerprint(parent, "parent_fingerprint") for parent in value)
        if len(parents) != len(set(parents)):
            raise ValueError("parent_fingerprints are invalid")
        return parents

    @field_validator("recorded_at", mode="before")
    @classmethod
    def _recorded(cls, value: object) -> str:
        return _utc(value, "recorded_at")


class ToolProbePort(Protocol):
    def version(self, tool_id: str) -> str | None: ...

    def module_version(self, module_name: str) -> str | None: ...

    def device_identity(self, uart_port: str) -> Mapping[str, str] | None: ...


class LocalPreflightProbe:
    """Uses only fixed, read-only version checks and the requested UART identity."""

    _COMMANDS: ClassVar[dict[str, tuple[str, str]]] = {
        "python": (sys.executable, "--version"),
        "esp_idf": ("idf.py", "--version"),
        "kicad": ("kicad-cli", "--version"),
    }

    def __init__(
        self, *, operator_device_identity: Mapping[str, str] | None = None
    ) -> None:
        self._operator_device_identity = (
            None if operator_device_identity is None else dict(operator_device_identity)
        )

    def version(self, tool_id: str) -> str | None:
        command = self._COMMANDS.get(tool_id)
        if command is None:
            return None
        try:
            result = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return (result.stdout or result.stderr).decode("utf-8", "replace").strip()

    def module_version(self, module_name: str) -> str | None:
        if module_name != "pcbnew" or importlib.util.find_spec(module_name) is None:
            return None
        return "installed"

    def device_identity(self, uart_port: str) -> Mapping[str, str] | None:
        # Device discovery is intentionally absent. An operator-supplied probe is required.
        del uart_port
        return self._operator_device_identity


def load_manifest(path: Path) -> AcceptanceManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceError("acceptance manifest is unavailable") from error
    try:
        return AcceptanceManifest.model_validate(deepcopy(raw))
    except (TypeError, ValueError) as error:
        raise AcceptanceError("acceptance manifest is invalid") from error


def load_approval(path: Path) -> ApprovalArtifact:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceError("approval artifact is unavailable") from error
    try:
        return ApprovalArtifact.model_validate(deepcopy(raw))
    except (TypeError, ValueError) as error:
        raise AcceptanceError("approval artifact is invalid") from error


def load_device_identity(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceError("device identity is unavailable") from error
    if (
        not isinstance(raw, dict)
        or set(raw)
        != {
            "usb_vid_pid",
            "uart_port",
            "board_model",
            "board_revision",
        }
        or not all(isinstance(value, str) for value in raw.values())
    ):
        raise AcceptanceError("device identity is invalid")
    return dict(raw)


def create_acceptance_run(
    *, manifest: AcceptanceManifest, run_id: str, started_at: str
) -> AcceptanceRun:
    return AcceptanceRun.from_manifest(
        run_id=run_id, manifest=manifest, started_at=started_at
    )


def verify_assets(
    *, manifest: AcceptanceManifest, workspace_root: Path
) -> tuple[PreflightStatus, ...]:
    """Hashes only four manifest-bound paths; it never enumerates the workspace."""
    assets = (
        manifest.firmware_project,
        manifest.kicad_project,
        manifest.kicad_schematic,
        manifest.kicad_board,
    )
    results: list[PreflightStatus] = []
    for asset in assets:
        try:
            candidate = _bound_file(workspace_root, asset.relative_path)
            actual = _sha256_file(candidate)
        except (OSError, ValueError):
            results.append(PreflightStatus.UNAVAILABLE)
        else:
            results.append(
                PreflightStatus.AVAILABLE
                if actual == asset.fingerprint
                else PreflightStatus.MISMATCH
            )
    return tuple(results)


def preflight(
    *,
    manifest: AcceptanceManifest,
    workspace_root: Path,
    probe: ToolProbePort,
) -> dict[str, PreflightStatus]:
    """Returns only statuses. Version text and device details never enter evidence."""
    checks: dict[str, PreflightStatus] = {}
    expected = {
        "python": manifest.toolchain.python_version,
        "esp_idf": manifest.toolchain.esp_idf_version,
        "kicad": manifest.toolchain.kicad_version,
    }
    for tool_id, expected_version in expected.items():
        observed = probe.version(tool_id)
        checks[tool_id] = _version_status(observed, expected_version)
    pcbnew = probe.module_version("pcbnew")
    checks["pcbnew"] = (
        PreflightStatus.UNAVAILABLE if pcbnew is None else PreflightStatus.AVAILABLE
    )
    identity = probe.device_identity(manifest.hardware.uart_port)
    checks["device"] = _device_status(identity, manifest.hardware)
    for index, result in enumerate(
        verify_assets(manifest=manifest, workspace_root=workspace_root)
    ):
        checks[f"asset_{index + 1}"] = result
    # Credential presence does not prove a provider connection or a completed analysis.
    checks["glm"] = PreflightStatus.UNVERIFIED
    return checks


def require_approval(
    *,
    approval: ApprovalArtifact,
    run: AcceptanceRun,
    manifest: AcceptanceManifest,
    capability: AcceptanceCapability,
    proposal_fingerprint: str,
    now: str,
    artifact: FirmwareArtifactReference | None = None,
) -> None:
    checked_now = _utc(now, "now")
    expected = (
        approval.acceptance_run_id == run.run_id
        and approval.project_id == manifest.project_id
        and approval.workspace_id == manifest.workspace_id
        and approval.target_id == manifest.target_id
        and approval.capability is capability
        and approval.proposal_fingerprint
        == _fingerprint(proposal_fingerprint, "proposal_fingerprint")
        and approval.reviewer_id == manifest.approver_id
        and checked_now < approval.expires_at
    )
    if not expected:
        raise AcceptanceError("approval rejected")
    if capability is AcceptanceCapability.FLASH:
        if (
            artifact is None
            or approval.artifact_fingerprint != artifact.firmware_fingerprint
        ):
            raise AcceptanceError("flash artifact approval rejected")
    elif artifact is not None:
        raise AcceptanceError("artifact is only valid for flash approval")


def promote_build_artifact(
    *,
    run: AcceptanceRun,
    manifest: AcceptanceManifest,
    receipt: BuildReceipt,
    workspace_root: Path,
) -> FirmwareArtifactReference:
    """Creates the only flashable reference after a successful, bound build receipt."""
    if (
        receipt.status != "SUCCEEDED"
        or receipt.acceptance_run_id != run.run_id
        or receipt.project_id != manifest.project_id
        or receipt.workspace_id != manifest.workspace_id
        or receipt.target_id != manifest.target_id
    ):
        raise AcceptanceError("build receipt rejected")
    try:
        content = _bound_file(
            workspace_root, manifest.firmware_artifact_relative_path
        ).read_bytes()
    except OSError as error:
        raise AcceptanceError("build artifact unavailable") from error
    if not 1 <= len(content) <= 64 * 1024 * 1024:
        raise AcceptanceError("build artifact rejected")
    fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
    if receipt.artifact_fingerprint != fingerprint:
        raise AcceptanceError("build artifact rejected")
    return FirmwareArtifactReference(
        artifact_id="artifact-" + fingerprint.removeprefix("sha256:")[:32],
        acceptance_run_id=run.run_id,
        project_id=manifest.project_id,
        workspace_id=manifest.workspace_id,
        target_id=manifest.target_id,
        firmware_fingerprint=fingerprint,
        size_bytes=len(content),
        build_receipt_fingerprint=receipt.receipt_fingerprint,
    )


def validate_lineage(
    *, run: AcceptanceRun, events: tuple[AcceptanceLineageEvent, ...]
) -> tuple[AcceptanceLineageEvent, ...]:
    """Checks projected lineage only; it never derives a validation outcome."""
    required = {
        AcceptanceLineagePhase.REQUIREMENT,
        AcceptanceLineagePhase.KNOWLEDGE_ANALYSIS,
        AcceptanceLineagePhase.FIRMWARE_PROPOSAL,
        AcceptanceLineagePhase.EDA_VALIDATION,
        AcceptanceLineagePhase.OPTIMIZATION_PROPOSAL,
    }
    if not events or {event.phase for event in events} < required:
        raise AcceptanceError("lineage is incomplete")
    seen: set[str] = set()
    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        if (
            event.acceptance_run_id != run.run_id
            or event.project_id != run.project_id
            or event.event_id in seen_ids
            or event.event_fingerprint in seen
            or event.recorded_at < run.started_at
        ):
            raise AcceptanceError("lineage is rejected")
        if index == 0:
            if (
                event.phase is not AcceptanceLineagePhase.REQUIREMENT
                or event.parent_fingerprints
            ):
                raise AcceptanceError("lineage is rejected")
        elif (
            not event.parent_fingerprints or not set(event.parent_fingerprints) <= seen
        ):
            raise AcceptanceError("lineage is rejected")
        seen.add(event.event_fingerprint)
        seen_ids.add(event.event_id)
    return tuple(events)


def write_evidence_bundle(
    *,
    evidence_root: Path,
    run: AcceptanceRun,
    final_state: AcceptanceFinalState,
    projections: Mapping[str, Mapping[str, Any]],
) -> tuple[Path, ...]:
    """Writes Layer 2 safe projections only; conflicting existing runs are rejected."""
    required = (
        "project_reference.json",
        "workspace_timeline.json",
        "build_receipt.json",
        "flash_receipt.json",
        "hil_observation.json",
        "optimization_review.json",
        "final_environment_snapshot.json",
    )
    if set(projections) != set(required):
        raise AcceptanceError("evidence projection set is invalid")
    destination = evidence_root / run.run_id
    destination.mkdir(parents=True, exist_ok=True)
    documents: list[tuple[Path, bytes]] = []
    for filename in required:
        document = {
            "acceptance_run": run.model_dump(mode="json"),
            "final_state": final_state.value,
            "projection": _sanitize_projection(projections[filename]),
        }
        encoded = _canonical_json(document)
        target = destination / filename
        documents.append((target, encoded))

    for target, encoded in documents:
        if target.exists() and target.read_bytes() != encoded:
            raise AcceptanceError("evidence run conflict")

    written: list[Path] = []
    for target, encoded in documents:
        if not target.exists():
            target.write_bytes(encoded)
        written.append(target)
    return tuple(written)


def _version_status(observed: str | None, expected: str) -> PreflightStatus:
    if observed is None:
        return PreflightStatus.UNAVAILABLE
    return (
        PreflightStatus.AVAILABLE if expected in observed else PreflightStatus.MISMATCH
    )


def _device_status(
    identity: Mapping[str, str] | None, hardware: HardwareBinding
) -> PreflightStatus:
    if identity is None:
        return PreflightStatus.UNAVAILABLE
    if set(identity) != {"usb_vid_pid", "uart_port", "board_model", "board_revision"}:
        return PreflightStatus.MISMATCH
    return (
        PreflightStatus.AVAILABLE
        if dict(identity)
        == {
            "usb_vid_pid": hardware.usb_vid_pid,
            "uart_port": hardware.uart_port,
            "board_model": hardware.board_model,
            "board_revision": hardware.board_revision,
        }
        else PreflightStatus.MISMATCH
    )


def _bound_file(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve(strict=True)
    candidate = root_resolved.joinpath(*relative_path.split("/")).resolve(strict=True)
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        raise ValueError("bound asset is invalid") from None
    if not candidate.is_file():
        raise ValueError("bound asset is invalid")
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_fingerprint(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sanitize_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AcceptanceError("evidence projection is invalid")

    def clean(item: Any, key: str = "") -> Any:
        if _SENSITIVE_KEY.search(key):
            return "[REDACTED]"
        if isinstance(item, Mapping):
            return {
                str(child_key): clean(child, str(child_key))
                for child_key, child in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [clean(child, key) for child in item]
        if isinstance(item, str):
            if (
                _WINDOWS_ABSOLUTE.search(item)
                or _UNIX_ABSOLUTE.search(item)
                or _SENSITIVE_TEXT.search(item)
            ):
                return "[REDACTED]"
            return item
        if item is None or isinstance(item, (bool, int, float)):
            return item
        raise AcceptanceError("evidence projection is invalid")

    return clean(deepcopy(dict(value)))


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="v5.0 operator-invoked acceptance preflight"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("workspace_root", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--device-identity", type=Path)
    arguments = parser.parse_args()
    manifest = load_manifest(arguments.manifest)
    run = create_acceptance_run(
        manifest=manifest,
        run_id=arguments.run_id,
        started_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    checks = preflight(
        manifest=manifest,
        workspace_root=arguments.workspace_root,
        probe=LocalPreflightProbe(
            operator_device_identity=(
                None
                if arguments.device_identity is None
                else load_device_identity(arguments.device_identity)
            )
        ),
    )
    print(
        json.dumps(
            {
                "run_id": run.run_id,
                "preflight": {key: value.value for key, value in checks.items()},
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
