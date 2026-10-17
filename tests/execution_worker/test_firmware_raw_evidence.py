from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.adapters import _raw_stage_evidence
from embedded_copilot.execution_worker.contracts import BuildEvidence


def _fp(letter: str) -> str:
    return "sha256:" + letter * 64


def test_raw_compiler_evidence_preserves_output_and_redacts_workspace_path(tmp_path: Path) -> None:
    raw = _raw_stage_evidence(
        "cmake_build",
        1,
        f"{tmp_path}\\generated\\main.c:3: error: intentional failure".encode(),
        b"collect2.exe: error: returned 1",
        workspace_root=tmp_path,
    ).decode("utf-8")

    assert "stage=cmake_build" in raw
    assert "intentional failure" in raw
    assert "collect2.exe" in raw
    assert str(tmp_path) not in raw
    assert "<PROJECT>" in raw


def test_build_evidence_requires_raw_artifact_to_be_in_output_artifacts() -> None:
    with pytest.raises(ValueError, match="raw build evidence artifact"):
        BuildEvidence(
            operation_id="operation-g6",
            request_fingerprint=_fp("a"),
            result_status="BUILD_FAIL",
            toolchain_id="ARM_NONE_EABI_GCC",
            toolchain_version="15.3.1",
            output_artifact_ids=(),
            raw_evidence_artifact_id="artifact-raw-g6",
            created_at=datetime(2026, 9, 18, tzinfo=UTC),
        )

