from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_requirements import merge_requirement_context
from embedded_copilot.engineering_research import (
    ArtifactState,
    EngineeringResearchService,
    GitHubResearchSource,
    InitialRequirementBrief,
    LicenseState,
    RequirementDecisionAction,
    RequirementGapAnalyzer,
    RequirementSuggestionStatus,
    ResearchSourceType,
    ResearchStatus,
    UnavailableResearchSource,
    apply_requirement_suggestion_decision,
)


class MappingTransport:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str) -> object:
        self.queries.append(query)
        return self.results


def _brief(*, question: str = "做一个 ESP32-S3 智能手表，有 IMU、屏幕、电池充电、USB。") -> InitialRequirementBrief:
    return EngineeringResearchService.build_initial_requirement_brief(
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance=question,
    )


def _github_source(transport: MappingTransport) -> GitHubResearchSource:
    return GitHubResearchSource(transport=transport)


def test_smartwatch_research_builds_typed_pack_and_deduplicates_suggestions() -> None:
    transport = MappingTransport(
        [
            {
                "project_identity": "alice/smartwatch",
                "title": "ESP32-S3 Smartwatch",
                "description": "A wearable watch with IMU, display and battery support.",
                "url": "https://github.com/alice/smartwatch",
                "owner": "alice",
                "repository": "smartwatch",
                "revision": "abc123",
                "license": "MIT",
                "artifact_hints": {
                    "schematic": "PRESENT",
                    "pcb": "PRESENT",
                    "bom": "UNKNOWN",
                    "firmware": "PRESENT",
                    "documentation": "PRESENT",
                },
                "extracted_facts": [
                    {
                        "category": "BATTERY_MEASUREMENT",
                        "value": "battery measurement",
                        "artifact": "README",
                        "location": "power section",
                    },
                    {
                        "category": "USB_DATA",
                        "value": "USB data/download",
                        "artifact": "README",
                        "location": "USB section",
                    },
                ],
            },
            {
                "project_identity": "alice/temperature-sensor",
                "title": "ESP32 Temperature Sensor",
                "description": "A basic ESP32 temperature sensor board.",
                "url": "https://github.com/alice/temperature-sensor",
                "owner": "alice",
                "repository": "temperature-sensor",
            },
            {
                "project_identity": "not-a-real-source-result",
                "title": "Invented project",
                "description": "should be rejected because the source URL is invalid",
                "url": "https://example.invalid/project",
            },
        ]
    )
    service = EngineeringResearchService(
        sources=(
            _github_source(transport),
            UnavailableResearchSource(ResearchSourceType.OSHWHUB, "API_UNAVAILABLE"),
            UnavailableResearchSource(ResearchSourceType.VENDOR_REFERENCE, "INDEX_UNAVAILABLE"),
        ),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    result = service.research(_brief())

    assert result.evidence_pack.status is ResearchStatus.PARTIAL
    assert len(result.evidence_pack.projects) == 1
    project = result.evidence_pack.projects[0]
    assert project.project_identity == "alice/smartwatch"
    assert project.license_state is LicenseState.KNOWN
    assert project.artifacts.bom is ArtifactState.UNKNOWN
    assert project.artifacts.firmware is ArtifactState.PRESENT
    assert all("temperature" not in item.project_identity for item in result.evidence_pack.projects)
    assert all(item.source_type is not ResearchSourceType.OSHWHUB for item in result.evidence_pack.projects)
    assert len({item.category for item in result.suggestions}) == len(result.suggestions)
    assert {item.category for item in result.suggestions} == {"POWER", "CONNECTIVITY"}
    assert all(item.current_requirement_status is RequirementSuggestionStatus.REFERENCE_SUGGESTED for item in result.suggestions)
    assert transport.queries


def test_explicit_requirement_suppresses_reference_suggestion() -> None:
    brief = _brief(question="做一个 ESP32-S3 智能手表，有 IMU、屏幕；不要 RTC。")
    evidence = EngineeringResearchService.evidence_pack_from_facts(
        brief,
        source_type=ResearchSourceType.GITHUB,
        project_identity="alice/watch",
        url="https://github.com/alice/watch",
        title="Watch",
        description="A wearable watch with RTC.",
        facts=(
            {
                "category": "RTC",
                "value": "real time clock",
                "artifact": "SCHEMATIC",
                "location": "U2",
            },
        ),
    )

    suggestions = RequirementGapAnalyzer().analyze(brief, evidence)

    rtc = next(item for item in suggestions if item.category == "TIMEKEEPING")
    assert rtc.current_requirement_status is RequirementSuggestionStatus.SUPPRESSED_BY_EXPLICIT_USER_REQUIREMENT
    assert rtc.user_decision is RequirementDecisionAction.SUPPRESSED


def test_user_decision_is_the_only_requirement_truth_promotion_boundary() -> None:
    context = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="做一个 ESP32-S3 智能手表，有 IMU、屏幕、电池充电、USB。",
    )
    brief = _brief()
    evidence = EngineeringResearchService.evidence_pack_from_facts(
        brief,
        source_type=ResearchSourceType.GITHUB,
        project_identity="alice/watch",
        url="https://github.com/alice/watch",
        title="Watch",
        description="A wearable watch with battery measurement.",
        facts=(
            {
                "category": "BATTERY_MEASUREMENT",
                "value": "battery measurement",
                "artifact": "README",
                "location": "power section",
            },
        ),
    )
    suggestion = next(item for item in RequirementGapAnalyzer().analyze(brief, evidence) if item.category == "POWER")

    unchanged = apply_requirement_suggestion_decision(
        context,
        suggestion,
        action=RequirementDecisionAction.REJECT,
        decided_by="engineer-1",
    )
    assert unchanged.fingerprint == context.fingerprint
    assert not any(item.category == "POWER" for item in unchanged.requirements)

    accepted = apply_requirement_suggestion_decision(
        context,
        suggestion,
        action=RequirementDecisionAction.ACCEPT,
        decided_by="engineer-1",
    )
    assert accepted.fingerprint != context.fingerprint
    assert any(item.category == "POWER" for item in accepted.requirements)
    assert all(item.status.value == "CONFIRMED" for item in accepted.requirements)


def test_fake_project_identity_cannot_enter_pack() -> None:
    transport = MappingTransport(
        [
            {
                "project_identity": "alice/watch",
                "title": "Watch",
                "description": "Wearable project",
                "url": "https://github.com/alice/watch",
            }
        ]
    )
    source = _github_source(transport)
    brief = _brief()

    result = source.search(("ESP32-S3 smartwatch",), brief)

    assert len(result.candidates) == 1
    assert result.candidates[0].project_identity == "alice/watch"
    with pytest.raises(ValueError):
        MappingTransport([{"project_identity": "fake", "title": "Fake", "description": "x", "url": "not-url"}])
        GitHubResearchSource(transport=MappingTransport([])).candidate_from_mapping(
            {"project_identity": "fake", "title": "Fake", "description": "x", "url": "not-url"}
        )


def test_all_source_failure_is_unavailable_and_does_not_look_like_empty_success() -> None:
    service = EngineeringResearchService(
        sources=(
            UnavailableResearchSource(ResearchSourceType.GITHUB, "SOURCE_UNAVAILABLE"),
            UnavailableResearchSource(ResearchSourceType.OSHWHUB, "API_UNAVAILABLE"),
            UnavailableResearchSource(ResearchSourceType.VENDOR_REFERENCE, "INDEX_UNAVAILABLE"),
        )
    )

    result = service.research(_brief())

    assert result.evidence_pack.status is ResearchStatus.UNAVAILABLE
    assert result.evidence_pack.projects == ()
    assert not result.suggestions
    assert {item.status.value for item in result.evidence_pack.source_reports} == {"UNAVAILABLE"}


def test_modify_decision_records_requirement_semantics_without_selecting_hardware() -> None:
    context = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="USB 充电接口",
    )
    brief = _brief(question="做一个 ESP32-S3 智能手表，有 USB。")
    evidence = EngineeringResearchService.evidence_pack_from_facts(
        brief,
        source_type=ResearchSourceType.GITHUB,
        project_identity="alice/watch",
        url="https://github.com/alice/watch",
        title="Watch",
        description="A wearable watch with USB data.",
        facts=({"category": "USB_DATA", "value": "USB data", "artifact": "README"},),
    )
    suggestion = next(
        item for item in RequirementGapAnalyzer().analyze(brief, evidence)
        if item.category == "CONNECTIVITY"
    )

    modified = apply_requirement_suggestion_decision(
        context,
        suggestion,
        action=RequirementDecisionAction.MODIFY,
        decided_by="engineer-1",
        modified_requirement="只需要充电，不需要数据",
    )

    assert any(
        item.category == "CONNECTIVITY"
        and item.normalized_value == "USB_CHARGING_ONLY"
        and item.status.value == "CONFIRMED"
        for item in modified.requirements
    )
