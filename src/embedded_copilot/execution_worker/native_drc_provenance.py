"""Offline, fail-closed KiCad DRC provenance v2 normalization and comparison.

This module never invokes a DRC tool or reads a Workspace. Its only byte source is
the caller's ArtifactStore.read-compatible callable.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import PurePosixPath

from .contracts import ArtifactIdentity, NativeMoveDrcPolicy, PcbDrcBaselineCollectorPolicy

NORMALIZER_ID = "KICAD_DRC_JSON_V1"
NORMALIZER_VERSION = "10.0.5-r1"
RAW_SCHEMA = "https://schemas.kicad.org/drc.v1.json"
RAW_VERSION = "10.0.5"
_RAW_FIELDS = {
    "$schema", "coordinate_units", "date", "ignored_checks", "included_severities",
    "kicad_version", "schematic_parity", "source", "unconnected_items", "violations",
}
_PROVENANCE_FIELDS = {
    "schema_version", "collector_id", "collector_version", "tool_id", "tool_version",
    "executable_sha256", "normalizer_id", "normalizer_version", "tool_bundle",
    "argv_template", "argv_slots", "environment", "input_pcb", "workspace_manifest",
    "rules_manifest", "dependency_manifest", "prl_relative_path", "prl_before",
    "prl_after", "raw_sha256", "scope", "policy",
}
_CONTEXT_FIELDS = {
    "schema_version", "raw_report", "provenance", "normalizer_id", "normalizer_version",
    "tool_id", "tool_version", "executable_sha256", "zone_fill_policy",
    "schematic_parity", "included_severities", "ignored_checks", "violations",
}
_BINDING_FIELDS = (
    "tenant_id", "project_id", "target_id", "workspace_fingerprint",
    "build_operation_id", "toolchain_id", "toolchain_version", "build_configuration",
)
_SEVERITY = {"warning": 1, "error": 2}
DRC_ARGV_TEMPLATE = ("pcb", "drc", "--format", "json", "--output", "{report}", "{pcb}")
DRC_ALLOWED_ENV_NAMES = frozenset({"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"})


def _fields(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} schema is unsupported")
    return value


def _pairs(items: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _json(data: bytes) -> dict:
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON number")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid UTF-8 JSON artifact") from exc
    if not isinstance(value, dict):
        raise TypeError("JSON artifact must be an object")
    return value


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha(value: object) -> str:
    if (not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in value[7:])):
        raise ValueError("invalid SHA-256 identity")
    return value


def _path(value: object) -> str:
    if (not isinstance(value, str) or not value or value.startswith("/") or "\\" in value
            or ":" in value or "\x00" in value or any(part in {"", ".", ".."} for part in value.split("/"))
            or str(PurePosixPath(value)) != value):
        raise ValueError("invalid relative artifact path")
    return value


def _identity(value: object, role: str) -> ArtifactIdentity:
    if isinstance(value, ArtifactIdentity):
        identity = value
    elif isinstance(value, dict):
        identity = ArtifactIdentity.model_validate(value)
    else:
        raise TypeError(f"{role} artifact identity missing")
    identity = ArtifactIdentity.model_validate(identity.model_dump(mode="json"))
    expected_type = "MANIFEST" if role in {"tool_bundle", "workspace_manifest", "rules_manifest", "dependency_manifest"} else "LOG"
    if identity.artifact_type != expected_type:
        raise ValueError(f"{role} artifact type mismatch")
    return identity


def _read(identity: ArtifactIdentity, read_artifact: Callable[[ArtifactIdentity], bytes]) -> bytes:
    data = read_artifact(identity)
    if not isinstance(data, bytes) or len(data) != identity.size_bytes or _digest(data) != identity.artifact_sha256:
        raise ValueError("artifact bytes differ from immutable identity")
    return data


def _bound(identity: ArtifactIdentity, anchor: ArtifactIdentity) -> None:
    if any(getattr(identity, field) != getattr(anchor, field) for field in _BINDING_FIELDS):
        raise ValueError("artifact owner, operation, tool, or Workspace scope mismatch")


def _manifest(identity: ArtifactIdentity, read_artifact: Callable[[ArtifactIdentity], bytes]) -> tuple[tuple, ...]:
    document = _fields(_json(_read(identity, read_artifact)), {"schema_version", "entries"}, "manifest")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1 or not isinstance(document["entries"], list):
        raise ValueError("manifest version or entries invalid")
    entries = []
    seen = set()
    for raw in document["entries"]:
        entry = _fields(raw, {"role", "relative_path", "sha256", "byte_size", "source"}, "manifest entry")
        role, path, source = entry["role"], _path(entry["relative_path"]), entry["source"]
        if not isinstance(role, str) or not role or not isinstance(source, str) or not source:
            raise ValueError("manifest role or source missing")
        if type(entry["byte_size"]) is not int or entry["byte_size"] < 0:
            raise ValueError("manifest byte size invalid")
        key = (role, path)
        if key in seen:
            raise ValueError("duplicate manifest entry")
        seen.add(key)
        entries.append((role, path, _sha(entry["sha256"]), entry["byte_size"], source))
    if not entries or entries != sorted(entries):
        raise ValueError("manifest must be nonempty and canonically ordered")
    return tuple(entries)


def _environment(identity: ArtifactIdentity, read_artifact: Callable[[ArtifactIdentity], bytes]) -> tuple[tuple[str, str], ...]:
    document = _fields(_json(_read(identity, read_artifact)), {"schema_version", "entries"}, "environment")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1 or not isinstance(document["entries"], list):
        raise ValueError("environment version or entries invalid")
    entries = []
    for raw in document["entries"]:
        entry = _fields(raw, {"name", "value"}, "environment entry")
        name, value = entry["name"], entry["value"]
        if name not in DRC_ALLOWED_ENV_NAMES or not isinstance(value, str) or "\x00" in value:
            raise ValueError("environment entry invalid")
        entries.append((name, value))
    if not entries or entries != sorted(entries) or len({name for name, _ in entries}) != len(entries):
        raise ValueError("environment must be allowlisted and canonically ordered")
    return tuple(entries)


def _normalize(raw_bytes: bytes, policy: NativeMoveDrcPolicy | PcbDrcBaselineCollectorPolicy, pcb_path: str) -> list[dict]:
    raw = _fields(_json(raw_bytes), _RAW_FIELDS, "KiCad DRC report")
    if (raw["$schema"] != RAW_SCHEMA or raw["kicad_version"] != RAW_VERSION
            or raw["coordinate_units"] != "mm" or not isinstance(raw["date"], str) or not raw["date"]
            or raw["source"] != PurePosixPath(pcb_path).name
            or raw["schematic_parity"] != [] or raw["unconnected_items"] != []
            or raw["included_severities"] != list(policy.included_severities)):
        raise ValueError("KiCad DRC schema, version or scope unsupported")
    ignored = raw["ignored_checks"]
    if not isinstance(ignored, list):
        raise TypeError("ignored checks invalid")
    keys = []
    for value in ignored:
        item = _fields(value, {"description", "key"}, "ignored check")
        if not isinstance(item["description"], str) or not isinstance(item["key"], str) or not item["key"]:
            raise ValueError("ignored check invalid")
        keys.append(item["key"])
    if tuple(keys) != policy.ignored_checks or not isinstance(raw["violations"], list):
        raise ValueError("KiCad DRC severity or ignored checks changed")
    result = []
    for value in raw["violations"]:
        violation = _fields(value, {"description", "items", "severity", "type"}, "violation")
        if (not isinstance(violation["type"], str) or not violation["type"]
                or not isinstance(violation["description"], str)
                or violation["severity"] not in _SEVERITY
                or not isinstance(violation["items"], list) or not violation["items"]):
            raise ValueError("violation fields invalid")
        items = []
        for value in violation["items"]:
            item = _fields(value, {"description", "pos", "uuid"}, "violation item")
            pos = _fields(item["pos"], {"x", "y"}, "violation position")
            if not isinstance(item["description"], str):
                raise TypeError("item description invalid")
            try:
                canonical_uuid = str(uuid.UUID(item["uuid"]))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError("item UUID unavailable") from exc
            if canonical_uuid != item["uuid"]:
                raise ValueError("item UUID is not canonical")
            if any(type(pos[axis]) not in {int, float} or not math.isfinite(pos[axis]) for axis in ("x", "y")):
                raise ValueError("item position invalid")
            items.append({"uuid": canonical_uuid, "description": item["description"], "pos": {"x": pos["x"], "y": pos["y"]}})
        uuids = [item["uuid"] for item in items]
        if len(set(uuids)) != len(uuids):
            raise ValueError("repeated item UUID in one violation")
        result.append({
            "type": violation["type"], "item_uuids": sorted(uuids),
            "severity": violation["severity"], "description": violation["description"],
            "items": sorted(items, key=lambda item: item["uuid"]),
        })
    return result


def _load(
    raw_report: ArtifactIdentity,
    provenance: ArtifactIdentity,
    read_artifact: Callable[[ArtifactIdentity], bytes],
    policy: NativeMoveDrcPolicy | PcbDrcBaselineCollectorPolicy,
) -> tuple[dict, tuple]:
    if not callable(read_artifact) or not isinstance(policy, (NativeMoveDrcPolicy, PcbDrcBaselineCollectorPolicy)):
        raise TypeError("DRC artifact reader or policy invalid")
    _bound(provenance, raw_report)
    if (raw_report.toolchain_id, raw_report.toolchain_version) != (policy.tool_id, policy.tool_version):
        raise ValueError("DRC tool identity mismatch")
    raw_bytes = _read(raw_report, read_artifact)
    document = _fields(_json(_read(provenance, read_artifact)), _PROVENANCE_FIELDS, "DRC provenance")
    if (type(document["schema_version"]) is not int or document["schema_version"] != 2
            or not isinstance(document["collector_id"], str) or not document["collector_id"]
            or not isinstance(document["collector_version"], str) or not document["collector_version"]
            or document["tool_id"] != policy.tool_id or document["tool_version"] != policy.tool_version
            or document["executable_sha256"] != policy.executable_sha256
            or document["normalizer_id"] != NORMALIZER_ID
            or document["normalizer_version"] != NORMALIZER_VERSION
            or document["raw_sha256"] != raw_report.artifact_sha256
            or document["scope"] != policy.comparison_scope):
        raise ValueError("DRC collector, tool, normalizer or raw identity mismatch")
    expected_policy = {
        "zone_fill_policy": policy.zone_fill_policy,
        "schematic_parity": policy.schematic_parity,
        "included_severities": list(policy.included_severities),
        "ignored_checks": list(policy.ignored_checks),
    }
    if _fields(document["policy"], set(expected_policy), "DRC policy") != expected_policy:
        raise ValueError("DRC policy evidence mismatch")
    refs = {}
    for role in ("tool_bundle", "environment", "input_pcb", "workspace_manifest", "rules_manifest", "dependency_manifest"):
        identity = _identity(document[role], role)
        _bound(identity, raw_report)
        refs[role] = identity
    prl = {}
    for role in ("prl_before", "prl_after"):
        value = document[role]
        if value is None:
            prl[role] = None
        else:
            identity = _identity(value, role)
            _bound(identity, raw_report)
            prl[role] = identity
    bundle = _manifest(refs["tool_bundle"], read_artifact)
    executables = [entry for entry in bundle if entry[0] == "EXECUTABLE"]
    if len(executables) != 1 or executables[0][2] != policy.executable_sha256:
        raise ValueError("tool bundle does not bind reviewed executable")
    rules = _manifest(refs["rules_manifest"], read_artifact)
    dependencies = _manifest(refs["dependency_manifest"], read_artifact)
    workspace = _manifest(refs["workspace_manifest"], read_artifact)
    environment = _environment(refs["environment"], read_artifact)
    pcb_bytes = _read(refs["input_pcb"], read_artifact)
    if not pcb_bytes:
        raise ValueError("input PCB artifact is empty")
    prl_bytes = {role: None if identity is None else _read(identity, read_artifact) for role, identity in prl.items()}
    slots = _fields(document["argv_slots"], {"pcb", "report"}, "DRC argv slots")
    pcb_slot = _fields(slots["pcb"], {"relative_path", "artifact"}, "PCB argv slot")
    report_slot = _fields(slots["report"], {"role", "relative_path", "artifact"}, "report argv slot")
    target = _path(pcb_slot["relative_path"])
    report_path = _path(report_slot["relative_path"])
    if (_identity(pcb_slot["artifact"], "input_pcb") != refs["input_pcb"]
            or report_slot["role"] != "DRC_REPORT"
            or report_path == target
            or _identity(report_slot["artifact"], "raw_report") != raw_report):
        raise ValueError("DRC argv path slots are not bound to input and output artifacts")
    argv = document["argv_template"]
    if not isinstance(argv, list) or tuple(argv) != DRC_ARGV_TEMPLATE:
        raise ValueError("DRC argv template is unsupported")
    targets = [entry for entry in workspace if entry[1] == target]
    if (len(targets) != 1 or targets[0][0] != "FILE"
            or targets[0][2:4] != (refs["input_pcb"].artifact_sha256, refs["input_pcb"].size_bytes)
            or len({entry[1] for entry in workspace}) != len(workspace)
            or report_path in {entry[1] for entry in workspace}):
        raise ValueError("Workspace manifest does not bind input PCB")
    prl_path = _path(document["prl_relative_path"])
    if not prl_path.endswith(".kicad_prl") or prl_path == target:
        raise ValueError("DRC PRL path invalid")
    current_prl = [entry for entry in workspace if entry[1] == prl_path]
    if (len(current_prl) > 1 or (prl["prl_after"] is None) != (not current_prl)
            or (current_prl and current_prl[0][2:4] != (prl["prl_after"].artifact_sha256, prl["prl_after"].size_bytes))):
        raise ValueError("Workspace manifest does not bind PRL after state")
    if isinstance(policy, PcbDrcBaselineCollectorPolicy):
        if (document["collector_id"], document["collector_version"]) != (policy.collector_id, policy.collector_version):
            raise ValueError("baseline collector identity mismatch")
        for role, expected in (
            ("tool_bundle", policy.tool_bundle_manifest_fingerprint),
            ("environment", policy.environment_content_fingerprint),
            ("rules_manifest", policy.rules_manifest_fingerprint),
            ("dependency_manifest", policy.dependency_manifest_fingerprint),
        ):
            if _digest(_read(refs[role], read_artifact)) != expected:
                raise ValueError("baseline frozen closure content mismatch")
        if prl_bytes["prl_before"] != prl_bytes["prl_after"]:
            raise ValueError("baseline PRL observation changed")
    violations = _normalize(raw_bytes, policy, target)
    context = {
        "schema_version": 2,
        "raw_report": raw_report.model_dump(mode="json"),
        "provenance": provenance.model_dump(mode="json"),
        "normalizer_id": NORMALIZER_ID,
        "normalizer_version": NORMALIZER_VERSION,
        "tool_id": policy.tool_id,
        "tool_version": policy.tool_version,
        "executable_sha256": policy.executable_sha256,
        **expected_policy,
        "violations": violations,
    }
    # An artifact proves these claimed entries were re-read, not that the collector
    # covered the actual Workspace. The post-APPLY seam must bind a canonical snapshot.
    comparison_basis = (
        raw_report.tenant_id, raw_report.project_id, raw_report.target_id,
        document["collector_id"], document["collector_version"],
        policy.tool_id, policy.tool_version, policy.executable_sha256,
        NORMALIZER_ID, NORMALIZER_VERSION, RAW_SCHEMA, RAW_VERSION,
        policy.comparison_scope, tuple(argv), target, prl_path,
        tuple(policy.included_severities), tuple(policy.ignored_checks),
        policy.zone_fill_policy, policy.schematic_parity,
        bundle, environment, rules, dependencies, targets[0][4],
        tuple(entry for entry in workspace if entry[1] != target),
        tuple(None if prl_bytes[role] is None else (_digest(prl_bytes[role]), len(prl_bytes[role])) for role in ("prl_before", "prl_after")),
    )
    return context, comparison_basis


def build_drc_v2_context(
    raw_report: ArtifactIdentity,
    provenance: ArtifactIdentity,
    *,
    read_artifact: Callable[[ArtifactIdentity], bytes],
    policy: NativeMoveDrcPolicy | PcbDrcBaselineCollectorPolicy,
) -> dict:
    """Build an internal v2 envelope only from re-read, bound immutable artifacts."""
    return _load(_identity(raw_report, "raw_report"), _identity(provenance, "provenance"), read_artifact, policy)[0]


def validate_drc_v2_context(
    context: object,
    *,
    read_artifact: Callable[[ArtifactIdentity], bytes],
    policy: NativeMoveDrcPolicy | PcbDrcBaselineCollectorPolicy,
) -> dict:
    """Recompute every normalized field; reject legacy or forged v2 envelopes."""
    value = _fields(context, _CONTEXT_FIELDS, "DRC v2 context")
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError("DRC legacy or unknown schema cannot be v2 evidence")
    expected = build_drc_v2_context(
        _identity(value["raw_report"], "raw_report"),
        _identity(value["provenance"], "provenance"),
        read_artifact=read_artifact, policy=policy,
    )
    if value != expected:
        raise ValueError("DRC normalization differs from raw evidence")
    return expected


def compare_drc_v2_contexts(
    before: object,
    after: object,
    *,
    read_artifact: Callable[[ArtifactIdentity], bytes],
    policy: NativeMoveDrcPolicy | PcbDrcBaselineCollectorPolicy,
) -> dict:
    """Return a limited comparison; never infer whole-board or parity verification."""
    unknown = {"status": "COMPARABILITY_UNKNOWN", "new_key_count": None, "severity_increase_count": None,
               "error_count": None, "warning_count": None}
    try:
        old = validate_drc_v2_context(before, read_artifact=read_artifact, policy=policy)
        new = validate_drc_v2_context(after, read_artifact=read_artifact, policy=policy)
        old_basis = _load(_identity(old["raw_report"], "raw_report"), _identity(old["provenance"], "provenance"), read_artifact, policy)[1]
        new_basis = _load(_identity(new["raw_report"], "raw_report"), _identity(new["provenance"], "provenance"), read_artifact, policy)[1]
        if (old_basis != new_basis or old_basis[-1][0] != old_basis[-1][1]
                or new_basis[-1][0] != new_basis[-1][1]):
            return unknown
        old_groups: dict[tuple, list[dict]] = defaultdict(list)
        new_groups: dict[tuple, list[dict]] = defaultdict(list)
        for violation in old["violations"]:
            old_groups[(violation["type"], tuple(violation["item_uuids"]))].append(violation)
        for violation in new["violations"]:
            new_groups[(violation["type"], tuple(violation["item_uuids"]))].append(violation)
        added = sum(max(0, len(items) - len(old_groups[key])) for key, items in new_groups.items())
        elevated = sum(
            max(0, sum(item["severity"] == "error" for item in items)
                - sum(item["severity"] == "error" for item in old_groups[key]))
            for key, items in new_groups.items() if key in old_groups
        )
        result = {
            "status": "FAIL" if added or elevated else "NO_NEW_KEYS",
            "new_key_count": added, "severity_increase_count": elevated,
            "error_count": sum(item["severity"] == "error" for item in new["violations"]),
            "warning_count": sum(item["severity"] == "warning" for item in new["violations"]),
        }
        if added or elevated:
            return result
        if set(old_groups) != set(new_groups):
            return unknown
        for key, items in old_groups.items():
            observed = new_groups[key]
            if len(items) != len(observed):
                return unknown
            canonical = lambda issue: json.dumps(issue, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if Counter(map(canonical, items)) != Counter(map(canonical, observed)):
                return unknown
        return result
    except Exception:  # noqa: BLE001 - missing or changed evidence fails closed
        return unknown


__all__ = (
    "DRC_ALLOWED_ENV_NAMES", "DRC_ARGV_TEMPLATE", "NORMALIZER_ID", "NORMALIZER_VERSION",
    "build_drc_v2_context", "compare_drc_v2_contexts", "validate_drc_v2_context",
)
