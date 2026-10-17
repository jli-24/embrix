"""Source-string counterexamples: fixtures are parsed, never imported/executed."""

from __future__ import annotations

import ast
from textwrap import dedent

import pytest

from tests.security.boundary_audit import (
    _B2_READER,
    _LAZY_BODY,
    _LAZY_CAPABILITIES,
    Sources,
    _verified_typing_imports,
    agent_api_execution_violations,
    api_domain_violations,
    dynamic_violations,
    gateway_dependency_violations,
    gateway_mutation_violations,
    v18_violations,
)

API = "embedded_copilot.api.nested.routes"
CORE = "embedded_copilot.model_gateway.invocation.example"
CATALOG = "embedded_copilot.component_recommendation.catalog"


@pytest.mark.parametrize("source", [
    "import embedded_copilot.agents.firmware as alias",
    "from embedded_copilot import agents as alias",
    "from ...agents import firmware as alias",
    "import embedded_copilot as parent; parent.agents.firmware.Agent()",
    "from embedded_copilot.firmware_engineering.service import FirmwareEngineeringService as DTO",
    "from embedded_copilot.pcb_production import adapters as DTO",
    "import embedded_copilot.pcb_production as parent; parent.service.PCBProductionService()",
    "from importlib import import_module as load; load('embedded_copilot.agents')",
    "__import__('embedded_copilot.agents')",
])
def test_api_rejects_alias_relative_parent_nested_and_dynamic(source: str) -> None:
    index = Sources(sources={API: source})
    assert api_domain_violations(index, [API])


@pytest.mark.parametrize("helper", [
    "from .service import FirmwareEngineeringService",
    "from .adapters import NativeAdapter",
    "from embedded_copilot.agents import firmware",
    "from importlib import import_module as load; load('embedded_copilot.agents')",
    "class FirmwareService: pass",
])
def test_contracts_label_does_not_launder_implementation(helper: str) -> None:
    domain = "embedded_copilot.firmware_engineering"
    index = Sources(sources={
        API: "from embedded_copilot.firmware_engineering.contracts import Configuration",
        domain + ".contracts": helper,
        domain + ".service": "class FirmwareEngineeringService: pass",
        domain + ".adapters": "class NativeAdapter: pass",
        "embedded_copilot.agents.firmware": "class FirmwareAgent: pass",
    })
    assert api_domain_violations(index, [API])


def test_eager_package_init_cannot_launder_service_import() -> None:
    domain = "embedded_copilot.firmware_engineering"
    index = Sources(sources={
        API: "from embedded_copilot.firmware_engineering.contracts import Configuration",
        domain: "from .service import FirmwareEngineeringService",
        domain + ".contracts": "class Configuration: pass",
        domain + ".service": "class FirmwareEngineeringService: pass",
    }, packages=[domain])
    assert api_domain_violations(index, [API])


def test_only_pure_contract_and_intent_leaves_are_allowed() -> None:
    domain = "embedded_copilot.firmware_engineering"
    index = Sources(sources={
        API: "from embedded_copilot.firmware_engineering.contracts import Configuration\nfrom embedded_copilot.firmware_engineering.request_intent import is_firmware_build_request",
        domain + ".contracts": "class Configuration: pass",
        domain + ".request_intent": "def is_firmware_build_request(value): return False",
    })
    assert api_domain_violations(index, [API]) == []


@pytest.mark.parametrize("source", [
    "import subprocess as safe",
    "from subprocess import run as safe",
    "from embedded_copilot import agents as safe",
    "from ...production_release.model_pool import ProviderModelPool",
    "import embedded_copilot as parent; parent.production_release.provider.load()",
    "import sqlite3 as safe",
    "from sqlite3 import connect as safe",
    "import urllib.request as safe",
    "from urllib import request as safe",
    "import urllib as safe; safe.request.urlopen('not-executed')",
    "import httpx",
    "from embedded_copilot.model_gateway.invocation.bundle_store import CounterBundleStoreMixin",
    "from embedded_copilot.model_gateway_storage import SQLiteModelGatewayStore",
    "from embedded_copilot.model_gateway.persistence import SQLiteModelGatewayStore",
    "from .openai_responses_qualification import OpenAIResponsesTransportSource",
    "from importlib import import_module as load; load('subprocess')",
])
def test_gateway_rejects_real_dependencies_not_substrings(source: str) -> None:
    index = Sources(sources={CORE: source})
    assert gateway_dependency_violations(index, [CORE])


def test_transitive_helper_cannot_hide_production_or_sql() -> None:
    helper = "embedded_copilot.boundary_helper"
    for forbidden in ("from embedded_copilot.production_release import provider", "import sqlite3"):
        index = Sources(sources={CORE: "from embedded_copilot.boundary_helper import value", helper: forbidden})
        assert gateway_dependency_violations(index, [CORE])


def test_name_runtime_and_pure_urllib_parse_are_not_external_capabilities() -> None:
    runtime = "embedded_copilot.model_gateway.invocation.bundle_runtime"
    index = Sources(sources={
        CORE: "from .bundle_runtime import BundleInvocationMixin\nfrom urllib.parse import urlsplit",
        runtime: "class BundleInvocationMixin: pass",
    })
    assert gateway_dependency_violations(index, [CORE]) == []


def test_httpx_only_at_exact_gateway_adapter_boundary() -> None:
    adapter = "embedded_copilot.model_gateway.adapters.protocol_http"
    index = Sources(sources={adapter: "import httpx"})
    assert gateway_dependency_violations(index, [adapter]) == []
    fake = "embedded_copilot.model_gateway.adapters_misleading"
    index = Sources(sources={fake: "import httpx"})
    assert gateway_dependency_violations(index, [fake])


@pytest.mark.parametrize("module,source", [
    ("embedded_copilot.model_gateway.adapters.protocol_http", "class ProtocolHTTPProviderAdapter:\n def prepare_transport(self):\n  headers = {}\n  headers.update({'x': ''})"),
    ("embedded_copilot.model_gateway.invocation.contracts", "class ModelInvocationAttempt:\n def _verify(self):\n  excluded = {'fingerprint'}\n  excluded.update({'version'})"),
    ("embedded_copilot.model_gateway.invocation.contracts", "class ModelInvocationReceipt:\n def _verify(self):\n  excluded = {'fingerprint'}\n  excluded.update({'version'})"),
    ("embedded_copilot.model_gateway.invocation.qualified_bundle_producer", "class QualifiedCounterBundleProducer:\n def _generation_evidence():\n  values = {}\n  values.update({'cost': '0'})"),
    ("embedded_copilot.model_gateway.invocation.qualified_budget_producer", "class QualifiedInvocationBudgetProducer:\n def _collect(self, context):\n  values = {}\n  return None, values, ()\n def _build(self):\n  envelope, values, heads = self._collect(None)\n  values.update({'cost': '0'})"),
])
def test_five_precise_fresh_local_container_updates(module: str, source: str) -> None:
    assert gateway_mutation_violations(Sources(sources={module: source}), [module]) == []


@pytest.mark.parametrize("body", [
    "headers = self.headers\nheaders.update({'x': ''})",
    "global headers\nheaders = {}\nheaders.update({'x': ''})",
    "headers = request.headers\nheaders.update({'x': ''})",
    "headers = {}\nalias = headers\nheaders.update({'x': ''})",
    "headers = {}\nexternal.append(headers)\nheaders.update({'x': ''})",
    "headers = {}\nheaders = self.headers\nheaders.update({'x': ''})",
    "headers = {}\nself.headers.update({'x': ''})",
    "headers = {}\nmutate = headers.update\nmutate({'x': ''})",
    "headers = {}\nheaders.update({'x': ''})\nheaders.update({'new': ''})",
])
def test_exact_function_name_does_not_allow_shared_or_escaped_receiver(body: str) -> None:
    module = "embedded_copilot.model_gateway.adapters.protocol_http"
    source = "class ProtocolHTTPProviderAdapter:\n def prepare_transport(self):\n" + "\n".join("  " + line for line in body.splitlines())
    assert gateway_mutation_violations(Sources(sources={module: source}), [module])


@pytest.mark.parametrize("source", [
    "connection.execute('not executed')",
    "send = connection.execute; send('not executed')",
    "getattr(connection, 'execute')('not executed')",
    "send = getattr(connection, 'execute'); send('not executed')",
    "getattr(connection, request.method)('not executed')",
    "from somewhere import execute as safe; safe()",
    "service.update({})", "store.delete()", "worker.run()", "worker.invoke()",
    "memory.promote()", "system('not executed')",
    "values = {}; values.update({})",
])
def test_mutation_aliases_sql_and_arbitrary_local_updates_remain_forbidden(source: str) -> None:
    assert gateway_mutation_violations(Sources(sources={CORE: source}), [CORE])


@pytest.mark.parametrize("source", [
    "import subprocess as alias", "from subprocess import Popen as Alias",
    "from ...execution_worker import worker as alias",
    "import embedded_copilot as parent; parent.execution_worker.worker.Worker()",
    "from embedded_copilot.execution_worker.ports import BuildPort as Alias",
    "from embedded_copilot.execution_worker.ports import FlashPort as Alias",
    "from importlib import import_module as load; load('subprocess')",
])
def test_nested_api_and_agent_execution_bypasses_remain_rejected(source: str) -> None:
    index = Sources(sources={API: source})
    assert agent_api_execution_violations(index, [API])


def _catalog() -> str:
    return dedent('''
        from pathlib import Path
        _APPROVED_RESOURCE_NAME = "approved_components_v1.json"
        _APPROVED_RESOURCE_SIZE = 13214
        def _approved_resource_path() -> Path:
            return Path(__file__).resolve().parent / "resources" / _APPROVED_RESOURCE_NAME
        def _validated_approved_source(data: bytes):
            return data
        def load_approved_component_source():
            return _validated_approved_source(_read_approved_resource())
    ''') + "\n" + ast.unparse(_B2_READER) + "\n"


def test_b2_is_one_fixed_bounded_read_capability() -> None:
    # This positive concerns static I/O capability only; the real catalog's SHA,
    # schema, source identity and tamper behavior have separate existing tests.
    assert v18_violations(Sources(sources={CATALOG: _catalog()}), [CATALOG]) == []


@pytest.mark.parametrize("old,new", [
    ("Path(__file__)", "Path(request.path)"),
    ("_APPROVED_RESOURCE_SIZE + 1", "99999999"),
    ("path.open('rb')", "path.open('wb')"),
    ("entry.lstat()", "entry.stat()"),
    ("entry.is_symlink()", "False"),
    ("& 1024", "& 0"),
    ("path.resolve().parent != root.resolve()", "False"),
    ("path.stat().st_size != _APPROVED_RESOURCE_SIZE", "False"),
    ("len(data) != _APPROVED_RESOURCE_SIZE", "False"),
    ("_APPROVED_RESOURCE_SIZE = 13214", "_APPROVED_RESOURCE_SIZE = 13215"),
])
def test_b2_name_is_not_a_pathlib_or_loader_exemption(old: str, new: str) -> None:
    source = _catalog()
    assert old in source
    source = source.replace(old, new)
    assert v18_violations(Sources(sources={CATALOG: source}), [CATALOG])


@pytest.mark.parametrize("extra", [
    "def other():\n return Path('/tmp/other').read_bytes()",
    "def other():\n return Path(__file__).parent.glob('*')",
    "def other():\n return open('other', 'rb').read()",
    "def other():\n return Path(__file__).write_text('changed')",
    "import os\ndef other():\n return os.environ['OVERRIDE']",
])
def test_other_catalog_functions_never_inherit_loader_capability(extra: str) -> None:
    source = _catalog() + "\n" + extra
    assert v18_violations(Sources(sources={CATALOG: source}), [CATALOG])


def test_capability_cannot_be_copied_to_a_different_v18_module() -> None:
    module = "embedded_copilot.toolchain.catalog"
    assert v18_violations(Sources(sources={module: _catalog()}), [module])


def test_lazy_compatibility_is_a_fixed_export_capability_not_arbitrary_importlib() -> None:
    module = "embedded_copilot.model_gateway.invocation.bundle_store"
    mapping = dict(_LAZY_CAPABILITIES[module])
    source = "_LAZY_EXPORTS = " + repr(mapping) + "\n" + ast.unparse(_LAZY_BODY)
    assert dynamic_violations(module, ast.parse(source)) == []
    for changed in [
        source.replace("target = _LAZY_EXPORTS.get(name)", "target = name"),
        source.replace("if target is None:", "if False:"),
        source.replace("getattr(import_module(target, __name__), name)", "import_module(name)"),
        source + "\n_LAZY_EXPORTS['extra'] = 'subprocess'\n",
        source.replace("embedded_copilot.model_gateway_persistence.bundle_store", "subprocess"),
    ]:
        assert dynamic_violations(module, ast.parse(changed))


def test_management_edge_does_not_grant_provider_core_storage_access() -> None:
    management = "embedded_copilot.model_gateway.management"
    source = "from embedded_copilot.model_gateway.persistence import ModelGatewayState, IdentityIntegrityIssue, LegacyIdentityCorrelation, SQLiteModelGatewayStore"
    assert gateway_dependency_violations(Sources(sources={management: source}), [management]) == []
    assert gateway_dependency_violations(Sources(sources={CORE: source}), [CORE])
    changed = source + "\nfrom .persistence import migrate_legacy_connections\n"
    assert gateway_dependency_violations(Sources(sources={management: changed}), [management])


def test_fixed_api_composition_imports_do_not_allow_arbitrary_dynamic_targets() -> None:
    module = "embedded_copilot.api.main"
    allowed = "import importlib; importlib.import_module('embedded_copilot.knowledge.manifest')"
    assert dynamic_violations(module, ast.parse(allowed)) == []
    for target in ("request.module", "'embedded_copilot.agents.firmware'", "'subprocess'"):
        assert dynamic_violations(module, ast.parse("import importlib; importlib.import_module(" + target + ")"))


def test_lazy_getter_type_annotations_do_not_change_capability() -> None:
    module = "embedded_copilot.model_gateway.invocation.bundle_store"
    source = "_LAZY_EXPORTS = " + repr(_LAZY_CAPABILITIES[module]) + "\n" + ast.unparse(_LAZY_BODY)
    source = source.replace("name: str", "name: Any").replace("-> object:", "-> Any:")
    assert dynamic_violations(module, ast.parse(source)) == []


@pytest.mark.parametrize("symbol", ["validate_phase_grant", "_grant", "_preview", "_attempt_row", "CounterBundleStoreMixin", "ConsumptionStoreMixin"])
@pytest.mark.parametrize("style", ["from", "module-alias", "parent", "getattr"])
def test_every_lazy_sql_export_is_resolved_to_real_infrastructure(symbol: str, style: str) -> None:
    leaf = "bundle_store" if symbol in {"_attempt_row", "CounterBundleStoreMixin"} else "consumption_store"
    facade = "embedded_copilot.model_gateway.invocation." + leaf
    getter = "_LAZY_EXPORTS = " + repr(_LAZY_CAPABILITIES[facade]) + "\n" + ast.unparse(_LAZY_BODY)
    styles = {
        "from": "from ." + leaf + " import " + symbol + " as safe",
        "module-alias": "import " + facade + " as safe; safe." + symbol + "()",
        "parent": "from embedded_copilot.model_gateway import invocation as safe; safe." + leaf + "." + symbol + "()",
        "getattr": "import " + facade + " as safe; getattr(safe, '" + symbol + "')()",
    }
    index = Sources(sources={CORE: styles[style], facade: getter})
    assert gateway_dependency_violations(index, [CORE])


def test_pure_consumption_exception_and_checks_are_not_sql_exports() -> None:
    facade = "embedded_copilot.model_gateway.invocation.consumption_store"
    getter = "_LAZY_EXPORTS = " + repr(_LAZY_CAPABILITIES[facade]) + "\n" + ast.unparse(_LAZY_BODY)
    getter += "\nclass ConsumptionRejected(ValueError): pass\ndef _account(*args): return None\ndef _checked(*args): return None\n"
    index = Sources(sources={CORE: "from .consumption_store import ConsumptionRejected, _account, _checked", facade: getter})
    assert gateway_dependency_violations(index, [CORE]) == []


def test_dynamic_facade_symbol_cannot_hide_a_sql_helper() -> None:
    facade = "embedded_copilot.model_gateway.invocation.consumption_store"
    source = "import " + facade + " as safe; helper = getattr(safe, request.helper); helper()"
    assert gateway_dependency_violations(Sources(sources={CORE: source}), [CORE])


@pytest.mark.parametrize("origin", [CORE, "embedded_copilot.model_gateway.providers.example"])
@pytest.mark.parametrize("implementation,symbol", [
    ("management", "ModelGatewayManagementService"),
    ("legacy_identity_migration", "LegacyIdentityMigrationService"),
])
@pytest.mark.parametrize("style", ["root-from", "direct-from", "root-alias", "module-alias", "transitive-helper"])
def test_pure_core_cannot_borrow_management_or_migration_sql_permission(
    origin: str, implementation: str, symbol: str, style: str,
) -> None:
    root = "embedded_copilot.model_gateway"
    persistence = root + ".persistence"
    helper = "embedded_copilot.pure_boundary_helper"
    def getter(module: str) -> str:
        return "_LAZY_EXPORTS = " + repr(_LAZY_CAPABILITIES[module]) + "\n" + ast.unparse(_LAZY_BODY)
    styles = {
        "root-from": "from embedded_copilot.model_gateway import " + symbol + " as safe",
        "direct-from": "from embedded_copilot.model_gateway." + implementation + " import " + symbol + " as safe",
        "root-alias": "import embedded_copilot.model_gateway as safe; safe." + symbol + "()",
        "module-alias": "from embedded_copilot.model_gateway import " + implementation + " as safe; safe." + symbol + "()",
        "transitive-helper": "from embedded_copilot.pure_boundary_helper import value",
    }
    sources = {
        origin: styles[style],
        root: getter(root),
        persistence: getter(persistence),
        helper: "from embedded_copilot.model_gateway." + implementation + " import " + symbol + " as value",
        root + "." + implementation: "class " + symbol + ": pass",
    }
    assert gateway_dependency_violations(Sources(sources=sources, packages=[root]), [origin])


def test_host_compatibility_root_and_application_management_remain_allowed() -> None:
    root = "embedded_copilot.model_gateway"
    management = root + ".management"
    host = "embedded_copilot.production_host"
    getter = "_LAZY_EXPORTS = " + repr(_LAZY_CAPABILITIES[root]) + "\n" + ast.unparse(_LAZY_BODY)
    source = "from embedded_copilot.model_gateway.persistence import ModelGatewayState, IdentityIntegrityIssue, LegacyIdentityCorrelation, SQLiteModelGatewayStore"
    index = Sources(sources={
        host: "from embedded_copilot.model_gateway import ModelGatewayManagementService",
        root: getter,
        management: source,
    }, packages=[root])
    assert gateway_dependency_violations(index, [host]) == []
    assert gateway_dependency_violations(index, [root]) == []


def _typed_lazy_facade(module: str, *, namespace: bool = False) -> str:
    flag = "typing.TYPE_CHECKING" if namespace else "TYPE_CHECKING"
    declaration = "import typing" if namespace else "from typing import TYPE_CHECKING"
    name, target = next(iter(_LAZY_CAPABILITIES[module].items()))
    return (
        declaration + "\n_LAZY_EXPORTS = " + repr(_LAZY_CAPABILITIES[module])
        + "\nif " + flag + ":\n    from " + target + " import " + name + " as " + name
        + "\n" + ast.unparse(_LAZY_BODY)
    )


@pytest.mark.parametrize("namespace", [False, True])
@pytest.mark.parametrize("module", [
    "embedded_copilot.model_gateway",
    "embedded_copilot.model_gateway.invocation.bundle_store",
    "embedded_copilot.model_gateway.invocation.consumption_store",
    "embedded_copilot.model_gateway.invocation.openai_responses_qualification",
])
def test_verified_lazy_typing_exports_have_no_runtime_import_capability(module: str, namespace: bool) -> None:
    source = _typed_lazy_facade(module, namespace=namespace)
    tree = ast.parse(source)
    assert len(_verified_typing_imports(module, tree)) == 1
    assert dynamic_violations(module, tree) == []
    index = Sources(sources={module: source}, packages=[module] if module == "embedded_copilot.model_gateway" else [])
    for eager in (False, True):
        imports = index.imports(module, eager=eager)
        assert not any("model_gateway_persistence" in target or "production_release" in target or "model_gateway.persistence" in target for target, _ in imports)


@pytest.mark.parametrize("change", [
    "true", "rebind", "wrong-stdlib", "shadow-import", "wrong-target",
    "wrong-symbol", "else-io", "body-call", "alias", "namespace-rebind",
    "namespace-attribute", "namespace-dict", "namespace-update", "unverified-getter",
])
def test_typing_label_does_not_hide_runtime_or_unapproved_exports(change: str) -> None:
    module = "embedded_copilot.model_gateway.invocation.bundle_store"
    source = _typed_lazy_facade(module)
    target = "embedded_copilot.model_gateway_persistence.bundle_store"
    if change == "true":
        source = source.replace("if TYPE_CHECKING:", "TYPE_CHECKING = True\nif TYPE_CHECKING:")
    elif change == "rebind":
        source += "\nTYPE_CHECKING = False\n"
    elif change == "wrong-stdlib":
        source = source.replace("from typing import TYPE_CHECKING", "from external import TYPE_CHECKING")
    elif change == "shadow-import":
        source = source.replace("if TYPE_CHECKING:", "from external import flag as TYPE_CHECKING\nif TYPE_CHECKING:")
    elif change == "wrong-target":
        source = source.replace("    from " + target + " import", "    from subprocess import")
    elif change == "wrong-symbol":
        source = source.replace("import _encode as _encode", "import unknown as unknown")
    elif change == "else-io":
        source = source.replace("\ndef __getattr__", "\nelse:\n    open('not-executed', 'wb')\ndef __getattr__")
    elif change == "body-call":
        source = source.replace("if TYPE_CHECKING:", "if TYPE_CHECKING:\n    open('not-executed', 'wb')")
    elif change == "alias":
        source = source.replace("import _encode as _encode", "import _encode as different")
    elif change.startswith("namespace-"):
        source = _typed_lazy_facade(module, namespace=True)
        mutations = {
            "namespace-rebind": "typing = external",
            "namespace-attribute": "typing.TYPE_CHECKING = True",
            "namespace-dict": "typing.__dict__['TYPE_CHECKING'] = True",
            "namespace-update": "typing.__dict__.update({'TYPE_CHECKING': True})",
        }
        source = source.replace("if typing.TYPE_CHECKING:", mutations[change] + "\nif typing.TYPE_CHECKING:")
    else:
        source = source.replace("target = _LAZY_EXPORTS.get(name)", "target = name")
    tree = ast.parse(source)
    assert _verified_typing_imports(module, tree) == set()
    assert dynamic_violations(module, tree)


def test_ordinary_core_typing_sql_import_has_no_facade_exception() -> None:
    source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n from sqlite3 import connect"
    index = Sources(sources={CORE: source})
    assert gateway_dependency_violations(index, [CORE])


def test_pcb_runtime_star_allows_only_original_named_pure_contract_typing_exports() -> None:
    module = "embedded_copilot.pcb_production"
    source = "from .contracts import *\n" + _typed_lazy_facade(module)
    source = source.replace("if TYPE_CHECKING:\n", "if TYPE_CHECKING:\n    from .contracts import PCBProductionState, PCBPhysicalDesignProposal\n")
    assert len(_verified_typing_imports(module, ast.parse(source))) == 2
    assert dynamic_violations(module, ast.parse(source)) == []
    for changed in [
        source.replace("from .contracts import PCBProductionState", "from .service import PCBProductionState"),
        source.replace("PCBProductionState, PCBPhysicalDesignProposal", "UnexpectedService, PCBPhysicalDesignProposal"),
        source.replace("from .contracts import *\n", ""),
    ]:
        assert _verified_typing_imports(module, ast.parse(changed)) == set()
        assert dynamic_violations(module, ast.parse(changed))


@pytest.mark.parametrize("binding", [
    "reader = getattr",
    "lookup = getattr; reader = lookup",
    "from builtins import getattr as reader",
    "import builtins as functions; reader = functions.getattr",
])
@pytest.mark.parametrize("selector", ["'validate_phase_grant'", "request.selector"])
@pytest.mark.parametrize("placement", ["direct", "assignment", "helper"])
def test_getattr_aliases_cannot_reflect_sql_facade_exports(
    binding: str, selector: str, placement: str,
) -> None:
    facade = "embedded_copilot.model_gateway.invocation.consumption_store"
    source = "import " + facade + " as facade\n" + binding + "\n"
    if placement == "direct":
        source += "reader(facade, " + selector + ")()"
    elif placement == "assignment":
        source += "validate = reader(facade, " + selector + "); validate()"
    else:
        source += "def choose_validator():\n    return reader(facade, " + selector + ")\nchoose_validator()()"
    getter = "_LAZY_EXPORTS = " + repr(_LAZY_CAPABILITIES[facade]) + "\n" + ast.unparse(_LAZY_BODY)
    index = Sources(sources={CORE: source, facade: getter})
    assert gateway_dependency_violations(index, [CORE])


@pytest.mark.parametrize("binding", ["reader = getattr", "from builtins import getattr as reader"])
@pytest.mark.parametrize("selector", ["'_account'", "'_checked'", "'ConsumptionRejected'"])
def test_alias_reflection_of_pure_legacy_consumption_objects_stays_allowed(binding: str, selector: str) -> None:
    facade = "embedded_copilot.model_gateway.invocation.consumption_store"
    source = "import " + facade + " as facade\n" + binding + "\nvalue = reader(facade, " + selector + ")"
    getter = "_LAZY_EXPORTS = " + repr(_LAZY_CAPABILITIES[facade]) + "\n" + ast.unparse(_LAZY_BODY)
    getter += "\nclass ConsumptionRejected(ValueError): pass\ndef _account(*args): return None\ndef _checked(*args): return None\n"
    assert gateway_dependency_violations(Sources(sources={CORE: source, facade: getter}), [CORE]) == []


def test_getattr_alias_on_non_facade_metadata_is_not_a_sql_capability() -> None:
    source = "reader = getattr\nname = reader(metadata, 'name')\nvalue = reader(metadata, request.field)"
    assert gateway_dependency_violations(Sources(sources={CORE: source}), [CORE]) == []
