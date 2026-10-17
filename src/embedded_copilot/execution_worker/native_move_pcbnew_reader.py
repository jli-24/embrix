"""Fixed, read-only pcbnew read-back. No native module is imported in this process."""

from __future__ import annotations

import hashlib
import json
import math
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from .adapters import SafeProcessRunner
from .contracts import (
    ApprovalStatus,
    ArtifactIdentity,
    KiCadNativeFootprintMoveOperation,
    NativeMoveReadBackV1,
    NativeMoveReaderPolicy,
    OperationStatus,
)
from .native_move_native_core import SCHEMA
from .native_move_projection import READER_ID

Role = Literal["INTERPRETER", "WRAPPER", "PCBNEW_PY", "PCBNEW_EXTENSION", "NATIVE_DEPENDENCY"]
_ROLES = {"INTERPRETER", "WRAPPER", "PCBNEW_PY", "PCBNEW_EXTENSION", "NATIVE_DEPENDENCY"}
_REQUIRED_NAMES = {
    "python.exe": "INTERPRETER",
    "native_move_wrapper.py": "WRAPPER",
    "pcbnew.py": "PCBNEW_PY",
    "_pcbnew.pyd": "PCBNEW_EXTENSION",
    "native_move_native_core.py": "NATIVE_DEPENDENCY",
}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _plain_path(path: Path, root: Path) -> bool:
    if not path.is_absolute() or not path.is_relative_to(root):
        return False
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink():
            return False
        try:
            if ancestor.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return False
        except AttributeError:
            pass
    cursor = root
    for part in path.relative_to(root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return False
        try:
            if cursor.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return False
        except AttributeError:
            pass
    return True


@dataclass(frozen=True, slots=True)
class NativeBundleFile:
    role: Role
    absolute_path: Path
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class NativeReadbackBundleV1:
    """Closed, immutable file inventory; static identity is detection, not loader proof."""

    root: Path
    files: tuple[NativeBundleFile, ...]
    environment: tuple[tuple[str, str], ...]

    @classmethod
    def freeze(cls, root: Path, environment: Mapping[str, str]) -> NativeReadbackBundleV1:
        root = Path(root)
        if not root.is_absolute() or not root.is_dir() or not _plain_path(root, root):
            raise ValueError("native bundle root unavailable")
        files = []
        for path in root.rglob("*"):
            if not _plain_path(path, root):
                raise ValueError("native bundle reparse path")
            if path.is_file():
                role = _REQUIRED_NAMES.get(path.name, "NATIVE_DEPENDENCY")
                content = path.read_bytes()
                files.append(NativeBundleFile(role, path, len(content), _sha(content)))
        frozen = cls(
            root=root, files=tuple(sorted(files, key=lambda item: (item.role, str(item.absolute_path)))),
            environment=tuple(sorted(environment.items())),
        )
        if not frozen.check():
            raise ValueError("native bundle closure unavailable")
        return frozen

    @property
    def schema_version(self) -> int:
        return 1

    @property
    def reader_id(self) -> str:
        return READER_ID

    @property
    def projection_schema(self) -> str:
        return SCHEMA

    @property
    def fingerprint(self) -> str:
        return _sha(_canonical({
            "schema_version": self.schema_version, "reader_id": self.reader_id,
            "projection_schema": self.projection_schema,
            "files": [
                {"role": item.role, "absolute_path": str(item.absolute_path),
                 "byte_size": item.byte_size, "sha256": item.sha256}
                for item in self.files
            ],
            "environment": [{"name": key, "value": value} for key, value in self.environment],
        }))

    @property
    def reader_version(self) -> str:
        return "native-v2-bundle-" + self.fingerprint.removeprefix("sha256:")

    def _single(self, role: str) -> Path:
        matches = [item.absolute_path for item in self.files if item.role == role]
        if len(matches) != 1:
            raise ValueError("native bundle role unavailable")
        return matches[0]

    @property
    def interpreter(self) -> Path:
        return self._single("INTERPRETER")

    @property
    def wrapper(self) -> Path:
        return self._single("WRAPPER")

    def environment_dict(self) -> dict[str, str]:
        return dict(self.environment)

    def check(self) -> bool:
        try:
            if not self.root.is_absolute() or not self.root.is_dir() or not _plain_path(self.root, self.root):
                return False
            if len(set(self.environment)) != len(self.environment) or len(dict(self.environment)) != len(self.environment):
                return False
            if tuple(sorted(self.environment)) != self.environment:
                return False
            if any(
                not key.isascii() or not key.isupper() or not key.replace("_", "").isalnum()
                or not isinstance(value, str) or not value or "\x00" in value
                for key, value in self.environment
            ):
                return False
            if dict(self.environment).get("PATH") != str(self.root):
                return False
            if any(key not in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"} for key, _ in self.environment):
                return False
            actual = []
            for path in self.root.rglob("*"):
                if not _plain_path(path, self.root):
                    return False
                if path.is_file():
                    actual.append(path)
            if set(actual) != {item.absolute_path for item in self.files} or len(actual) != len(self.files):
                return False
            if tuple(sorted(self.files, key=lambda item: (item.role, str(item.absolute_path)))) != self.files:
                return False
            for item in self.files:
                if (
                    item.role not in _ROLES or type(item.byte_size) is not int
                    or item.byte_size < 0 or type(item.sha256) is not str
                    or not _plain_path(item.absolute_path, self.root)
                ):
                    return False
                data = item.absolute_path.read_bytes()
                if len(data) != item.byte_size or _sha(data) != item.sha256:
                    return False
                if _REQUIRED_NAMES.get(item.absolute_path.name, "NATIVE_DEPENDENCY") != item.role:
                    return False
            for name, role in _REQUIRED_NAMES.items():
                if len([item for item in self.files if item.absolute_path == self.root / name and item.role == role]) != 1:
                    return False
            # CPython's fixed _pth file constrains startup sys.path before wrapper code runs.
            # The exact closed roots are the reviewed zip, Lib, site-packages and bundle root.
            pth = self.root / "python311._pth"
            if not pth.is_file() or pth.read_bytes() != (
                b"python311.zip\nLib\nLib/site-packages\n.\n"
            ):
                return False
            for name in ("python311.zip", "python311.dll", "Lib/os.py"):
                if not (self.root / name).is_file():
                    return False
            if not (self.root / "Lib/site-packages").is_dir():
                return False
            if any(item.absolute_path.suffix == ".pth" for item in self.files):
                return False
            reviewed = Path(__file__).parent
            if self.wrapper.read_bytes() != (reviewed / "native_move_wrapper.py").read_bytes():
                return False
            core = next(item.absolute_path for item in self.files if item.absolute_path.name == "native_move_native_core.py")
            return core.read_bytes() == (reviewed / "native_move_native_core.py").read_bytes()
        except (OSError, ValueError, TypeError, AttributeError, StopIteration):
            return False


@dataclass(frozen=True, slots=True)
class NativePcbnewReadbackInvocation:
    reader: NativePcbnewReader
    operation: KiCadNativeFootprintMoveOperation
    board_path: Path
    policy: NativeMoveReaderPolicy
    bundle: NativeReadbackBundleV1
    approval_id: str
    approval_fingerprint: str
    operation_fingerprint: str
    production_context_fingerprint: str
    workspace_root: Path
    pcb_relative_path: str
    pcb_sha256: str
    native_uuid: str
    deadline: datetime
    phase_view: object
    phase: Literal["NATIVE_READBACK"] = "NATIVE_READBACK"

    def __post_init__(self) -> None:
        from .native_move import NativeReadbackPhase

        if (
            type(self.reader) is not NativePcbnewReader
            or type(self.operation) is not KiCadNativeFootprintMoveOperation
            or type(self.policy) is not NativeMoveReaderPolicy
            or type(self.bundle) is not NativeReadbackBundleV1
            or type(self.phase_view) is not NativeReadbackPhase
            or type(self.deadline) is not datetime or self.deadline.utcoffset() is None
            or type(self.phase) is not str or self.phase != "NATIVE_READBACK"
        ):
            raise TypeError("strict native invocation types required")
        for path in (self.board_path, self.workspace_root):
            if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
                raise ValueError("absolute canonical native paths required")
        for value in (self.approval_id, self.pcb_relative_path, self.native_uuid):
            if type(value) is not str or not value:
                raise TypeError("strict native invocation identifiers required")
        if str(UUID(self.native_uuid)) != self.native_uuid:
            raise ValueError("canonical native UUID required")
        for value in (
            self.approval_fingerprint, self.operation_fingerprint,
            self.production_context_fingerprint, self.pcb_sha256,
        ):
            if type(value) is not str or len(value) != 71 or not value.startswith("sha256:"):
                raise TypeError("strict native invocation digest required")
            if any(character not in "0123456789abcdef" for character in value[7:]):
                raise ValueError("canonical native invocation digest required")

    @property
    def operation_id(self) -> str:
        return self.operation.operation_id

    def fixed_argv(self) -> tuple[str, ...]:
        return (
            str(self.bundle.interpreter), "-I", "-S", str(self.bundle.wrapper),
            str(self.board_path), self.operation.native_uuid,
        )

    def authorize(self, phase: object) -> bool:
        return self.reader.authorize(self, phase)


class NativePcbnewReader:
    """Trusted composition of the fixed bundle, canonical stores and one process boundary."""

    def __init__(
        self, *, bundle: NativeReadbackBundleV1, runner: SafeProcessRunner,
        store: object, catalog: object, workspace: object, artifact_store: object,
        authorize_phase: Callable[[str, KiCadNativeFootprintMoveOperation, object, datetime], bool],
        clock: Callable[[], datetime],
    ) -> None:
        self.bundle = bundle
        self.runner = runner
        self.store = store
        self.catalog = catalog
        self.workspace = workspace
        self.artifact_store = artifact_store
        self.authorize_phase = authorize_phase
        self.clock = clock

    def ready_for(self, policy: NativeMoveReaderPolicy) -> bool:
        try:
            interpreter = next(item for item in self.bundle.files if item.role == "INTERPRETER")
            return (
                type(self.runner) is SafeProcessRunner
                and type(self.bundle) is NativeReadbackBundleV1
                and policy.reader_id == READER_ID
                and policy.reader_version == self.bundle.reader_version
                and policy.executable_sha256 == interpreter.sha256
                and self.bundle.check()
            )
        except (ValueError, TypeError, AttributeError, StopIteration):
            return False

    def authorize(self, invocation: NativePcbnewReadbackInvocation, phase: object) -> bool:
        from .native_move import NativeReadbackPhase

        try:
            if type(invocation) is not NativePcbnewReadbackInvocation or invocation.reader is not self:
                return False
            op = invocation.operation
            if type(phase) is not NativeReadbackPhase or not self.ready_for(invocation.policy):
                return False
            context = op.production_context
            if context is None or invocation.bundle is not self.bundle:
                return False
            if (
                invocation.phase != "NATIVE_READBACK"
                or invocation.phase_view is not phase
                or invocation.operation_fingerprint != op.fingerprint
                or invocation.production_context_fingerprint != context.fingerprint
                or invocation.pcb_relative_path != op.pcb_relative_path
                or invocation.pcb_sha256 != context.changeset.entries[0].after_fingerprint
                or invocation.native_uuid != op.native_uuid
                or phase.deadline != invocation.deadline
                or phase.policy_timeout_seconds > invocation.policy.timeout_seconds
                or phase.is_cancelled() or phase.timeout_seconds() < 1
            ):
                return False
            if self.store.get_operation(op.tenant_id, op.project_id, op.operation_id) != op:
                return False
            status = self.store.get_status(op.tenant_id, op.project_id, op.operation_id)
            if (
                status is None or status.status is not OperationStatus.RUNNING
                or status.cancellation_requested or status.approval_id != invocation.approval_id
            ):
                return False
            approval = self.store.get_approval(op.tenant_id, op.project_id, invocation.approval_id)
            now = self.clock()
            if (
                approval is None or approval.status is not ApprovalStatus.CONSUMED
                or approval.fingerprint != invocation.approval_fingerprint
                or approval.operation_id != op.operation_id
                or approval.operation_fingerprint != op.fingerprint
                or approval.reviewer_id != op.requester_id
            ):
                return False
            if now >= min(op.expires_at, approval.expires_at, phase.deadline):
                return False
            if self.authorize_phase("NATIVE_READBACK", op, approval, now) is not True:
                return False
            binding = self.catalog._native_move_readback_binding(op)
            if binding is None or self.catalog.revalidate_native_move_readback(op, approval) is not True:
                return False
            root = self.workspace._resolve(op.tenant_id, op.project_id, op.target_id).root
            canonical = self.workspace._safe_path(root, op.pcb_relative_path)
            if (
                invocation.workspace_root != root or invocation.board_path != canonical
                or not canonical.is_file() or context.native_reader_policy != invocation.policy
            ):
                return False
            if _sha(canonical.read_bytes()) != invocation.pcb_sha256:
                return False
            # Catalog and file I/O may outlive the authority checked above.
            if (
                self.store.get_operation(op.tenant_id, op.project_id, op.operation_id) != op
                or self.store.get_approval(op.tenant_id, op.project_id, invocation.approval_id) != approval
            ):
                return False
            status = self.store.get_status(op.tenant_id, op.project_id, op.operation_id)
            if (
                status is None or status.status is not OperationStatus.RUNNING
                or status.cancellation_requested or status.approval_id != invocation.approval_id
            ):
                return False
            if (
                self.authorize_phase("NATIVE_READBACK", op, approval, self.clock()) is not True
                or phase.is_cancelled() or phase.timeout_seconds() < 1
                or self.clock() >= min(op.expires_at, approval.expires_at, phase.deadline)
            ):
                return False
            if self.catalog._native_move_readback_binding(op) != binding:
                return False
            # Phase authority can change during the canonical row read; keep this bounded.
            if (
                self.authorize_phase("NATIVE_READBACK", op, approval, self.clock()) is not True
                or phase.timeout_seconds() < 1
            ):
                return False
            if self.catalog._native_move_readback_binding(op) != binding:
                return False
            if phase.timeout_seconds() < 1:
                return False
            # The final canonical read can itself outlive Worker authority or the deadline.
            if (
                self.store.get_operation(op.tenant_id, op.project_id, op.operation_id) != op
                or self.store.get_approval(op.tenant_id, op.project_id, invocation.approval_id) != approval
            ):
                return False
            status = self.store.get_status(op.tenant_id, op.project_id, op.operation_id)
            return (
                status is not None and status.status is OperationStatus.RUNNING
                and not status.cancellation_requested and status.approval_id == invocation.approval_id
                and self.clock() < min(op.expires_at, approval.expires_at, phase.deadline)
            )
        except Exception:  # noqa: BLE001 - canonical I/O and policy checks fail closed
            return False

    def read_back(
        self, operation: KiCadNativeFootprintMoveOperation, board: Path,
        policy: NativeMoveReaderPolicy, phase: object,
    ) -> NativeMoveReadBackV1:
        from .native_move import NativeReadbackPhase

        if type(phase) is not NativeReadbackPhase:
            raise TypeError("native phase unavailable")
        status = self.store.get_status(operation.tenant_id, operation.project_id, operation.operation_id)
        if status is None or status.status is not OperationStatus.RUNNING or not status.approval_id:
            raise PermissionError("native operation is not running")
        approval = self.store.get_approval(operation.tenant_id, operation.project_id, status.approval_id)
        context = operation.production_context
        if approval is None or context is None:
            raise PermissionError("native approval or context unavailable")
        workspace_root = self.workspace._resolve(operation.tenant_id, operation.project_id, operation.target_id).root
        invocation = NativePcbnewReadbackInvocation(
            self, operation, Path(board), policy, self.bundle,
            approval.grant_id, approval.fingerprint, operation.fingerprint,
            context.fingerprint, workspace_root, operation.pcb_relative_path,
            context.changeset.entries[0].after_fingerprint, operation.native_uuid, phase.deadline, phase,
        )
        if not invocation.authorize(phase):
            raise PermissionError("native read-back canonical authorization unavailable")
        result = self.runner.run_native_readback(invocation, phase)
        if result.exit_code != 0 or result.stderr or result.stdout_overflow or result.stderr_overflow:
            raise ValueError("native read-back process failed")
        if not invocation.authorize(phase):
            raise PermissionError("native read-back result authorization expired")
        def pairs(items: list[tuple[str, object]]) -> dict:
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("native read-back duplicate field")
                result[key] = value
            return result
        document = json.loads(
            result.stdout.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite native JSON")),
        )
        if result.stdout != _canonical(document):
            raise ValueError("native stdout is not one exact canonical object")
        expected_keys = {
            "projection_schema", "coverage", "native_uuid", "x_mm", "y_mm",
            "rotation_degrees", "pad_net_fingerprint", "non_target_summary_fingerprint",
        }
        if type(document) is not dict or set(document) != expected_keys:
            raise ValueError("native read-back schema unavailable")
        if document["projection_schema"] != SCHEMA or document["native_uuid"] != operation.native_uuid:
            raise ValueError("native read-back schema or UUID drift")
        coverage = document["coverage"]
        if type(coverage) is not dict or set(coverage) != {"footprints", "pads", "target_uuid", "fields"}:
            raise ValueError("native read-back coverage unavailable")
        if coverage["target_uuid"] != operation.native_uuid:
            raise ValueError("native read-back footprint coverage unavailable")
        if type(coverage["footprints"]) is not int or coverage["footprints"] < 1:
            raise ValueError("native read-back footprint coverage unavailable")
        if type(coverage["pads"]) is not int or coverage["pads"] < 0:
            raise ValueError("native read-back pad coverage unavailable")
        if coverage["fields"] != "uuid,reference,layer,placement,pad-uuid-number-type-shape-local-position-rotation-size-net":
            raise ValueError("native read-back field coverage unavailable")
        for key in ("x_mm", "y_mm", "rotation_degrees"):
            value = document[key]
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 10_000:
                raise ValueError("native coordinate type or range unavailable")
        for key in ("pad_net_fingerprint", "non_target_summary_fingerprint"):
            value = document[key]
            if type(value) is not str or len(value) != 71 or not value.startswith("sha256:"):
                raise ValueError("native digest type unavailable")
            if any(character not in "0123456789abcdef" for character in value[7:]):
                raise ValueError("native digest unavailable")
        context = operation.production_context
        if context is None:
            raise ValueError("native production context unavailable")
        pcb_sha = _sha(board.read_bytes())
        if pcb_sha != context.changeset.entries[0].after_fingerprint:
            raise ValueError("native PCB changed after read-back")
        document["pcb_sha256"] = pcb_sha
        encoded = _canonical(document)
        digest = _sha(encoded)
        identity = ArtifactIdentity(
            artifact_id="native-readback-" + hashlib.sha256((operation.operation_id + digest).encode()).hexdigest()[:32],
            tenant_id=operation.tenant_id, project_id=operation.project_id,
            target_id=operation.target_id, workspace_fingerprint=operation.workspace_fingerprint,
            build_operation_id=operation.operation_id, toolchain_id=policy.reader_id,
            toolchain_version=policy.reader_version, build_configuration="native-readback",
            artifact_type="LOG", artifact_sha256=digest, size_bytes=len(encoded), created_at=self.clock(),
        )
        if not invocation.authorize(phase):
            raise PermissionError("native read-back expired before artifact persistence")
        self.artifact_store.put(identity, encoded)
        if not invocation.authorize(phase):
            raise PermissionError("native read-back expired after artifact persistence")
        return NativeMoveReadBackV1(
            status="PASS", reader_id=policy.reader_id, reader_version=policy.reader_version,
            executable_sha256=policy.executable_sha256, pcb_sha256=pcb_sha,
            native_uuid=document["native_uuid"], x_mm=document["x_mm"], y_mm=document["y_mm"],
            rotation_degrees=document["rotation_degrees"],
            pad_net_fingerprint=document["pad_net_fingerprint"],
            non_target_summary_fingerprint=document["non_target_summary_fingerprint"],
            observation_artifact=identity,
        )
