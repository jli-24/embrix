from __future__ import annotations

import copy
import hashlib
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementItem,
    RequirementItemStatus,
    RequirementLifecycle,
    RequirementPriority,
    RequirementProvenance,
    RequirementStatus,
)

from .adapters import ResearchSourcePort
from .contracts import (
    _AUTO,
    ArtifactState,
    EngineeringResearchSnapshot,
    FactProvenance,
    FeatureDecision,
    FeatureDecisionAction,
    FeatureDeltaKind,
    FeatureEvidenceState,
    InitialRequirementBrief,
    ReferenceFact,
    ReferenceFeatureDelta,
    ReferenceProjectCandidate,
    ReferenceProjectDecisionAction,
    ReferenceProjectEvidence,
    ReferenceProjectEvidencePack,
    ReferenceProjectReview,
    ReferenceType,
    RelevanceAssessment,
    RequirementChangeAction,
    RequirementChangeProposal,
    RequirementChangeStatus,
    RequirementDecision,
    RequirementDecisionAction,
    RequirementFinalConfirmation,
    RequirementSuggestion,
    RequirementSuggestionDecision,
    RequirementSuggestionStatus,
    ResearchQuery,
    ResearchResultClassification,
    ResearchSourceType,
    ResearchStatus,
    SourceResearchReport,
    SourceResearchResponse,
    SourceResultStatus,
    _identifier,
    _text,
)

_GOAL = re.compile(r"(?:做个|做一个|开发一个|实现一个|设计一个)\s*([^，。,.!?！？\n]{1,64})")
_FEATURES: tuple[tuple[str, str], ...] = (
    ("IMU", "IMU"),
    ("陀螺仪", "IMU"),
    ("加速度计", "IMU"),
    ("屏幕", "DISPLAY"),
    ("显示", "DISPLAY"),
    ("display", "DISPLAY"),
    ("电池充电", "BATTERY_CHARGING"),
    ("充电", "BATTERY_CHARGING"),
    ("USB", "USB"),
    ("触摸", "TOUCH"),
    ("touch", "TOUCH"),
    ("振动", "VIBRATION"),
    ("vibration", "VIBRATION"),
    ("RTC", "RTC"),
    ("按键", "BUTTONS"),
    ("按钮", "BUTTONS"),
    ("backlight", "BACKLIGHT"),
    ("背光", "BACKLIGHT"),
)
_FACT_TO_SUGGESTION = {
    "BATTERY_MEASUREMENT": ("POWER", "BATTERY_LEVEL_MONITORING", "是否需要电池电量检测或剩余电量显示？"),
    "USB_DATA": ("CONNECTIVITY", "USB_DATA_ROLE", "USB 是否需要数据或下载功能？"),
    "USB_DATA_ROLE": ("CONNECTIVITY", "USB_DATA_ROLE", "USB 是否需要数据或下载功能？"),
    "TOUCH": ("INPUT", "TOUCH", "是否需要触摸输入？"),
    "RTC": ("TIMEKEEPING", "RTC", "是否需要 RTC 或离线时间保持？"),
    "VIBRATION": ("FEEDBACK", "VIBRATION", "是否需要振动反馈？"),
    "BUTTONS": ("INPUT", "BUTTONS", "是否需要实体按键？"),
    "BACKLIGHT": ("DISPLAY", "BACKLIGHT", "是否需要背光控制？"),
    "CHARGING_STATUS": ("POWER", "CHARGING_STATUS", "是否需要充电状态指示？"),
    "BATTERY_LIFE": ("POWER", "BATTERY_LIFE_TARGET", "续航目标是多少？"),
    "ESD": ("RELIABILITY", "ESD_PROTECTION", "是否需要明确 ESD 防护要求？"),
    "DEBUG_INTERFACE": ("DEBUG", "DEBUG_INTERFACE", "是否需要保留调试或下载接口？"),
}


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _now() -> datetime:
    return datetime.now(UTC)


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            result.append(normalized)
    return tuple(result)


def _product_type(utterance: str) -> str | None:
    lowered = utterance.casefold()
    if "智能手表" in utterance or "smartwatch" in lowered:
        return "SMARTWATCH"
    if "手表" in utterance or "watch" in lowered:
        return "WATCH"
    if "可穿戴" in utterance or "wearable" in lowered:
        return "WEARABLE"
    match = _GOAL.search(utterance)
    return None if match is None else match.group(1).strip()


def _negative_constraints(utterance: str) -> tuple[str, ...]:
    lowered = utterance.casefold()
    values: list[str] = []
    for marker, value in (
        ("rtc", "NO_RTC"),
        ("振动", "NO_VIBRATION"),
        ("vibration", "NO_VIBRATION"),
        ("触摸", "NO_TOUCH"),
        ("按键", "NO_BUTTONS"),
        ("背光", "NO_BACKLIGHT"),
    ):
        if marker in lowered and any(phrase in lowered for phrase in ("不要", "不需要", "不做", "无需")):
            values.append(value)
    if "只需要充电" in utterance or "仅需要充电" in utterance or "不需要数据" in utterance:
        values.append("USB_CHARGING_ONLY")
    return _unique(values)


def is_requirement_change_feedback(utterance: str) -> bool:
    """Identify refinement language without treating free text as approved truth."""

    if not isinstance(utterance, str) or not utterance.strip() or len(utterance) > 8192 or "\x00" in utterance:
        return False
    checked = utterance.strip()
    lowered = checked.casefold()
    if any(lowered.startswith(marker) for marker in ("做个", "做一个", "开发一个", "实现一个", "设计一个")):
        return False
    if any(marker in lowered for marker in ("需要", "我要", "想要", "希望有", "带有")) and any(
        marker in lowered for marker in ("imu", "屏幕", "显示", "电量", "电池", "usb", "gps", "rtc", "振动", "按键", "触摸")
    ):
        return True
    return any(
        marker in lowered
        for marker in (
            "加上",
            "不要",
            "不需要",
            "去掉",
            "删掉",
            "改成",
            "改为",
            "改圆形",
            "不确定",
            "待定",
            "先不",
        )
    )


def extract_reference_urls(utterance: str) -> tuple[str, ...]:
    if not isinstance(utterance, str) or len(utterance) > 8192:
        return ()
    checked = utterance.strip()
    return tuple(dict.fromkeys(item.rstrip(".,;:!?！？。；，）》)】") for item in re.findall(r"https://[^\s<>\"']+", checked)))


class RequirementGapAnalyzer:
    def analyze(
        self,
        brief: InitialRequirementBrief,
        evidence_pack: ReferenceProjectEvidencePack,
        *,
        excluded_project_identities: tuple[str, ...] = (),
        allowed_project_features: dict[str, tuple[str, ...]] | None = None,
    ) -> tuple[RequirementSuggestion, ...]:
        if brief.project_id != evidence_pack.project_id or brief.conversation_id != evidence_pack.conversation_id:
            raise ValueError("research gap analysis identity mismatch")
        grouped: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: {"projects": set(), "evidence": set()})
        excluded = set(excluded_project_identities)
        for project in evidence_pack.projects:
            if project.project_identity in excluded:
                continue
            allowed = None if allowed_project_features is None else allowed_project_features.get(project.project_identity)
            for fact in project.extracted_facts:
                if allowed is not None and EngineeringResearchService._feature_name(f"{fact.category} {fact.value}") not in {item.upper() for item in allowed}:
                    continue
                mapping = _FACT_TO_SUGGESTION.get(fact.category.upper())
                if mapping is None:
                    continue
                category, value, _ = mapping
                grouped[(category, value)]["projects"].add(project.source_identity)
                grouped[(category, value)]["evidence"].add(f"{project.source_identity}:fact:{fact.fact_id}")

        explicit = {item.casefold() for item in brief.explicit_features}
        constraints = {item.casefold() for item in brief.explicit_constraints}
        suggestions: list[RequirementSuggestion] = []
        for category, value in sorted(grouped):
            question = _FACT_TO_SUGGESTION[next(key for key, result in _FACT_TO_SUGGESTION.items() if result[:2] == (category, value))][2]
            feature_markers = {value.casefold(), category.casefold()}
            suppressed = any(
                marker in constraints
                for marker in {
                    f"no_{value.casefold()}",
                    f"no_{category.casefold()}",
                    "usb_charging_only" if value == "USB_DATA_ROLE" else "__none__",
                }
            )
            if not suppressed and feature_markers & explicit:
                continue
            status = (
                RequirementSuggestionStatus.SUPPRESSED_BY_EXPLICIT_USER_REQUIREMENT
                if suppressed
                else RequirementSuggestionStatus.REFERENCE_SUGGESTED
            )
            action = RequirementDecisionAction.SUPPRESSED if suppressed else RequirementDecisionAction.PENDING
            projects = tuple(sorted(grouped[(category, value)]["projects"]))
            evidence = tuple(sorted(grouped[(category, value)]["evidence"]))
            suggestions.append(
                RequirementSuggestion(
                    suggestion_id=_stable_id("suggestion", brief.project_id, brief.conversation_id, category, value),
                    project_id=brief.project_id,
                    conversation_id=brief.conversation_id,
                    category=category,
                    suggested_requirement=value,
                    requirement_value=value,
                    reason=f"{len(projects)} 个相关参考项目包含 {value} 相关设计模式；这只是参考证据，不是当前工程事实。",
                    source_project_refs=projects,
                    supporting_evidence_refs=evidence,
                    current_requirement_status=status,
                    user_decision=action,
                    question_to_user=question,
                )
            )
        return tuple(suggestions)


class EngineeringResearchService:
    def __init__(
        self,
        *,
        sources: tuple[ResearchSourcePort, ...],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(sources) is not tuple or not sources:
            raise TypeError("research sources are invalid")
        source_types = tuple(source.source_type for source in sources)
        if len(set(source_types)) != len(source_types):
            raise ValueError("research sources must be unique")
        if any(not isinstance(source.source_type, ResearchSourceType) for source in sources):
            raise TypeError("research source type is invalid")
        self._sources = sources
        self._clock = clock or _now

    @staticmethod
    def build_initial_requirement_brief(
        *, project_id: str, conversation_id: str, turn_id: str, utterance: str
    ) -> InitialRequirementBrief:
        checked = _text(utterance, field="initial requirement", maximum=8192)
        lowered = checked.casefold()
        features = [normalized for marker, normalized in _FEATURES if marker.casefold() in lowered]
        platform = None
        for candidate in ("ESP32-S3", "ESP32-C6", "ESP32", "STM32H7", "STM32F4", "STM32"):
            if candidate.casefold() in lowered:
                platform = candidate
                break
        interfaces = [item for marker, item in (("usb", "USB"), ("i2c", "I2C"), ("spi", "SPI"), ("uart", "UART"), ("ble", "BLE"), ("蓝牙", "BLE"), ("wifi", "WIFI")) if marker in lowered]
        power = [item for marker, item in (("电池", "BATTERY"), ("锂电", "BATTERY"), ("充电", "CHARGING"), ("低功耗", "LOW_POWER"), ("续航", "BATTERY_LIFE")) if marker in lowered]
        return InitialRequirementBrief(
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            product_type=_product_type(checked),
            explicit_features=_unique(features),
            explicit_constraints=_negative_constraints(checked),
            preferred_platform=platform,
            known_interfaces=_unique(interfaces),
            known_power_requirements=_unique(power),
            user_goals=(checked,),
        )

    @staticmethod
    def queries_for(brief: InitialRequirementBrief) -> tuple[ResearchQuery, ...]:
        platform = brief.preferred_platform or "embedded"
        product = {
            "SMARTWATCH": "smartwatch",
            "WATCH": "watch",
            "WEARABLE": "wearable",
        }.get(brief.product_type or "", brief.product_type or "embedded device")
        features = " ".join(brief.explicit_features[:3])
        return (
            ResearchQuery(source_type=ResearchSourceType.GITHUB, query=f"{platform} {product}"),
            ResearchQuery(source_type=ResearchSourceType.GITHUB, query=f"{platform} {product} KiCad"),
            ResearchQuery(source_type=ResearchSourceType.OSHWHUB, query=f"{platform} {product}"),
            ResearchQuery(source_type=ResearchSourceType.OSHWHUB, query=f"{platform} {features}" if features else f"{platform} {product}"),
            ResearchQuery(source_type=ResearchSourceType.VENDOR_REFERENCE, query=f"{platform} {product} reference design"),
            ResearchQuery(source_type=ResearchSourceType.VENDOR_REFERENCE, query=f"{platform} USB battery reference"),
        )

    @staticmethod
    def _relevance(brief: InitialRequirementBrief, candidate: ReferenceProjectCandidate) -> RelevanceAssessment | None:
        text = f"{candidate.title} {candidate.description} {candidate.project_identity}".casefold()
        product_tokens = {
            "SMARTWATCH": ("smartwatch", "smart watch", "watch", "手表"),
            "WATCH": ("watch", "手表"),
            "WEARABLE": ("wearable", "watch", "可穿戴"),
        }.get(brief.product_type or "", (brief.product_type or "",))
        product_overlap = any(token.casefold() in text for token in product_tokens if token)
        platform_overlap = bool(brief.preferred_platform and brief.preferred_platform.casefold() in text)
        feature_tokens = {
            "IMU": ("imu", "inertial", "加速度", "陀螺"),
            "DISPLAY": ("display", "screen", "oled", "lcd", "屏幕"),
            "BATTERY_CHARGING": ("battery", "charging", "充电", "电池"),
            "USB": ("usb",),
            "TOUCH": ("touch", "触摸"),
        }
        overlap = tuple(
            feature
            for feature in brief.explicit_features
            if any(token in text for token in feature_tokens.get(feature, (feature.casefold(),)))
        )
        hardware = any(getattr(candidate.artifacts, field) is ArtifactState.PRESENT for field in ("schematic", "pcb", "bom"))
        firmware = candidate.artifacts.firmware is ArtifactState.PRESENT
        relevant = (product_overlap and (platform_overlap or bool(overlap))) or (platform_overlap and bool(overlap))
        if candidate.source_type is ResearchSourceType.VENDOR_REFERENCE:
            relevant = platform_overlap or bool(overlap)
        if not relevant:
            return None
        rationale = tuple(
            item
            for item in (
                "product_type" if product_overlap else "",
                "MCU/platform" if platform_overlap else "",
                "feature overlap=" + ",".join(overlap) if overlap else "",
                "hardware artifacts" if hardware else "",
                "firmware artifact" if firmware else "",
            )
            if item
        )
        return RelevanceAssessment(
            product_type_overlap=product_overlap,
            platform_overlap=platform_overlap,
            feature_overlap=overlap,
            hardware_artifact_overlap=hardware,
            firmware_overlap=firmware,
            rationale=rationale,
        )

    @staticmethod
    def _reference_type(candidate: ReferenceProjectCandidate) -> ReferenceType:
        if candidate.source_type is ResearchSourceType.VENDOR_REFERENCE:
            return ReferenceType.VENDOR_REFERENCE
        hardware = any(getattr(candidate.artifacts, field) is ArtifactState.PRESENT for field in ("schematic", "pcb", "bom"))
        firmware = candidate.artifacts.firmware is ArtifactState.PRESENT
        if hardware and firmware:
            return ReferenceType.FULL_PROJECT
        if hardware and not firmware:
            return ReferenceType.HARDWARE_REFERENCE
        if firmware and not hardware:
            return ReferenceType.FIRMWARE_REFERENCE
        return ReferenceType.PARTIAL_REFERENCE

    @classmethod
    def _evidence(cls, brief: InitialRequirementBrief, candidate: ReferenceProjectCandidate, relevance: RelevanceAssessment) -> ReferenceProjectEvidence:
        source_identity = f"{candidate.source_type.value.lower()}:{candidate.project_identity}"
        fact_refs = tuple(f"{source_identity}:fact:{fact.fact_id}" for fact in candidate.extracted_facts)
        if candidate.source_type is ResearchSourceType.VENDOR_REFERENCE:
            classification = ResearchResultClassification.OFFICIAL_REFERENCE_ONLY
        elif relevance.product_type_overlap and relevance.platform_overlap and relevance.feature_overlap:
            classification = ResearchResultClassification.DIRECT_MATCH
        elif relevance.product_type_overlap or relevance.platform_overlap:
            classification = ResearchResultClassification.ANALOGOUS_MATCH
        else:
            classification = ResearchResultClassification.SUBSYSTEM_MATCH
        return ReferenceProjectEvidence(
            source_type=candidate.source_type,
            source_identity=source_identity,
            project_identity=candidate.project_identity,
            url=candidate.url,
            revision=candidate.revision,
            license_state=candidate.license_state,
            license_name=candidate.license_name,
            title=candidate.title,
            description=candidate.description,
            reference_type=cls._reference_type(candidate),
            artifacts=candidate.artifacts,
            extracted_facts=candidate.extracted_facts,
            relevance=relevance,
            provenance=(candidate.url, *(fact_refs or (source_identity,))),
            classification=classification,
        )

    @staticmethod
    def _feature_name(value: str) -> str:
        lowered = value.casefold()
        for marker, normalized in (
            ("imu", "IMU"),
            ("陀螺", "IMU"),
            ("加速度", "IMU"),
            ("display", "DISPLAY"),
            ("screen", "DISPLAY"),
            ("屏幕", "DISPLAY"),
            ("显示", "DISPLAY"),
            ("oled", "DISPLAY"),
            ("amoled", "DISPLAY"),
            ("battery", "BATTERY"),
            ("电池", "BATTERY"),
            ("charging", "BATTERY"),
            ("充电", "BATTERY"),
            ("usb", "USB"),
            ("touch", "TOUCH"),
            ("触摸", "TOUCH"),
            ("vibration", "VIBRATION"),
            ("振动", "VIBRATION"),
            ("gps", "GPS"),
            ("rtc", "RTC"),
            ("button", "BUTTONS"),
            ("按键", "BUTTONS"),
        ):
            if marker in lowered:
                return normalized
        return value.strip().upper()

    @classmethod
    def _reference_features(cls, evidence: ReferenceProjectEvidence) -> tuple[tuple[str, FeatureEvidenceState, tuple[str, ...]], ...]:
        found: dict[str, tuple[FeatureEvidenceState, tuple[str, ...]]] = {}
        for fact in evidence.extracted_facts:
            feature = cls._feature_name(f"{fact.category} {fact.value}")
            refs = (f"{evidence.source_identity}:fact:{fact.fact_id}",)
            found[feature] = (FeatureEvidenceState.PRESENT, refs)
        text = f"{evidence.title} {evidence.description}".casefold()
        for marker, feature in (("display", "DISPLAY"), ("screen", "DISPLAY"), ("屏幕", "DISPLAY"), ("battery", "BATTERY"), ("电池", "BATTERY"), ("usb", "USB"), ("imu", "IMU"), ("gps", "GPS")):
            if marker in text and feature not in found:
                found[feature] = (FeatureEvidenceState.INFERRED, (evidence.url,))
        return tuple((feature, state, refs) for feature, (state, refs) in sorted(found.items()))

    @classmethod
    def _review_for_evidence(
        cls,
        brief: InitialRequirementBrief,
        evidence: ReferenceProjectEvidence,
        research_fingerprint: str,
    ) -> ReferenceProjectReview:
        user_features = {cls._feature_name(item) for item in brief.explicit_features}
        reference_features = {item[0]: item[1:] for item in cls._reference_features(evidence)}
        deltas: list[ReferenceFeatureDelta] = []
        for feature in sorted(user_features | set(reference_features)):
            in_user = feature in user_features
            in_reference = feature in reference_features
            if in_user and in_reference:
                kind = FeatureDeltaKind.MATCHED
                state, refs = reference_features[feature]
                description = f"用户需求与参考项目均包含 {feature}。"
            elif in_reference:
                kind = FeatureDeltaKind.REFERENCE_EXTRA
                state, refs = reference_features[feature]
                description = f"参考项目包含用户未明确提出的 {feature}，需要人工判断是否采纳。"
            else:
                kind = FeatureDeltaKind.USER_ONLY
                state, refs = FeatureEvidenceState.INFERRED, (f"user:{brief.turn_id}:{feature}",)
                description = f"用户明确提出 {feature}，参考项目未提供对应证据。"
            deltas.append(
                ReferenceFeatureDelta(
                    delta_id=_stable_id("delta", brief.project_id, evidence.project_identity, feature),
                    project_id=brief.project_id,
                    conversation_id=brief.conversation_id,
                    project_identity=evidence.project_identity,
                    research_fingerprint=research_fingerprint,
                    reference_fingerprint=evidence.fingerprint,
                    feature=feature,
                    delta=kind,
                    evidence_state=state,
                    evidence_refs=refs,
                    description=description,
                )
            )
        return ReferenceProjectReview(
            project_id=brief.project_id,
            conversation_id=brief.conversation_id,
            project_identity=evidence.project_identity,
            research_fingerprint=research_fingerprint,
            reference_fingerprint=evidence.fingerprint,
            title=evidence.title,
            description=evidence.description,
            feature_deltas=tuple(deltas),
        )

    def research(
        self,
        brief: InitialRequirementBrief,
        *,
        reference_urls: tuple[str, ...] = (),
    ) -> EngineeringResearchSnapshot:
        checked = InitialRequirementBrief.model_validate(copy.deepcopy(brief.model_dump(mode="python")), strict=False)
        queries = self.queries_for(checked)
        by_source: dict[ResearchSourceType, tuple[str, ...]] = {
            source_type: tuple(query.query for query in queries if query.source_type is source_type)
            for source_type in (source.source_type for source in self._sources)
        }
        reports: list[SourceResearchReport] = []
        projects: list[ReferenceProjectEvidence] = []
        seen: set[tuple[ResearchSourceType, str]] = set()
        direct_urls = tuple(_url for _url in reference_urls if isinstance(_url, str) and _url.strip())
        for source in self._sources:
            source_queries = by_source.get(source.source_type, ())
            try:
                source_direct_urls = tuple(
                    url for url in direct_urls
                    if (
                        (source.source_type is ResearchSourceType.GITHUB and urlsplit(url).hostname == "github.com")
                        or (source.source_type is ResearchSourceType.OSHWHUB and urlsplit(url).hostname == "oshwhub.com")
                        or (source.source_type is ResearchSourceType.VENDOR_REFERENCE and urlsplit(url).hostname is not None and any(urlsplit(url).hostname == host or urlsplit(url).hostname.endswith("." + host) for host in ("espressif.com", "st.com", "arm.com", "ti.com", "nxp.com", "silabs.com", "freertos.org")))
                    )
                )
                if source_direct_urls:
                    fetcher = getattr(source, "fetch", None)
                    if not callable(fetcher):
                        response = SourceResearchResponse(source_type=source.source_type, status=SourceResultStatus.UNAVAILABLE, error_code="DIRECT_FETCH_UNAVAILABLE")
                    else:
                        fetched: list[ReferenceProjectCandidate] = []
                        response = None
                        for url in source_direct_urls:
                            current = fetcher(url, checked)
                            if current.status not in {SourceResultStatus.SUCCESS, SourceResultStatus.NO_RESULT}:
                                response = current
                                break
                            fetched.extend(current.candidates)
                        response = response or SourceResearchResponse(
                            source_type=source.source_type,
                            status=SourceResultStatus.SUCCESS if fetched else SourceResultStatus.NO_RESULT,
                            candidates=tuple(fetched),
                        )
                elif direct_urls:
                    response = SourceResearchResponse(source_type=source.source_type, status=SourceResultStatus.NO_RESULT)
                else:
                    response = source.search(source_queries, checked)
                response = SourceResearchResponse.model_validate(copy.deepcopy(response.model_dump(mode="python")), strict=False)
            except Exception:  # noqa: BLE001 - source port boundary is fail-closed
                response = SourceResearchResponse(source_type=source.source_type, status=SourceResultStatus.ERROR, error_code="SOURCE_ERROR")
            reports.append(
                SourceResearchReport(
                    source_type=source.source_type,
                    status=response.status,
                    capabilities=source.capabilities,
                    queries=source_queries,
                    candidate_count=len(response.candidates),
                    error_code=response.error_code,
                )
            )
            for candidate in response.candidates:
                key = (candidate.source_type, candidate.project_identity)
                if key in seen:
                    continue
                seen.add(key)
                relevance = self._relevance(checked, candidate)
                if relevance is None and candidate.url in direct_urls:
                    relevance = RelevanceAssessment(
                        product_type_overlap=False,
                        platform_overlap=False,
                        feature_overlap=(),
                        rationale=("direct_url_fetch",),
                    )
                if relevance is not None:
                    projects.append(self._evidence(checked, candidate, relevance))
        operational = {SourceResultStatus.SUCCESS, SourceResultStatus.NO_RESULT}
        failed_reports = [report for report in reports if report.status not in operational and report.status is not SourceResultStatus.PARTIAL]
        if not reports or len(failed_reports) == len(reports):
            status = ResearchStatus.UNAVAILABLE
        elif not projects and all(report.status in operational for report in reports):
            status = ResearchStatus.NO_RELEVANT_REFERENCE
        elif failed_reports or any(report.status is SourceResultStatus.PARTIAL for report in reports):
            status = ResearchStatus.PARTIAL
        else:
            status = ResearchStatus.COMPLETE
        pack = ReferenceProjectEvidencePack(
            project_id=checked.project_id,
            conversation_id=checked.conversation_id,
            brief_fingerprint=checked.fingerprint,
            queries=queries,
            projects=tuple(sorted(projects, key=lambda item: (item.source_type.value, item.project_identity))),
            source_reports=tuple(reports),
            status=status,
            retrieved_at=self._clock(),
        )
        reviews = tuple(self._review_for_evidence(checked, item, pack.fingerprint) for item in pack.projects)
        classification = (
            ResearchResultClassification.NO_RELEVANT_REFERENCE
            if not pack.projects
            else min(
                (item.classification for item in pack.projects),
                key=(
                    ResearchResultClassification.DIRECT_MATCH,
                    ResearchResultClassification.ANALOGOUS_MATCH,
                    ResearchResultClassification.SUBSYSTEM_MATCH,
                    ResearchResultClassification.OFFICIAL_REFERENCE_ONLY,
                ).index,
            )
        )
        suggestions = RequirementGapAnalyzer().analyze(checked, pack, excluded_project_identities=())
        return EngineeringResearchSnapshot(
            project_id=checked.project_id,
            conversation_id=checked.conversation_id,
            brief=checked,
            evidence_pack=pack,
            suggestions=suggestions,
            classification=classification,
            reference_reviews=reviews,
        )

    @staticmethod
    def decide_suggestion(
        snapshot: EngineeringResearchSnapshot,
        decision: RequirementSuggestionDecision,
    ) -> EngineeringResearchSnapshot:
        if decision.project_id != snapshot.project_id or decision.conversation_id != snapshot.conversation_id:
            raise ValueError("requirement decision identity mismatch")
        target = next((item for item in snapshot.suggestions if item.suggestion_id == decision.suggestion_id), None)
        if target is None:
            raise ValueError("requirement suggestion was not found")
        if target.user_decision not in {RequirementDecisionAction.PENDING, RequirementDecisionAction.SUPPRESSED}:
            if target.user_decision is decision.action:
                return snapshot
            raise ValueError("requirement suggestion decision is immutable")
        status = {
            RequirementDecisionAction.ACCEPT: RequirementSuggestionStatus.USER_APPROVED,
            RequirementDecisionAction.MODIFY: RequirementSuggestionStatus.USER_APPROVED,
            RequirementDecisionAction.REJECT: RequirementSuggestionStatus.USER_REJECTED,
            RequirementDecisionAction.DEFER: RequirementSuggestionStatus.DEFERRED,
        }.get(decision.action)
        if status is None:
            raise ValueError("requirement decision action is invalid")
        updated_target = target.model_copy(update={"user_decision": decision.action, "current_requirement_status": status, "fingerprint": _AUTO})
        updated = tuple(updated_target if item.suggestion_id == target.suggestion_id else item for item in snapshot.suggestions)
        return snapshot.model_copy(update={"suggestions": updated, "fingerprint": _AUTO})

    @staticmethod
    def decide_reference_project(
        snapshot: EngineeringResearchSnapshot,
        *,
        project_identity: str,
        decision: ReferenceProjectDecisionAction,
        decided_by: str,
        decided_at: datetime,
        selected_features: tuple[str, ...] = (),
    ) -> EngineeringResearchSnapshot:
        target = next((item for item in snapshot.reference_reviews if item.project_identity == project_identity), None)
        if target is None:
            raise ValueError("reference project review was not found")
        _identifier(decided_by, field="decided_by")
        selected = tuple(_text(item, field="selected_feature", maximum=256) for item in selected_features)
        updated_review = target.model_copy(
            update={"decision": decision, "selected_features": selected, "fingerprint": _AUTO}
        )
        reviews = tuple(updated_review if item.project_identity == project_identity else item for item in snapshot.reference_reviews)
        excluded = tuple(item.project_identity for item in reviews if item.decision is ReferenceProjectDecisionAction.EXCLUDE)
        allowed = {
            item.project_identity: item.selected_features
            for item in reviews
            if item.decision is ReferenceProjectDecisionAction.PARTIAL_REFERENCE
        }
        suggestions = RequirementGapAnalyzer().analyze(
            snapshot.brief,
            snapshot.evidence_pack,
            excluded_project_identities=excluded,
            allowed_project_features=allowed,
        )
        return snapshot.model_copy(update={"reference_reviews": reviews, "suggestions": suggestions, "fingerprint": _AUTO})

    @staticmethod
    def decide_feature(snapshot: EngineeringResearchSnapshot, decision: FeatureDecision) -> EngineeringResearchSnapshot:
        if decision.project_id != snapshot.project_id or decision.conversation_id != snapshot.conversation_id:
            raise ValueError("feature decision identity mismatch")
        project = next((item for item in snapshot.evidence_pack.projects if item.project_identity == decision.project_identity), None)
        if project is None or project.fingerprint != decision.reference_fingerprint or decision.research_fingerprint != snapshot.evidence_pack.fingerprint:
            raise ValueError("feature decision is stale or unbound")
        existing = tuple(item for item in snapshot.feature_decisions if item.project_identity == decision.project_identity and item.feature == decision.feature)
        if existing and existing[-1].fingerprint == decision.fingerprint:
            return snapshot
        kept = tuple(item for item in snapshot.feature_decisions if not (item.project_identity == decision.project_identity and item.feature == decision.feature))
        return snapshot.model_copy(update={"feature_decisions": (*kept, decision), "fingerprint": _AUTO})

    @staticmethod
    def _current_requirement(context: RequirementContext, category: str, value: str) -> RequirementItem | None:
        normalized = value.casefold()
        return next(
            (
                item
                for item in (*context.requirements, *context.constraints)
                if item.status is RequirementItemStatus.CONFIRMED
                and (item.category.casefold() == category.casefold() or item.normalized_value.casefold() == normalized)
            ),
            None,
        )

    def interpret_requirement_feedback(
        self,
        context: RequirementContext,
        utterance: str,
        *,
        research_snapshot: EngineeringResearchSnapshot | None,
        decided_by: str,
        created_at: datetime,
    ) -> RequirementChangeProposal:
        checked = RequirementContext.model_validate(copy.deepcopy(context.model_dump(mode="python")), strict=False)
        text = _text(utterance, field="requirement feedback", maximum=8192)
        _identifier(decided_by, field="decided_by")
        decisions: list[RequirementDecision] = []

        def add(
            action: RequirementChangeAction,
            *,
            target: str,
            category: str,
            value: str | None = None,
            current: RequirementItem | None = None,
        ) -> None:
            decisions.append(
                RequirementDecision(
                    decision_id=_stable_id("decision", checked.project_id, checked.conversation_id, action.value, target, value or ""),
                    project_id=checked.project_id,
                    conversation_id=checked.conversation_id,
                    action=action,
                    target=target,
                    category=category,
                    value=value,
                    current_requirement_id=None if current is None else current.requirement_id,
                    evidence_refs=() if research_snapshot is None else tuple(
                        item.provenance[0] for item in research_snapshot.evidence_pack.projects if item.provenance
                    ),
                    source="FREE_TEXT_INTERPRETATION",
                )
            )

        if any(marker in text for marker in ("电量", "电池电量", "电量检测", "battery level")):
            current = self._current_requirement(checked, "POWER", "BATTERY_LEVEL_MONITORING")
            add(RequirementChangeAction.KEEP if current else RequirementChangeAction.ADD, target="BATTERY_LEVEL_MONITORING", category="POWER", value=None if current else "BATTERY_LEVEL_MONITORING", current=current)
        if any(marker in text for marker in ("振动加", "加入振动", "需要振动", "我要振动", "想要振动", "带有振动", "vibration")):
            current = self._current_requirement(checked, "FEEDBACK", "VIBRATION")
            add(RequirementChangeAction.KEEP if current else RequirementChangeAction.ADD, target="VIBRATION", category="FEEDBACK", value=None if current else "VIBRATION", current=current)

        for marker, category, value in (("GPS", "NAVIGATION", "GPS"), ("NFC", "CONNECTIVITY", "NFC"), ("麦克风", "AUDIO", "MICROPHONE"), ("IMU", "SENSOR", "IMU")):
            if marker.casefold() in text.casefold() and any(phrase in text for phrase in ("不要", "不需要", "去掉", "删掉", "不用")):
                current = self._current_requirement(checked, category, value)
                add(RequirementChangeAction.REMOVE if current else RequirementChangeAction.REJECT, target=value, category=category, value=None if current else value, current=current)

        if "GPS" in text.upper() and any(phrase in text for phrase in ("需要", "我要", "想要", "加上")) and not any(phrase in text for phrase in ("不要", "不需要")):
            current = self._current_requirement(checked, "NAVIGATION", "GPS")
            add(RequirementChangeAction.KEEP if current else RequirementChangeAction.ADD, target="GPS", category="NAVIGATION", value=None if current else "GPS", current=current)

        if "RTC" in text.upper() and any(marker in text for marker in ("不确定", "待定", "以后再定", "暂不")):
            add(RequirementChangeAction.DEFER, target="RTC", category="TIMEKEEPING")

        if any(marker in text for marker in ("圆形 AMOLED", "圆形AMOLED", "round AMOLED", "round amoled")):
            current = self._current_requirement(checked, "DISPLAY", "DISPLAY")
            add(RequirementChangeAction.MODIFY, target="DISPLAY", category="DISPLAY", value="ROUND_AMOLED", current=current)

        if "USB" in text.upper() and any(marker in text for marker in ("只需要充电", "仅需要充电", "不需要数据")):
            current = self._current_requirement(checked, "CONNECTIVITY", "USB")
            add(RequirementChangeAction.MODIFY, target="USB", category="CONNECTIVITY", value="USB_CHARGING_ONLY", current=current)

        if not decisions:
            add(RequirementChangeAction.DEFER, target="UNSPECIFIED_FEEDBACK", category="UNSPECIFIED")
        summary = "；".join(
            f"{item.action.value} {item.target}" + (f" -> {item.value}" if item.value else "")
            for item in decisions
        )
        return RequirementChangeProposal(
            proposal_id=_stable_id("proposal", checked.project_id, checked.conversation_id, text),
            project_id=checked.project_id,
            conversation_id=checked.conversation_id,
            base_requirement_fingerprint=checked.fingerprint,
            research_fingerprint=None if research_snapshot is None else research_snapshot.evidence_pack.fingerprint,
            reference_fingerprints=() if research_snapshot is None else tuple(item.fingerprint for item in research_snapshot.evidence_pack.projects),
            decisions=tuple(decisions),
            summary=summary,
            created_by=decided_by,
            created_at=created_at,
        )

    def proposal_from_feature_decision(
        self,
        context: RequirementContext,
        snapshot: EngineeringResearchSnapshot,
        decision: FeatureDecision,
    ) -> RequirementChangeProposal | None:
        if decision.action in {FeatureDecisionAction.KEEP, FeatureDecisionAction.DEFER}:
            return None
        category = {
            "IMU": "SENSOR",
            "DISPLAY": "DISPLAY",
            "BATTERY": "POWER",
            "USB": "CONNECTIVITY",
            "GPS": "NAVIGATION",
            "RTC": "TIMEKEEPING",
            "VIBRATION": "FEEDBACK",
            "BUTTONS": "INPUT",
        }.get(self._feature_name(decision.feature), "FEATURE")
        feature = self._feature_name(decision.feature)
        current = self._current_requirement(context, category, feature)
        action = {
            FeatureDecisionAction.ADD: RequirementChangeAction.ADD,
            FeatureDecisionAction.REMOVE: RequirementChangeAction.REMOVE if current is not None else RequirementChangeAction.REJECT,
            FeatureDecisionAction.MODIFY: RequirementChangeAction.MODIFY,
            FeatureDecisionAction.REJECT: RequirementChangeAction.REJECT,
        }[decision.action]
        value = decision.value if action is RequirementChangeAction.MODIFY else (feature if action in {RequirementChangeAction.ADD, RequirementChangeAction.REJECT} else None)
        review = next((item for item in snapshot.reference_reviews if item.project_identity == decision.project_identity), None)
        refs = () if review is None else tuple(
            ref for delta in review.feature_deltas if delta.feature == feature for ref in delta.evidence_refs
        )
        requirement_decision = RequirementDecision(
            decision_id=_stable_id("decision", decision.decision_id, feature),
            project_id=context.project_id,
            conversation_id=context.conversation_id,
            action=action,
            target=feature,
            category=category,
            value=value,
            current_requirement_id=None if current is None else current.requirement_id,
            evidence_refs=refs,
            source="FEATURE_DELTA_DECISION",
        )
        return RequirementChangeProposal(
            proposal_id=_stable_id("proposal", context.project_id, context.conversation_id, decision.decision_id),
            project_id=context.project_id,
            conversation_id=context.conversation_id,
            base_requirement_fingerprint=context.fingerprint,
            research_fingerprint=snapshot.evidence_pack.fingerprint,
            reference_fingerprints=(decision.reference_fingerprint,),
            decisions=(requirement_decision,),
            summary=f"{action.value} {feature}" + (f" -> {value}" if value else ""),
            created_by=decision.decided_by,
            created_at=decision.decided_at,
        )

    @staticmethod
    def commit_requirement_change(
        context: RequirementContext,
        proposal: RequirementChangeProposal,
        confirmation: RequirementFinalConfirmation,
    ) -> RequirementContext:
        checked = RequirementContext.model_validate(copy.deepcopy(context.model_dump(mode="python")), strict=False)
        if (
            proposal.project_id != checked.project_id
            or proposal.conversation_id != checked.conversation_id
            or confirmation.project_id != checked.project_id
            or confirmation.conversation_id != checked.conversation_id
            or confirmation.proposal_id != proposal.proposal_id
            or confirmation.proposal_fingerprint != proposal.fingerprint
        ):
            raise ValueError("requirement confirmation identity mismatch")
        if checked.fingerprint != proposal.base_requirement_fingerprint:
            raise ValueError("stale requirement context")
        if proposal.status is not RequirementChangeStatus.PENDING:
            raise ValueError("requirement change proposal is not pending")
        if not confirmation.confirmed:
            return checked
        requirements = list(checked.requirements)
        constraints = list(checked.constraints)
        all_items = requirements + constraints
        for decision in proposal.decisions:
            current = next((item for item in all_items if item.requirement_id == decision.current_requirement_id), None)
            if decision.action in {RequirementChangeAction.KEEP, RequirementChangeAction.DEFER}:
                continue
            if decision.action is RequirementChangeAction.REJECT:
                if decision.value is not None and current is None:
                    requirements.append(
                        RequirementItem(
                            requirement_id=_stable_id("requirement", proposal.proposal_id, decision.decision_id, decision.value),
                            project_id=checked.project_id,
                            conversation_id=checked.conversation_id,
                            category=decision.category,
                            normalized_value=decision.value,
                            status=RequirementItemStatus.REMOVED_BY_USER,
                            priority=RequirementPriority.WONT,
                            source_turn_id=proposal.proposal_id,
                            provenance=RequirementProvenance.USER_UTTERANCE,
                        )
                    )
                    all_items = requirements + constraints
                continue
            if decision.action is RequirementChangeAction.REMOVE:
                if current is None:
                    continue
                replacement = current.model_copy(update={"status": RequirementItemStatus.REMOVED_BY_USER, "fingerprint": _AUTO})
                target_list = requirements if current in requirements else constraints
                target_list[target_list.index(current)] = replacement
                all_items = requirements + constraints
                continue
            if decision.value is None:
                raise ValueError("requirement change value is missing")
            if decision.action is RequirementChangeAction.ADD and any(
                item.status is RequirementItemStatus.CONFIRMED
                and item.category == decision.category
                and item.normalized_value == decision.value
                for item in all_items
            ):
                continue
            new_id = _stable_id("requirement", proposal.proposal_id, decision.decision_id, decision.value)
            if decision.action is RequirementChangeAction.MODIFY and current is not None:
                superseded = current.model_copy(update={"status": RequirementItemStatus.SUPERSEDED, "superseded_by": new_id, "fingerprint": _AUTO})
                target_list = requirements if current in requirements else constraints
                target_list[target_list.index(current)] = superseded
            new_item = RequirementItem(
                requirement_id=new_id,
                project_id=checked.project_id,
                conversation_id=checked.conversation_id,
                category=decision.category,
                normalized_value=decision.value,
                status=RequirementItemStatus.CONFIRMED,
                priority=RequirementPriority.SHOULD,
                source_turn_id=proposal.proposal_id,
                provenance=RequirementProvenance.USER_UTTERANCE,
                supersedes=None if current is None else current.requirement_id,
            )
            requirements.append(new_item)
            all_items = requirements + constraints
        return checked.model_copy(
            update={
                "requirements": tuple(requirements),
                "constraints": tuple(constraints),
                "status": RequirementStatus.CONFIRMED,
                "lifecycle": RequirementLifecycle.REQUIREMENTS_READY,
                "fingerprint": _AUTO,
            }
        )

    @staticmethod
    def evidence_pack_from_facts(
        brief: InitialRequirementBrief,
        *,
        source_type: ResearchSourceType,
        project_identity: str,
        url: str,
        title: str,
        description: str,
        facts: tuple[dict[str, object], ...],
    ) -> ReferenceProjectEvidencePack:
        candidate = ReferenceProjectCandidate(
            source_type=source_type,
            project_identity=project_identity,
            url=url,
            owner_or_author=project_identity.split("/", 1)[0],
            repository_name=project_identity.split("/", 1)[-1],
            title=title,
            description=description,
            extracted_facts=tuple(ReferenceFact(
                fact_id=_stable_id("fact", project_identity, str(item.get("category")), str(item.get("value"))),
                category=str(item["category"]),
                value=str(item["value"]),
                artifact=FactProvenance(str(item.get("artifact", "README"))),
                location=str(item.get("location", "source metadata")),
            ) for item in facts),
        )
        relevance = RelevanceAssessment(
            product_type_overlap=True,
            platform_overlap=True,
            feature_overlap=brief.explicit_features[:1],
            rationale=("test evidence",),
        )
        evidence = EngineeringResearchService._evidence(brief, candidate, relevance)
        return ReferenceProjectEvidencePack(
            project_id=brief.project_id,
            conversation_id=brief.conversation_id,
            brief_fingerprint=brief.fingerprint,
            queries=EngineeringResearchService.queries_for(brief),
            projects=(evidence,),
            source_reports=tuple(
                SourceResearchReport(
                    source_type=source,
                    status=SourceResultStatus.COMPLETE,
                    capabilities=(),
                    queries=tuple(query.query for query in EngineeringResearchService.queries_for(brief) if query.source_type is source),
                    candidate_count=1 if source is source_type else 0,
                )
                for source in (ResearchSourceType.GITHUB, ResearchSourceType.OSHWHUB, ResearchSourceType.VENDOR_REFERENCE)
            ),
            status=ResearchStatus.COMPLETE,
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def apply_requirement_suggestion_decision(
    context: RequirementContext,
    suggestion: RequirementSuggestion,
    *,
    action: RequirementDecisionAction,
    decided_by: str,
    modified_requirement: str | None = None,
) -> RequirementContext:
    checked = RequirementContext.model_validate(copy.deepcopy(context.model_dump(mode="python")), strict=False)
    if checked.project_id != suggestion.project_id or checked.conversation_id != suggestion.conversation_id:
        raise ValueError("requirement suggestion context identity mismatch")
    _identifier(decided_by, field="decided_by")
    if action in {RequirementDecisionAction.REJECT, RequirementDecisionAction.DEFER, RequirementDecisionAction.SUPPRESSED}:
        return checked
    if action is RequirementDecisionAction.MODIFY:
        if modified_requirement is None:
            raise ValueError("modify decision requires modified requirement")
        normalized = _text(modified_requirement, field="modified requirement", maximum=512)
        if "只需要充电" in normalized or "仅需要充电" in normalized or "不需要数据" in normalized:
            normalized = "USB_CHARGING_ONLY"
    elif action is RequirementDecisionAction.ACCEPT:
        normalized = suggestion.requirement_value
    else:
        raise ValueError("requirement decision action is invalid")
    if any(item.category == suggestion.category and item.normalized_value == normalized and item.status is RequirementItemStatus.CONFIRMED for item in (*checked.requirements, *checked.constraints)):
        return checked
    item = RequirementItem(
        requirement_id=_stable_id("requirement", suggestion.suggestion_id, decided_by, normalized),
        project_id=checked.project_id,
        conversation_id=checked.conversation_id,
        category=suggestion.category,
        normalized_value=normalized,
        status=RequirementItemStatus.CONFIRMED,
        priority=RequirementPriority.SHOULD,
        source_turn_id=suggestion.suggestion_id,
        provenance=RequirementProvenance.USER_UTTERANCE,
    )
    return checked.model_copy(
        update={
            "requirements": (*checked.requirements, item),
            "status": RequirementStatus.ANALYZING,
            "fingerprint": _AUTO,
        }
    )


__all__ = (
    "EngineeringResearchService",
    "RequirementGapAnalyzer",
    "apply_requirement_suggestion_decision",
    "extract_reference_urls",
    "is_requirement_change_feedback",
)
