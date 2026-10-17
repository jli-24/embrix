from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from embedded_copilot.knowledge.exceptions import (
    ProviderInvalidResult,
    ProviderUnavailable,
)
from embedded_copilot.knowledge.models import KnowledgeResult, KnowledgeSource
from embedded_copilot.schemas.result import ContractModel


_DOMAINS = ("firmware", "hardware", "pcb", "debug")
_BODY_METADATA_KEYS = {"body", "content", "snippet", "text"}


class _JsonDocument(ContractModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    category: str = Field(min_length=1)
    domain: Literal["firmware", "hardware", "pcb", "debug"]
    source: Literal[KnowledgeSource.LOCAL] = KnowledgeSource.LOCAL
    score: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("id", "title", "content", "category", "domain", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("metadata", mode="before")
    @classmethod
    def reject_body_metadata(cls, value: object) -> object:
        if _contains_body_metadata(value):
            raise ValueError("local metadata contains document body content")
        return value


class LocalDocumentLoader:
    """Load a deterministic local knowledge snapshot from an explicit root."""

    def load(self, root: Path) -> list[KnowledgeResult]:
        try:
            if not root.exists() or not root.is_dir():
                raise ProviderUnavailable("local knowledge root is unavailable")
            if root.is_symlink():
                raise ProviderInvalidResult(
                    "local knowledge snapshot is invalid"
                )
            resolved_root = root.resolve(strict=True)
            documents: list[KnowledgeResult] = []
            for domain in _DOMAINS:
                directory = root / domain
                if not directory.exists():
                    continue
                resolved_directory = directory.resolve(strict=True)
                if (
                    not directory.is_dir()
                    or directory.is_symlink()
                    or not resolved_directory.is_relative_to(resolved_root)
                ):
                    raise ProviderInvalidResult(
                        "local knowledge snapshot is invalid"
                    )
                entries = list(directory.rglob("*"))
                for entry in entries:
                    resolved_entry = entry.resolve(strict=True)
                    if (
                        entry.is_symlink()
                        or not resolved_entry.is_relative_to(resolved_directory)
                    ):
                        raise ProviderInvalidResult(
                            "local knowledge snapshot is invalid"
                        )
                paths = sorted(
                    (
                        path
                        for path in entries
                        if path.is_file()
                    ),
                    key=lambda path: (
                        path.relative_to(root).as_posix().casefold(),
                        path.relative_to(root).as_posix(),
                    ),
                )
                for path in paths:
                    if (
                        domain == "firmware"
                        and path.name.casefold() == "compile_commands.json"
                    ):
                        continue
                    suffix = path.suffix.casefold()
                    if suffix == ".md":
                        documents.append(_load_markdown(path, directory, domain))
                    elif suffix == ".json" and not path.name.casefold().endswith(
                        ".metadata.json"
                    ):
                        documents.append(_load_json(path, domain))
            return documents
        except (ProviderInvalidResult, ProviderUnavailable):
            raise
        except OSError as exc:
            raise ProviderUnavailable("local knowledge root is unavailable") from exc
        except Exception as exc:
            raise ProviderInvalidResult(
                "local knowledge snapshot is invalid"
            ) from exc


def _contains_body_metadata(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            isinstance(key, str)
            and key.strip().casefold() in _BODY_METADATA_KEYS
            or _contains_body_metadata(nested)
            for key, nested in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return any(_contains_body_metadata(item) for item in value)
    return False


def _load_json(path: Path, expected_domain: str) -> KnowledgeResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("local JSON document must be an object")
    document = _JsonDocument.model_validate(payload)
    if document.domain != expected_domain:
        raise ValueError("local JSON domain does not match its directory")
    return KnowledgeResult(
        id=document.id,
        title=document.title,
        content=document.content,
        source=document.source,
        score=document.score,
        metadata={
            **document.metadata,
            "category": document.category,
            "domain": document.domain,
        },
    )


def _load_markdown(
    path: Path,
    domain_root: Path,
    domain: str,
) -> KnowledgeResult:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError("local Markdown document must not be empty")
    title = next(
        (
            line.removeprefix("# ").strip()
            for line in content.splitlines()
            if line.startswith("# ") and line.removeprefix("# ").strip()
        ),
        path.stem.replace("_", " ").replace("-", " ").strip(),
    )
    relative_stem = path.relative_to(domain_root).with_suffix("").as_posix()
    return KnowledgeResult(
        id=f"{domain}:{relative_stem}",
        title=title,
        content=content,
        source=KnowledgeSource.LOCAL,
        metadata={"category": domain, "domain": domain},
    )
