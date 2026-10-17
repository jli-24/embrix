from __future__ import annotations

import asyncio
import copy

from pydantic import ValidationError

from .exceptions import (
    MultimodalEvidenceMissing,
    MultimodalProviderError,
    MultimodalRejected,
    MultimodalUnavailable,
)
from .models import (
    EngineeringArtifact,
    EngineeringObservation,
    MultimodalModelReference,
    validate_artifact,
    validate_observation,
)
from .ports import MultimodalModelPort


class MultimodalEngineeringService:
    __slots__ = ("_model_reference", "_provider")

    def __init__(self, *, provider: MultimodalModelPort) -> None:
        if not isinstance(provider, MultimodalModelPort):
            raise TypeError("multimodal model port is invalid")
        try:
            reference = MultimodalModelReference.model_validate(
                copy.deepcopy(provider.model_reference.model_dump(mode="python"))
            )
        except Exception as error:
            raise TypeError("multimodal model binding is invalid") from error
        self._provider = provider
        self._model_reference = reference

    @property
    def model_reference(self) -> MultimodalModelReference:
        return MultimodalModelReference.model_validate(
            copy.deepcopy(self._model_reference.model_dump(mode="python"))
        )

    def _request(self, value: object) -> EngineeringArtifact:
        try:
            return validate_artifact(value)
        except (TypeError, ValueError, ValidationError) as error:
            raise MultimodalRejected() from error

    def _response(
        self, artifact: EngineeringArtifact, value: object
    ) -> EngineeringObservation:
        if type(value) is EngineeringObservation and not value.evidence_refs:
            raise MultimodalEvidenceMissing()
        try:
            checked = validate_observation(value)
        except (TypeError, ValueError, ValidationError) as error:
            raise MultimodalRejected() from error
        if checked.artifact_reference != artifact:
            raise MultimodalRejected()
        if checked.model_reference != self._model_reference:
            raise MultimodalRejected()
        if not checked.evidence_refs:
            raise MultimodalEvidenceMissing()
        return checked

    def analyze(self, artifact: EngineeringArtifact) -> EngineeringObservation:
        checked = self._request(artifact)
        try:
            result = self._provider.analyze(copy.deepcopy(checked))
        except MultimodalProviderError:
            raise
        except (MultimodalEvidenceMissing, MultimodalRejected):
            raise
        except Exception as error:
            raise MultimodalUnavailable() from error
        return self._response(checked, result)

    async def aanalyze(self, artifact: EngineeringArtifact) -> EngineeringObservation:
        checked = self._request(artifact)
        try:
            result = await self._provider.aanalyze(copy.deepcopy(checked))
        except asyncio.CancelledError:
            raise
        except MultimodalProviderError:
            raise
        except (MultimodalEvidenceMissing, MultimodalRejected):
            raise
        except Exception as error:
            raise MultimodalUnavailable() from error
        return self._response(checked, result)


__all__ = ("MultimodalEngineeringService",)
