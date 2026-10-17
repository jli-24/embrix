from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_requirements import merge_requirement_context
from embedded_copilot.engineering_research import (
    EngineeringResearchService,
    FeatureDeltaKind,
    GitHubResearchSource,
    InitialRequirementBrief,
    ReferenceProjectDecisionAction,
    RequirementChangeAction,
    RequirementFinalConfirmation,
    ResearchResultClassification,
    ResearchStatus,
    SourceResultStatus,
)


def _brief(text: str = "做一个 ESP32-S3 智能手表，有 IMU、屏幕、电池充电和 USB") -> InitialRequirementBrief:
    return EngineeringResearchService.build_initial_requirement_brief(
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance=text,
    )


class _Transport:
    def __init__(self, results: list[dict[str, object]], *, fetched: dict[str, object] | None = None) -> None:
        self.results = results
        self.fetched = fetched
        self.search_calls: list[str] = []
        self.fetch_calls: list[str] = []

    def search(self, query: str) -> object:
        self.search_calls.append(query)
        return self.results

    def fetch(self, url: str) -> object:
        self.fetch_calls.append(url)
        if self.fetched is None:
            raise AssertionError("fetch result was not configured")
        return self.fetched


def test_successful_empty_source_is_no_result_and_not_unavailable() -> None:
    transport = _Transport([])
    source = GitHubResearchSource(transport=transport)
    snapshot = EngineeringResearchService(sources=(source,)).research(_brief())

    report = snapshot.evidence_pack.source_reports[0]
    assert report.status is SourceResultStatus.NO_RESULT
    assert snapshot.evidence_pack.status is ResearchStatus.NO_RELEVANT_REFERENCE
    assert snapshot.classification is ResearchResultClassification.NO_RELEVANT_REFERENCE


def test_reference_review_exclude_removes_project_from_gap_analysis_and_keeps_delta() -> None:
    transport = _Transport(
        [
            {
                "project_identity": "alice/watch",
                "title": "ESP32-S3 Smartwatch",
                "description": "Wearable watch with display and battery monitoring.",
                "url": "https://github.com/alice/watch",
                "owner": "alice",
                "repository": "watch",
                "artifact_hints": {"schematic": "PRESENT", "firmware": "PRESENT"},
                "extracted_facts": [
                    {"category": "BATTERY_MEASUREMENT", "value": "battery monitoring", "artifact": "README", "location": "power"},
                    {"category": "GPS", "value": "GPS", "artifact": "README", "location": "features"},
                ],
            }
        ]
    )
    service = EngineeringResearchService(sources=(GitHubResearchSource(transport=transport),))
    snapshot = service.research(_brief())
    review = snapshot.reference_reviews[0]
    kinds = {item.delta for item in review.feature_deltas}
    assert FeatureDeltaKind.MATCHED in kinds
    assert FeatureDeltaKind.REFERENCE_EXTRA in kinds
    assert FeatureDeltaKind.USER_ONLY in kinds

    excluded = service.decide_reference_project(
        snapshot,
        project_identity="alice/watch",
        decision=ReferenceProjectDecisionAction.EXCLUDE,
        decided_by="engineer-1",
        decided_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert excluded.reference_reviews[0].decision is ReferenceProjectDecisionAction.EXCLUDE
    assert excluded.reference_reviews[0].feature_deltas
    assert excluded.suggestions == ()


def test_free_text_creates_pending_typed_proposal_and_only_confirmation_changes_truth() -> None:
    context = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="做一个 ESP32-S3 智能手表，有 IMU、屏幕、电池充电和 USB",
    )
    service = EngineeringResearchService(sources=(GitHubResearchSource(transport=_Transport([])),))
    proposal = service.interpret_requirement_feedback(
        context,
        "电量和振动加上，GPS不要，RTC先不确定，屏幕改圆形 AMOLED。",
        research_snapshot=None,
        decided_by="engineer-1",
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
    )

    assert proposal.status.value == "PENDING"
    assert {item.action for item in proposal.decisions} >= {
        RequirementChangeAction.ADD,
        RequirementChangeAction.REJECT,
        RequirementChangeAction.DEFER,
        RequirementChangeAction.MODIFY,
    }
    assert context.fingerprint == proposal.base_requirement_fingerprint
    confirmation = RequirementFinalConfirmation(
        confirmation_id="confirmation-1",
        proposal_id=proposal.proposal_id,
        project_id=proposal.project_id,
        conversation_id=proposal.conversation_id,
        proposal_fingerprint=proposal.fingerprint,
        confirmed=True,
        confirmed_by="engineer-1",
        confirmed_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    updated = service.commit_requirement_change(context, proposal, confirmation)
    assert updated.fingerprint != context.fingerprint
    assert any(item.normalized_value == "BATTERY_LEVEL_MONITORING" for item in updated.requirements)
    assert any(item.status.value == "REMOVED_BY_USER" for item in updated.requirements)


def test_direct_url_fetch_does_not_call_search() -> None:
    transport = _Transport(
        [],
        fetched={
            "project_identity": "alice/watch",
            "title": "Watch",
            "description": "Fetched GitHub reference",
            "url": "https://github.com/alice/watch",
            "owner": "alice",
            "repository": "watch",
            "revision": "abc123",
        },
    )
    service = EngineeringResearchService(sources=(GitHubResearchSource(transport=transport),))

    snapshot = service.research(_brief(), reference_urls=("https://github.com/alice/watch",))

    assert transport.fetch_calls == ["https://github.com/alice/watch"]
    assert transport.search_calls == []
    assert snapshot.evidence_pack.projects[0].revision == "abc123"


def test_confirmation_rejects_stale_requirement_context() -> None:
    original = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="做一个 ESP32-S3 智能手表，有 IMU、屏幕",
    )
    service = EngineeringResearchService(sources=(GitHubResearchSource(transport=_Transport([])),))
    proposal = service.interpret_requirement_feedback(
        original,
        "振动加上",
        research_snapshot=None,
        decided_by="engineer-1",
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    changed = merge_requirement_context(
        original,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="屏幕改圆形 AMOLED",
    )
    confirmation = RequirementFinalConfirmation(
        confirmation_id="confirmation-1",
        proposal_id=proposal.proposal_id,
        project_id=proposal.project_id,
        conversation_id=proposal.conversation_id,
        proposal_fingerprint=proposal.fingerprint,
        confirmed=True,
        confirmed_by="engineer-1",
        confirmed_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="stale"):
        service.commit_requirement_change(changed, proposal, confirmation)
