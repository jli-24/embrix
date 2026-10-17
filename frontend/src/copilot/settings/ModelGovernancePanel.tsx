import { useEffect, useMemo, useRef, useState } from "react";
import { ModelGatewayRequestError, probeModelTextCapability } from "../api/modelGatewayClient";

import {
  createGovernanceModelPolicy,
  fetchGovernanceModels,
  fetchGovernanceStages,
  fetchProjectDefaultPolicy,
  fetchStagePolicies,
  ModelGovernanceRequestError,
  saveProjectDefaultPolicy,
  saveStagePolicy,
  setGovernanceModelEnabled,
  type GovernanceModel,
  type GovernanceModelIdentity,
  type GovernanceProjectDefaultPolicy,
  type GovernanceSelectionMode,
  type GovernanceStage,
  type GovernanceStageId,
  type GovernanceStagePolicy,
} from "../api/modelGovernanceClient";

type ModelGovernancePanelProps = { projectId?: string; projectName?: string; fetcher?: typeof fetch };

export const STAGE_LABELS: Record<string, string> = {
  REQUIREMENT_ANALYSIS: "需求分析",
  TECH_RESEARCH: "技术调研",
  SYSTEM_ARCHITECTURE: "系统架构",
  HARDWARE_SELECTION: "硬件选型",
  SCHEMATIC_DESIGN: "原理图设计",
  PCB_REVIEW: "PCB 审查",
  FIRMWARE_DEVELOPMENT: "固件开发",
  DEBUGGING: "调试",
  SIMULATION: "仿真",
  DOCUMENTATION: "文档",
};

const capabilityLabels: Record<string, string> = {
  TEXT: "文本",
  CODING: "代码",
  VISION: "视觉",
  AUDIO_INPUT: "音频输入",
  REASONING: "推理",
  TOOL_CALLING: "工具调用",
  PARALLEL_TOOL_CALLING: "并行工具调用",
  STRUCTURED_OUTPUT: "结构化输出",
  STREAMING: "流式输出",
  FILE_INPUT: "文件输入",
  EMBEDDING: "向量",
  WEB_SEARCH: "网络搜索",
  IMAGE_GENERATION: "图像生成",
  COMPUTER_USE: "计算机操作",
};

function capabilityLabel(value: string): string { return capabilityLabels[value] ?? value; }
function stageLabel(stage: GovernanceStage): string { return STAGE_LABELS[stage.stage_id] ?? stage.stage_id; }
function billingLabel(value: string): string {
  if (value === "FREE") return "免费";
  if (value === "LOCAL") return "本地";
  if (value === "MIXED") return "混合计费";
  if (["PAID", "METERED", "SUBSCRIPTION"].includes(value)) return "付费";
  return "费用未知";
}
function modeLabel(value: GovernanceSelectionMode): string {
  return ({ AUTO: "自动选择", FIXED_MODEL: "固定模型", CANDIDATE_GROUP: "候选模型组" })[value];
}
function identity(model: GovernanceModel): GovernanceModelIdentity {
  return { provider_id: model.provider_id, endpoint_id: model.endpoint_id, credential_ref: model.credential_ref, model_profile_id: model.model_profile_id, model_id: model.model_id };
}
export function modelIdentityKey(model: GovernanceModelIdentity): string {
  return `${model.provider_id}:${model.endpoint_id}:${model.credential_ref}:${model.model_profile_id}:${model.model_id}`;
}

function evidenceMatches(model: GovernanceModel, evidence: GovernanceModel["capability_provenance"][number]): boolean {
  return evidence.provider_id === model.provider_id
    && evidence.endpoint_id === model.endpoint_id
    && evidence.credential_ref === model.credential_ref
    && evidence.model_profile_id === model.model_profile_id
    && evidence.model_id === model.model_id
    && (evidence.credential_revision ?? null) === (model.credential_revision ?? null)
    && (evidence.endpoint_revision ?? null) === (model.endpoint_revision ?? null);
}

export function confirmedCapabilities(model: GovernanceModel): string[] {
  const confirmed = new Set(model.capability_provenance.filter((item) => item.status === "PASS" && evidenceMatches(model, item)).map((item) => item.capability));
  return model.capabilities.filter((item) => confirmed.has(item));
}

export type ModelReadiness = {
  operational: "UNVERIFIED" | "AVAILABLE" | "RATE_LIMITED" | "UNAVAILABLE";
  capabilities: string[];
};

/** Pure UI projection. It never creates a backend state or changes governance. */
export function deriveModelReadiness(model: GovernanceModel): ModelReadiness {
  const capabilities = confirmedCapabilities(model);
  if (model.verification_status !== "VERIFIED") return { operational: "UNVERIFIED", capabilities };
  if (model.identity_status !== "VALID") return { operational: "UNAVAILABLE", capabilities };
  if (model.availability_status === "RATE_LIMITED") return { operational: "RATE_LIMITED", capabilities };
  if (model.availability_status === "AVAILABLE") return { operational: "AVAILABLE", capabilities };
  return { operational: "UNAVAILABLE", capabilities };
}

function readinessLabel(value: ModelReadiness["operational"]): string {
  return ({ UNVERIFIED: "未验证", AVAILABLE: "可用", RATE_LIMITED: "受限", UNAVAILABLE: "不可用" })[value];
}

function identityReason(model: GovernanceModel): string {
  return model.identity_status_reason ?? (model.identity_status === "VALID" ? "身份有效" : "未提供");
}

export type GovernanceModelGroup = {
  key: string;
  connection_id: string | null;
  provider_display_name: string;
  connection_name: string | null;
  models: GovernanceModel[];
};

/** Connection grouping uses only the backend connection_id. No tuple inference or ordinal names. */
export function providerConnectionIdentity(model: Pick<GovernanceModel, "connection_id">): string {
  return model.connection_id ?? "__UNBOUND_CONNECTION__";
}

export function groupGovernanceModels(models: GovernanceModel[]): GovernanceModelGroup[] {
  const groups = new Map<string, GovernanceModelGroup>();
  for (const model of models) {
    const key = providerConnectionIdentity(model);
    const current = groups.get(key);
    if (current) current.models.push(model);
    else groups.set(key, { key, connection_id: model.connection_id ?? null, provider_display_name: model.provider_display_name ?? model.provider_id, connection_name: model.connection_name ?? null, models: [model] });
  }
  return [...groups.values()];
}

function modelGroupHeading(group: GovernanceModelGroup): string {
  const provider = group.provider_display_name || "提供商信息不可用";
  const connection = group.connection_name ? ` · ${group.connection_name}` : " · 连接信息不可用";
  return `${provider}${connection} · ${group.models.length} 个模型`;
}

function candidateLabel(model: GovernanceModel): string {
  const connection = model.connection_name ? ` · ${model.connection_name}` : "";
  return `${model.provider_display_name ?? model.provider_id}${connection} · ${model.display_name}`;
}

function capabilityWarningLabel(value: string): string {
  return value.replace("MISSING_CAPABILITY:", "").split(",").filter(Boolean).map(capabilityLabel).join("、");
}

function requestError(reason: unknown): string {
  return reason instanceof ModelGovernanceRequestError ? reason.code : "MODEL_GOVERNANCE_UNAVAILABLE";
}

export function ModelGovernancePanel({ projectId, projectName, fetcher = fetch }: ModelGovernancePanelProps) {
  const [tab, setTab] = useState<"current" | "project" | "stage">("current");
  const [stageId, setStageId] = useState<GovernanceStageId>("REQUIREMENT_ANALYSIS");
  const [stages, setStages] = useState<GovernanceStage[]>([]);
  const [models, setModels] = useState<GovernanceModel[]>([]);
  const [projectModels, setProjectModels] = useState<GovernanceModel[]>([]);
  const [stageModels, setStageModels] = useState<GovernanceModel[]>([]);
  const [projectCandidatesState, setProjectCandidatesState] = useState<"LOADING" | "EMPTY" | "ERROR" | "READY">("LOADING");
  const [stageCandidatesState, setStageCandidatesState] = useState<"LOADING" | "EMPTY" | "ERROR" | "READY">("LOADING");
  const [stagePolicies, setStagePolicies] = useState<GovernanceStagePolicy[]>([]);
  const [projectDefault, setProjectDefault] = useState<GovernanceProjectDefaultPolicy | null>(null);
  const [mode, setMode] = useState<GovernanceSelectionMode>("AUTO");
  const [fixedModel, setFixedModel] = useState("");
  const [candidateModels, setCandidateModels] = useState<string[]>([]);
  const [projectMode, setProjectMode] = useState<GovernanceSelectionMode>("AUTO");
  const [projectFixedModel, setProjectFixedModel] = useState("");
  const [projectCandidateModels, setProjectCandidateModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [stageError, setStageError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [probingKey, setProbingKey] = useState<string | null>(null);
  const [probeNotice, setProbeNotice] = useState<{ key: string; message: string; success: boolean } | null>(null);
  const [allowedRevision, setAllowedRevision] = useState(0);
  const globalRequestId = useRef(0);

  useEffect(() => {
    let alive = true;
    const request = ++globalRequestId.current;
    void Promise.all([fetchGovernanceStages(fetcher), fetchGovernanceModels(fetcher, "GLOBAL")]).then(([stageValue, modelValue]) => {
      if (!alive || request !== globalRequestId.current) return;
      setStages(stageValue);
      setModels(modelValue);
      if (stageValue[0]) setStageId(stageValue[0].stage_id);
    }).catch((reason: unknown) => { if (alive && request === globalRequestId.current) setError(requestError(reason)); }).finally(() => { if (alive && request === globalRequestId.current) setLoading(false); });
    return () => { alive = false; };
  }, [fetcher]);

  useEffect(() => {
    let alive = true;
    setProjectModels([]);
    setProjectCandidatesState("LOADING");
    setProjectDefault(null);
    setProjectError(null);
    if (!projectId) return () => { alive = false; };
    void Promise.allSettled([fetchGovernanceModels(fetcher, "PROJECT", projectId), fetchProjectDefaultPolicy(fetcher, projectId)]).then(([modelResult, policyResult]) => {
      if (!alive) return;
      if (modelResult.status === "fulfilled") {
        setProjectModels(modelResult.value);
        setProjectCandidatesState(modelResult.value.length ? "READY" : "EMPTY");
      } else setProjectCandidatesState("ERROR");
      if (policyResult.status === "fulfilled") {
        const policy = policyResult.value;
        setProjectDefault(policy);
        setProjectMode(policy?.selection_mode ?? "AUTO");
        setProjectFixedModel(policy?.fixed_model ? modelIdentityKey(policy.fixed_model) : "");
        setProjectCandidateModels(policy?.candidate_models.map(modelIdentityKey) ?? []);
      }
      const failures = [
        modelResult.status === "rejected" ? `项目候选读取失败（${requestError(modelResult.reason)}）` : null,
        policyResult.status === "rejected" ? `项目策略读取失败（${requestError(policyResult.reason)}）` : null,
      ].filter(Boolean);
      setProjectError(failures.length ? failures.join("；") : null);
    });
    return () => { alive = false; };
  }, [fetcher, projectId, allowedRevision]);

  useEffect(() => {
    let alive = true;
    setStageModels([]);
    setStageCandidatesState("LOADING");
    setStagePolicies([]);
    setStageError(null);
    if (!projectId) return () => { alive = false; };
    void Promise.allSettled([fetchGovernanceModels(fetcher, "STAGE", projectId, stageId), fetchStagePolicies(fetcher, projectId)]).then(([modelResult, policyResult]) => {
      if (!alive) return;
      if (modelResult.status === "fulfilled") {
        setStageModels(modelResult.value);
        setStageCandidatesState(modelResult.value.length ? "READY" : "EMPTY");
      } else setStageCandidatesState("ERROR");
      if (policyResult.status === "fulfilled") {
        const policies = policyResult.value;
        setStagePolicies(policies);
        const policy = policies.find((item) => item.stage_id === stageId);
        setMode(policy?.selection_mode ?? "AUTO");
        setFixedModel(policy?.fixed_model ? modelIdentityKey(policy.fixed_model) : "");
        setCandidateModels(policy?.candidate_models.map(modelIdentityKey) ?? []);
      }
      const failures = [
        modelResult.status === "rejected" ? `阶段候选读取失败（${requestError(modelResult.reason)}）` : null,
        policyResult.status === "rejected" ? `阶段策略读取失败（${requestError(policyResult.reason)}）` : null,
      ].filter(Boolean);
      setStageError(failures.length ? failures.join("；") : null);
    });
    return () => { alive = false; };
  }, [fetcher, projectId, stageId, allowedRevision]);

  const modelGroups = useMemo(() => groupGovernanceModels(models), [models]);
  const eligibleProjectModels = useMemo(() => projectModels.filter((model) => model.allowed && model.can_enable), [projectModels]);
  const eligibleStageModels = useMemo(() => stageModels.filter((model) => model.allowed && model.can_enable && !model.capability_warning), [stageModels]);
  const assignmentByKey = useMemo(() => new Map([...eligibleProjectModels, ...eligibleStageModels].map((model) => [modelIdentityKey(identity(model)), model])), [eligibleProjectModels, eligibleStageModels]);
  const selectedStage = stages.find((stage) => stage.stage_id === stageId);

  async function toggle(model: GovernanceModel): Promise<void> {
    if (!model.allowed && !model.can_enable) return;
    try {
      if (model.policy_id && model.policy_scope === "GLOBAL") await setGovernanceModelEnabled(fetcher, model.policy_id, !model.allowed);
      else await createGovernanceModelPolicy(fetcher, model, "GLOBAL", undefined, undefined, !model.allowed);
      const request = ++globalRequestId.current;
      const refreshed = await fetchGovernanceModels(fetcher, "GLOBAL");
      if (request === globalRequestId.current) setModels(refreshed);
      setAllowedRevision((value) => value + 1);
    } catch (reason: unknown) { setError(requestError(reason)); }
  }

  async function probeText(model: GovernanceModel): Promise<void> {
    const key = modelIdentityKey(identity(model));
    setProbingKey(key);
    setProbeNotice(null);
    try {
      const result = await probeModelTextCapability(model.model_profile_id, fetcher);
      const request = ++globalRequestId.current;
      const refreshed = await fetchGovernanceModels(fetcher, "GLOBAL");
      if (request === globalRequestId.current) setModels(refreshed);
      setAllowedRevision((value) => value + 1);
      setProbeNotice({
        key,
        success: result.status === "PASS",
        message: result.status === "PASS" ? "文本能力验证通过；请单独决定是否允许使用。" : `文本能力未通过验证（${result.status}）。`,
      });
    } catch (reason: unknown) {
      const code = reason instanceof ModelGatewayRequestError ? reason.code : "CAPABILITY_PROBE_UNAVAILABLE";
      setProbeNotice({ key, success: false, message: `文本能力验证失败（${code}）。` });
    } finally {
      setProbingKey(null);
    }
  }

  async function saveProject(): Promise<void> {
    if (!projectId) return;
    setSaving(true); setProjectError(null);
    try {
      const fixed = projectMode === "FIXED_MODEL" ? assignmentByKey.get(projectFixedModel) : undefined;
      const candidates = projectMode === "CANDIDATE_GROUP" ? projectCandidateModels.flatMap((key) => { const value = assignmentByKey.get(key); return value ? [identity(value)] : []; }) : [];
      setProjectDefault(await saveProjectDefaultPolicy(fetcher, projectId, { selection_mode: projectMode, fixed_model: fixed ? identity(fixed) : null, candidate_models: candidates }));
    } catch (reason: unknown) { setProjectError(`项目策略保存失败（${requestError(reason)}）`); } finally { setSaving(false); }
  }

  async function saveStage(): Promise<void> {
    if (!projectId) return;
    setSaving(true); setStageError(null);
    try {
      const fixed = mode === "FIXED_MODEL" ? assignmentByKey.get(fixedModel) : undefined;
      const candidates = mode === "CANDIDATE_GROUP" ? candidateModels.flatMap((key) => { const value = assignmentByKey.get(key); return value ? [identity(value)] : []; }) : [];
      await saveStagePolicy(fetcher, projectId, stageId, { selection_mode: mode, fixed_model: fixed ? identity(fixed) : null, candidate_models: candidates });
      setStagePolicies(await fetchStagePolicies(fetcher, projectId));
    } catch (reason: unknown) { setStageError(`阶段策略保存失败（${requestError(reason)}）`); } finally { setSaving(false); }
  }

  const noProjectCandidates = projectId !== undefined && projectCandidatesState !== "ERROR" && projectCandidatesState !== "LOADING" && eligibleProjectModels.length === 0;
  const noStageCandidates = projectId !== undefined && stageCandidatesState !== "ERROR" && stageCandidatesState !== "LOADING" && eligibleStageModels.length === 0;
  const projectCandidatesUnavailable = projectCandidatesState !== "READY" || noProjectCandidates;
  const stageCandidatesUnavailable = stageCandidatesState !== "READY" || noStageCandidates;

  return <div className="ide-model-governance" data-model-governance>
    <div className="ide-settings__section-heading"><div><span className="eyebrow">模型治理</span><h3 id="model-governance-heading">模型</h3></div></div>
    <div className="ide-segmented" role="tablist" aria-label="模型治理视图"><button type="button" role="tab" aria-selected={tab === "current"} className={tab === "current" ? "is-active" : ""} onClick={() => setTab("current")} data-model-governance-tab="current">当前模型</button><button type="button" role="tab" aria-selected={tab === "project"} className={tab === "project" ? "is-active" : ""} onClick={() => setTab("project")} data-model-governance-tab="project">项目策略</button><button type="button" role="tab" aria-selected={tab === "stage"} className={tab === "stage" ? "is-active" : ""} onClick={() => setTab("stage")} data-model-governance-tab="stage">阶段策略</button></div>
    {tab === "current" && error && <p className="ide-form-error" role="alert">模型管理暂不可用 · {error}</p>}
    {tab === "project" && projectError && <p className="ide-form-error" role="alert">{projectError}</p>}
    {tab === "stage" && stageError && <p className="ide-form-error" role="alert">{stageError}</p>}

    {tab === "current" && <section className="ide-model-governance__current" aria-labelledby="current-models-heading"><h4 id="current-models-heading">当前模型</h4>{loading && <p role="status">当前模型加载中…</p>}{!loading && !error && models.length === 0 && <p role="status">当前 Connection 没有发现模型，请先在“提供商管理”中刷新模型。</p>}{!loading && models.length > 0 && <div className="ide-model-governance__table" role="list" aria-label="按 Provider Connection 分组的当前模型">{modelGroups.map((group, groupIndex) => <section className="ide-model-governance__group" data-provider-connection-group={group.connection_id ?? "unbound"} key={group.key} aria-labelledby={`model-group-${groupIndex}`}><header className="ide-model-governance__group-header"><span className="eyebrow">提供商连接</span><h5 id={`model-group-${groupIndex}`}>{modelGroupHeading(group)}</h5></header><div className="ide-model-governance__group-models" role="list">{group.models.map((model) => { const key = modelIdentityKey(identity(model)); const readiness = deriveModelReadiness(model); return <article className="ide-model-governance__row ide-model-governance__row--model" data-model-row data-model-id={model.model_id} role="listitem" key={key}><span className="ide-model-governance__model-summary" data-model-name title={model.display_name}><strong>{model.display_name}</strong>{model.display_name !== model.model_id && <small className="ide-model-governance__model-id">{model.model_id}</small>}</span><span className="ide-model-governance__cell--capability">{readiness.capabilities.length ? readiness.capabilities.map(capabilityLabel).join("、") : "能力证据未确认"}</span><span className="ide-model-governance__status">{readinessLabel(readiness.operational)}</span><span className="ide-model-governance__cell--action ide-model-governance__cell--allow"><button type="button" className="ide-secondary-button" data-model-allow-button aria-label={`${model.display_name} 允许 Embrix 使用`} disabled={!model.allowed && !model.can_enable} onClick={() => void toggle(model)} aria-pressed={model.allowed}>{model.allowed ? "允许 Embrix 使用" : "允许使用"}</button></span><span className="ide-model-governance__cell--action ide-model-governance__cell--details"><button type="button" className="ide-secondary-button" data-model-details-button aria-label={`${model.display_name} 详情`} onClick={() => setExpanded(expanded === key ? null : key)}>{expanded === key ? "收起" : "详情"}</button></span>{expanded === key && <div className="ide-model-governance__details" data-model-detail><p className="ide-settings__hint">{"\u5f53\u524d\u4ec5\u652f\u6301 TEXT \u771f\u5b9e\u63a2\u9488\uff1b\u4e0d\u4f1a\u81ea\u52a8\u5141\u8bb8\u4f7f\u7528\u3002"}</p><button type="button" className="ide-secondary-button" data-capability-probe="TEXT" disabled={probingKey !== null} onClick={() => void probeText(model)}>{probingKey === key ? "TEXT probe..." : "\u9a8c\u8bc1\u6587\u672c\u80fd\u529b\uff08\u53d1\u8d77\u771f\u5b9e\u8bf7\u6c42\uff09"}</button>{probeNotice?.key === key && <p role={probeNotice.success ? "status" : "alert"}>{probeNotice.message}</p>}<dl className="ide-model-governance__identity"><div><dt>Provider</dt><dd>{model.provider_display_name}（{model.provider_id}）</dd></div><div><dt>Connection</dt><dd>{model.connection_name ?? "未提供"} · {model.connection_id ?? "未提供"}</dd></div><div><dt>Endpoint</dt><dd>{model.endpoint_id}</dd></div><div><dt>Credential Ref</dt><dd>{model.credential_ref}</dd></div><div><dt>Protocol</dt><dd>{model.protocol_family}</dd></div><div><dt>Model Profile</dt><dd>{model.model_profile_id}</dd></div><div><dt>Model ID</dt><dd>{model.model_id}</dd></div><div><dt>Discovery Source</dt><dd>{model.record_provenance} · {model.metadata_source ?? "未提供"}</dd></div><div><dt>Discovery Time</dt><dd>{model.discovered_at ?? "未提供"}</dd></div><div><dt>Identity Status</dt><dd>{model.identity_status} · {identityReason(model)}</dd></div><div><dt>Verification</dt><dd>{model.verification_status} · {model.verified_at ?? "未验证"}</dd></div><div><dt>Availability</dt><dd>{model.availability_status}</dd></div><div><dt>Capability Evidence</dt><dd>{readiness.capabilities.map(capabilityLabel).join("、") || "未确认"}</dd></div><div><dt>Billing Provenance</dt><dd>{billingLabel(model.billing_class)} · {model.billing_provenance}{model.billing_source ? ` · ${model.billing_source}` : ""}</dd></div><div><dt>Credential Revision</dt><dd>{model.credential_revision ?? "未提供"}</dd></div><div><dt>Endpoint Revision</dt><dd>{model.endpoint_revision ?? "未提供"}</dd></div></dl></div>}</article>; })}</div></section>)}</div>}</section>}

    {tab === "project" && <section className="ide-model-governance__assignment" aria-labelledby="project-strategy-heading"><h4 id="project-strategy-heading">项目策略</h4>{!projectId && <p className="ide-settings__hint">需要当前工程才能配置项目策略。</p>}{projectId && <><p className="ide-settings__hint">当前项目：{projectName?.trim() || projectId}</p><details><summary>Project ID</summary>{projectId}</details>{projectCandidatesState === "LOADING" && <p role="status">项目候选加载中…</p>}{noProjectCandidates && <p role="status">当前没有同时满足已允许、已验证且身份有效条件的模型。</p>}<label>默认模型策略<select value={projectMode} disabled={projectCandidatesUnavailable} onChange={(event) => setProjectMode(event.target.value as GovernanceSelectionMode)} aria-label="项目默认模型策略"><option value="AUTO">{modeLabel("AUTO")}</option><option value="FIXED_MODEL">{modeLabel("FIXED_MODEL")}</option><option value="CANDIDATE_GROUP">{modeLabel("CANDIDATE_GROUP")}</option></select></label>{projectMode === "FIXED_MODEL" && !projectCandidatesUnavailable && <label>默认模型<select value={projectFixedModel} onChange={(event) => setProjectFixedModel(event.target.value)} aria-label="项目默认模型"><option value="">选择模型</option>{eligibleProjectModels.map((model) => <option key={modelIdentityKey(identity(model))} value={modelIdentityKey(identity(model))}>{candidateLabel(model)}</option>)}</select></label>}{projectMode === "CANDIDATE_GROUP" && !projectCandidatesUnavailable && <fieldset><legend>候选模型</legend>{eligibleProjectModels.map((model) => { const key = modelIdentityKey(identity(model)); return <label key={key}><input type="checkbox" checked={projectCandidateModels.includes(key)} onChange={(event) => setProjectCandidateModels((current) => event.target.checked ? [...current, key] : current.filter((item) => item !== key))} /> {candidateLabel(model)}</label>; })}</fieldset>}<button type="button" className="ide-primary-button" disabled={saving || projectCandidatesUnavailable || (projectMode === "FIXED_MODEL" && !projectFixedModel) || (projectMode === "CANDIDATE_GROUP" && projectCandidateModels.length < 2)} onClick={() => void saveProject()}>{saving ? "保存中…" : "保存项目策略"}</button>{projectDefault && <small className="ide-settings__hint">已保存：{modeLabel(projectDefault.selection_mode)}</small>}</>}</section>}

    {tab === "stage" && <section className="ide-model-governance__assignment" aria-labelledby="stage-strategy-heading"><h4 id="stage-strategy-heading">阶段策略</h4>{!projectId && <p className="ide-settings__hint">需要当前工程才能配置阶段策略。</p>}{projectId && <><label>阶段<select value={stageId} onChange={(event) => setStageId(event.target.value as GovernanceStageId)} aria-label="编辑阶段策略"><option value="">选择阶段</option>{stages.map((stage) => <option key={stage.stage_id} value={stage.stage_id}>{stageLabel(stage)}</option>)}</select></label><p className="ide-settings__hint">要求能力：{selectedStage?.required_capabilities.map(capabilityLabel).join(" · ") || "未提供"}</p>{stageCandidatesState === "LOADING" && <p role="status">阶段候选加载中…</p>}{noStageCandidates && <p role="status">当前没有同时满足以下条件的模型：{selectedStage?.required_capabilities.map(capabilityLabel).join("、") || "阶段要求能力"}、已验证、允许使用。</p>}<label>模式<select value={mode} disabled={stageCandidatesUnavailable} onChange={(event) => setMode(event.target.value as GovernanceSelectionMode)} aria-label="阶段策略模式"><option value="AUTO">{modeLabel("AUTO")}</option><option value="FIXED_MODEL">{modeLabel("FIXED_MODEL")}</option><option value="CANDIDATE_GROUP">{modeLabel("CANDIDATE_GROUP")}</option></select></label>{selectedStage && !stageCandidatesUnavailable && <div className="ide-settings__hint"><strong>为什么只有这些模型？</strong><ul>{selectedStage.required_capabilities.map((capability) => <li key={capability}>✓ 支持{capabilityLabel(capability)}（后端候选 gate）</li>)}<li>✓ 已验证且已允许（后端候选 gate）</li></ul></div>}{mode === "FIXED_MODEL" && !stageCandidatesUnavailable && <label>模型<select value={fixedModel} onChange={(event) => setFixedModel(event.target.value)} aria-label="阶段固定模型"><option value="">选择模型</option>{eligibleStageModels.map((model) => <option key={modelIdentityKey(identity(model))} value={modelIdentityKey(identity(model))}>{candidateLabel(model)}</option>)}</select></label>}{mode === "CANDIDATE_GROUP" && !stageCandidatesUnavailable && <fieldset><legend>候选模型</legend>{eligibleStageModels.map((model) => { const key = modelIdentityKey(identity(model)); return <label key={key}><input type="checkbox" checked={candidateModels.includes(key)} onChange={(event) => setCandidateModels((current) => event.target.checked ? [...current, key] : current.filter((item) => item !== key))} /> {candidateLabel(model)}{model.capability_warning ? ` · 缺少${capabilityWarningLabel(model.capability_warning)}` : ""}</label>; })}</fieldset>}<button type="button" className="ide-primary-button" disabled={saving || stageCandidatesUnavailable || (mode === "FIXED_MODEL" && !fixedModel) || (mode === "CANDIDATE_GROUP" && candidateModels.length < 2)} onClick={() => void saveStage()}>{saving ? "保存中…" : "保存阶段策略"}</button></>}</section>}
  </div>;
}
