"""Pure synthetic original-byte normalization; no actual Workspace or CLI evidence."""
import pytest
from embedded_copilot.execution_worker.contracts import PcbDrcBaselineCollectorPolicy
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.native_drc_provenance import build_drc_v2_context,validate_drc_v2_context
from tests.execution_worker.test_native_drc_provenance_v2 import Evidence,encoded,sha

def _fixture(tmp_path):
    # Reuse the existing immutable-artifact builder with the new independent policy.
    # No NativeMoveDrcPolicy or manufactured baseline_report is constructed.
    e=Evidence.__new__(Evidence);e.store=ArtifactStore(tmp_path/"artifacts");e.tenant_id="tenant-1";e.project_id="project-1";e.target_id="project-root"
    entry=lambda role,path,blob:{"role":role,"relative_path":path,"sha256":sha(blob),"byte_size":len(blob),"source":"trusted-snapshot"}
    manifest=lambda entries:encoded({"schema_version":1,"entries":entries})
    e.policy=PcbDrcBaselineCollectorPolicy(collector_id="KICAD_DRC_COLLECTOR",collector_version="1",tool_id="kicad-cli",tool_version="10.0.5",executable_sha256=sha(b"executable"),timeout_seconds=30,tool_bundle_manifest_fingerprint=sha(manifest([entry("EXECUTABLE","bin/kicad-cli",b"executable"),entry("NATIVE_DEPENDENCY","lib/tool",b"tool")])),rules_manifest_fingerprint=sha(manifest([entry("RULE","project/rules",b"rules")])),dependency_manifest_fingerprint=sha(manifest([entry("DEPENDENCY","lib/footprints",b"library")])),environment_content_fingerprint=sha(encoded({"schema_version":1,"entries":[{"name":"PATH","value":"fixed"}]})))
    return e

def test_independent_input_policy_normalizes_without_a_write_or_baseline_output(tmp_path):
    e=_fixture(tmp_path);context,raw,provenance=e.capture("baseline-op")
    assert validate_drc_v2_context(context,read_artifact=e.store.read,policy=e.policy)==context
    assert len(context["violations"])==1
    assert "baseline_report" not in e.policy.model_dump()

@pytest.mark.parametrize("field",("tool_bundle_manifest_fingerprint","rules_manifest_fingerprint","dependency_manifest_fingerprint","environment_content_fingerprint","collector_id","collector_version"))
def test_independent_policy_rejects_changed_frozen_closure_or_collector(tmp_path,field):
    e=_fixture(tmp_path);context,raw,provenance=e.capture("baseline-op")
    data=e.policy.model_dump(mode="python",exclude={"fingerprint"});data[field]="different" if field.startswith("collector") else sha(b"changed")
    with pytest.raises((ValueError,TypeError)):build_drc_v2_context(raw,provenance,read_artifact=e.store.read,policy=PcbDrcBaselineCollectorPolicy(**data))

def test_baseline_requires_unchanged_actual_prl_artifacts(tmp_path):
    e=_fixture(tmp_path)
    with pytest.raises(ValueError):e.capture("baseline-op",provenance_update=lambda data:data.update(prl_before=None))
