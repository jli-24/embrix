from __future__ import annotations

import asyncio
import copy
import hashlib

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)
from embedded_copilot.model_gateway import ModelGatewayPort, ModelPolicy, ModelRequest

from .contracts import (
    FailureAnalysis,
    FailureAnalysisNote,
    FailureCategory,
    FailureObservation,
)
from .exceptions import FailureAnalysisRejected
from .ports import FailureContextPort


class FailureAnalysisService:
    __slots__ = ("_context", "_model")

    def __init__(
        self,
        *,
        context_provider: FailureContextPort,
        model_gateway: ModelGatewayPort | None = None,
    ) -> None:
        if not isinstance(context_provider, FailureContextPort):
            raise TypeError("failure context port is invalid")
        self._context = context_provider
        self._model = model_gateway

    def analyze(
        self,
        observation: FailureObservation,
        request: EngineeringContextRequest,
        *,
        model_policy: ModelPolicy | None = None,
    ) -> FailureAnalysis:
        checked_observation = FailureObservation.model_validate(
            copy.deepcopy(observation.model_dump(mode="python"))
        )
        checked_request = EngineeringContextRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        if checked_observation.project_id != checked_request.project_id:
            raise FailureAnalysisRejected()
        context = self._context.get_context(checked_request)
        if context is None:
            raise FailureAnalysisRejected()
        try:
            checked_context = EngineeringContextSnapshot.model_validate(
                copy.deepcopy(context.model_dump(mode="python"))
            )
        except (TypeError, ValueError) as error:
            raise FailureAnalysisRejected() from error
        if checked_context.project_id != checked_request.project_id:
            raise FailureAnalysisRejected()
        evidence = (
            *checked_context.memory_evidence,
            *checked_context.graph_evidence,
            *checked_context.knowledge_evidence,
            *checked_context.multimodal_evidence,
        )
        if not evidence:
            raise FailureAnalysisRejected()
        evidence_refs = tuple(sorted(item.evidence_id for item in evidence))
        analysis = FailureAnalysis.create(
            failure_id="failure-"
            + hashlib.sha256(
                f"{checked_observation.fingerprint}:{checked_context.fingerprint}".encode()
            ).hexdigest()[:32],
            project_id=checked_request.project_id,
            target_id=checked_observation.target_id,
            category=checked_observation.category,
            observation_fingerprint=checked_observation.fingerprint,
            context_fingerprint=checked_context.fingerprint,
            root_cause_candidate=_candidate(checked_observation.category),
            evidence_refs=evidence_refs,
            confidence=_analysis_confidence(checked_observation.category),
            provenance=checked_context.provenance,
            verification_status=checked_context.verification_status,
        )
        note = self._optional_note(
            analysis, checked_context, checked_request, model_policy
        )
        if note is None:
            return analysis
        material = analysis.model_dump(mode="python")
        material["note"] = note
        material["fingerprint"] = analysis.fingerprint
        return FailureAnalysis.model_validate(material)

    def _optional_note(self, analysis, context, request, policy):
        if self._model is None or policy is None:
            return None
        model_request = ModelRequest(
            request_id="failure-note-" + analysis.failure_id.removeprefix("failure-"),
            task=f"Provide a bounded note for {analysis.category.value}",
            engineering_context=context,
            system_instruction="Only summarize the supplied evidence. Do not propose repairs or commands.",
            constraints=("non-authoritative note",),
            model_policy=policy,
        )
        try:
            response = self._model.generate(model_request)
            content = getattr(response, "content", None)
            if not isinstance(content, str):
                return None
            return FailureAnalysisNote.create(note=content)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - optional model failures are fail-soft and sanitized.
            return None


def _candidate(category: FailureCategory) -> str:
    return {
        FailureCategory.BUILD_FAILED: "Build exited with a non-zero status; configuration or source evidence requires review.",
        FailureCategory.BUILD_TIMEOUT: "Build did not complete before the configured timeout.",
        FailureCategory.FLASH_FAILED: "Flash exited with a failure status; target and artifact evidence requires review.",
        FailureCategory.FLASH_TIMEOUT: "Flash did not complete before the configured timeout.",
        FailureCategory.UART_ERROR: "UART observation contains an error-level signal requiring evidence-based review.",
        FailureCategory.EXECUTION_UNAVAILABLE: "The requested execution capability was unavailable.",
        FailureCategory.MALFORMED_OBSERVATION: "The execution observation was malformed and cannot establish a cause.",
    }[category]


def _analysis_confidence(category: FailureCategory) -> float:
    return (
        0.8
        if category in {FailureCategory.BUILD_FAILED, FailureCategory.FLASH_FAILED}
        else 0.6
    )


__all__ = ("FailureAnalysisService",)
