from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(("module", "forbidden"), [
    ("embedded_copilot.firmware_engineering.contracts", ["embedded_copilot.firmware_engineering.service"]),
    ("embedded_copilot.pcb_production.contracts", ["embedded_copilot.pcb_production.service", "embedded_copilot.pcb_production.adapters"]),
    ("embedded_copilot.model_gateway.invocation.consumption_source", ["embedded_copilot.model_gateway_storage", "embedded_copilot.model_gateway_persistence"]),
])
def test_contract_and_consent_imports_do_not_activate_implementations(tmp_path, module, forbidden):
    script = "import importlib,json,sys; importlib.import_module(" + repr(module) + "); print(json.dumps({'modules': sorted(sys.modules), 'files': {n:getattr(m, '__file__', None) for n,m in sys.modules.items() if n.startswith('embedded_copilot')}, 'path':sys.path}))"
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                            capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    modules = payload["modules"]
    assert not [name for name in modules if any(name == p or name.startswith(p + ".") for p in forbidden)], payload


def test_endpoint_revision_preserves_original_non_ascii_encoding():
    from embedded_copilot.model_gateway._endpoint_revision import endpoint_revision
    from embedded_copilot.production_release.model_pool import ProviderModelPool

    endpoint = SimpleNamespace(endpoint_id="endpoint-1", provider_id="provider-1",
                               protocol_family=SimpleNamespace(value="OPENAI_RESPONSES"),
                               base_url="https://example.invalid/模型", credential_ref="vault:fixture", enabled=True)
    expected = {"endpoint_id": "endpoint-1", "provider_id": "provider-1", "protocol_family": "OPENAI_RESPONSES",
                "base_url": "https://example.invalid/模型", "credential_ref": "vault:fixture", "enabled": True}
    encoded = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    assert endpoint_revision(endpoint) == ProviderModelPool.endpoint_revision(endpoint) == digest


def test_legacy_store_symbols_preserve_identity_and_core_has_no_sql():
    from embedded_copilot.model_gateway.invocation.consumption_store import (
        ConsumptionRejected,
        ConsumptionStoreMixin,
    )
    from embedded_copilot.model_gateway_persistence.consumption_store import (
        ConsumptionRejected as adapted,
    )
    from embedded_copilot.model_gateway_storage import SQLiteModelGatewayStore

    assert adapted is ConsumptionRejected
    assert ConsumptionRejected.__module__ == "embedded_copilot.model_gateway.invocation.consumption_store"
    assert issubclass(SQLiteModelGatewayStore, ConsumptionStoreMixin)
    root = Path("src/embedded_copilot")
    for name in ("bundle_store", "consumption_store"):
        tree = ast.parse((root / "model_gateway/invocation" / (name + ".py")).read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "execute"]


SQL_DIGESTS = {'bundle_store': '7cf1a230b827f6e958ed8a911c566f108f0785cbc3f1d95b983da73e8db071dd', 'consumption_store': 'a334746a10c1ea8f9e2b35ca0502374e0249e1a25a2d2deb147575836973b987'}


@pytest.mark.parametrize("name", ["bundle_store", "consumption_store"])
def test_sql_statement_values_and_order_are_preserved(name):
    path = Path("src/embedded_copilot/model_gateway_persistence") / (name + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    statements = [n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr == "execute"
                  and n.args and isinstance(n.args[0], ast.Constant)]
    assert hashlib.sha256(json.dumps(statements, ensure_ascii=False).encode()).hexdigest() == SQL_DIGESTS[name]


def test_build_classifier_reexport_preserves_exact_command_language():
    from embedded_copilot.firmware_engineering.readiness import (
        is_firmware_build_request as old,
    )
    from embedded_copilot.firmware_engineering.request_intent import (
        is_firmware_build_request,
    )
    assert old is is_firmware_build_request
    assert old("　ＢＵＩＬＤＦＩＲＭＷＡＲＥ！") is True
    assert old("帮我检查当前固件是否能构建") is False
    assert old("运行任意命令") is False
    assert old(None) is False


@pytest.fixture
def sealed_resource(tmp_path, monkeypatch):
    from embedded_copilot.component_recommendation import catalog
    content = catalog._approved_resource_path().read_bytes()
    root = tmp_path / "package"
    resource = root / "resources" / "approved_components_v1.json"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(content)
    monkeypatch.setattr(catalog, "__file__", str(root / "catalog.py"))
    return catalog, resource, content


def test_fixed_resource_rejects_escape_and_oversize(sealed_resource, tmp_path, monkeypatch):
    catalog, resource, content = sealed_resource
    assert catalog.load_approved_component_source().records
    resource.write_bytes(content + b" ")
    with pytest.raises(ValueError):
        catalog.load_approved_component_source()
    resource.write_bytes(content)
    outside = tmp_path / "approved_components_v1.json"
    outside.write_bytes(content)
    monkeypatch.setattr(catalog, "_approved_resource_path", lambda: outside)
    with pytest.raises(ValueError):
        catalog.load_approved_component_source()


def test_fixed_resource_rejects_reparse_before_read(sealed_resource, monkeypatch):
    catalog, resource, _ = sealed_resource
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == resource:
            return SimpleNamespace(st_file_attributes=0x400, st_mode=0o100644)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError):
        catalog.load_approved_component_source()


def test_fixed_resource_read_is_bounded(sealed_resource, monkeypatch):
    import io
    catalog, resource, content = sealed_resource
    original = Path.open
    reads = []
    class Guarded(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            assert size == len(content) + 1
            return super().read(size)
    def opened(path, *args, **kwargs):
        return Guarded(content) if path == resource else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", opened)
    assert catalog.load_approved_component_source().records
    assert reads == [len(content) + 1]


@pytest.mark.parametrize(("module", "required"), [
    ("embedded_copilot.model_gateway.invocation.bundle_store", {"CounterBundleStoreMixin"}),
    ("embedded_copilot.model_gateway.invocation.consumption_store", {"ConsumptionRejected", "ConsumptionStoreMixin", "initialize_consumption_schema", "validate_phase_grant"}),
    ("embedded_copilot.model_gateway.invocation.openai_responses_qualification", {"OpenAIResponsesTransportSource", "count_request_from_final", "parse_count_response"}),
])
def test_legacy_defined_exports_support_star_and_explicit_identity(module, required):
    from importlib import import_module
    legacy = import_module(module)
    namespace = {}
    exec("from " + module + " import *", namespace)  # noqa: S102 - fixed test allowlist checks legacy star exports.
    assert required <= namespace.keys()
    assert all(namespace[name] is getattr(legacy, name) for name in required)
    if "OpenAIResponsesTransportSource" in required:
        from embedded_copilot.production_release.model_transport_qualification import (
            OpenAIResponsesTransportSource,
        )
        assert namespace["OpenAIResponsesTransportSource"] is OpenAIResponsesTransportSource


def test_invalid_private_store_property_maps_to_fixed_safe_error():
    from embedded_copilot.model_gateway.invocation.consumption_source import (
        ConsumptionGrantSource,
    )
    from embedded_copilot.model_gateway.invocation.consumption_store import (
        ConsumptionRejected,
    )
    class Broken:
        @property
        def consumption_grant_for_request(self):
            raise RuntimeError("private injection diagnostic")
    with pytest.raises(ConsumptionRejected, match="^GRANT_STORE_IDENTITY_INVALID$"):
        ConsumptionGrantSource(Broken())


@pytest.mark.parametrize("mutation", ["unknown-version", "wrong-id", "duplicate-id", "bad-record-fingerprint"])
def test_approved_decoder_keeps_post_digest_validation(mutation, monkeypatch):
    from embedded_copilot.component_recommendation import catalog
    payload = json.loads(catalog._approved_resource_path().read_bytes())
    if mutation == "unknown-version":
        payload["schema_version"] = 2
    elif mutation == "wrong-id":
        payload["records"][0]["source_id"] = "unapproved"
    elif mutation == "duplicate-id":
        payload["records"].append(payload["records"][0])
    else:
        payload["records"][0]["fingerprint"] = "sha256:" + "0" * 64
    raw = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(catalog, "_APPROVED_RESOURCE_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises((TypeError, ValueError)):
        catalog._validated_approved_source(raw)


def test_pcb_repair_rejects_unvalidated_missing_generation_receipt() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import Mock

    from embedded_copilot.project_catalog.exceptions import PCBProductionRejected
    from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime

    # A malformed injected projection must fail before any repair/write path.
    production = SimpleNamespace(drc_result=object(), generation_receipt=None,
                                 repair_proposals=(SimpleNamespace(repair_id="repair-1", fingerprint="sha256:" + "a" * 64),))
    saved = Mock()
    host = SimpleNamespace(conversation=lambda *args, **kwargs: SimpleNamespace(pcb_production=production),
                           _repository=SimpleNamespace(save_conversation=saved))
    decision = {"decision_id": "decision-1", "repair_id": "repair-1", "proposal_fingerprint": "sha256:" + "a" * 64,
                "artifact_fingerprint": "sha256:" + "b" * 64, "action": "REJECT_FIX", "reviewer": "reviewer-1",
                "decided_at": datetime(2026, 10, 17, tzinfo=UTC)}
    with pytest.raises(PCBProductionRejected, match="requires a current DRC result"):
        ProjectCatalogRuntime.decide_pcb_repair(host, "project-1", "conversation-1", tenant_id="tenant-1",
                                               requester="reviewer-1", decision=decision)
    saved.assert_not_called()
