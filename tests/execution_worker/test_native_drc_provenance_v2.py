"""Offline DRC v2 evidence contract; no EDA executable or Workspace access."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    NativeMoveDrcPolicy,
)
from embedded_copilot.execution_worker.native_drc_provenance import (
    NORMALIZER_ID,
    NORMALIZER_VERSION,
    _normalize,
    build_drc_v2_context,
    compare_drc_v2_contexts,
    validate_drc_v2_context,
)

NOW = datetime(2026, 10, 7, tzinfo=UTC)
UUID_A = "00000000-0000-0000-0000-000000000001"
UUID_B = "00000000-0000-0000-0000-000000000002"


def sha(blob: bytes) -> str:
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def issue(*, severity: str = "warning", description: str = "现有问题", x: float = 1.0, uuids: tuple[str, ...] = (UUID_A, UUID_B)) -> dict:
    return {
        "description": description,
        "items": [{"description": f"item {index}", "pos": {"x": x + index, "y": 2.0}, "uuid": native_uuid}
                  for index, native_uuid in enumerate(uuids)],
        "severity": severity,
        "type": "clearance",
    }


class Evidence:
    def __init__(self, tmp_path, *, tenant_id="tenant-1", project_id="project-1", target_id="project-root"):
        self.store = ArtifactStore(tmp_path / "artifacts")
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.target_id = target_id
        self.policy = NativeMoveDrcPolicy(
            tool_id="kicad-cli", tool_version="10.0.5", executable_sha256=sha(b"executable"),
            timeout_seconds=30, ignored_checks=(),
            baseline_report=self.identity(b"legacy placeholder", "baseline", "baseline-op", "LOG", sha(b"before")),
        )

    def identity(self, blob: bytes, name: str, operation: str, kind: str, workspace_fp: str) -> ArtifactIdentity:
        return ArtifactIdentity(
            artifact_id=f"{operation}-{name}", tenant_id=self.tenant_id, project_id=self.project_id,
            target_id=self.target_id, workspace_fingerprint=workspace_fp,
            build_operation_id=operation, toolchain_id="kicad-cli", toolchain_version="10.0.5",
            build_configuration="offline", artifact_type=kind, artifact_sha256=sha(blob),
            size_bytes=len(blob), created_at=NOW,
        )

    def put(self, blob: bytes, name: str, operation: str, kind: str, workspace_fp: str) -> ArtifactIdentity:
        identity = self.identity(blob, name, operation, kind, workspace_fp)
        self.store.put(identity, blob)
        return identity

    def capture(self, operation: str, *, board: bytes = b"board", violations: list[dict] | None = None,
                bundle: bytes = b"tool", environment: str = "fixed", rules: bytes = b"rules",
                dependencies: bytes = b"library", other: bytes = b"unchanged", prl: bytes = b"prl",
                environment_name: str = "PATH",
                raw_update=None, provenance_update=None) -> tuple[dict, ArtifactIdentity, ArtifactIdentity]:
        workspace_fp = sha(board)
        put = lambda blob, name, kind: self.put(blob, name, operation, kind, workspace_fp)
        manifest = lambda entries: encoded({"schema_version": 1, "entries": entries})
        entry = lambda role, path, blob: {"role": role, "relative_path": path, "sha256": sha(blob),
                                           "byte_size": len(blob), "source": "trusted-snapshot"}
        bundle_ref = put(manifest([
            entry("EXECUTABLE", "bin/kicad-cli", b"executable"),
            entry("NATIVE_DEPENDENCY", "lib/tool", bundle),
        ]), "bundle", "MANIFEST")
        rules_ref = put(manifest([entry("RULE", "project/rules", rules)]), "rules", "MANIFEST")
        deps_ref = put(manifest([entry("DEPENDENCY", "lib/footprints", dependencies)]), "dependencies", "MANIFEST")
        env_ref = put(encoded({"schema_version": 1, "entries": [{"name": environment_name, "value": environment}]}), "env", "LOG")
        pcb_ref = put(board, "pcb", "LOG")
        prl_ref = put(prl, "prl", "LOG")
        workspace_ref = put(manifest([
            entry("FILE", "native/board.kicad_pcb", board),
            entry("FILE", "native/board.kicad_prl", prl),
            entry("FILE", "notes.txt", other),
        ]), "workspace", "MANIFEST")
        raw = {
            "$schema": "https://schemas.kicad.org/drc.v1.json", "coordinate_units": "mm",
            "date": "2026-10-07T00:00:00", "ignored_checks": [],
            "included_severities": ["error", "warning"], "kicad_version": "10.0.5",
            "schematic_parity": [], "source": "board.kicad_pcb", "unconnected_items": [],
            "violations": [issue()] if violations is None else violations,
        }
        if raw_update is not None:
            raw_update(raw)
        raw_ref = put(encoded(raw), "raw", "LOG")
        provenance = {
            "schema_version": 2, "collector_id": "KICAD_DRC_COLLECTOR", "collector_version": "1",
            "tool_id": self.policy.tool_id, "tool_version": self.policy.tool_version,
            "executable_sha256": self.policy.executable_sha256,
            "normalizer_id": NORMALIZER_ID, "normalizer_version": NORMALIZER_VERSION,
            "tool_bundle": bundle_ref.model_dump(mode="json"),
            "argv_template": ["pcb", "drc", "--format", "json", "--output", "{report}", "{pcb}"],
            "argv_slots": {
                "pcb": {"relative_path": "native/board.kicad_pcb", "artifact": pcb_ref.model_dump(mode="json")},
                "report": {"role": "DRC_REPORT", "relative_path": f"evidence/{operation}.json",
                           "artifact": raw_ref.model_dump(mode="json")},
            },
            "environment": env_ref.model_dump(mode="json"), "input_pcb": pcb_ref.model_dump(mode="json"),
            "workspace_manifest": workspace_ref.model_dump(mode="json"),
            "rules_manifest": rules_ref.model_dump(mode="json"),
            "dependency_manifest": deps_ref.model_dump(mode="json"),
            "prl_relative_path": "native/board.kicad_prl",
            "prl_before": prl_ref.model_dump(mode="json"), "prl_after": prl_ref.model_dump(mode="json"),
            "raw_sha256": raw_ref.artifact_sha256, "scope": "BASELINE_COMPARISON_ONLY",
            "policy": {"zone_fill_policy": "NO_REFILL", "schematic_parity": "NOT_RUN",
                       "included_severities": ["error", "warning"], "ignored_checks": []},
        }
        if provenance_update is not None:
            provenance_update(provenance)
        provenance_ref = put(encoded(provenance), "provenance", "LOG")
        context = build_drc_v2_context(raw_ref, provenance_ref, read_artifact=self.store.read, policy=self.policy)
        return context, raw_ref, provenance_ref


def test_reloads_all_evidence_and_compares_distinct_operations(tmp_path):
    evidence = Evidence(tmp_path)
    before, _, _ = evidence.capture("baseline", board=b"old board")
    after, _, _ = evidence.capture("after", board=b"moved board")
    assert validate_drc_v2_context(before, read_artifact=evidence.store.read, policy=evidence.policy) == before
    assert compare_drc_v2_contexts(before, after, read_artifact=evidence.store.read, policy=evidence.policy) == {
        "status": "NO_NEW_KEYS", "new_key_count": 0, "severity_increase_count": 0,
        "error_count": 0, "warning_count": 1,
    }


@pytest.mark.parametrize("field,value", [
    ("bundle", b"different tool"), ("environment", "different"), ("rules", b"different rules"),
    ("dependencies", b"different library"), ("other", b"changed note"), ("prl", b"changed prl"),
])
def test_comparison_rejects_context_content_drift(tmp_path, field, value):
    evidence = Evidence(tmp_path)
    before, _, _ = evidence.capture("baseline")
    after, _, _ = evidence.capture("after", board=b"moved board", **{field: value})
    assert compare_drc_v2_contexts(before, after, read_artifact=evidence.store.read, policy=evidence.policy)["status"] == "COMPARABILITY_UNKNOWN"


@pytest.mark.parametrize("change", [
    lambda raw: raw.update({"new_unknown_field": 1}),
    lambda raw: raw.update({"kicad_version": "10.0.6"}),
    lambda raw: raw["violations"][0]["items"][0].pop("uuid"),
    lambda raw: raw["violations"][0]["items"][1].update({"uuid": UUID_A}),
    lambda raw: raw["violations"][0]["items"][0].update({"unexpected": 1}),
])
def test_raw_unknown_version_or_uuid_fails_closed(tmp_path, change):
    evidence = Evidence(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        evidence.capture("bad", raw_update=change)


def test_raw_and_provenance_byte_drift_and_forged_normalization(tmp_path):
    evidence = Evidence(tmp_path)
    context, raw_ref, provenance_ref = evidence.capture("baseline")
    for artifact in (raw_ref, provenance_ref):
        def corrupt(identity, target_id=artifact.artifact_id):
            return b"changed" if identity.artifact_id == target_id else evidence.store.read(identity)
        with pytest.raises(ValueError):
            validate_drc_v2_context(context, read_artifact=corrupt, policy=evidence.policy)
    forged = {**context, "violations": []}
    with pytest.raises(ValueError):
        validate_drc_v2_context(forged, read_artifact=evidence.store.read, policy=evidence.policy)
    assert compare_drc_v2_contexts(context, forged, read_artifact=evidence.store.read, policy=evidence.policy)["status"] == "COMPARABILITY_UNKNOWN"


@pytest.mark.parametrize("role", [
    "tool_bundle", "environment", "input_pcb", "workspace_manifest",
    "rules_manifest", "dependency_manifest", "prl_before", "prl_after",
])
def test_every_referenced_artifact_is_reread(tmp_path, role):
    evidence = Evidence(tmp_path)
    context, _, provenance_ref = evidence.capture("baseline")
    provenance = json.loads(evidence.store.read(provenance_ref))
    target_id = provenance[role]["artifact_id"]

    def corrupt(identity):
        return b"drift" if identity.artifact_id == target_id else evidence.store.read(identity)

    with pytest.raises(ValueError):
        validate_drc_v2_context(context, read_artifact=corrupt, policy=evidence.policy)
    assert compare_drc_v2_contexts(context, context, read_artifact=corrupt, policy=evidence.policy)["status"] == "COMPARABILITY_UNKNOWN"


def test_duplicate_key_multiset_and_severity_escalation(tmp_path):
    evidence = Evidence(tmp_path)
    before, _, _ = evidence.capture("baseline", violations=[issue(), issue()])
    same, _, _ = evidence.capture("same", board=b"moved", violations=[issue(), issue()])
    added, _, _ = evidence.capture("added", board=b"moved", violations=[issue(), issue(), issue()])
    elevated, _, _ = evidence.capture("elevated", board=b"moved", violations=[issue(), issue(severity="error")])
    assert compare_drc_v2_contexts(before, same, read_artifact=evidence.store.read, policy=evidence.policy)["status"] == "NO_NEW_KEYS"
    assert compare_drc_v2_contexts(before, added, read_artifact=evidence.store.read, policy=evidence.policy)["new_key_count"] == 1
    result = compare_drc_v2_contexts(before, elevated, read_artifact=evidence.store.read, policy=evidence.policy)
    assert result["status"] == "FAIL" and result["severity_increase_count"] == 1


@pytest.mark.parametrize("violations", [
    [issue(description="不同文本")], [issue(x=10.0)], [], [issue(severity="warning")],
])
def test_unexplained_same_key_or_removed_issue_is_unknown(tmp_path, violations):
    evidence = Evidence(tmp_path)
    before, _, _ = evidence.capture("baseline", violations=[issue(severity="error")])
    after, _, _ = evidence.capture("after", board=b"moved", violations=violations)
    assert compare_drc_v2_contexts(before, after, read_artifact=evidence.store.read, policy=evidence.policy)["status"] == "COMPARABILITY_UNKNOWN"


def test_provenance_and_legacy_fail_closed(tmp_path):
    evidence = Evidence(tmp_path)
    with pytest.raises(ValueError):
        evidence.capture("bad", provenance_update=lambda p: p.update({"raw_sha256": sha(b"forged")}))
    with pytest.raises(ValueError):
        evidence.capture("wrong-owner", provenance_update=lambda p: p["rules_manifest"].update({"tenant_id": "other"}))
    context, _, _ = evidence.capture("valid")
    legacy = {**context, "schema_version": 1}
    with pytest.raises(ValueError):
        validate_drc_v2_context(legacy, read_artifact=evidence.store.read, policy=evidence.policy)
    assert compare_drc_v2_contexts(legacy, context, read_artifact=evidence.store.read, policy=evidence.policy)["status"] == "COMPARABILITY_UNKNOWN"


@pytest.mark.parametrize("other_scope", [
    {"tenant_id": "other-tenant"}, {"project_id": "other-project"}, {"target_id": "other-target"},
])
def test_cross_scope_contexts_cannot_compare_even_when_internally_valid(tmp_path, other_scope):
    first = Evidence(tmp_path / "first")
    second = Evidence(tmp_path / "second", **other_scope)
    before, _, _ = first.capture("baseline")
    after, _, _ = second.capture("after", board=b"moved board")
    def read(identity):
        return (first.store if identity.build_operation_id == "baseline" else second.store).read(identity)
    assert compare_drc_v2_contexts(before, after, read_artifact=read, policy=first.policy)["status"] == "COMPARABILITY_UNKNOWN"


@pytest.mark.parametrize("extra_flag", ["--unknown-reviewed-flag", "--refill-zones=true"])
def test_argv_template_does_not_accept_extra_flags(tmp_path, extra_flag):
    evidence = Evidence(tmp_path)
    with pytest.raises(ValueError):
        evidence.capture("bad", provenance_update=lambda p: p["argv_template"].insert(2, extra_flag))


def test_environment_name_must_be_in_fixed_allowlist(tmp_path):
    evidence = Evidence(tmp_path)
    with pytest.raises(ValueError):
        evidence.capture("bad", environment_name="ARBITRARY_NOT_ALLOWLISTED")


def test_real_kicad_raw_shape_is_normalized_without_promoting_historical_provenance(tmp_path):
    sample = Path(__file__).parents[2] / "acceptance" / "s06-third-party" / "kicad-pic-programmer-8.0.9" / "evidence" / "pcb-drc-10.0.5.json"
    if not sample.is_file():
        pytest.skip("historical raw fixture unavailable")
    evidence = Evidence(tmp_path)
    policy = NativeMoveDrcPolicy(
        tool_id="kicad-cli", tool_version="10.0.5", executable_sha256=sha(b"executable"),
        timeout_seconds=30, baseline_report=evidence.policy.baseline_report,
        ignored_checks=("track_not_centered_on_via", "tuning_profile_track_geometries",
                        "footprint_filters_mismatch", "silk_edge_clearance"),
    )
    issues = _normalize(sample.read_bytes(), policy, "pic_programmer.kicad_pcb")
    assert len(issues) == 58
    assert sum(item["severity"] == "error" for item in issues) == 4
    assert all(item["item_uuids"] == sorted(item["item_uuids"]) for item in issues)
