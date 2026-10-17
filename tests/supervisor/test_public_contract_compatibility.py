from __future__ import annotations

import hashlib
import inspect
import json

import pytest

from embedded_copilot.agents.types import AgentTask
from embedded_copilot.benchmark.models import (
    BenchmarkBaseline,
    BenchmarkCase,
    BenchmarkReport,
    BenchmarkResult,
    BenchmarkTrace,
    ExecutionMetrics,
    RegressionReport,
    TraceEvent,
)
from embedded_copilot.debug.models import DebugReport
from embedded_copilot.firmware.project.models import FirmwareProject
from embedded_copilot.hardware.models import HardwarePlan
from embedded_copilot.knowledge.gateway import KnowledgeGateway
from embedded_copilot.pcb.models import PCBReviewReport
from embedded_copilot.schemas.api import HealthResponse
from embedded_copilot.schemas.result import ContractModel

PUBLIC_SCHEMA_HASHES: tuple[tuple[type[ContractModel], str], ...] = (
    (
        AgentTask,
        "79538070fc529dd2c2afbef09ef3e6059cf9a4380621279282ff25db9184410f",
    ),
    (
        FirmwareProject,
        "c00beb11127f9034f55702239a43f518e9968d0d74cc366f483815054d00c9bf",
    ),
    (
        HardwarePlan,
        "3fa3ec944e0e2f5ed72a9374c7ba5f0f72ba750de513eec48a19b1b20b78d1b3",
    ),
    (
        PCBReviewReport,
        "fce6e2dc50442013ae84bb1e7f30a98d778f7cfdadecfeb95781f1ca64343242",
    ),
    (
        DebugReport,
        "bb3db84675154c86ffb42a0a1551a4c7ad2b544fad09211d643327d51eaf5a08",
    ),
    (
        BenchmarkCase,
        "7958885680c3d430774c642003d18d3a9a99e4c6e7295aaafd9eedab395473c5",
    ),
    (
        BenchmarkResult,
        "bade650cfae732b330a37f6c04585efc24ca9d8ec22d1f6f7aca7105891aacc7",
    ),
    (
        BenchmarkReport,
        "8e520263ec79591069ae5032ea8bdffd85ac35403901b76f247318d31eb65412",
    ),
    (
        TraceEvent,
        "6b4c6fe2b85ba9becd71e1381579ed421a707ba790e11796fbdcf14134934caf",
    ),
    (
        BenchmarkTrace,
        "834cb5350a20dfbf72e24cb3ecc4b30f5252f61ca37b588cd567a7f30d9cc061",
    ),
    (
        ExecutionMetrics,
        "378f9426d28b155ca18f177fe068a2ea805d733b27e2469ab76ee7a13a1ad2c9",
    ),
    (
        BenchmarkBaseline,
        "c78ae43734c5137bf478013bf9021d92b554a3980ec13748785b3dcb1ab89e4b",
    ),
    (
        RegressionReport,
        "b0b4f332160b352f3c00a3eb9df9c2412659dab5dd121cbeafeca905ac3a0a50",
    ),
)


def _schema_hash(model: type[ContractModel]) -> str:
    canonical = json.dumps(
        model.model_json_schema(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("model", "expected_hash"), PUBLIC_SCHEMA_HASHES)
def test_public_model_schema_matches_v011_contract(
    model: type[ContractModel],
    expected_hash: str,
) -> None:
    assert _schema_hash(model) == expected_hash


def test_knowledge_gateway_signature_matches_current_public_contract() -> None:
    assert str(inspect.signature(KnowledgeGateway.__init__)) == (
        "(self, providers: 'Sequence[KnowledgeProvider] | None' = None) -> 'None'"
    )
    assert str(inspect.signature(KnowledgeGateway.search)) == (
        "(self, query: 'KnowledgeQuery') -> 'list[KnowledgeResult]'"
    )


def test_health_schema_preserves_fields_and_required_state() -> None:
    schema = HealthResponse.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["status", "mode"]
    assert set(schema["properties"]) == {"status", "version", "mode"}
    assert schema["properties"]["status"]["enum"] == ["ok", "degraded", "unavailable"]
    assert schema["properties"]["mode"]["enum"] == ["offline", "llm"]
    assert schema["properties"]["version"]["const"] == "7.1.0"
    assert schema["properties"]["version"]["default"] == "7.1.0"
