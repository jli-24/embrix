from __future__ import annotations

import json
from pathlib import Path

import pytest

from embedded_copilot.knowledge.exceptions import (
    ProviderInvalidResult,
    ProviderUnavailable,
)
from embedded_copilot.knowledge.local import (
    LocalKnowledgeProvider as CompatibleLocalKnowledgeProvider,
)
from embedded_copilot.knowledge.models import KnowledgeQuery, KnowledgeResult
from embedded_copilot.knowledge.providers.local import LocalKnowledgeProvider


def _write_json(
    root: Path,
    directory_domain: str,
    name: str,
    **updates: object,
) -> Path:
    directory = root / directory_domain
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "id": f"{directory_domain}-{name}",
        "title": f"{directory_domain} {name}",
        "content": f"SPI {directory_domain} synthetic guidance",
        "category": "communication",
        "domain": directory_domain,
    }
    payload.update(updates)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_local_provider_import_path_remains_compatible() -> None:
    assert CompatibleLocalKnowledgeProvider is LocalKnowledgeProvider


def test_filesystem_mode_loads_canonical_json_and_markdown_in_stable_order(
    tmp_path: Path,
) -> None:
    _write_json(tmp_path, "firmware", "spi", score=0.2)
    hardware = tmp_path / "hardware"
    hardware.mkdir()
    (hardware / "power.md").write_text(
        "# Power Integrity\n\nESP32 camera power guidance.",
        encoding="utf-8",
    )

    provider = LocalKnowledgeProvider(knowledge_root=tmp_path)
    results = provider.search(KnowledgeQuery(query="SPI power", top_k=1))

    assert [result.id for result in results] == [
        "firmware-spi",
        "hardware:power",
    ]
    assert results[0].score == 0.2
    assert results[0].content == "SPI firmware synthetic guidance"
    assert results[0].metadata == {
        "category": "communication",
        "domain": "firmware",
    }
    assert results[1].title == "Power Integrity"
    assert results[1].metadata == {
        "category": "hardware",
        "domain": "hardware",
    }


def test_filesystem_mode_excludes_sidecars_and_noncanonical_content(
    tmp_path: Path,
) -> None:
    _write_json(tmp_path, "debug", "fault")
    (tmp_path / "debug" / "fault.md.metadata.json").write_text(
        "not-json",
        encoding="utf-8",
    )
    ignored = tmp_path / "other"
    ignored.mkdir()
    (ignored / "private.md").write_text("# Private\nSECRET", encoding="utf-8")
    (tmp_path / "debug" / "manual.pdf").write_bytes(b"synthetic")

    results = LocalKnowledgeProvider(knowledge_root=tmp_path).search(
        KnowledgeQuery(query="fault")
    )

    assert [result.id for result in results] == ["debug-fault"]


def test_filesystem_mode_ignores_compile_commands_build_manifest(
    tmp_path: Path,
) -> None:
    firmware = tmp_path / "firmware"
    firmware.mkdir()
    (firmware / "compile_commands.json").write_text("[]", encoding="utf-8")
    (firmware / "i2c.md").write_text(
        "# I2C\n\nI2C initialization evidence.", encoding="utf-8"
    )

    results = LocalKnowledgeProvider(knowledge_root=tmp_path).search(
        KnowledgeQuery(query="I2C")
    )

    assert [result.id for result in results] == ["firmware:i2c"]


def test_filesystem_mode_is_a_construction_time_snapshot(tmp_path: Path) -> None:
    _write_json(tmp_path, "firmware", "first")
    provider = LocalKnowledgeProvider(knowledge_root=tmp_path)
    _write_json(tmp_path, "firmware", "later")

    assert [
        result.id for result in provider.search(KnowledgeQuery(query="SPI"))
    ] == ["firmware-first"]


def test_filesystem_mode_rejects_invalid_json_without_path_or_content_leak(
    tmp_path: Path,
) -> None:
    path = _write_json(
        tmp_path,
        "pcb",
        "invalid",
        content="SECRET_SENTINEL",
        domain="firmware",
    )

    with pytest.raises(
        ProviderInvalidResult,
        match="local knowledge snapshot is invalid",
    ) as captured:
        LocalKnowledgeProvider(knowledge_root=tmp_path)

    message = str(captured.value)
    assert str(path) not in message
    assert "SECRET_SENTINEL" not in message


def test_filesystem_mode_rejects_content_in_json_metadata(tmp_path: Path) -> None:
    _write_json(
        tmp_path,
        "firmware",
        "unsafe-metadata",
        metadata={"snippet": "SECRET_SENTINEL"},
    )

    with pytest.raises(ProviderInvalidResult) as captured:
        LocalKnowledgeProvider(knowledge_root=tmp_path)

    assert str(captured.value) == "local knowledge snapshot is invalid"


def test_filesystem_mode_rejects_nested_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text(
        "# Private\n\nSECRET_SENTINEL",
        encoding="utf-8",
    )
    firmware = tmp_path / "firmware"
    firmware.mkdir()
    link = firmware / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with pytest.raises(
        ProviderInvalidResult,
        match="local knowledge snapshot is invalid",
    ):
        LocalKnowledgeProvider(knowledge_root=tmp_path)


def test_filesystem_mode_enforces_resolved_root_containment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firmware = tmp_path / "firmware"
    firmware.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n\nSECRET_SENTINEL", encoding="utf-8")
    original_rglob = Path.rglob

    def escaped_rglob(path: Path, pattern: str):
        if path == firmware:
            return iter([outside])
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", escaped_rglob)

    with pytest.raises(
        ProviderInvalidResult,
        match="local knowledge snapshot is invalid",
    ):
        LocalKnowledgeProvider(knowledge_root=tmp_path)


def test_filesystem_mode_rejects_missing_root_safely(tmp_path: Path) -> None:
    missing = tmp_path / "C-Users-private-SECRET_SENTINEL"

    with pytest.raises(
        ProviderUnavailable,
        match="local knowledge root is unavailable",
    ) as captured:
        LocalKnowledgeProvider(knowledge_root=missing)

    assert "SECRET_SENTINEL" not in str(captured.value)


class RecordingLoader:
    def __init__(self, results: list[KnowledgeResult]) -> None:
        self.results = results
        self.roots: list[Path] = []

    def load(self, root: Path) -> list[KnowledgeResult]:
        self.roots.append(root)
        return self.results


class LeakingUnavailableLoader:
    def load(self, root: Path) -> list[KnowledgeResult]:
        raise ProviderUnavailable("C:/Users/private/SECRET_SENTINEL")


class LeakingInvalidLoader:
    def load(self, root: Path) -> list[KnowledgeResult]:
        raise ProviderInvalidResult("C:/Users/private/SECRET_SENTINEL")


def test_injected_loader_is_called_once_and_snapshot_is_deeply_isolated(
    tmp_path: Path,
) -> None:
    original = KnowledgeResult(
        id="firmware-spi",
        title="SPI",
        content="SPI synthetic content",
        source="LOCAL",
        score=0.5,
        metadata={"category": "communication", "domain": "firmware"},
    )
    loader = RecordingLoader([original])
    provider = LocalKnowledgeProvider(knowledge_root=tmp_path, loader=loader)
    original.metadata["category"] = "mutated"

    results = provider.search(KnowledgeQuery(query="SPI"))

    assert loader.roots == [tmp_path]
    assert results[0].metadata["category"] == "communication"


def test_injected_loader_classified_error_is_sanitized(tmp_path: Path) -> None:
    with pytest.raises(ProviderUnavailable) as captured:
        LocalKnowledgeProvider(
            knowledge_root=tmp_path,
            loader=LeakingUnavailableLoader(),
        )

    assert str(captured.value) == "local knowledge root is unavailable"

    with pytest.raises(ProviderInvalidResult) as invalid:
        LocalKnowledgeProvider(
            knowledge_root=tmp_path,
            loader=LeakingInvalidLoader(),
        )

    assert str(invalid.value) == "local knowledge snapshot is invalid"


def test_filesystem_mode_is_mutually_exclusive_with_legacy_inputs(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ProviderInvalidResult,
        match="local provider configuration is invalid",
    ):
        LocalKnowledgeProvider(knowledge_root=tmp_path, firmware_retriever=object())

    provider = LocalKnowledgeProvider(knowledge_root=tmp_path)
    with pytest.raises(
        ProviderInvalidResult,
        match="filesystem snapshot is read-only",
    ):
        provider.add_documents([])
