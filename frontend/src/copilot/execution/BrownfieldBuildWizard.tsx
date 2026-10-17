import { useEffect, useState } from "react";

import {
  createBrownfieldBuildProposal, decideBrownfieldBuildPlan, fetchBrownfieldBuildContext,
  fetchBrownfieldBuildPreview, type BrownfieldBuildContext, type BrownfieldBuildPreview,
  type BrownfieldBuildConfiguration,
} from "../api/brownfieldBuildClient";
import { useDesktopSessionState } from "../api/sessionState";

type Draft = {
  targetPlatform: string; mcuFamily: string; mcuExactPart: string; sourcePaths: string;
  buildSystem: "" | "CMAKE"; toolchainId: string; toolchainVersion: string; profile: string;
  buildDirectory: string; outputStem: string; cmakeProject: string; cmakeToolchainFile: string;
  startupSource: string; systemSource: string; cmsisInclude: string; deviceInclude: string; linkerScript: string;
  elf: boolean;
};

const emptyDraft: Draft = {
  targetPlatform: "", mcuFamily: "", mcuExactPart: "", sourcePaths: "", buildSystem: "",
  toolchainId: "", toolchainVersion: "", profile: "", buildDirectory: "", outputStem: "",
  cmakeProject: "", cmakeToolchainFile: "", startupSource: "", systemSource: "",
  cmsisInclude: "", deviceInclude: "", linkerScript: "", elf: false,
};

const textFields: Array<[keyof Draft, string]> = [
  ["targetPlatform", "Target platform"], ["mcuFamily", "MCU family"], ["mcuExactPart", "MCU exact part"],
  ["toolchainId", "Toolchain ID"], ["toolchainVersion", "Toolchain version"], ["profile", "Build profile"],
  ["buildDirectory", "Build directory"], ["outputStem", "Output stem"], ["cmakeProject", "CMake project"],
  ["cmakeToolchainFile", "CMake toolchain file"], ["startupSource", "Startup source"],
  ["systemSource", "System source"], ["cmsisInclude", "CMSIS include"], ["deviceInclude", "Device include"],
  ["linkerScript", "Linker script"],
];

function code(error: unknown): string { return error instanceof Error ? error.message : "BROWNFIELD_BUILD_UNAVAILABLE"; }

function currentPreview(value: BrownfieldBuildPreview | null, projectId: string | null): value is BrownfieldBuildPreview {
  return Boolean(value && projectId && value.project_id === projectId && value.project_fingerprint && value.workspace_fingerprint && value.workspace_target_id && value.workspace_target_fingerprint);
}

function sourcePaths(value: string): string[] {
  return value.split(/[,\n]/).map((path) => path.trim().replaceAll("\\", "/")).filter(Boolean);
}

function ready(draft: Draft, preview: BrownfieldBuildPreview | null, projectId: string | null): boolean {
  if (!currentPreview(preview, projectId) || draft.buildSystem !== "CMAKE" || !draft.elf) return false;
  if (textFields.slice(0, -1).some(([key]) => !String(draft[key]).trim())) return false;
  const sources = sourcePaths(draft.sourcePaths);
  return sources.length > 0 && sources.length === new Set(sources).size && sources.every((path) => preview.source_files.includes(path))
    && [draft.cmakeProject, draft.cmakeToolchainFile, draft.startupSource, draft.systemSource]
      .every((path) => preview.source_files.includes(path.trim().replaceAll("\\", "/")));
}

function samePreview(a: BrownfieldBuildPreview, b: BrownfieldBuildPreview): boolean {
  return a.project_id === b.project_id && a.project_fingerprint === b.project_fingerprint
    && a.workspace_fingerprint === b.workspace_fingerprint && a.workspace_target_id === b.workspace_target_id
    && a.workspace_target_fingerprint === b.workspace_target_fingerprint;
}

function contextMatchesPreview(context: BrownfieldBuildContext, preview: BrownfieldBuildPreview): boolean {
  return context.project_id === preview.project_id && context.project_fingerprint === preview.project_fingerprint
    && context.workspace_fingerprint === preview.workspace_fingerprint
    && context.workspace_target_id === preview.workspace_target_id
    && context.workspace_target_fingerprint === preview.workspace_target_fingerprint;
}

export function BrownfieldBuildWizard({ projectId, onApproved }: { projectId: string | null; onApproved?: () => void }) {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const [preview, setPreview] = useState<BrownfieldBuildPreview | null>(null);
  const [context, setContext] = useState<BrownfieldBuildContext | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setPreview(null); setContext(null); setDraft(emptyDraft); setError(null);
    if (projectId && !sessionExpired) {
      void Promise.allSettled([fetchBrownfieldBuildPreview(projectId), fetchBrownfieldBuildContext(projectId)]).then(([previewResult, contextResult]) => {
        if (!active) return;
        if (previewResult.status === "fulfilled" && currentPreview(previewResult.value, projectId)) setPreview(previewResult.value);
        else setError(previewResult.status === "rejected" ? code(previewResult.reason) : "WORKSPACE_PREVIEW_INVALID");
        if (contextResult.status === "fulfilled") {
          const saved = contextResult.value;
          if (saved && previewResult.status === "fulfilled" && !contextMatchesPreview(saved, previewResult.value)) {
            setError((previous) => previous ?? "APPROVAL_STALE");
          } else if (saved && previewResult.status === "fulfilled") setContext(saved);
        } else setError((previous) => previous ?? code(contextResult.reason));
      });
    }
    return () => { active = false; };
  }, [projectId, sessionExpired]);

  function edit<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((previous) => ({ ...previous, [key]: value }));
    setContext(null);
    setError(null);
  }

  async function refresh() {
    if (!projectId || sessionExpired || busy) return;
    setBusy(true); setError(null);
    try {
      const latest = await fetchBrownfieldBuildPreview(projectId);
      if (!currentPreview(latest, projectId)) throw new Error("WORKSPACE_PREVIEW_INVALID");
      setPreview(latest);
      const current = await fetchBrownfieldBuildContext(projectId);
      setContext(current && contextMatchesPreview(current, latest) ? current : null);
      if (current && !contextMatchesPreview(current, latest)) setError("APPROVAL_STALE");
    } catch (cause) { setPreview(null); setContext(null); setError(code(cause)); }
    finally { setBusy(false); }
  }

  async function propose() {
    if (!projectId || sessionExpired || busy || !ready(draft, preview, projectId)) return;
    setBusy(true); setError(null);
    try {
      const latest = await fetchBrownfieldBuildPreview(projectId);
      if (!samePreview(preview!, latest)) throw new Error("APPROVAL_STALE");
      const configuration: BrownfieldBuildConfiguration = {
        target_kind: draft.mcuExactPart.trim(), build_system: "CMAKE", toolchain_id: draft.toolchainId.trim(),
        toolchain_version: draft.toolchainVersion.trim(), profile: draft.profile.trim(),
        build_directory: draft.buildDirectory.trim(), expected_artifact_types: ["ELF"], output_stem: draft.outputStem.trim(),
        compiler_flags: [], linker_script: draft.linkerScript.trim() || null, cmake_project: draft.cmakeProject.trim(),
        cmake_toolchain_file: draft.cmakeToolchainFile.trim(), startup_source: draft.startupSource.trim(),
        system_source: draft.systemSource.trim(), cmsis_include: draft.cmsisInclude.trim(), device_include: draft.deviceInclude.trim(),
      };
      const next = await createBrownfieldBuildProposal(projectId, {
        workspace_fingerprint: latest.workspace_fingerprint, target_platform: draft.targetPlatform.trim(),
        mcu_family: draft.mcuFamily.trim(), mcu_exact_part: draft.mcuExactPart.trim(),
        source_layout: sourcePaths(draft.sourcePaths), configuration,
      });
      if (!contextMatchesPreview(next, latest) || !next.proposal_fingerprint) throw new Error("BUILD_PROPOSAL_RESPONSE_INVALID");
      setContext(next);
    } catch (cause) { setContext(null); setError(code(cause)); }
    finally { setBusy(false); }
  }

  async function decide(decision: "APPROVED" | "REJECTED") {
    if (!projectId || !context?.proposal_fingerprint || context.plan.status !== "PROPOSED" || !preview || sessionExpired || busy) return;
    setBusy(true); setError(null);
    try {
      const latest = await fetchBrownfieldBuildPreview(projectId);
      const current = await fetchBrownfieldBuildContext(projectId);
      if (!samePreview(preview, latest) || !current || !contextMatchesPreview(current, latest) || current.proposal_fingerprint !== context.proposal_fingerprint || current.fingerprint !== context.fingerprint) throw new Error("APPROVAL_STALE");
      const next = await decideBrownfieldBuildPlan(projectId, context.proposal_fingerprint, decision);
      if (next.project_id !== projectId || next.proposal_fingerprint !== context.proposal_fingerprint) throw new Error("BUILD_DECISION_RESPONSE_INVALID");
      setContext(next);
      if (decision === "APPROVED" && next.plan.status === "APPROVED" && next.review?.status === "APPROVED") onApproved?.();
    } catch (cause) { setContext(null); setError(code(cause)); }
    finally { setBusy(false); }
  }

  const blocked = !projectId || sessionExpired || !currentPreview(preview, projectId);
  const canDecide = Boolean(!blocked && !busy && context?.plan.status === "PROPOSED" && context.proposal_fingerprint && context.workspace_fingerprint === preview?.workspace_fingerprint);
  return <section className="ide-document-section" aria-label="Brownfield Firmware Build">
    <h4>已有工程 · Build Context</h4>
    <p className="muted">从 Workspace Owner 读取当前源码身份。此处只建立并审查 Build Plan；执行仍需独立 Operation Approval。</p>
    {blocked && <p role="status">BLOCKED · {sessionExpired ? "DESKTOP_SESSION_EXPIRED" : !projectId ? "PROJECT_REQUIRED" : error ?? "WORKSPACE_PREVIEW_UNAVAILABLE"}</p>}
    {preview && <dl>
      <div><dt>Project</dt><dd>{preview.project_id}</dd></div>
      <div><dt>Workspace / Source Fingerprint</dt><dd>{preview.workspace_fingerprint}</dd></div>
      <div><dt>Target ID</dt><dd>{preview.workspace_target_id}</dd></div>
      <div><dt>Target Fingerprint</dt><dd>{preview.workspace_target_fingerprint}</dd></div>
      <div><dt>Source files</dt><dd>{preview.source_files.length} 个已扫描文件</dd></div>
    </dl>}
    {preview && <details><summary>查看已扫描文件</summary><ul>{preview.source_files.map((path) => <li key={path}>{path}</li>)}</ul></details>}
    {context && <dl>
      <div><dt>Build Plan</dt><dd>{context.plan.title} · {context.plan.status}</dd></div>
      <div><dt>Proposal Fingerprint</dt><dd>{context.proposal_fingerprint}</dd></div>
      <div><dt>MCU</dt><dd>{context.firmware_spec.mcu_exact_part}</dd></div>
      <div><dt>Toolchain</dt><dd>{context.configuration.toolchain_id} · {context.configuration.toolchain_version}</dd></div>
      <div><dt>Build Configuration</dt><dd>{context.configuration.build_system} · {context.configuration.profile}</dd></div>
      <div><dt>Plan Review</dt><dd>{context.review?.status ?? "待审核"}</dd></div>
    </dl>}
    <button type="button" disabled={busy || sessionExpired || !projectId} onClick={() => void refresh()}>刷新工程快照</button>
    <h4>确认构建配置</h4>
    <p className="muted">目标和工具链均须从真实工程核对后填写；字段不会代替本机工具链验证。</p>
    <label>Source paths（每行一条，须属于扫描清单）<textarea aria-label="Source paths" value={draft.sourcePaths} onChange={(event) => edit("sourcePaths", event.target.value)} /></label>
    <label>Build system<select aria-label="Build system" value={draft.buildSystem} onChange={(event) => edit("buildSystem", event.target.value as Draft["buildSystem"])}><option value="">请选择</option><option value="CMAKE">CMake / Ninja</option></select></label>
    {textFields.map(([key, label]) => <label key={key}>{label}<input aria-label={label} value={String(draft[key])} onChange={(event) => edit(key, event.target.value)} /></label>)}
    <label>ELF artifact<input type="checkbox" aria-label="ELF artifact" checked={draft.elf} onChange={(event) => edit("elf", event.target.checked)} /></label>
    {draft.buildSystem === "CMAKE" && <p>受控命令预览：cmake --build &lt;Operation Sandbox&gt;（不可编辑；尚未执行）</p>}
    <button type="button" disabled={busy || sessionExpired || !ready(draft, preview, projectId)} onClick={() => void propose()}>创建 Build Plan Proposal</button>
    <button type="button" disabled={!canDecide} onClick={() => void decide("APPROVED")}>批准 Build Plan</button>
    <button type="button" disabled={!canDecide} onClick={() => void decide("REJECTED")}>拒绝 Build Plan</button>
    {error && <p role="alert">Brownfield Build：{error}</p>}
  </section>;
}
