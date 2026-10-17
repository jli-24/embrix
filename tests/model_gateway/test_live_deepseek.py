from __future__ import annotations

import os

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelGatewayService,
    ModelProviderSettings,
    ModelRequest,
    create_model_provider,
)


def test_deepseek_live_completion_is_explicitly_opt_in() -> None:
    if os.getenv("RUN_DEEPSEEK_LIVE") != "1":
        pytest.skip("set RUN_DEEPSEEK_LIVE=1 to run the real DeepSeek check")

    settings = ModelProviderSettings.from_environment()
    provider = create_model_provider(settings=settings)
    context = EngineeringContextSnapshot.create(
        project_id="live-model-verification",
        task_description="Explain the supplied I2C evidence.",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    response = ModelGatewayService(
        providers={settings.provider: provider}
    ).generate(
        ModelRequest(
            request_id="live-model-verification",
            task="Explain the supplied I2C evidence.",
            engineering_context=context,
            system_instruction="Answer only from supplied engineering evidence.",
            model_policy=settings.to_model_policy(),
        )
    )

    assert response.provider == "deepseek"
    assert response.model_name == settings.model_name
    assert response.content.strip()
