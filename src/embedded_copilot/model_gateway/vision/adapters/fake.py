from __future__ import annotations

from ..contracts import VisionObservation, VisionRequest, VisionResponse
from ..ports import VisionModelProviderPort


class FakeVisionProviderAdapter(VisionModelProviderPort):
    """Deterministic offline provider used only for contract tests."""

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-vision"

    def generate(self, request: VisionRequest) -> VisionResponse:
        del request
        return self._response()

    async def agenerate(self, request: VisionRequest) -> VisionResponse:
        del request
        return self._response()

    def _response(self) -> VisionResponse:
        return VisionResponse(
            provider=self.provider,
            model=self.model,
            observation=VisionObservation(
                observation_id="observation-fake-1",
                objects=("microcontroller",),
                components=("STM32",),
                interfaces=("UART",),
                issues=("possible_missing_decoupling",),
                confidence=0.85,
                evidence=("fake-observation",),
            ),
            metadata=(),
        )


__all__ = ("FakeVisionProviderAdapter",)
