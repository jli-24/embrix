from __future__ import annotations

from pydantic import BaseModel

from embedded_copilot.agents.types import AgentTask
from embedded_copilot.api.main import app
from embedded_copilot.benchmark.models import (
    BenchmarkCase,
    BenchmarkReport,
    BenchmarkResult,
    BenchmarkTrace,
    ExecutionMetrics,
)
from embedded_copilot.integration.report import EngineeringReport
from embedded_copilot.schemas.api import HealthResponse


def _shape(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()
    return {
        "properties": tuple(schema["properties"]),
        "required": tuple(schema.get("required", ())),
        "additionalProperties": schema.get("additionalProperties"),
    }


def test_core_model_contract_shapes_remain_compatible() -> None:
    assert _shape(AgentTask) == {
        "properties": ("task_id", "task_type", "requirement", "metadata"),
        "required": ("task_id", "task_type", "requirement"),
        "additionalProperties": False,
    }
    assert _shape(EngineeringReport) == {
        "properties": (
            "summary",
            "hardware_section",
            "firmware_section",
            "pcb_section",
            "debug_section",
            "recommendations",
            "trace",
        ),
        "required": ("summary",),
        "additionalProperties": False,
    }


def test_benchmark_public_model_contract_shapes_remain_compatible() -> None:
    assert tuple(BenchmarkCase.model_fields) == (
        "id",
        "name",
        "category",
        "input",
        "expected",
        "metadata",
    )
    assert tuple(BenchmarkResult.model_fields) == (
        "case_id",
        "success",
        "score",
        "metrics",
        "errors",
        "metadata",
    )
    assert tuple(BenchmarkReport.model_fields) == (
        "name",
        "total_cases",
        "passed_cases",
        "failed_cases",
        "average_score",
        "metrics",
        "results",
        "summary",
        "metadata",
    )
    assert tuple(BenchmarkTrace.model_fields) == (
        "case_id",
        "events",
        "execution_metrics",
    )
    assert tuple(ExecutionMetrics.model_fields) == (
        "execution_time_ms",
        "agent_calls",
        "knowledge_calls",
    )


def test_fastapi_public_paths_and_methods_remain_compatible() -> None:
    paths = app.openapi()["paths"]
    actual_routes = {path: tuple(methods) for path, methods in paths.items()}
    expected_legacy_routes = {
        "/api/v1/analyze": ("post",),
        "/api/v1/status/{execution_id}": ("get",),
        "/api/v1/report/{execution_id}": ("get",),
        "/api/v1/chat": ("post",),
        "/api/v1/health": ("get",),
        "/api/v1/copilot/models/status": ("get",),
        "/api/v1/copilot/sessions": ("post",),
        "/api/v1/copilot/sessions/{session_id}": ("get",),
        "/api/v1/copilot/sessions/{session_id}/attachments": ("post",),
        "/api/v1/copilot/sessions/{session_id}/messages": ("post",),
        "/api/v1/copilot/sessions/{session_id}/vision": ("post",),
        "/api/v1/copilot/sessions/{session_id}/workspace": ("get",),
        "/api/v1/copilot/sessions/{session_id}/artifact-view": ("get",),
        "/api/v1/copilot/sessions/{session_id}/files": ("get",),
        "/api/v1/copilot/sessions/{session_id}/files/analyze": ("post",),
        "/api/v1/copilot/sessions/{session_id}/progress": ("get",),
        "/api/v1/copilot/sessions/{session_id}/review": ("post",),
    }
    assert expected_legacy_routes.items() <= actual_routes.items()


def test_health_contract_changes_only_version_literal() -> None:
    schema = HealthResponse.model_json_schema()
    assert tuple(schema["properties"]) == ("status", "version", "mode")
    assert tuple(schema["required"]) == ("status", "mode")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["version"]["const"] == "7.1.0"
    assert schema["properties"]["version"]["default"] == "7.1.0"
