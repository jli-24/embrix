from __future__ import annotations

from pathlib import Path

import embedded_copilot.copilot as copilot
from embedded_copilot.agents.types import AgentResult
from embedded_copilot.api.models import AnalyzeRequest, AnalyzeResponse
from embedded_copilot.hardware_design.artifact import HardwareDesignArtifact
from embedded_copilot.integration.report import EngineeringReport


def test_existing_public_contracts_remain_unchanged() -> None:
    assert set(HardwareDesignArtifact.model_fields) == {
        "schema_version",
        "blueprint",
        "evidence",
        "decisions",
        "approval",
    }
    assert set(AgentResult.model_fields) == {
        "agent_name",
        "status",
        "output",
        "metadata",
    }
    assert set(AnalyzeRequest.model_fields) == {
        "request",
        "attachments",
        "options",
    }
    assert set(AnalyzeResponse.model_fields) == {"execution_id", "status"}
    assert set(EngineeringReport.model_fields) == {
        "summary",
        "hardware_section",
        "firmware_section",
        "pcb_section",
        "debug_section",
        "recommendations",
        "trace",
    }


def test_copilot_package_exports_only_workspace_contract_operations() -> None:
    expected = {
        "ApprovalAction",
        "ApprovalEvent",
        "ArtifactDecisionView",
        "ArtifactEvidenceView",
        "ArtifactView",
        "ChatMessage",
        "ChatRole",
        "DesignSessionContext",
        "DesignStage",
        "KnowledgeTrace",
        "KnowledgeTraceAction",
        "ModelInputType",
        "ModelRequest",
        "ModelTaskType",
        "ProjectWorkspace",
        "SessionApprovalStatus",
        "WorkflowProgress",
        "WorkflowProgressStatus",
        "WorkspaceFile",
        "WorkspaceFileSource",
        "WorkspaceFileStatus",
        "WorkspaceFileType",
        "advance_stage",
        "bind_artifact",
        "create_session",
        "create_workspace",
        "project_artifact_view",
        "record_approval_event",
        "record_knowledge_trace",
        "record_message",
        "record_progress",
        "track_file",
        "update_progress",
    }

    assert set(copilot.__all__) == expected
    assert all(hasattr(copilot, name) for name in expected)
    for forbidden in (
        "create_artifact",
        "update_artifact",
        "approve_artifact",
        "reject_artifact",
        "delete_artifact",
        "execute_model",
        "run_agent",
    ):
        assert not hasattr(copilot, forbidden)


def test_only_approved_adapters_may_depend_on_workspace() -> None:
    package_root = Path(__file__).parents[2] / "src" / "embedded_copilot"
    approved_adapters = {
        package_root / "api" / "copilot_models.py",
        package_root / "api" / "copilot_routes.py",
        package_root / "api" / "copilot_environment_routes.py",
        package_root / "experience" / "existing_contracts.py",
        package_root / "intelligence" / "esp32.py",
        package_root / "services" / "experience_runtime.py",
    }
    consumers = tuple(
        path
        for path in package_root.rglob("*.py")
        if not {
            "copilot",
            "conversation",
        }.intersection(path.relative_to(package_root).parts)
        and path not in approved_adapters
    )

    assert consumers
    for path in consumers:
        source = path.read_text(encoding="utf-8")
        assert "embedded_copilot.copilot" not in source
