from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
)
from embedded_copilot.engineering_context.exceptions import (
    EngineeringContextExternalEvidenceUnavailable,
)
from embedded_copilot.knowledge.external import (
    ExternalKnowledgeSearchUnavailable,
    OfficialWebSearchSettings,
)
from embedded_copilot.engineering_context.adapters.external_knowledge import (
    ExternalKnowledgeEvidenceAdapter as OfficialWebSearchAdapter,
)
from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextService,
    ReadOnlyProjectionSource,
)


class _Transport:
    def __init__(self, payload: object | Exception) -> None:
        self.payload = payload
        self.calls = 0

    def search(self, *, query: str, settings: OfficialWebSearchSettings) -> object:
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _settings(**changes: object) -> OfficialWebSearchSettings:
    values: dict[str, object] = {
        "enabled": True,
        "provider": "brave",
        "endpoint": "https://search.example.test/res/v1/web/search",
        "api_key": "test-key",
        "allowed_domains": ("st.com", "github.com"),
        "timeout_seconds": 2.0,
        "max_response_bytes": 8192,
        "top_k": 2,
    }
    values.update(changes)
    return OfficialWebSearchSettings(**values)


def test_disabled_adapter_never_calls_transport_and_fails_closed() -> None:
    transport = _Transport({"web": {"results": []}})
    adapter = OfficialWebSearchAdapter(
        settings=_settings(enabled=False, provider="unavailable", endpoint=None, api_key=None),
        transport=transport,
    )

    with pytest.raises(EngineeringContextExternalEvidenceUnavailable):
        adapter.list_evidence("project-1", "I2C initialization", ())
    assert transport.calls == 0


def test_official_results_become_temporary_traceable_context_evidence() -> None:
    transport = _Transport(
        {
            "web": {
                "results": [
                    {
                        "title": "STM32 HAL I2C documentation",
                        "url": "https://www.st.com/resource/en/user_manual/um1725.pdf",
                        "description": "HAL_I2C_Init initializes the I2C peripheral.",
                        "page_age": "2025-05-01",
                    },
                    {
                        "title": "Rejected mirror",
                        "url": "https://untrusted.example/i2c",
                        "description": "must not be used",
                    },
                    {
                        "title": "Uncontrolled GitHub repository",
                        "url": "https://github.com/untrusted/i2c",
                        "description": "must not be used",
                    },
                ]
            }
        }
    )
    adapter = OfficialWebSearchAdapter(settings=_settings(), transport=transport)

    evidence = adapter.list_evidence("project-1", "I2C initialization", ())

    assert transport.calls == 1
    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_type is ContextSourceType.KNOWLEDGE_GATEWAY
    assert item.verification_status is ContextVerificationStatus.UNVERIFIED
    assert item.source_reference == "https://www.st.com/resource/en/user_manual/um1725.pdf"
    assert item.subject == "STM32 HAL I2C documentation"
    assert "HAL_I2C_Init" in item.summary
    assert item.source_fingerprint.startswith("sha256:")
    assert item.source_domain == "www.st.com"
    assert item.retrieved_at is not None
    assert item.document_date == "2025-05-01"


@pytest.mark.parametrize(
    "payload",
    [
        {"web": {"results": []}},
        {"web": {"results": [{"title": "bad", "url": "https://untrusted.example/doc", "description": "bad"}]}},
        {"unexpected": []},
    ],
)
def test_no_trusted_or_invalid_provider_result_fails_closed(payload: object) -> None:
    adapter = OfficialWebSearchAdapter(settings=_settings(), transport=_Transport(payload))

    with pytest.raises(EngineeringContextExternalEvidenceUnavailable):
        adapter.list_evidence("project-1", "I2C initialization", ())


def test_transport_failure_is_not_converted_into_evidence() -> None:
    adapter = OfficialWebSearchAdapter(
        settings=_settings(), transport=_Transport(TimeoutError("timeout"))
    )

    with pytest.raises(EngineeringContextExternalEvidenceUnavailable):
        adapter.list_evidence("project-1", "I2C initialization", ())


def test_external_evidence_is_available_to_the_context_before_answer_generation() -> None:
    adapter = OfficialWebSearchAdapter(
        settings=_settings(),
        transport=_Transport(
            {"web": {"results": [{
                "title": "STM32 I2C reference",
                "url": "https://www.st.com/i2c",
                "description": "HAL I2C initialization reference.",
            }]}}
        ),
    )
    service = EngineeringContextService(
        source_port=ReadOnlyProjectionSource(external_knowledge_port=adapter)
    )

    snapshot = service.get_context(
        EngineeringContextRequest(
            project_id="project-1",
            task="I2C initialization",
            constraints=(),
            external_search=True,
        )
    )

    assert snapshot is not None
    assert snapshot.knowledge_evidence[0].source_reference == "https://www.st.com/i2c"


def test_settings_rejects_insecure_endpoint_and_untrusted_domain() -> None:
    with pytest.raises(ValueError):
        _settings(endpoint="http://search.example.test")
    with pytest.raises(ValueError):
        _settings(allowed_domains=("example.test",))


def test_settings_can_be_loaded_from_environment_without_exposing_the_key() -> None:
    settings = OfficialWebSearchSettings.from_environment(
        {
            "EMBEDDED_COPILOT_EXTERNAL_SEARCH_ENABLED": "true",
            "EMBEDDED_COPILOT_EXTERNAL_SEARCH_PROVIDER": "brave",
            "EMBEDDED_COPILOT_EXTERNAL_SEARCH_ENDPOINT": "https://search.example.test/res/v1/web/search",
            "EMBEDDED_COPILOT_EXTERNAL_SEARCH_API_KEY": "test-key",
            "EMBEDDED_COPILOT_EXTERNAL_SEARCH_ALLOWED_DOMAINS": "st.com,github.com",
            "EMBEDDED_COPILOT_EXTERNAL_SEARCH_TIMEOUT_SECONDS": "2",
            "EMBEDDED_COPILOT_EXTERNAL_SEARCH_MAX_RESPONSE_BYTES": "8192",
            "EMBEDDED_COPILOT_EXTERNAL_SEARCH_TOP_K": "2",
        }
    )

    assert settings.enabled is True
    assert settings.allowed_domains == ("st.com", "github.com")
