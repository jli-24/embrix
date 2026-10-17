from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


_SOURCE_SUFFIXES = frozenset({".c", ".h", ".cpp", ".hpp"})
_BINARY_SUFFIXES = frozenset({".elf", ".map", ".hex", ".bin"})
_MANIFEST = "compile_commands.json"
_MAX_FILES = 256
_MAX_FILE_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 8 * 1024 * 1024


class FirmwareSourceUnavailable(RuntimeError):
    """The bound project cannot provide a safe firmware source snapshot."""

    def __init__(self, message: str, *, missing_source: bool = False) -> None:
        super().__init__(message)
        self.missing_source = missing_source


@dataclass(frozen=True, slots=True)
class FirmwareSourceReference:
    project_id: str
    relative_path: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class FirmwareSourceFile:
    """Private provider-to-parser envelope; never project `content` outward."""

    reference: FirmwareSourceReference
    content: str


@dataclass(frozen=True, slots=True)
class FirmwareArtifactMetadata:
    reference: FirmwareSourceReference
    size_bytes: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class FirmwareSourceSnapshot:
    project_id: str
    fingerprint: str
    sources: tuple[FirmwareSourceFile, ...]
    artifacts: tuple[FirmwareArtifactMetadata, ...]


class FirmwareSourceProvider:
    """Read bounded, project-scoped firmware sources without path leakage.

    Existing projects historically expose a ``firmware`` subtree, while CubeMX
    projects commonly place ``Core`` directly below the attached project root.
    The provider accepts both layouts and keeps references relative to the
    selected source root.
    """

    __slots__ = ("_root_resolver",)

    def __init__(self, root_resolver: Callable[[str], Path]) -> None:
        if not callable(root_resolver):
            raise TypeError("firmware root resolver is invalid")
        self._root_resolver = root_resolver

    @staticmethod
    def _project_id(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise ValueError("firmware project identity is invalid")
        return value.strip()

    @staticmethod
    def _reference(project_id: str, root: Path, path: Path, fingerprint: str) -> FirmwareSourceReference:
        relative = path.relative_to(root).as_posix()
        checked = PurePosixPath(relative)
        if checked.is_absolute() or ".." in checked.parts:
            raise FirmwareSourceUnavailable("firmware source path is invalid")
        return FirmwareSourceReference(project_id, relative, fingerprint)

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(block)
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def _snapshot_fingerprint(
        project_id: str,
        sources: tuple[FirmwareSourceFile, ...],
        artifacts: tuple[FirmwareArtifactMetadata, ...],
    ) -> str:
        digest = hashlib.sha256(project_id.encode("utf-8"))
        for item in sources:
            digest.update(item.reference.relative_path.encode("utf-8"))
            digest.update(item.reference.fingerprint.encode("ascii"))
        for item in artifacts:
            digest.update(item.reference.relative_path.encode("utf-8"))
            digest.update(item.reference.fingerprint.encode("ascii"))
            digest.update(str(item.size_bytes).encode("ascii"))
            digest.update(str(item.modified_ns).encode("ascii"))
        return "sha256:" + digest.hexdigest()

    def snapshot(
        self, project_id: str, *, allow_empty_source: bool = False
    ) -> FirmwareSourceSnapshot:
        if type(allow_empty_source) is not bool:
            raise TypeError("allow_empty_source is invalid")
        checked_id = self._project_id(project_id)
        project_root = self._root_resolver(checked_id)
        if not isinstance(project_root, Path):
            raise FirmwareSourceUnavailable("project root is invalid")
        if not project_root.exists() or not project_root.is_dir() or project_root.is_symlink():
            raise FirmwareSourceUnavailable("project root is unavailable")
        resolved_project = project_root.resolve(strict=True)
        nested_root = resolved_project / "firmware"
        nested_has_supported_file = False
        if nested_root.exists() and nested_root.is_dir() and not nested_root.is_symlink():
            try:
                nested_has_supported_file = any(
                    item.is_file()
                    and (
                        item.suffix.casefold() in _SOURCE_SUFFIXES
                        or item.suffix.casefold() in _BINARY_SUFFIXES
                        or item.name.casefold() == _MANIFEST
                    )
                    for item in nested_root.rglob("*")
                )
            except OSError as error:
                raise FirmwareSourceUnavailable(
                    "firmware source enumeration failed"
                ) from error
        if nested_has_supported_file:
            firmware_root = nested_root
        else:
            # A greenfield project is allowed to be source-empty.  Returning an
            # empty snapshot lets the caller distinguish that state from a
            # broken path and lets project metadata provide its own context.
            firmware_root = resolved_project
        resolved_firmware = firmware_root.resolve(strict=True)
        if not resolved_firmware.is_relative_to(resolved_project):
            raise FirmwareSourceUnavailable("firmware source root is invalid")

        source_items: list[FirmwareSourceFile] = []
        artifact_items: list[FirmwareArtifactMetadata] = []
        total_bytes = 0
        try:
            paths = tuple(sorted(resolved_firmware.rglob("*"), key=lambda item: item.as_posix().casefold()))
        except OSError as error:
            raise FirmwareSourceUnavailable("firmware source enumeration failed") from error
        for path in paths:
            if path.is_symlink():
                raise FirmwareSourceUnavailable("firmware source symlink is rejected")
            if not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(resolved_firmware):
                raise FirmwareSourceUnavailable("firmware source path escapes root")
            name = resolved.name.casefold()
            suffix = resolved.suffix.casefold()
            if suffix not in _SOURCE_SUFFIXES and suffix not in _BINARY_SUFFIXES and name != _MANIFEST:
                continue
            if len(source_items) + len(artifact_items) >= _MAX_FILES:
                raise FirmwareSourceUnavailable("firmware source file limit exceeded")
            try:
                stat = resolved.stat()
            except OSError as error:
                raise FirmwareSourceUnavailable("firmware source metadata is unavailable") from error
            if stat.st_size < 0 or stat.st_size > _MAX_FILE_BYTES:
                raise FirmwareSourceUnavailable("firmware source file size is invalid")
            digest = self._digest(resolved)
            reference = self._reference(checked_id, resolved_firmware, resolved, digest)
            if suffix in _BINARY_SUFFIXES:
                artifact_items.append(FirmwareArtifactMetadata(reference, stat.st_size, stat.st_mtime_ns))
                continue
            total_bytes += stat.st_size
            if total_bytes > _MAX_TOTAL_BYTES:
                raise FirmwareSourceUnavailable("firmware source total size limit exceeded")
            try:
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise FirmwareSourceUnavailable("firmware source content is unavailable") from error
            source_items.append(FirmwareSourceFile(reference, content))

        sources = tuple(source_items)
        artifacts = tuple(artifact_items)
        if not sources and not artifacts and not allow_empty_source:
            raise FirmwareSourceUnavailable(
                "firmware source root is unavailable", missing_source=True
            )
        return FirmwareSourceSnapshot(
            project_id=checked_id,
            fingerprint=self._snapshot_fingerprint(checked_id, sources, artifacts),
            sources=sources,
            artifacts=artifacts,
        )

    def is_empty_project(self, project_id: str) -> bool:
        checked_id = self._project_id(project_id)
        root = self._root_resolver(checked_id)
        if not isinstance(root, Path) or not root.exists() or not root.is_dir() or root.is_symlink():
            raise FirmwareSourceUnavailable("project root is unavailable")
        ignored = {".git", ".idea", ".vscode", "build", "dist", "out"}
        return not any(item.name.casefold() not in ignored for item in root.iterdir())


__all__ = (
    "FirmwareArtifactMetadata",
    "FirmwareSourceFile",
    "FirmwareSourceProvider",
    "FirmwareSourceReference",
    "FirmwareSourceSnapshot",
    "FirmwareSourceUnavailable",
)
