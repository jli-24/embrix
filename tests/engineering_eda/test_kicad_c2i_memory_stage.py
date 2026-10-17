"""C2-I domain adapter is an offline bytes-only stage, not native validation."""

from __future__ import annotations

from hashlib import sha256

import pytest

from embedded_copilot.engineering_eda.adapters.kicad import (
    stage_kicad_footprint_position_bytes,
)
from embedded_copilot.pcb.parser.kicad import KiCadByteSpanRejected

UUID = "01234567-89ab-4cde-8f01-23456789abcd"
SOURCE = (
    b"(kicad_pcb (version 20240108) "
    b'(footprint "L:P" (uuid "' + UUID.encode() + b'") '
    b'(at 10 20 90) (pad "1" smd rect (at 0 0) (net 1 "GND"))))'
)


def test_adapter_returns_only_in_memory_stage() -> None:
    result = stage_kicad_footprint_position_bytes(
        SOURCE,
        UUID,
        expected_old=(10, 20),
        new=(111.25, -22.5),
        expected_source_sha256=sha256(SOURCE).hexdigest(),
    )
    first, second = result.edits
    assert result.staged_bytes == (
        SOURCE[: first.start]
        + first.after
        + SOURCE[first.end : second.start]
        + second.after
        + SOURCE[second.end :]
    )
    assert b'(pad "1" smd rect (at 0 0) (net 1 "GND"))' in result.staged_bytes
    assert SOURCE != result.staged_bytes


def test_adapter_rejects_source_change() -> None:
    with pytest.raises(KiCadByteSpanRejected) as error:
        stage_kicad_footprint_position_bytes(
            SOURCE,
            UUID,
            expected_old=(10, 20),
            new=(11, 21),
            expected_source_sha256="0" * 64,
        )
    assert error.value.code == "SOURCE_CHANGED"
