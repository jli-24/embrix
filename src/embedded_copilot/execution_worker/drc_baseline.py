"""Independent C1 DRC observation through the existing fixed process boundary.

No collector is installed by default. A trusted composition must freeze actual
closure files, environment, Workspace and policy before an operation is created.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService
from .adapters import SafeProcessRunner
from .contracts import ArtifactIdentity, PcbDrcBaselineCollectorPolicy, PcbDrcBaselineOperation
from .native_drc_provenance import DRC_ALLOWED_ENV_NAMES, DRC_ARGV_TEMPLATE, build_drc_v2_context
from .ports import AdapterResult


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class BaselineClosureFile:
    role: str
    relative_path: str
    absolute_path: Path
    sha256: str
    byte_size: int

    def read(self) -> bytes:
        path = self.absolute_path
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("baseline closure path unavailable")
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("baseline closure reparse path")
        value = path.read_bytes()
        if len(value) != self.byte_size or _sha(value) != self.sha256:
            raise ValueError("baseline closure bytes changed")
        return value

    def entry(self) -> dict:
        self.read()
        if (not self.role or not self.relative_path or "\\" in self.relative_path or ":" in self.relative_path
            or any(part in {"", ".", ".."} for part in self.relative_path.split("/"))):
            raise ValueError("baseline closure manifest path unavailable")
        return {"role": self.role, "relative_path": self.relative_path, "sha256": self.sha256,
                "byte_size": self.byte_size, "source": "reviewed-server-closure"}


class PcbDrcBaselineCollector:
    def __init__(self, *, workspace: WorkspaceApplyService, policy: PcbDrcBaselineCollectorPolicy,
                 tool_bundle: tuple[BaselineClosureFile, ...], rules: tuple[BaselineClosureFile, ...],
                 dependencies: tuple[BaselineClosureFile, ...], environment: tuple[tuple[str, str], ...],
                 report_root: Path, runner=None, clock: Callable[[], datetime] | None = None) -> None:
        if not isinstance(workspace, WorkspaceApplyService):
            raise TypeError("canonical baseline Workspace is required")
        self.workspace = workspace
        self.policy = PcbDrcBaselineCollectorPolicy.model_validate(policy.model_dump(mode="python"))
        self._bundle, self._rules, self._dependencies = tool_bundle, rules, dependencies
        self._environment = environment
        self._report_root = Path(report_root)
        self._runner = runner if runner is not None else SafeProcessRunner()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._policy_inputs()

    @staticmethod
    def _manifest(files: tuple[BaselineClosureFile, ...]) -> bytes:
        if type(files) is not tuple or any(not isinstance(item, BaselineClosureFile) for item in files):
            raise ValueError("baseline closure manifest unavailable")
        entries = sorted((item.entry() for item in files), key=lambda item: (item["role"], item["relative_path"]))
        if len({item["relative_path"] for item in entries}) != len(entries):
            raise ValueError("baseline closure contains duplicate paths")
        return _encoded({"schema_version": 1, "entries": entries})

    def _policy_inputs(self) -> dict[str, bytes]:
        if (type(self._environment) is not tuple or not self._environment
            or self._environment != tuple(sorted(self._environment))
            or len({name for name, _ in self._environment}) != len(self._environment)
            or any(name not in DRC_ALLOWED_ENV_NAMES or type(value) is not str or "\x00" in value for name,value in self._environment)):
            raise ValueError("baseline frozen environment unavailable")
        executable = [item for item in self._bundle if item.role == "EXECUTABLE"]
        if (len(executable) != 1 or executable[0].sha256 != self.policy.executable_sha256
            or executable[0].absolute_path.name.casefold() not in {"kicad-cli", "kicad-cli.exe"}
            or self.policy.tool_version != self.policy.raw_version):
            raise ValueError("baseline fixed CLI identity unavailable")
        values = {"tool_bundle": self._manifest(self._bundle), "rules_manifest": self._manifest(self._rules),
                  "dependency_manifest": self._manifest(self._dependencies),
                  "environment": _encoded({"schema_version": 1, "entries": [{"name":name,"value":value} for name,value in self._environment]})}
        for role,expected in (("tool_bundle",self.policy.tool_bundle_manifest_fingerprint),
                ("rules_manifest",self.policy.rules_manifest_fingerprint),
                ("dependency_manifest",self.policy.dependency_manifest_fingerprint),
                ("environment",self.policy.environment_content_fingerprint)):
            if _sha(values[role]) != expected:
                raise ValueError("baseline frozen policy contents changed")
        return values

    def available(self) -> bool:
        try:
            self._policy_inputs()
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False

    def target_current(self, operation: PcbDrcBaselineOperation) -> bool:
        snapshot = self.workspace.snapshot(tenant_id=operation.tenant_id,project_id=operation.project_id,target_id=operation.target_id)
        prl = next((item for item in snapshot.files if item.relative_file_reference == operation.prl_relative_path),None)
        pcb = next((item for item in snapshot.files if item.relative_file_reference == operation.pcb_relative_path),None)
        return ((snapshot.project_fingerprint,snapshot.target_fingerprint) == (operation.workspace_fingerprint,operation.target_fingerprint)
            and (None if prl is None else prl.content_fingerprint) == operation.prl_before_sha256
            and pcb is not None and pcb.content_fingerprint == operation.pcb_sha256)

    def execute(self, operation: PcbDrcBaselineOperation, *, deadline: datetime, cancellation: object,
                revalidate: Callable[[], bool]) -> AdapterResult:
        pairs: list[tuple[ArtifactIdentity, bytes]] = []
        report: Path | None = None
        prl_path: Path | None = None
        phase_error: Exception | None = None
        def put(role: str, content: bytes, kind: str="LOG") -> ArtifactIdentity:
            identity=ArtifactIdentity(artifact_id=operation.operation_id+"-"+role,tenant_id=operation.tenant_id,
                project_id=operation.project_id,target_id=operation.target_id,workspace_fingerprint=operation.workspace_fingerprint,
                build_operation_id=operation.operation_id,toolchain_id=operation.collector_policy.tool_id,
                toolchain_version=operation.collector_policy.tool_version,build_configuration="DRC_BASELINE_OBSERVATION",
                artifact_type=kind,artifact_sha256=_sha(content),size_bytes=len(content),created_at=self._clock())
            pairs.append((identity,content));return identity
        def authority() -> None:
            def stop() -> None:
                if (deadline - self._clock()).total_seconds() < 1:
                    raise TimeoutError("baseline deadline exhausted")
                if getattr(cancellation,"is_cancelled",lambda:False)():
                    raise InterruptedError("baseline cancelled")
            stop()
            if operation.collector_policy != self.policy or revalidate() is not True:
                raise ValueError("baseline canonical input changed")
            stop()
        def require() -> None:
            if not self.target_current(operation):
                raise ValueError("baseline Workspace changed")
            # Workspace scanning is slow. Authority must be checked after it.
            authority()
        def preserve() -> None:
            existing={identity.artifact_id for identity,_ in pairs}
            def once(role: str, content: bytes, kind: str="LOG") -> None:
                if operation.operation_id+"-"+role not in existing:
                    put(role,content,kind)
            if report is not None:
                try:
                    if report.is_file() and not report.is_symlink():once("raw-report",report.read_bytes())
                except (OSError,ValueError):pass
            try:
                after=self.workspace.snapshot(tenant_id=operation.tenant_id,project_id=operation.project_id,target_id=operation.target_id)
                once("workspace-after",_encoded({"schema_version":1,"entries":[{"role":"FILE","relative_path":item.relative_file_reference,
                    "sha256":item.content_fingerprint,"byte_size":item.size,"source":"canonical-workspace"}
                    for item in sorted(after.files,key=lambda item:item.relative_file_reference)]}),"MANIFEST")
            except (AttributeError,OSError,RuntimeError,TypeError,ValueError):pass
            if prl_path is not None:
                try:
                    if prl_path.is_file() and not prl_path.is_symlink():once("prl-after",prl_path.read_bytes())
                except (OSError,ValueError):pass
        try:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", operation.operation_id) is None:
                raise ValueError("baseline report operation identity unavailable")
            require();values=self._policy_inputs()
            root=self.workspace._resolve(operation.tenant_id,operation.project_id,operation.target_id).root.resolve(strict=True)
            output=self._report_root.resolve(strict=False)
            if not self._report_root.is_absolute() or output==root or output.is_relative_to(root) or root.is_relative_to(output):
                raise ValueError("baseline report root overlaps Workspace")
            for parent in (output,*output.parents):
                if parent.exists() and (parent.is_symlink() or getattr(parent.lstat(),"st_file_attributes",0)&0x400):
                    raise ValueError("baseline report root is a reparse path")
            snapshot=self.workspace.snapshot(tenant_id=operation.tenant_id,project_id=operation.project_id,target_id=operation.target_id)
            pcb_path=self.workspace._safe_path(root,operation.pcb_relative_path)
            pcb_bytes=pcb_path.read_bytes()
            if _sha(pcb_bytes)!=operation.pcb_sha256:raise ValueError("baseline PCB changed")
            workspace_bytes=_encoded({"schema_version":1,"entries":[{"role":"FILE","relative_path":item.relative_file_reference,
                "sha256":item.content_fingerprint,"byte_size":item.size,"source":"canonical-workspace"}
                for item in sorted(snapshot.files,key=lambda item:item.relative_file_reference)]})
            refs={role:put(role,content,"MANIFEST" if role.endswith("manifest") or role=="tool_bundle" else "LOG") for role,content in values.items()}
            refs["input_pcb"]=put("input-pcb",pcb_bytes)
            refs["workspace_manifest"]=put("workspace-manifest",workspace_bytes,"MANIFEST")
            prl_path=self.workspace._safe_path(root,operation.prl_relative_path)
            prl_bytes=prl_path.read_bytes() if prl_path.is_file() else None
            prl_before=None if prl_bytes is None else put("prl-before",prl_bytes)
            require();self._policy_inputs()
            timeout=min(self.policy.timeout_seconds,int((deadline-self._clock()).total_seconds()))
            if timeout<1:raise TimeoutError("baseline deadline exhausted")
            output.mkdir(parents=True,exist_ok=True);report=output/(operation.operation_id+".json")
            if report.exists():raise ValueError("baseline report output already exists")
            executable=next(item.absolute_path for item in self._bundle if item.role=="EXECUTABLE")
            collector=self
            class Phase:
                def is_cancelled(self) -> bool:
                    nonlocal phase_error
                    try:collector._policy_inputs();require();return False
                    except (AttributeError,OSError,RuntimeError,TypeError,ValueError) as error:
                        phase_error=error
                        return True
            # All output/path/closure I/O has completed. The same phase is also
            # checked by the sole runner immediately before its actual Popen.
            authority()
            exit_code,_,_=self._runner.run_drc_baseline((str(executable),"pcb","drc","--format","json","--output",str(report),str(pcb_path)),
                cwd=root,timeout_seconds=timeout,environment=dict(self._environment),cancellation=Phase())
            raw=report.read_bytes();raw_ref=put("raw-report",raw)
            after=self.workspace.snapshot(tenant_id=operation.tenant_id,project_id=operation.project_id,target_id=operation.target_id)
            put("workspace-after",_encoded({"schema_version":1,"entries":[{"role":"FILE","relative_path":item.relative_file_reference,
                "sha256":item.content_fingerprint,"byte_size":item.size,"source":"canonical-workspace"}
                for item in sorted(after.files,key=lambda item:item.relative_file_reference)]}),"MANIFEST")
            current_prl=prl_path.read_bytes() if prl_path.is_file() else None
            prl_after=None if current_prl is None else put("prl-after",current_prl)
            provenance={"schema_version":2,"collector_id":self.policy.collector_id,"collector_version":self.policy.collector_version,
                "tool_id":self.policy.tool_id,"tool_version":self.policy.tool_version,"executable_sha256":self.policy.executable_sha256,
                "normalizer_id":self.policy.normalizer_id,"normalizer_version":self.policy.normalizer_version,
                **{key:value.model_dump(mode="json") for key,value in refs.items()},
                "argv_template":list(DRC_ARGV_TEMPLATE),"argv_slots":{"pcb":{"relative_path":operation.pcb_relative_path,"artifact":refs["input_pcb"].model_dump(mode="json")},
                    "report":{"role":"DRC_REPORT","relative_path":"baseline-reports/"+report.name,"artifact":raw_ref.model_dump(mode="json")}},
                "prl_relative_path":operation.prl_relative_path,"prl_before":None if prl_before is None else prl_before.model_dump(mode="json"),
                "prl_after":None if prl_after is None else prl_after.model_dump(mode="json"),"raw_sha256":raw_ref.artifact_sha256,
                "scope":self.policy.comparison_scope,"policy":{"zone_fill_policy":self.policy.zone_fill_policy,
                    "schematic_parity":self.policy.schematic_parity,"included_severities":list(self.policy.included_severities),"ignored_checks":[]}}
            provenance_ref=put("provenance",_encoded(provenance))
            require();self._policy_inputs()
            if exit_code!=0:raise ValueError("baseline collector exited with failure")
            if snapshot.files!=after.files or prl_bytes!=current_prl:raise ValueError("baseline Workspace side effect")
            content={identity.artifact_id:blob for identity,blob in pairs}
            context=build_drc_v2_context(raw_ref,provenance_ref,read_artifact=lambda identity:content[identity.artifact_id],policy=self.policy)
            primary=put("context",_encoded(context));require()
            return AdapterResult(status="SUCCEEDED",summary="DRC baseline observation",artifact=primary,
                artifact_content=pairs[-1][1],additional_artifacts=tuple(pairs[:-1]),evidence_references=tuple(identity.artifact_id for identity,_ in pairs))
        except TimeoutError:
            preserve()
            return AdapterResult(status="TIMED_OUT",summary="DRC baseline timed out",additional_artifacts=tuple(pairs))
        except InterruptedError:
            preserve()
            status="TIMED_OUT" if isinstance(phase_error,TimeoutError) else "FAILED" if phase_error is not None and not isinstance(phase_error,InterruptedError) else "CANCELLED"
            return AdapterResult(status=status,summary="DRC baseline phase stopped",additional_artifacts=tuple(pairs))
        except (AttributeError,OSError,RuntimeError,TypeError,ValueError):
            preserve()
            return AdapterResult(status="FAILED",summary="DRC baseline collection failed",additional_artifacts=tuple(pairs))
