from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import ArtifactIdentity, fingerprint


class ArtifactStore:
    """Immutable bytes store; public callers receive identity, never a path."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve(strict=False)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, identity: ArtifactIdentity, content: bytes) -> ArtifactIdentity:
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if digest != identity.artifact_sha256 or len(content) != identity.size_bytes:
            raise ValueError("artifact identity verification failed")
        target = (self._root / identity.artifact_id).resolve()
        if not target.is_relative_to(self._root):
            raise ValueError("artifact path escaped store")
        if target.exists():
            if target.read_bytes() != content:
                raise ValueError("immutable artifact drift")
            return identity
        target.write_bytes(content)
        return identity

    def read(self, identity: ArtifactIdentity) -> bytes:
        target = (self._root / identity.artifact_id).resolve()
        if not target.is_relative_to(self._root) or not target.is_file():
            raise FileNotFoundError("artifact unavailable")
        content = target.read_bytes()
        if "sha256:" + hashlib.sha256(content).hexdigest() != identity.artifact_sha256:
            raise ValueError("artifact identity drift")
        return content


__all__ = ("ArtifactStore",)
