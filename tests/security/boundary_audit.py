"""Pure source boundary checks; never import or execute the inspected package.

This is a conservative regression guard, not a Python sandbox or a proof of all
runtime behavior. Behavioral cold-import, dispatch and resource tests complement
the static checks. New capabilities require explicit rules and negative cases.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Violation:
    module: str
    line: int
    rule: str
    detail: str


def within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return base + "." + node.attr if base else node.attr
    return ""


class Sources:
    """Lazy, source-only module index, also usable with synthetic source strings."""

    def __init__(
        self, root: Path | None = None, sources: Mapping[str, str] | None = None,
        packages: Iterable[str] = (),
    ) -> None:
        self.root = root
        self.sources = dict(sources or {})
        self.packages = set(packages)
        self.trees: dict[str, ast.Module] = {}

    def get(self, module: str) -> ast.Module | None:
        if module in self.trees:
            return self.trees[module]
        if module not in self.sources and self.root is not None:
            path = self.root.joinpath(*module.split("."))
            if (path / "__init__.py").is_file():
                path = path / "__init__.py"
                self.packages.add(module)
            else:
                path = path.with_suffix(".py")
            if path.is_file():
                self.sources[module] = path.read_text(encoding="utf-8-sig")
        if module not in self.sources:
            return None
        tree = ast.parse(self.sources[module], filename=module)
        self.trees[module] = tree
        return tree

    def modules_under(self, prefix: str) -> tuple[str, ...]:
        names = {name for name in self.sources if within(name, prefix)}
        if self.root is not None:
            directory = self.root.joinpath(*prefix.split("."))
            for path in directory.rglob("*.py"):
                parts = path.relative_to(self.root).with_suffix("").parts
                name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
                names.add(name)
        return tuple(sorted(names))

    def imports(self, module: str, *, eager: bool = False) -> tuple[tuple[str, int], ...]:
        tree = self.get(module)
        if tree is None:
            return ()
        nodes: list[ast.AST] = []
        typing_imports = _verified_typing_imports(module, tree)

        def walk(node: ast.AST) -> None:
            if node in typing_imports:
                return
            nodes.append(node)
            if eager and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                # Lazy __getattr__ bodies do not run when importing a DTO leaf.
                # Direct imports of their implementation symbols are checked below.
                return
            for child in ast.iter_child_nodes(node):
                walk(child)

        walk(tree)
        result: list[tuple[str, int]] = []
        for node in nodes:
            if isinstance(node, ast.Import):
                result.extend((alias.name, node.lineno) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                package = module if module in self.packages else module.rpartition(".")[0]
                if node.level:
                    bits = package.split(".")
                    if node.level > len(bits):
                        result.append(("<invalid-relative-import>", node.lineno))
                        continue
                    base = ".".join(bits[: len(bits) - node.level + 1])
                    base += ("." + node.module) if node.module else ""
                else:
                    base = node.module or ""
                result.append((base, node.lineno))
                # Keep the full symbol even if it is not a module: parent imports
                # of agents/service/adapter or BuildPort must not evade the rule.
                result.extend((base + "." + alias.name, node.lineno) for alias in node.names)
        return tuple(result)

    def closure(self, seeds: Iterable[str], *, stop: Iterable[str] = ()) -> set[str]:
        pending = list(seeds)
        seen: set[str] = set()
        while pending:
            module = pending.pop()
            if module in seen or any(within(module, item) for item in stop):
                continue
            if self.get(module) is None:
                continue
            seen.add(module)
            pending.extend(target for target, _ in self.imports(module))
            # Python activates parent package initializers for a dotted import.
            for length in range(1, len(module.split("."))):
                parent = ".".join(module.split(".")[:length])
                if self.get(parent) is not None and parent in self.packages:
                    pending.extend(target for target, _ in self.imports(parent, eager=True))
        return seen

    def references(self, module: str) -> tuple[tuple[str, int], ...]:
        """Imports plus module-attribute use through aliases and parent modules."""
        tree = self.get(module)
        if tree is None:
            return ()
        imports = self.imports(module)
        typing_imports = _verified_typing_imports(module, tree)
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if node in typing_imports:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                base = next((target for target, line in imports if line == node.lineno), "")
                for alias in node.names:
                    aliases[alias.asname or alias.name] = base + "." + alias.name
        # Propagate simple alias assignments to a fixed point, regardless of
        # whether the RHS was imported under a spelling different from its name.
        for _ in range(8):
            previous = dict(aliases)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    value = _resolve(dotted(node.value), aliases)
                    if isinstance(node.value, ast.Call) and _resolve(dotted(node.value.func), aliases) in {"getattr", "builtins.getattr"} and len(node.value.args) >= 2 and isinstance(node.value.args[1], ast.Constant) and isinstance(node.value.args[1].value, str):
                        value = _resolve(dotted(node.value.args[0]), aliases) + "." + node.value.args[1].value
                    if value and value.partition(".")[0] != node.targets[0].id:
                        aliases[node.targets[0].id] = value
            if aliases == previous:
                break
        attributes = tuple(
            (_resolve(dotted(node), aliases), node.lineno)
            for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        )
        static_getattrs = tuple(
            (_resolve(dotted(node.args[0]), aliases) + "." + node.args[1].value, node.lineno)
            for node in ast.walk(tree) if isinstance(node, ast.Call) and _resolve(dotted(node.func), aliases) in {"getattr", "builtins.getattr"} and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
        )
        name_calls = tuple(
            (_resolve(node.func.id, aliases), node.lineno)
            for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        )
        dynamic_getattrs = tuple(
            (_resolve(dotted(node.args[0]), aliases) + ".<dynamic-attribute>", node.lineno)
            for node in ast.walk(tree) if isinstance(node, ast.Call) and _resolve(dotted(node.func), aliases) in {"getattr", "builtins.getattr"} and len(node.args) >= 2 and not (isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str))
        )
        return imports + attributes + static_getattrs + name_calls + dynamic_getattrs


_DYNAMIC = {"__import__", "eval", "exec", "compile", "importlib.import_module"}
_AGENT = ("embedded_copilot.agents", "embedded_copilot.supervisor")
_DOMAIN = (
    "firmware", "firmware_engineering", "hardware", "hardware_intelligence",
    "pcb", "pcb_production", "debug", "debug_analysis", "debug_intelligence", "knowledge",
)
_GATEWAY_FORBIDDEN = _AGENT + tuple(
    "embedded_copilot." + name for name in (
        "production_release", "bootstrap", "execution", "execution_worker",
        "tool_runtime", "tools", "workspace_runtime", "engineering_memory",
        "engineering_knowledge", "knowledge_governance", "memory_automation",
        "runtime", "workflow", "memory", "graph", "build", "flash", "device", "tool",
    )
) + tuple("embedded_copilot." + name for name in _DOMAIN) + (
    "embedded_copilot.model_runtime", "langgraph", "langchain", "openai", "deepseek", "qwen", "anthropic",
    "google.genai", "subprocess", "socket", "requests", "urllib.request",
    "urllib.error", "urllib3", "sqlite3", "aiosqlite",
)
_MUTATION = {"write", "update", "delete", "execute", "executemany", "executescript", "run", "invoke", "promote", "system"}

_STORAGE_DATA = {"IdentityIntegrityIssue", "LegacyIdentityCorrelation", "ModelGatewayState"}
_STORAGE_EXPORTS = _STORAGE_DATA | {"LegacyModelMigrationError", "SQLiteModelGatewayStore", "migrate_legacy_connections"}
_MIGRATION_EXPORTS = {"LegacyIdentityMigrationService", "LegacyIdentityMigrationSummary", "apply_legacy_identity_migration"}
_LEGACY_EXPORTS = {"LegacyIdentityDryRunPreview", "LegacyIdentityResolution", "LegacyIdentityResolutionStatus", "LegacyIdentityResolver", "LegacyIdentitySource", "LegacyIdentityStatus", "integrity_issue_from_resolution"}
_LAZY_CAPABILITIES: dict[str, dict[str, str]] = {
    "embedded_copilot.model_gateway": {
        **{name: "embedded_copilot.model_gateway.persistence" for name in _STORAGE_EXPORTS | _MIGRATION_EXPORTS | _LEGACY_EXPORTS},
        "ConnectionProbePort": "embedded_copilot.model_gateway.management",
        "ModelGatewayManagementService": "embedded_copilot.model_gateway.management",
    },
    "embedded_copilot.model_gateway.persistence": {
        **{name: "embedded_copilot.model_gateway_storage" for name in _STORAGE_EXPORTS},
        **{name: "embedded_copilot.model_gateway.legacy_identity_migration" for name in _MIGRATION_EXPORTS},
    },
    "embedded_copilot.model_gateway.invocation.bundle_store": {
        name: "embedded_copilot.model_gateway_persistence.bundle_store" for name in ("_encode", "_changed", "_attempt_row", "_read_claim", "_update_attempt", "_update_claim", "CounterBundleStoreMixin")
    },
    "embedded_copilot.model_gateway.invocation.consumption_store": {
        name: "embedded_copilot.model_gateway_persistence.consumption_store" for name in ("_encode", "_decode", "initialize_consumption_schema", "_owner", "_preview", "_grant", "_event", "validate_phase_grant", "ConsumptionStoreMixin")
    },
    "embedded_copilot.model_gateway.invocation.openai_responses_qualification": {
        "OpenAIResponsesTransportSource": "embedded_copilot.production_release.model_transport_qualification",
    },
    "embedded_copilot.firmware_engineering": {
        **{name: "embedded_copilot.firmware_engineering.readiness" for name in ("blocked_build_result", "create_firmware_build_request", "evaluate_build_readiness", "is_firmware_build_request")},
        **{name: "embedded_copilot.firmware_engineering.service" for name in ("FirmwareBuildService", "FirmwareDebugService", "FirmwareEngineeringService")},
    },
    "embedded_copilot.pcb_production": {
        **{name: "embedded_copilot.pcb_production.service" for name in ("PCBProductionService", "create_pcb_production_state", "create_pcb_repair_proposal", "decide_pcb_proposal", "decide_pcb_repair")},
        **{name: "embedded_copilot.pcb_production.serializer" for name in ("inspect_serializer_boundary", "serialize_kicad_pcb")},
        "KiCadFootprintAvailability": "embedded_copilot.pcb_production.adapters",
    },
    "embedded_copilot.debug_analysis": {
        "DebugAnalysisService": "embedded_copilot.debug_analysis.service",
    },
}
_LAZY_BODY = ast.parse('''
def __getattr__(name: str) -> object:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module
    return getattr(import_module(target, __name__), name)
''').body[0]


def _function_logic(function: ast.FunctionDef) -> str:
    normalized = copy.deepcopy(function)
    normalized.returns = None
    for arg in (*normalized.args.posonlyargs, *normalized.args.args, *normalized.args.kwonlyargs):
        arg.annotation = None
    if normalized.args.vararg:
        normalized.args.vararg.annotation = None
    if normalized.args.kwarg:
        normalized.args.kwarg.annotation = None
    return ast.dump(normalized, include_attributes=False)


def _verified_lazy(module: str, tree: ast.AST) -> ast.FunctionDef | None:
    if not isinstance(tree, ast.Module) or module not in _LAZY_CAPABILITIES:
        return None
    getters = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "__getattr__"]
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_LAZY_EXPORTS" for t in n.targets)]
    if len(getters) != 1 or len(assignments) != 1:
        return None
    try:
        mapping = ast.literal_eval(assignments[0].value)
    except (ValueError, TypeError):
        return None
    if type(mapping) is not dict or mapping != _LAZY_CAPABILITIES[module]:
        return None
    if not isinstance(_LAZY_BODY, ast.FunctionDef) or _function_logic(getters[0]) != _function_logic(_LAZY_BODY):
        return None
    stores = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "_LAZY_EXPORTS" and isinstance(n.ctx, ast.Store)]
    if len(stores) != 1:
        return None
    for n in ast.walk(tree):
        if (
            isinstance(n, (ast.Subscript, ast.Attribute))
            and isinstance(n.value, ast.Name)
            and n.value.id == "_LAZY_EXPORTS"
            and not (isinstance(n, ast.Attribute) and n.attr == "get" and getters[0].lineno < n.lineno <= getters[0].end_lineno)
        ):
            return None
    return getters[0]


def _resolve_lazy_symbol(index: Sources, name: str) -> str:
    seen: set[str] = set()
    while name not in seen:
        seen.add(name)
        matched = False
        for facade, exports in _LAZY_CAPABILITIES.items():
            if not name.startswith(facade + "."):
                continue
            symbol, dot, tail = name[len(facade) + 1:].partition(".")
            tree = index.get(facade)
            if symbol in exports and (tree is None or _verified_lazy(facade, tree) is None):
                return "<unverified-lazy-capability>." + name
            if symbol in exports:
                name = exports[symbol] + "." + symbol + (dot + tail if dot else "")
                matched = True
                break
        if not matched:
            return name
    return name


_LAZY_PACKAGE_ROOTS = {
    "embedded_copilot.model_gateway", "embedded_copilot.firmware_engineering",
    "embedded_copilot.pcb_production", "embedded_copilot.debug_analysis",
}
_PCB_CONTRACT_TYPING_EXPORTS = {
    "PCBProductionContract", "PCBReadinessStatus", "PCBProductionStatus",
    "PCBProposalDecisionStatus", "PCBDrcStatus", "PCBParseValidationStatus",
    "PCBEngineeringStatus", "PCBArtifactDiffStatus", "PCBDrcIssueSeverity",
    "PCBDrcIssueStatus", "PCBDrcIssueClassification", "PCBRepairDecisionAction",
    "PCBRepairApplicationStatus", "PCBEndpointTruth", "PCBComponentTruth",
    "PCBNetTruth", "PCBBoardDefinition", "PCBPlacementConstraint",
    "PCBRoutingConstraint", "PCBRouteSegment", "PCBPhysicalDesignProposal",
    "PCBProductionRequest", "PCBReadinessResult", "PCBFootprint",
    "PCBNetAssignment", "PCBProposal", "PCBArtifactDiff", "PCBProposalDecision",
    "DrcIssue", "PCBDrcViolation", "PCBDrcResult", "PCBDrcReceipt",
    "PCBArtifactRecord", "PCBParseValidationEvidence", "PCBGenerationReceipt",
    "PCBRepairProposal", "PCBRepairDecision", "PCBRepairApplicationReceipt",
    "PCBProductionState", "PCBFootprintAvailabilityPort",
}


def _typing_test(node: ast.AST) -> bool:
    return dotted(node) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}


def _trusted_typing_flag(tree: ast.Module, spelling: str) -> bool:
    binding = "TYPE_CHECKING" if spelling == "TYPE_CHECKING" else "typing"
    declarations = []
    for node in tree.body:
        if binding == "TYPE_CHECKING" and isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "typing":
            declarations.extend(alias for alias in node.names if alias.name == "TYPE_CHECKING" and alias.asname is None)
        elif binding == "typing" and isinstance(node, ast.Import):
            declarations.extend(alias for alias in node.names if alias.name == "typing" and alias.asname is None)
    if len(declarations) != 1:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == binding and isinstance(node.ctx, (ast.Store, ast.Del)):
            return False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == binding:
            return False
        if isinstance(node, ast.arg) and node.arg == binding:
            return False
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                if bound == binding and alias is not declarations[0]:
                    return False
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)) and any(isinstance(part, ast.Name) and part.id == "typing" for part in ast.walk(node)):
            return False
        if isinstance(node, ast.Call) and dotted(node.func) in {"globals", "locals", "vars", "setattr", "delattr"}:
            return False
        if isinstance(node, ast.Call) and (dotted(node.func).startswith("typing.") or any(isinstance(arg, ast.Name) and arg.id == "typing" for arg in node.args)):
            return False
    return True


def _verified_typing_imports(module: str, tree: ast.AST) -> set[ast.ImportFrom]:
    """Only false, fixed stdlib typing branches of a verified lazy facade.

    No global TYPE_CHECKING skip: every declaration must re-export the exact
    mapped symbol/target (or PCB's existing, explicit pure-contract star edge).
    """
    if not isinstance(tree, ast.Module) or _verified_lazy(module, tree) is None:
        return set()
    result: set[ast.ImportFrom] = set()
    for node in tree.body:
        if not isinstance(node, ast.If) or not _typing_test(node.test):
            continue
        if not _trusted_typing_flag(tree, dotted(node.test)) or node.orelse or not node.body:
            continue
        accepted: set[ast.ImportFrom] = set()
        for declaration in node.body:
            if not isinstance(declaration, ast.ImportFrom):
                break
            package = module if module in _LAZY_PACKAGE_ROOTS else module.rpartition(".")[0]
            if declaration.level:
                bits = package.split(".")
                if declaration.level > len(bits):
                    break
                target = ".".join(bits[:len(bits) - declaration.level + 1])
                target += ("." + declaration.module) if declaration.module else ""
            else:
                target = declaration.module or ""
            runtime_star = (
                module == "embedded_copilot.pcb_production"
                and target == module + ".contracts"
                and any(isinstance(n, ast.ImportFrom) and n.level == 1 and n.module == "contracts" and [a.name for a in n.names] == ["*"] for n in tree.body)
            )
            if not declaration.names or any(
                alias.asname not in {None, alias.name}
                or not (
                    _LAZY_CAPABILITIES[module].get(alias.name) == target
                    or (runtime_star and alias.name in _PCB_CONTRACT_TYPING_EXPORTS)
                )
                for alias in declaration.names
            ):
                break
            accepted.add(declaration)
        else:
            result.update(accepted)
    return result


def _typing_branch_violations(module: str, tree: ast.AST) -> list[Violation]:
    if module not in _LAZY_CAPABILITIES or not isinstance(tree, ast.Module):
        return []
    accepted = _verified_typing_imports(module, tree)
    return [
        Violation(module, node.lineno, "invalid-lazy-typing-branch", ast.unparse(node.test))
        for node in tree.body if isinstance(node, ast.If) and _typing_test(node.test)
        and (node.orelse or not node.body or any(declaration not in accepted for declaration in node.body))
    ]


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = (node.module + "." if node.module else "") + alias.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = dotted(node.value)
            if isinstance(node.value, ast.Call) and dotted(node.value.func) == "getattr" and len(node.value.args) >= 2 and isinstance(node.value.args[1], ast.Constant) and isinstance(node.value.args[1].value, str):
                value = dotted(node.value.args[0]) + "." + node.value.args[1].value
            if value:
                aliases[node.targets[0].id] = value
    return aliases


def _resolve(name: str, aliases: Mapping[str, str]) -> str:
    seen: set[str] = set()
    while name:
        head, dot, tail = name.partition(".")
        if head in seen:
            break
        seen.add(head)
        if head not in aliases or aliases[head] == head:
            break
        name = aliases[head] + (dot + tail if dot else "")
    return name


_FIXED_DYNAMIC_IMPORTS = {
    "embedded_copilot.api.main": {
        "embedded_copilot.api.optimization_v26_routes",
        "embedded_copilot.knowledge.manifest",
        "embedded_copilot.firmware_source_execution",
    },
    "embedded_copilot.api.engineering_completion_v28_routes": {
        "embedded_copilot.engineering_completion.contracts",
        "embedded_copilot.engineering_completion.models",
    },
    "embedded_copilot.api.knowledge_v27_routes": {
        "embedded_copilot.knowledge_evolution.contracts",
    },
}


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        return left + right if left is not None and right is not None else None
    return None


def dynamic_violations(module: str, tree: ast.AST) -> list[Violation]:
    aliases = _aliases(tree)
    lazy = _verified_lazy(module, tree)
    return _typing_branch_violations(module, tree) + [
        Violation(module, node.lineno, "dynamic-import-or-code", ast.unparse(node.func))
        for node in ast.walk(tree) if isinstance(node, ast.Call)
        and _resolve(dotted(node.func), aliases) in _DYNAMIC
        and not (lazy is not None and lazy.lineno <= node.lineno <= lazy.end_lineno)
        and not (_resolve(dotted(node.func), aliases) == "importlib.import_module" and len(node.args) == 1 and not node.keywords and _literal_text(node.args[0]) in _FIXED_DYNAMIC_IMPORTS.get(module, set()))
    ]


def _pure_data_leaf(index: Sources, module: str) -> bool:
    """Check helper substance, including its transitive imports; no basename pass."""
    tree = index.get(module)
    if tree is None or dynamic_violations(module, tree):
        return False
    if any(isinstance(n, ast.ClassDef) and n.name.endswith(("Service", "Adapter", "Agent", "Runtime")) for n in ast.walk(tree)):
        return False
    forbidden = {"os", "pathlib", "subprocess", "socket", "httpx", "requests", "sqlite3", "importlib", "langgraph", "openai", "deepseek", "qwen"}
    lazy = _verified_lazy(module, tree)
    for target, line in index.references(module):
        if lazy is not None and lazy.lineno <= line <= lazy.end_lineno:
            continue
        if target.split(".")[0] in forbidden or any(part in {"agents", "supervisor", "service", "adapters", "provider", "production_release", "execution_worker"} for part in target.split(".")):
            return False
    denied_calls = _MUTATION | {"open", "read_bytes", "read_text", "write_bytes", "write_text", "unlink", "mkdir", "rmdir", "glob", "rglob", "iterdir"}
    return not any(isinstance(n, ast.Call) and dotted(n.func).rsplit(".", 1)[-1] in denied_calls for n in ast.walk(tree))


def api_domain_violations(index: Sources, modules: Iterable[str]) -> list[Violation]:
    violations: list[Violation] = []
    for module in modules:
        tree = index.get(module)
        if tree is None:
            continue
        violations.extend(dynamic_violations(module, tree))
        for target, line in index.references(module):
            parts = target.split(".")
            if any(within(target, item) for item in _AGENT):
                violations.append(Violation(module, line, "api-agent", target))
            elif len(parts) > 1 and parts[0] == "embedded_copilot" and parts[1] in _DOMAIN:
                domain = ".".join(parts[:2])
                allowed_leaves = {domain + ".contracts", domain + ".models", domain + ".exceptions"}
                allowed = domain + ".contracts"
                intent = "embedded_copilot.firmware_engineering.request_intent"
                # A from-import emits the parent module as well as its symbols.
                # A parent is allowed only when it has a concrete approved leaf.
                targets = {item for item, _ in index.imports(module)}
                if target == domain and any(within(item, allowed) or within(item, intent) for item in targets):
                    continue
                leaf = next((name for name in allowed_leaves if within(target, name)), None)
                if not (leaf is not None or within(target, intent)):
                    violations.append(Violation(module, line, "api-domain-implementation", target))
                    continue
                seed = intent if within(target, intent) else leaf
                assert seed is not None
                for reached in index.closure([seed]):
                    reached_tree = index.get(reached)
                    assert reached_tree is not None
                    violations.extend(dynamic_violations(reached, reached_tree))
                    if within(reached, domain) and reached not in {domain, intent} and not _pure_data_leaf(index, reached):
                        violations.append(Violation(module, line, "dto-transitive-implementation", reached))
                    if any(within(reached, prefix) for prefix in _AGENT):
                        violations.append(Violation(module, line, "dto-transitive-agent", reached))
                    if not _pure_data_leaf(index, reached):
                        violations.append(Violation(module, line, "dto-transitive-capability", reached))
                    if any(isinstance(n, ast.ClassDef) and n.name.endswith(("Service", "Adapter", "Agent")) for n in ast.walk(reached_tree)):
                        violations.append(Violation(module, line, "dto-implementation-class", reached))
    return violations


def gateway_dependency_violations(index: Sources, modules: Iterable[str]) -> list[Violation]:
    violations: list[Violation] = []
    application_modules = {
        "embedded_copilot.model_gateway",
        "embedded_copilot.model_gateway.management",
        "embedded_copilot.model_gateway.legacy_identity_migration",
        "embedded_copilot.model_gateway.legacy_identity",
        "embedded_copilot.model_gateway.persistence",
    }
    # Preserve the original caller role through every helper and lazy alias.
    # An application module's legitimate SQL edge cannot sanitize a core caller.
    pending = [
        (module, within(module, "embedded_copilot.model_gateway") and module not in application_modules)
        for module in modules
    ]
    seen: set[tuple[str, bool]] = set()
    # Exact existing application-management edges, not a directory capability.
    app_edges = {
        "embedded_copilot.model_gateway.management": _STORAGE_DATA | {"SQLiteModelGatewayStore"},
        "embedded_copilot.model_gateway.legacy_identity_migration": _STORAGE_DATA | {"SQLiteModelGatewayStore"},
        "embedded_copilot.model_gateway.legacy_identity": {"IdentityIntegrityIssue"},
    }
    persistence_targets = ("embedded_copilot.model_gateway_storage", "embedded_copilot.model_gateway_persistence")
    while pending:
        module, pure_origin = pending.pop()
        if (module, pure_origin) in seen or index.get(module) is None:
            continue
        seen.add((module, pure_origin))
        tree = index.get(module)
        assert tree is not None
        violations.extend(dynamic_violations(module, tree))
        imported = index.imports(module)
        def allowed_app_edge(
            target: str,
            line: int,
            *,
            source_module: str,
            references: tuple[tuple[str, int], ...],
        ) -> bool:
            symbols = app_edges.get(source_module, set())
            for base in ("embedded_copilot.model_gateway.persistence", "embedded_copilot.model_gateway_storage"):
                if target == base:
                    children = {t[len(base) + 1:] for t, ln in references if ln == line and t.startswith(base + ".")}
                    return bool(children) and children <= symbols
                if target.startswith(base + "."):
                    return target[len(base) + 1:].split(".", 1)[0] in symbols
            return False
        for target, line in index.references(module):
            if allowed_app_edge(target, line, source_module=module, references=imported):
                continue
            if target.endswith(".<dynamic-attribute>") and target.removesuffix(".<dynamic-attribute>") in _LAZY_CAPABILITIES:
                violations.append(Violation(module, line, "dynamic-facade-symbol", target))
                continue
            target = _resolve_lazy_symbol(index, target)
            if target.startswith("<unverified-lazy-capability>."):
                violations.append(Violation(module, line, "unverified-lazy-capability", target))
                continue
            if pure_origin and any(within(target, implementation) for implementation in (
                "embedded_copilot.model_gateway.management",
                "embedded_copilot.model_gateway.legacy_identity_migration",
            )):
                violations.append(Violation(module, line, "pure-core-application-backdoor", target))
                continue
            if any(within(target, prefix) for prefix in persistence_targets):
                violations.append(Violation(module, line, "core-concrete-persistence", target))
                continue
            data_leaves = ("embedded_copilot.engineering_knowledge.models", "embedded_copilot.knowledge.models", "embedded_copilot.knowledge.source")
            pure_data = next((prefix for prefix in data_leaves if within(target, prefix) and _pure_data_leaf(index, prefix)), None)
            if any(within(target, prefix) for prefix in _GATEWAY_FORBIDDEN) and pure_data is None:
                violations.append(Violation(module, line, "gateway-forbidden-dependency", target))
            if within(target, "httpx") and not within(module, "embedded_copilot.model_gateway.adapters") and not within(module, "embedded_copilot.model_gateway.vision.adapters"):
                violations.append(Violation(module, line, "http-outside-adapter", target))
            if target.startswith("embedded_copilot."):
                pending.append((target, pure_origin))
    return violations


def _scopes(tree: ast.AST) -> dict[ast.AST, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef | None]]:
    result: dict[ast.AST, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef | None]] = {}

    def visit(node: ast.AST, names: tuple[str, ...], function: ast.FunctionDef | ast.AsyncFunctionDef | None) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names += (node.name,)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node
        result[node] = (".".join(names), function)
        for child in ast.iter_child_nodes(node):
            visit(child, names, function)

    visit(tree, (), None)
    return result


_LOCAL_UPDATES = {
    ("embedded_copilot.model_gateway.adapters.protocol_http", "ProtocolHTTPProviderAdapter.prepare_transport", "headers"): "dict",
    ("embedded_copilot.model_gateway.invocation.contracts", "ModelInvocationAttempt._verify", "excluded"): "set",
    ("embedded_copilot.model_gateway.invocation.contracts", "ModelInvocationReceipt._verify", "excluded"): "set",
    ("embedded_copilot.model_gateway.invocation.qualified_budget_producer", "QualifiedInvocationBudgetProducer._build", "values"): "collect",
    ("embedded_copilot.model_gateway.invocation.qualified_bundle_producer", "QualifiedCounterBundleProducer._generation_evidence", "values"): "dict",
}


def _fresh_local_update(module: str, call: ast.Call, tree: ast.Module) -> bool:
    if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name):
        return False
    scopes = _scopes(tree)
    scope, function = scopes[call]
    receiver = call.func.value.id
    kind = _LOCAL_UPDATES.get((module, scope, receiver))
    if kind is None or function is None:
        return False
    same_updates = [node for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update" and isinstance(node.func.value, ast.Name) and node.func.value.id == receiver]
    if len(same_updates) != 1:
        return False
    if any(isinstance(node, (ast.Global, ast.Nonlocal)) and receiver in node.names for node in ast.walk(function)):
        return False
    if any(arg.arg == receiver for arg in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)):
        return False
    writes = [node for node in ast.walk(function) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == receiver]
    if len(writes) != 1:
        return False
    assignments = [node for node in ast.walk(function) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == receiver for target in node.targets)]
    if kind in {"dict", "set"}:
        if len(assignments) != 1 or assignments[0].lineno >= call.lineno:
            return False
        if not isinstance(assignments[0].value, ast.Dict if kind == "dict" else ast.Set):
            return False
    else:
        # _build receives the second element of self._collect(context). That
        # helper must allocate its result dict locally and return that exact name.
        assignments = [n for n in ast.walk(function) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and dotted(n.value.func) == "self._collect" and len(n.targets) == 1 and isinstance(n.targets[0], ast.Tuple) and len(n.targets[0].elts) == 3 and isinstance(n.targets[0].elts[1], ast.Name) and n.targets[0].elts[1].id == receiver]
        if len(assignments) != 1 or assignments[0].lineno >= call.lineno:
            return False
        cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "QualifiedInvocationBudgetProducer"), None)
        helpers = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_collect"] if cls else []
        if len(helpers) != 1:
            return False
        if any(isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store) and n.attr == "_collect" for n in ast.walk(tree)):
            return False
        helper = helpers[0]
        returns = [n for n in ast.walk(helper) if isinstance(n, ast.Return)]
        if len(returns) != 1 or not isinstance(returns[0].value, ast.Tuple) or len(returns[0].value.elts) != 3:
            return False
        output = returns[0].value.elts[1]
        if not isinstance(output, ast.Name):
            return False
        allocs = [n for n in helper.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == output.id for t in n.targets)]
        if not allocs or not isinstance(allocs[-1].value, ast.Dict):
            return False
        # No reassignment or alias/escape after its allocation before return.
        for n in ast.walk(helper):
            if allocs[-1].lineno < getattr(n, "lineno", 0) < returns[0].lineno and isinstance(n, ast.Name) and n.id == output.id:
                return False
    # Reject aliases, external storage and calls receiving the mutable object.
    # **values constructs a new keyword mapping; canonical_fingerprint's exclude
    # is an explicit read-only dependency checked by its existing behavior tests.
    for n in ast.walk(function):
        if not isinstance(n, ast.Name) or n.id != receiver or not isinstance(n.ctx, ast.Load) or n.lineno >= call.lineno:
            continue
        parent = next((p for p in ast.walk(function) if n in ast.iter_child_nodes(p)), None)
        if isinstance(parent, ast.Attribute) and parent.value is n:
            if parent.attr not in {"add", "update"}:
                return False
        elif (
            isinstance(parent, ast.Subscript) and parent.value is n
        ) or (
            isinstance(parent, ast.keyword) and parent.arg is None
        ):
            continue
        else:
            return False
    return True


def gateway_mutation_violations(index: Sources, modules: Iterable[str]) -> list[Violation]:
    result: list[Violation] = []
    for module in modules:
        tree = index.get(module)
        if tree is None:
            continue
        aliases = _aliases(tree)
        mutation_aliases: set[str] = set()
        for _ in range(len(aliases) + 1):
            for name, target in aliases.items():
                if target.rsplit(".", 1)[-1].casefold() in _MUTATION or target in mutation_aliases:
                    mutation_aliases.add(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = _resolve(dotted(node.func), aliases).rsplit(".", 1)[-1].casefold()
                raw = dotted(node.func).rsplit(".", 1)[-1].casefold()
                if raw in _MUTATION:
                    called = raw
                if isinstance(node.func, ast.Name) and node.func.id in mutation_aliases:
                    called = "execute"  # a proven alias of a forbidden operation
                if isinstance(node.func, ast.Call) and dotted(node.func.func) == "getattr":
                    if len(node.func.args) >= 2 and isinstance(node.func.args[1], ast.Constant) and isinstance(node.func.args[1].value, str):
                        called = node.func.args[1].value.casefold()
                    else:
                        result.append(Violation(module, node.lineno, "gateway-indirect-dynamic-call", ast.unparse(node.func)))
                if called in _MUTATION and not (called == "update" and _fresh_local_update(module, node, tree)):
                    result.append(Violation(module, node.lineno, "gateway-external-mutation", ast.unparse(node.func)))
    return result


def agent_api_execution_violations(index: Sources, modules: Iterable[str]) -> list[Violation]:
    result: list[Violation] = []
    forbidden = ("subprocess", "embedded_copilot.execution_worker.worker")
    for module in modules:
        tree = index.get(module)
        if tree is None:
            continue
        result.extend(dynamic_violations(module, tree))
        for target, line in index.references(module):
            if any(within(target, item) for item in forbidden) or target.rsplit(".", 1)[-1] in {"BuildPort", "FlashPort"}:
                result.append(Violation(module, line, "agent-api-execution", target))
        # Retain legacy lexical prohibitions as additional conservative checks.
        for token in ("execution_worker.worker", "BuildPort", "FlashPort"):
            if token in index.sources[module]:
                result.append(Violation(module, 0, "legacy-execution-token", token))
    return result


_B2_READER = ast.parse('''
def _read_approved_resource() -> bytes:
    root = Path(__file__).resolve().parent / "resources"
    path = _approved_resource_path()
    if path.name != _APPROVED_RESOURCE_NAME or path.resolve().parent != root.resolve():
        raise ValueError("approved component resource layout is invalid")
    for entry in (root, path):
        metadata = entry.lstat()
        if entry.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("approved component resource reparse point is invalid")
    if path.stat().st_size != _APPROVED_RESOURCE_SIZE:
        raise ValueError("approved component resource size is invalid")
    with path.open("rb") as stream:
        data = stream.read(_APPROVED_RESOURCE_SIZE + 1)
    if len(data) != _APPROVED_RESOURCE_SIZE:
        raise ValueError("approved component resource size is invalid")
    return data
''').body[0]


def _b2_capability(module: str, tree: ast.Module) -> bool:
    if module != "embedded_copilot.component_recommendation.catalog":
        return False
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    functions = {node.name: node for node in definitions}
    if len(definitions) != len(functions):
        return False
    required = {"_approved_resource_path", "_read_approved_resource", "load_approved_component_source", "_validated_approved_source"}
    if not required <= functions.keys():
        return False
    for name in required - {"_validated_approved_source"}:
        function = functions[name]
        if function.args.args or function.args.posonlyargs or function.args.kwonlyargs or function.args.vararg or function.args.kwarg:
            return False
    loader = functions["load_approved_component_source"]
    returns = [node for node in ast.walk(loader) if isinstance(node, ast.Return)]
    if len(returns) != 1 or ast.unparse(returns[0].value) != "_validated_approved_source(_read_approved_resource())":
        return False
    path_helper = functions["_approved_resource_path"]
    returns = [n for n in ast.walk(path_helper) if isinstance(n, ast.Return)]
    if len(returns) != 1 or ast.unparse(returns[0].value) != "Path(__file__).resolve().parent / 'resources' / _APPROVED_RESOURCE_NAME":
        return False
    if len(path_helper.body) != 1:
        return False
    reader = functions["_read_approved_resource"]
    # Compare the fixed capability's control/data flow, not source bytes/hash.
    # This rejects removal of bounds/reparse/layout checks, extra reads, fallback,
    # request paths or writes even if the file/function names remain unchanged.
    if ast.dump(reader, include_attributes=False) != ast.dump(_B2_READER, include_attributes=False):
        return False
    expected = {"_APPROVED_RESOURCE_NAME": "approved_components_v1.json", "_APPROVED_RESOURCE_SIZE": 13214}
    for name, value in expected.items():
        stores = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == name]
        constants = [n for n in tree.body if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name]
        if len(stores) != 1 or len(constants) != 1 or not isinstance(constants[0].value, ast.Constant) or type(constants[0].value.value) is not type(value) or constants[0].value.value != value:
            return False
    return not any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id in {"Path", "__file__"} for n in ast.walk(tree))


def v18_violations(index: Sources, modules: Iterable[str]) -> list[Violation]:
    forbidden = {"os", "pathlib", "subprocess", "socket", "httpx", "requests", "sqlite3"}
    result: list[Violation] = []
    for module in modules:
        tree = index.get(module)
        if tree is None:
            continue
        capability = _b2_capability(module, tree)
        result.extend(dynamic_violations(module, tree))
        for target, line in index.references(module):
            if target.split(".")[0] in forbidden and not (capability and target in {"pathlib", "pathlib.Path"}):
                result.append(Violation(module, line, "v18-external-resource", target))
            if any(token in target for token in ("supervisor", "knowledge_writer", "memory_automation", "reasoning_runtime")):
                result.append(Violation(module, line, "v18-legacy-forbidden", target))
        scopes = _scopes(tree)
        io_calls = {"open", "read_bytes", "read_text", "write_bytes", "write_text", "unlink", "mkdir", "rmdir", "rename", "replace", "glob", "rglob", "iterdir", "read", "write"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = dotted(node.func)
                scope, _ = scopes[node]
                if called.rsplit(".", 1)[-1] in io_calls and not (capability and scope == "_read_approved_resource" and called in {"path.open", "stream.read"}):
                    result.append(Violation(module, node.lineno, "v18-resource-call", ast.unparse(node.func)))
                if called == "Path" and not (capability and scope in {"_approved_resource_path", "_read_approved_resource"} and len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__"):
                    result.append(Violation(module, node.lineno, "v18-arbitrary-path", ast.unparse(node)))
    return result
