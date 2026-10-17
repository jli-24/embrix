from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_research import (
    EngineeringResearchService,
    EngineeringResearchSnapshot,
    GitHubResearchSource,
    RequirementChangeStatus,
    RequirementDecisionAction,
    RequirementGapAnalyzer,
    ResearchSourceType,
    UnavailableResearchSource,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
    RequirementSuggestionDecisionRejected,
)
from embedded_copilot.engineering_requirements import merge_requirement_context

NOW = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


class Answer:
    def model_dump(self, mode: str = "json") -> dict[str, object]:
        del mode
        return {
            "answer_text": "已完成参考项目调研",
            "evidence_details": (),
            "recommendations": (),
        }


def _runtime(tmp_path: Path, research: EngineeringResearchService) -> ProjectCatalogRuntime:
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    return ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=Clock(),
        initialize_async=False,
        research_service=research,
    )


def test_g0r_snapshot_and_decision_survive_sqlite_restart(tmp_path: Path) -> None:
    service = _runtime(
        tmp_path,
        EngineeringResearchService(
            sources=(
                # No live source is needed: this test persists a typed pack
                # through the same production conversation boundary.
                UnavailableResearchSource(ResearchSourceType.GITHUB, "TEST_UNAVAILABLE"),
            )
        ),
    )
    preview = service.preview(
        ProjectCreationRequest(
            name="Smartwatch",
            relative_path="smartwatch",
            project_type=ProjectType.ESP_IDF,
        ),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    conversation = service.create_conversation(
        project.project_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    detail = service.conversation(
        project.project_id,
        conversation.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    brief = EngineeringResearchService.build_initial_requirement_brief(
        project_id=project.project_id,
        conversation_id=detail.conversation_id,
        turn_id="request-1",
        utterance="做一个 ESP32-S3 智能手表，带 IMU、屏幕、电池充电和 USB",
    )
    pack = EngineeringResearchService.evidence_pack_from_facts(
        brief,
        source_type=ResearchSourceType.GITHUB,
        project_identity="alice/smartwatch",
        url="https://github.com/alice/smartwatch",
        title="ESP32-S3 smartwatch reference",
        description="A smartwatch reference with battery and USB design notes",
        facts=(
            {"category": "BATTERY_MEASUREMENT", "value": "battery gauge", "artifact": "README"},
        ),
    )
    snapshot = EngineeringResearchSnapshot(
        project_id=project.project_id,
        conversation_id=detail.conversation_id,
        brief=brief,
        evidence_pack=pack,
        suggestions=RequirementGapAnalyzer().analyze(brief, pack),
    )
    service.begin_turn(
        conversation.session_id,
        request_id="request-1",
        trace_id="trace-1",
        question=brief.user_goals[0],
        created_at=NOW,
    )
    service.complete_turn(
        conversation.session_id,
        "request-1",
        Answer(),
        NOW,
        route="GREENFIELD_DISCOVERY",
        trace_id="trace-1",
        analysis_state="REQUIREMENTS_PARTIAL",
        requirement_context=detail.requirement_context,
        research_snapshot=snapshot,
    )

    reopened = _runtime(tmp_path, service.research_service)
    restored = reopened.conversation(
        project.project_id,
        detail.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.research_snapshot is not None
    suggestion = restored.research_snapshot.suggestions[0]
    assert restored.research_snapshot.fingerprint == snapshot.fingerprint

    decided = reopened.decide_requirement_suggestion(
        project.project_id,
        detail.conversation_id,
        suggestion.suggestion_id,
        RequirementDecisionAction.ACCEPT,
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
    )
    assert decided.requirement_suggestion_decisions[0].action is RequirementDecisionAction.ACCEPT
    assert decided.research_snapshot is not None
    assert decided.research_snapshot.suggestions[0].user_decision is RequirementDecisionAction.ACCEPT
    assert any(item.category == "POWER" for item in decided.requirement_context.requirements)

    restarted_again = _runtime(tmp_path, service.research_service)
    final = restarted_again.conversation(
        project.project_id,
        detail.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert final.requirement_suggestion_decisions[0].action is RequirementDecisionAction.ACCEPT
    assert final.research_snapshot is not None
    assert final.research_snapshot.suggestions[0].user_decision is RequirementDecisionAction.ACCEPT

    try:
        restarted_again.decide_requirement_suggestion(
            project.project_id,
            detail.conversation_id,
            suggestion.suggestion_id,
            RequirementDecisionAction.REJECT,
            tenant_id="tenant-1",
            requester="engineer-1",
            decided_at=NOW,
        )
    except RequirementSuggestionDecisionRejected:
        pass
    else:
        raise AssertionError("terminal G0R decision must be immutable")


class _ProductionTransport:
    def search(self, query: str) -> object:
        del query
        return [{
            "project_identity": "alice/watch",
            "title": "ESP32-S3 smartwatch",
            "description": "watch display reference",
            "url": "https://github.com/alice/watch",
            "owner": "alice",
            "repository": "watch",
            "artifact_hints": {"firmware": "PRESENT"},
        }]

    def fetch(self, url: str) -> object:
        del url
        return self.search("direct") [0]


def test_g0r_production_proposal_and_confirmation_survive_restart(tmp_path: Path) -> None:
    research = EngineeringResearchService(sources=(GitHubResearchSource(transport=_ProductionTransport()),))
    runtime = _runtime(tmp_path, research)
    preview = runtime.preview(
        ProjectCreationRequest(name="Smartwatch", relative_path="smartwatch", project_type=ProjectType.ESP_IDF),
        tenant_id="tenant-1", requester="engineer-1",
    )
    project = runtime.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    created = runtime.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    detail = runtime.conversation(project.project_id, created.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    context = merge_requirement_context(
        None,
        project_id=project.project_id,
        conversation_id=detail.conversation_id,
        turn_id="request-1",
        utterance="做一个 ESP32-S3 智能手表，有 IMU、屏幕",
    )
    brief = research.build_initial_requirement_brief(
        project_id=project.project_id,
        conversation_id=detail.conversation_id,
        turn_id="request-1",
        utterance="做一个 ESP32-S3 智能手表，有 IMU、屏幕",
    )
    snapshot = research.research(brief)
    runtime.begin_turn(created.session_id, request_id="request-1", question=brief.user_goals[0], created_at=NOW)
    runtime.complete_turn(created.session_id, "request-1", Answer(), NOW, requirement_context=context, research_snapshot=snapshot)
    proposal_detail = runtime.propose_requirement_changes(
        project.project_id, detail.conversation_id, "振动加上", tenant_id="tenant-1", requester="engineer-1", created_at=NOW,
    )
    assert proposal_detail.requirement_change_proposal is not None
    proposal = proposal_detail.requirement_change_proposal
    assert proposal.status is RequirementChangeStatus.PENDING
    restarted = _runtime(tmp_path, research)
    restored = restarted.conversation(project.project_id, detail.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    assert restored.requirement_change_proposal is not None
    confirmed = restarted.confirm_requirement_changes(
        project.project_id,
        detail.conversation_id,
        proposal.proposal_id,
        tenant_id="tenant-1",
        requester="engineer-1",
        confirmed=True,
        proposal_fingerprint=proposal.fingerprint,
        confirmed_at=NOW,
    )
    assert confirmed.requirement_change_proposal is not None
    assert confirmed.requirement_change_proposal.status is RequirementChangeStatus.CONFIRMED
    final = _runtime(tmp_path, research).conversation(project.project_id, detail.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    assert any(item.normalized_value == "VIBRATION" for item in final.requirement_context.requirements)
