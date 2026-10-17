from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from embedded_copilot.model_gateway.governance import (
    BoundModelIdentity,
    PolicyScope,
    ProjectDefaultModelPolicy,
    SelectionMode,
)
from embedded_copilot.model_gateway.management import ModelGatewayManagementService
from embedded_copilot.model_gateway.metrics import ModelExecutionRecord
from embedded_copilot.model_gateway.persistence import SQLiteModelGatewayStore
from embedded_copilot.model_gateway.presets import manual_model_profile
from embedded_copilot.model_gateway.profiles import (
    CapabilityEvidence,
    CapabilityProvenance,
    CapabilityStatus,
    EndpointProfile,
    EndpointType,
    ModelAvailability,
    ModelCapability,
    ModelProfile,
    ProtocolFamily,
    ProviderProfile,
    RecordProvenance,
    TaskType,
    VerificationStatus,
)


def test_management_state_and_metrics_recover_after_restart(tmp_path: Path) -> None:
    service = ModelGatewayManagementService(store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3"))
    service.add_provider(ProviderProfile(provider_id="local", display_name="Local", supported_protocols=(ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,)))
    service.add_endpoint(EndpointProfile(endpoint_id="endpoint-local", provider_id="local", display_name="Ollama", endpoint_type=EndpointType.LOCAL, protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE, base_url="http://127.0.0.1:11434/v1"))
    service.add_model(manual_model_profile(model_profile_id="local-model", provider_id="local", endpoint_id="endpoint-local", remote_model_id="llama3", display_name="Llama 3"))
    service.record_execution(ModelExecutionRecord(execution_id="execution-1", request_id="request-1", project_id="project-a", task_type=TaskType.FIRMWARE, selected_model="local-model", provider_id="local", endpoint_id="endpoint-local", status="SUCCEEDED"), task_accepted=True)
    service.save()

    recovered = ModelGatewayManagementService(store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3"))
    assert recovered.restore_from_store() is True
    assert recovered.list_models()[0].model_profile_id == "local-model"
    assert recovered.reliability()[0].request_success_rate == 1.0
    assert recovered.task_metrics()[0].task_acceptance_rate == 1.0


def test_store_reads_known_legacy_model_profile_shape_without_disabling_tamper_check(
    tmp_path: Path,
) -> None:
    service = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    )
    service.add_provider(
        ProviderProfile(
            provider_id="local",
            display_name="Local",
            supported_protocols=(ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,),
        )
    )
    service.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-local",
            provider_id="local",
            display_name="Ollama",
            endpoint_type=EndpointType.LOCAL,
            protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    service.add_model(
        manual_model_profile(
            model_profile_id="local-model",
            provider_id="local",
            endpoint_id="endpoint-local",
            remote_model_id="llama3",
            display_name="Llama 3",
        )
    )
    state = service.state()
    service.save()
    payload = state.model_dump(mode="json")
    for model in payload["models"]:
        for field in (
            "availability",
            "availability_source",
            "credential_revision",
            "endpoint_revision",
            "discovered_at",
            "verified_at",
        ):
            model.pop(field, None)
    legacy_encoded = json.dumps(
        {key: value for key, value in payload.items() if key != "fingerprint"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    legacy_fingerprint = "sha256:" + hashlib.sha256(legacy_encoded).hexdigest()
    payload["fingerprint"] = legacy_fingerprint
    path = tmp_path / "gateway.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE model_gateway_state SET fingerprint = ?, payload = ? WHERE id = 1",
            (
                legacy_fingerprint,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )

    restored = SQLiteModelGatewayStore(path).load()

    assert restored is not None
    assert restored.models == state.models
    assert restored.fingerprint != legacy_fingerprint

    payload["models"][0]["display_name"] = "Tampered"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE model_gateway_state SET payload = ? WHERE id = 1",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        SQLiteModelGatewayStore(path).load()


def test_store_migrates_legacy_model_identity_fields_when_binding_is_unique(
    tmp_path: Path,
) -> None:
    service = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    )
    service.add_provider(
        ProviderProfile(
            provider_id="local",
            display_name="Local",
            supported_protocols=(ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,),
        )
    )
    service.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-local",
            provider_id="local",
            display_name="Ollama",
            endpoint_type=EndpointType.LOCAL,
            protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    service.add_model(
        manual_model_profile(
            model_profile_id="local-model",
            provider_id="local",
            endpoint_id="endpoint-local",
            remote_model_id="llama3",
            display_name="Llama 3",
        )
    )
    service.save()
    path = tmp_path / "gateway.sqlite3"
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0])
        payload["models"] = [{
            "id": "local-model",
            "provider": "local",
            "endpoint": "endpoint-local",
            "model_id": "llama3",
            "display_name": "Llama 3",
            "advertised_family": "llama",
        }]
        encoded = json.dumps({key: value for key, value in payload.items() if key != "fingerprint"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()
        payload["fingerprint"] = fingerprint
        connection.execute("UPDATE model_gateway_state SET fingerprint = ?, payload = ? WHERE id = 1", (fingerprint, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))

    restored = SQLiteModelGatewayStore(path).load()

    assert restored is not None
    assert len(restored.models) == 1
    assert restored.models[0].provider_id == "local"
    assert restored.models[0].endpoint_id == "endpoint-local"
    assert restored.models[0].remote_model_id == "llama3"
    assert not any(issue.reason == "RECORD_SCHEMA_INVALID" for issue in restored.integrity_issues)


def test_store_migrates_legacy_allowed_policy_identity_fields(
    tmp_path: Path,
) -> None:
    service = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    )
    service.add_provider(ProviderProfile(provider_id="local", display_name="Local"))
    service.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-local",
            provider_id="local",
            display_name="Local",
            endpoint_type=EndpointType.LOCAL,
            protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    service.add_model(
        manual_model_profile(
            model_profile_id="local-model",
            provider_id="local",
            endpoint_id="endpoint-local",
            remote_model_id="llama3",
            display_name="Llama 3",
        )
    )
    service.save()
    path = tmp_path / "gateway.sqlite3"
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0])
        payload["allowed_model_policies"] = [{
            "id": "legacy-policy",
            "scope": "GLOBAL",
            "project_id": None,
            "stage_id": None,
            "provider_id": "local",
            "model_id": "llama3",
            "enabled": True,
            "priority": 100,
            "created_at": "2026-09-12T00:00:00Z",
            "updated_at": "2026-09-12T00:00:00Z",
        }]
        encoded = json.dumps({key: value for key, value in payload.items() if key != "fingerprint"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()
        payload["fingerprint"] = fingerprint
        connection.execute("UPDATE model_gateway_state SET fingerprint = ?, payload = ? WHERE id = 1", (fingerprint, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))

    restored = SQLiteModelGatewayStore(path).load()

    assert restored is not None
    assert len(restored.allowed_model_policies) == 1
    assert restored.allowed_model_policies[0].endpoint_id == "endpoint-local"
    assert restored.allowed_model_policies[0].model_profile_id == "local-model"
    assert restored.allowed_model_policies[0].credential_ref == "credential:local"


def test_store_verifies_schema_v1_fingerprint_before_injecting_v2_defaults(
    tmp_path: Path,
) -> None:
    service = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    )
    service.add_provider(
        ProviderProfile(
            provider_id="local",
            display_name="Local",
            supported_protocols=(ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,),
        )
    )
    service.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-local",
            provider_id="local",
            display_name="Ollama",
            endpoint_type=EndpointType.LOCAL,
            protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    service.add_model(
        manual_model_profile(
            model_profile_id="local-model",
            provider_id="local",
            endpoint_id="endpoint-local",
            remote_model_id="llama3",
            display_name="Llama 3",
        )
    )
    state = service.state()
    service.save()
    payload = state.model_dump(mode="json")
    for field in (
        "engineering_stages",
        "allowed_model_policies",
        "stage_model_policies",
        "project_default_model_policies",
    ):
        payload.pop(field, None)
    for model in payload["models"]:
        for field in (
            "billing_provenance",
            "billing_source",
            "catalog_source",
            "lifecycle_status",
            "deprecated",
            "metadata_updated_at",
            "metadata_source",
        ):
            model.pop(field, None)
    legacy_encoded = json.dumps(
        {key: value for key, value in payload.items() if key != "fingerprint"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    legacy_fingerprint = "sha256:" + hashlib.sha256(legacy_encoded).hexdigest()
    payload["fingerprint"] = legacy_fingerprint
    path = tmp_path / "gateway.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE model_gateway_state SET schema_version = 1, fingerprint = ?, payload = ? WHERE id = 1",
            (
                legacy_fingerprint,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )

    restored = SQLiteModelGatewayStore(path).load()

    assert restored is not None
    assert restored.models == state.models
    assert restored.engineering_stages
    assert restored.fingerprint != legacy_fingerprint


def test_governance_policies_survive_restart_in_the_existing_state_store(tmp_path: Path) -> None:
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    service = ModelGatewayManagementService(store=store)
    service.add_provider(ProviderProfile(provider_id="local", display_name="Local", supported_protocols=(ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,)))
    service.add_endpoint(EndpointProfile(endpoint_id="endpoint-local", provider_id="local", display_name="Ollama", endpoint_type=EndpointType.LOCAL, protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE, base_url="http://127.0.0.1:11434/v1"))
    service.add_model(ModelProfile(
        model_profile_id="local-model",
        provider_id="local",
        endpoint_id="endpoint-local",
        remote_model_id="llama3",
        display_name="Llama 3",
        advertised_family="llama",
        capabilities=(ModelCapability.TEXT,),
            capability_sources=(CapabilityEvidence(capability=ModelCapability.TEXT, provenance=CapabilityProvenance.DECLARED, status=CapabilityStatus.PASS, provider_id="local", endpoint_id="endpoint-local", credential_ref="vault:local", model_profile_id="local-model", model_id="llama3"),),
            availability=ModelAvailability.AVAILABLE,
            verification_status=VerificationStatus.VERIFIED,
            record_provenance=RecordProvenance.EXPLICIT_CONFIGURATION,
        ))
    identity = BoundModelIdentity(provider_id="local", endpoint_id="endpoint-local", credential_ref="vault:local", model_profile_id="local-model", model_id="llama3")
    allowed = service.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=identity)
    now = datetime(2026, 9, 12, tzinfo=UTC)
    service.set_project_default_model_policy(ProjectDefaultModelPolicy(id="project-default-1", project_id="project-a", selection_mode=SelectionMode.FIXED_MODEL, fixed_model=identity, created_at=now, updated_at=now))
    service.save()

    recovered = ModelGatewayManagementService(store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3"))
    assert recovered.restore_from_store() is True
    assert recovered.allowed_model_policy(allowed.id) is not None
    assert recovered.project_default_model_policy("project-a") is not None
    assert recovered.project_default_model_policy("project-a").fixed_model == identity


def test_store_quarantines_invalid_policy_without_rewriting_the_source_row(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    service = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    service.add_provider(ProviderProfile(provider_id="local", display_name="Local"))
    service.add_endpoint(EndpointProfile(endpoint_id="endpoint-local", provider_id="local", display_name="Local", endpoint_type=EndpointType.LOCAL, protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE, base_url="http://127.0.0.1:11434/v1"))
    service.add_model(manual_model_profile(model_profile_id="local-model", provider_id="local", endpoint_id="endpoint-local", remote_model_id="llama3", display_name="Llama 3"))
    service.save()

    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0])
        payload["allowed_model_policies"] = [{
            "id": "allowed-model-invalid",
            "scope": "GLOBAL",
            "project_id": None,
            "stage_id": None,
            "provider_id": "local",
            "endpoint_id": "endpoint-local",
            "credential_ref": "vault:local",
            "model_profile_id": "local-model",
            "model_id": "not-llama3",
            "enabled": True,
            "priority": 100,
            "created_at": "2026-09-12T00:00:00Z",
            "updated_at": "2026-09-12T00:00:00Z",
        }]
        encoded = json.dumps({key: value for key, value in payload.items() if key != "fingerprint"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()
        payload["fingerprint"] = fingerprint
        connection.execute("UPDATE model_gateway_state SET fingerprint = ?, payload = ? WHERE id = 1", (fingerprint, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))

    restored = SQLiteModelGatewayStore(path).load()

    assert restored is not None
    assert restored.allowed_model_policies == ()
    assert restored.models[0].remote_model_id == "llama3"
    assert restored.integrity_issues[0].reason == "MODEL_GOVERNANCE_IDENTITY_INVALID"
    with sqlite3.connect(path) as connection:
        source_payload = json.loads(connection.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0])
    assert source_payload["allowed_model_policies"][0]["model_id"] == "not-llama3"


@pytest.mark.parametrize("schema_version", [1, 2, 3])
def test_store_loads_untampered_payload_for_each_supported_schema(
    tmp_path: Path, schema_version: int
) -> None:
    path = tmp_path / "gateway.sqlite3"
    service = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    service.add_provider(ProviderProfile(provider_id="local", display_name="Local"))
    service.save()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE model_gateway_state SET schema_version = ? WHERE id = 1",
            (schema_version,),
        )

    restored = SQLiteModelGatewayStore(path).load()
    assert restored is not None
    assert restored.providers[0].provider_id == "local"


@pytest.mark.parametrize("schema_version", [1, 2, 3])
def test_store_rejects_tampered_policy_before_quarantine(
    tmp_path: Path, schema_version: int
) -> None:
    path = tmp_path / "gateway.sqlite3"
    store = SQLiteModelGatewayStore(path)
    service = ModelGatewayManagementService(store=store)
    service.add_provider(ProviderProfile(provider_id="local", display_name="Local"))
    service.add_endpoint(EndpointProfile(
        endpoint_id="endpoint-local", provider_id="local", display_name="Local",
        endpoint_type=EndpointType.LOCAL,
        protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1",
    ))
    service.add_model(manual_model_profile(
        model_profile_id="local-model", provider_id="local",
        endpoint_id="endpoint-local", remote_model_id="llama3", display_name="Llama 3",
    ))
    service.save()

    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute(
            "SELECT payload FROM model_gateway_state WHERE id = 1"
        ).fetchone()[0])
        payload["allowed_model_policies"] = [{
            "id": "allowed-model-invalid", "scope": "GLOBAL", "project_id": None,
            "stage_id": None, "provider_id": "local", "endpoint_id": "endpoint-local",
            "credential_ref": "vault:local", "model_profile_id": "local-model",
            "model_id": "not-llama3", "enabled": True, "priority": 100,
            "created_at": "2026-09-12T00:00:00Z",
            "updated_at": "2026-09-12T00:00:00Z",
        }]
        encoded = json.dumps(
            {key: value for key, value in payload.items() if key != "fingerprint"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()
        payload["fingerprint"] = fingerprint
        payload["allowed_model_policies"][0]["enabled"] = False
        raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "UPDATE model_gateway_state SET schema_version = ?, fingerprint = ?, payload = ? WHERE id = 1",
            (schema_version, fingerprint, raw_payload),
        )

    with (
        patch("embedded_copilot.model_gateway_storage._load_state_with_quarantine") as quarantine,
        patch.object(store, "save") as save,
    ):
        with pytest.raises(ValueError, match="fingerprint mismatch"):
            store.load()
    quarantine.assert_not_called()
    save.assert_not_called()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT schema_version, fingerprint, payload FROM model_gateway_state WHERE id = 1"
        ).fetchone() == (schema_version, fingerprint, raw_payload)
