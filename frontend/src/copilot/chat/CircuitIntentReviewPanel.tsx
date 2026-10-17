import { useEffect, useState } from "react";

import { fetchExecutionStatus, type ExecutionOperationStatus } from "../api/executionClient";
import {
  decideCircuitIntent, decidePredesignCircuitIntent, previewCircuitIntent, previewPredesignCircuitIntent,
  type CircuitIntentDecision, type CircuitIntentPreview, type CircuitIntentSpecInput,
  type PredesignCircuitDecision, type PredesignCircuitPreview, type PredesignSourceExpectations,
} from "../api/projectClient";
import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";

type Turn = { turn_id: string; route?: string | null; status: string; question: string };
type Props = {
  mode: "PREDESIGN_APPROVAL" | "ERC_PUBLICATION";
  projectId: string;
  conversationId: string;
  ercOperationId?: string;
  predesignSourceExpectations?: PredesignSourceExpectations | null;
  approvedPredesignSources?: {
    requirements: Array<{ requirement_id: string; label: string }>;
    architectureComponents: Array<{ component_id: string; label: string }>;
  } | null;
  turns: Turn[];
  projection: DesktopWorkspaceProjection | null;
  busy: boolean;
  onOwnerRefresh: () => Promise<void>;
};

type ReviewPreview = CircuitIntentPreview | PredesignCircuitPreview;
type ReviewDecision = CircuitIntentDecision | PredesignCircuitDecision;
const lines = (value: string) => value.split("\n").map((line) => line.trim()).filter(Boolean);

export function CircuitIntentReviewPanel({ mode, projectId, conversationId, ercOperationId, predesignSourceExpectations, approvedPredesignSources, turns, projection, busy, onOwnerRefresh }: Props) {
  const predesign = mode === "PREDESIGN_APPROVAL";
  const designTurns = turns.filter((turn) => turn.route === "GREENFIELD_DESIGN" && turn.status === "COMPLETED");
  const [turnId, setTurnId] = useState("");
  const [objectId, setObjectId] = useState("");
  const [operation, setOperation] = useState<ExecutionOperationStatus | null>(null);
  const [functionText, setFunctionText] = useState("");
  const [topology, setTopology] = useState("");
  const [parameters, setParameters] = useState("");
  const [calculation, setCalculation] = useState("");
  const [assumptions, setAssumptions] = useState("");
  const [risks, setRisks] = useState("");
  const [blocks, setBlocks] = useState<Array<{ block_id: string; function: string; requirement_refs: string[]; architecture_refs: string[] }>>([{ block_id: "", function: "", requirement_refs: [], architecture_refs: [] }]);
  const [claims, setClaims] = useState<Array<{ verification_id: string; claim: string; verification_type: string; method: string; owner_ref: string }>>([{ verification_id: "", claim: "", verification_type: "", method: "", owner_ref: "" }]);
  const [preview, setPreview] = useState<ReviewPreview | null>(null);
  const [decisionResult, setDecisionResult] = useState<ReviewDecision | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ercId = ercOperationId ?? "";

  useEffect(() => {
    setTurnId(predesign ? "" : designTurns.at(-1)?.turn_id ?? "");
    setOperation(null);
    setPreview(null);
    setDecisionResult(null);
    setError(null);
    if (predesign || !ercId) return;
    let active = true;
    void fetchExecutionStatus(projectId, ercId).then((value) => {
      if (active) setOperation(value);
    }).catch(() => {
      if (active) setError("ERC_OPERATION_UNAVAILABLE");
    });
    return () => { active = false; };
  }, [projectId, conversationId, ercId, predesign]);

  const operationCurrent = Boolean(ercId) && operation?.operation_id === ercId
    && operation.project_id === projectId && operation.operation_type === "KICAD_SCHEMATIC_ERC"
    && operation.status === "SUCCEEDED" && operation.recovery_state === "NONE"
    && Boolean(operation.receipt_fingerprint);
  const revisionTargets = (projection?.authoritative_facts ?? []).filter((item) =>
    item.domain === "CIRCUIT" && item.project_id === projectId
    && item.currentness === "CURRENT" && item.object_id && item.revision,
  );
  const publishedFacts = (projection?.authoritative_facts ?? []).filter((item) =>
    item.domain === "CIRCUIT" && item.project_id === projectId
    && (item.conversation_id == null || item.conversation_id === conversationId)
    && item.object_id && item.revision,
  );
  const fact = preview && projection?.authoritative_facts?.find((item) =>
    item.domain === "CIRCUIT" && item.project_id === projectId
    && item.object_id === preview.object_id && item.revision === preview.revision,
  );
  const hasSourceExpectations = Boolean(predesignSourceExpectations
    && Object.values(predesignSourceExpectations).every((value) => typeof value === "string" && value.trim()));
  const normalizedBlocks = blocks.map((item) => ({ block_id: item.block_id.trim(), function: item.function.trim() }));
  const normalizedClaims = claims.map((item) => ({ verification_id: item.verification_id.trim(), claim: item.claim.trim(), verification_type: item.verification_type.trim(), method: item.method.trim(), owner_ref: item.owner_ref.trim() }));
  const validRequirementIds = new Set(approvedPredesignSources?.requirements.map((item) => item.requirement_id) ?? []);
  const validArchitectureIds = new Set(approvedPredesignSources?.architectureComponents.map((item) => item.component_id) ?? []);
  const blockIdsUnique = new Set(normalizedBlocks.map((item) => item.block_id)).size === normalizedBlocks.length;
  const hasBlockSources = blocks.every((block) => block.requirement_refs.length > 0 && block.architecture_refs.length > 0
    && block.requirement_refs.every((id) => validRequirementIds.has(id))
    && block.architecture_refs.every((id) => validArchitectureIds.has(id)));
  const hasBlockAndClaim = normalizedBlocks.length > 0 && blockIdsUnique && normalizedClaims.length > 0 && hasBlockSources
    && normalizedBlocks.every((item) => item.block_id && item.function)
    && normalizedClaims.every((item) => item.verification_id && item.claim && item.verification_type && item.method && normalizedBlocks.some((block) => block.block_id === item.owner_ref))
    && normalizedBlocks.every((block) => normalizedClaims.some((claim) => claim.owner_ref === block.block_id));
  const mappedBlocks = normalizedBlocks.map((block) => ({
    ...block,
    requirement_refs: blocks.find((item) => item.block_id.trim() === block.block_id)?.requirement_refs ?? [],
    architecture_refs: blocks.find((item) => item.block_id.trim() === block.block_id)?.architecture_refs ?? [],
    verification_refs: normalizedClaims.filter((claim) => claim.owner_ref === block.block_id).map((claim) => claim.verification_id), status: "DRAFT" as const,
  }));
  const mappedClaims = normalizedClaims.map((claim) => ({
    ...claim, status: "PLANNED" as const, produced_evidence_refs: [] as [],
  }));
  const hasTarget = predesign ? Boolean(objectId) : true;
  const spec: CircuitIntentSpecInput = {
    function: functionText.trim(), topology: lines(topology), parameters: lines(parameters),
    calculation: lines(calculation), assumptions: lines(assumptions), risks: lines(risks),
    ...(predesign && hasBlockAndClaim ? {
      blocks: mappedBlocks,
      verification_requirements: mappedClaims,
    } : {}),
  };
  const canPrepare = Boolean(turnId && spec.function && (!predesign || (hasSourceExpectations && hasBlockAndClaim && hasTarget))
    && (predesign || operationCurrent));

  async function prepare() {
    if (!canPrepare || pending || busy) return;
    setPending(true);
    setError(null);
    setPreview(null);
    setDecisionResult(null);
    try {
      if (predesign && predesignSourceExpectations) {
        const result = await previewPredesignCircuitIntent(projectId, {
          purpose: "PREDESIGN_APPROVAL", conversation_id: conversationId, turn_id: turnId,
          circuit_intent: spec, source_expectations: predesignSourceExpectations,
          ...(objectId && objectId !== "NEW" ? { object_id: objectId, base_revision: revisionTargets.find((item) => item.object_id === objectId)?.revision, base_fingerprint: revisionTargets.find((item) => item.object_id === objectId)?.fingerprint } : {}),
          idempotency_key: crypto.randomUUID(),
        });
        if (result.project_id !== projectId || result.conversation_id !== conversationId || result.turn_id !== turnId
          || result.purpose !== "PREDESIGN_APPROVAL" || result.completion !== "DESIGN_ONLY") throw new Error("PREDESIGN_PREVIEW_IDENTITY_MISMATCH");
        setPreview(result);
      } else if (!predesign && ercId) {
        const result = await previewCircuitIntent(projectId, {
          conversation_id: conversationId, turn_id: turnId, circuit_intent: spec, erc_operation_id: ercId,
          ...(objectId ? { object_id: objectId } : {}), idempotency_key: crypto.randomUUID(),
        });
        if (result.project_id !== projectId || result.conversation_id !== conversationId || result.turn_id !== turnId || result.evidence_receipt_id !== ercId) throw new Error("CIRCUIT_PREVIEW_IDENTITY_MISMATCH");
        setPreview(result);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "CIRCUIT_PREVIEW_UNAVAILABLE");
    } finally {
      setPending(false);
    }
  }

  async function decide(decision: "APPROVE" | "REJECT") {
    if (!preview || decisionResult || pending || busy) return;
    setPending(true);
    setError(null);
    try {
      const result = predesign
        ? await decidePredesignCircuitIntent(projectId, {
          purpose: "PREDESIGN_APPROVAL", proposal_id: preview.proposal_id,
          proposal_fingerprint: preview.proposal_fingerprint, source_fingerprint: preview.source_fingerprint,
          base_revision: preview.base_revision, base_fingerprint: preview.base_fingerprint, decision,
        })
        : await decideCircuitIntent(projectId, {
          proposal_id: preview.proposal_id, proposal_fingerprint: preview.proposal_fingerprint,
          base_revision: preview.base_revision, base_fingerprint: preview.base_fingerprint,
          context_fingerprint: (preview as CircuitIntentPreview).context_fingerprint,
          evidence_receipt_fingerprint: (preview as CircuitIntentPreview).evidence_receipt_fingerprint, decision,
        });
      setDecisionResult(result);
      await onOwnerRefresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "CIRCUIT_DECISION_UNAVAILABLE");
    } finally {
      setPending(false);
    }
  }

  async function refreshOwner() {
    if (pending || busy) return;
    setPending(true);
    setError(null);
    try { await onOwnerRefresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "CIRCUIT_OWNER_REFRESH_UNAVAILABLE"); }
    finally { setPending(false); }
  }

  if (!predesign && !designTurns.length) return null;
  return <section aria-label="Circuit Intent 审核" className="ide-review-card" data-review-mode={mode}>
    <h3>Circuit Intent 审核</h3>
    {predesign ? <><p>审核模式：PREDESIGN_APPROVAL · DESIGN_ONLY</p>
      <p>仅已批准且可解析的约束可用于设计；通过性 claim 必须独立验证。来源缺失或未批准的约束应保持未决。</p>
    </> : <><p>ERC Operation：{ercId}</p><p>当前操作：{operationCurrent ? "已完成，Receipt 可读取" : "不可审核：ERC 状态或身份尚未确认"}</p></>}
    <section aria-label="已发布 Circuit">
      <h4>已发布 Circuit（Owner 重读）</h4><button type="button" disabled={pending || busy} onClick={() => void refreshOwner()}>从 Owner 重新读取</button>
      {publishedFacts.map((item) => <p key={`${item.object_id}:${item.revision}`}>{item.object_id} · {item.revision} · {item.currentness} · {item.reason_code}<br />Owner：{item.owner} · 来源：{item.source_reference ?? "SOURCE_UNAVAILABLE"}<br />Circuit 指纹：{item.fingerprint ?? "UNAVAILABLE"} · 来源指纹：{item.source_fingerprint ?? "UNAVAILABLE"}</p>)}
      {!publishedFacts.length && <p>暂无可显示的已发布 Circuit</p>}
    </section>
    <label>发布目标<select value={objectId} disabled={pending || busy || Boolean(preview)} onChange={(event) => setObjectId(event.target.value)}>{predesign && <option value="">请选择发布目标</option>}<option value={predesign ? "NEW" : ""}>{predesign ? "新建 Circuit" : "新建 Circuit"}</option>{revisionTargets.map((item) => <option key={item.object_id} value={item.object_id ?? ""}>{item.object_id} · {item.revision}</option>)}</select></label>
    <label>来源设计回合<select value={turnId} disabled={pending || busy || Boolean(preview)} onChange={(event) => setTurnId(event.target.value)}><option value="">请选择来源回合</option>{designTurns.map((turn) => <option key={turn.turn_id} value={turn.turn_id}>{turn.question}</option>)}</select></label>
    <label>电路功能<textarea value={functionText} disabled={pending || busy || Boolean(preview)} onChange={(event) => setFunctionText(event.target.value)} /></label>
    <label>拓扑（每行一项）<textarea value={topology} disabled={pending || busy || Boolean(preview)} onChange={(event) => setTopology(event.target.value)} /></label>
    <label>参数（每行一项）<textarea value={parameters} disabled={pending || busy || Boolean(preview)} onChange={(event) => setParameters(event.target.value)} /></label>
    <label>计算（每行一项）<textarea value={calculation} disabled={pending || busy || Boolean(preview)} onChange={(event) => setCalculation(event.target.value)} /></label>
    {predesign && <p>计算：UNKNOWN / 待证。逐项来源尚未建立，提交的计算文本不作为验证方法或已证据支持的结论。</p>}
    <label>假设（每行一项）<textarea value={assumptions} disabled={pending || busy || Boolean(preview)} onChange={(event) => setAssumptions(event.target.value)} /></label>
    <label>风险（每行一项）<textarea value={risks} disabled={pending || busy || Boolean(preview)} onChange={(event) => setRisks(event.target.value)} /></label>
    {predesign && <fieldset disabled={pending || busy || Boolean(preview)}><legend>设计 block 与验证 claim（用户输入）</legend>
      {blocks.map((block, index) => <div key={`block-${index}`} aria-label={`设计 block ${index + 1}`}>
        <label>Block ID<input value={block.block_id} onChange={(event) => setBlocks((current) => current.map((item, row) => row === index ? { ...item, block_id: event.target.value } : item))} /></label>
        <label>Block 功能<input value={block.function} onChange={(event) => setBlocks((current) => current.map((item, row) => row === index ? { ...item, function: event.target.value } : item))} /></label>
        <button type="button" disabled={blocks.length <= 1} onClick={() => setBlocks((current) => current.filter((_, row) => row !== index))}>删除 Block</button>
      </div>)}
      <button type="button" onClick={() => setBlocks((current) => [...current, { block_id: "", function: "", requirement_refs: [], architecture_refs: [] }])}>添加 Block</button>
      {claims.map((claim, index) => <div key={`claim-${index}`} aria-label={`验证 claim ${index + 1}`}>
        <label>Claim ID<input value={claim.verification_id} onChange={(event) => setClaims((current) => current.map((item, row) => row === index ? { ...item, verification_id: event.target.value } : item))} /></label>
        <label>待验证 claim<input value={claim.claim} onChange={(event) => setClaims((current) => current.map((item, row) => row === index ? { ...item, claim: event.target.value } : item))} /></label>
        <label>验证类型<input value={claim.verification_type} onChange={(event) => setClaims((current) => current.map((item, row) => row === index ? { ...item, verification_type: event.target.value } : item))} /></label>
        <label>验证方法<input value={claim.method} onChange={(event) => setClaims((current) => current.map((item, row) => row === index ? { ...item, method: event.target.value } : item))} /></label>
        <label>所属 Block<select value={claim.owner_ref} onChange={(event) => setClaims((current) => current.map((item, row) => row === index ? { ...item, owner_ref: event.target.value } : item))}><option value="">请选择 Block</option>{normalizedBlocks.filter((item) => item.block_id).map((item) => <option key={item.block_id} value={item.block_id}>{item.block_id}</option>)}</select></label>
        <button type="button" disabled={claims.length <= 1} onClick={() => setClaims((current) => current.filter((_, row) => row !== index))}>删除 Claim</button>
      </div>)}
      <button type="button" onClick={() => setClaims((current) => [...current, { verification_id: "", claim: "", verification_type: "", method: "", owner_ref: "" }])}>添加 Claim</button>
      {blocks.map((block, index) => <div key={`block-sources-${index}`} aria-label={`Block ${block.block_id || index + 1} 来源`}>
        <p>{block.block_id || `Block ${index + 1}`} 的 Requirement 来源</p>
        {(approvedPredesignSources?.requirements ?? []).map((source) => <label key={source.requirement_id}>
          <input type="checkbox" aria-label={`Requirement ${source.requirement_id} for ${block.block_id || index + 1}`} checked={block.requirement_refs.includes(source.requirement_id)} onChange={(event) => setBlocks((current) => current.map((item, row) => row === index ? { ...item, requirement_refs: event.target.checked ? [...item.requirement_refs, source.requirement_id] : item.requirement_refs.filter((id) => id !== source.requirement_id) } : item))} />
          {source.label} · {source.requirement_id}
        </label>)}
        <p>{block.block_id || `Block ${index + 1}`} 的 Architecture 来源</p>
        {(approvedPredesignSources?.architectureComponents ?? []).map((source) => <label key={source.component_id}>
          <input type="checkbox" aria-label={`Architecture ${source.component_id} for ${block.block_id || index + 1}`} checked={block.architecture_refs.includes(source.component_id)} onChange={(event) => setBlocks((current) => current.map((item, row) => row === index ? { ...item, architecture_refs: event.target.checked ? [...item.architecture_refs, source.component_id] : item.architecture_refs.filter((id) => id !== source.component_id) } : item))} />
          {source.label} · {source.component_id}
        </label>)}
      </div>)}
    </fieldset>}
    {!preview && <button type="button" disabled={!canPrepare || pending || busy} onClick={() => void prepare()}>生成审核预览</button>}
    {predesign && !hasSourceExpectations && <p role="status">Owner 已批准来源快照缺失，预设计预览不可用。</p>}
    {predesign && !hasBlockAndClaim && <p role="status">请显式填写至少一个设计 block 和一个待验证 claim。</p>}
    {predesign && (!(approvedPredesignSources?.requirements.length) || !(approvedPredesignSources?.architectureComponents.length)) && <p role="status">Owner 没有提供已批准的 Requirement 与 Architecture 来源选项，预览不可用。</p>}
    {predesign && !hasTarget && <p role="status">请显式选择新建 Circuit 或现有修订目标。</p>}
    {preview && <div role="group" aria-label="Circuit 审核候选">
      {predesign && <p>DESIGN_ONLY：仅表示设计提案已审核，不代表已生成原理图、已执行验证或硬件通过。</p>}
      <p>目标：{preview.object_id} · 版次：{preview.revision} · base：{preview.base_revision ?? "新建"} · {preview.base_fingerprint ?? "无"}</p>
      <p>来源：{preview.conversation_id} / {preview.turn_id} · {preview.source_fingerprint}</p>
      {predesign && <><p>来源快照：</p><pre aria-label="来源快照">{JSON.stringify((preview as PredesignCircuitPreview).source_snapshot, null, 2)}</pre><p>未决 claim：{(preview.circuit_intent.verification_requirements ?? []).map((claim) => claim.claim).join("；") || "无 claim，审核不可完成"}</p></>}
      {!predesign && <><p>上下文指纹：{(preview as CircuitIntentPreview).context_fingerprint}</p><p>ERC Receipt：{(preview as CircuitIntentPreview).evidence_receipt_id} · {(preview as CircuitIntentPreview).evidence_receipt_fingerprint}</p></>}
      <p>Proposal：{preview.proposal_id} · {preview.proposal_fingerprint}</p><p>待批准 Circuit 规格：</p><pre aria-label="待批准 Circuit 规格">{JSON.stringify(preview.circuit_intent, null, 2)}</pre>
      {!decisionResult && <><button type="button" disabled={pending || busy} onClick={() => void decide("APPROVE")}>{predesign ? "批准设计提案" : "确认并发布"}</button><button type="button" disabled={pending || busy} onClick={() => void decide("REJECT")}>拒绝</button><button type="button" disabled={pending || busy} onClick={() => { setPreview(null); setError(null); }}>重新预览</button></>}
      {decisionResult?.status === "APPLIED" && predesign && <p role="status">设计提案已批准（DESIGN_ONLY）：{decisionResult.object_id} · {decisionResult.revision}。未触发 Worker 或原理图验证。</p>}
      {decisionResult?.status === "APPLIED" && !predesign && <p role="status">发布已提交：{decisionResult.object_id} · {decisionResult.revision}。Owner 状态：{fact?.currentness ?? "UNAVAILABLE"}{fact ? ` · ${fact.reason_code}` : " · 未读取到已发布 Circuit"}</p>}
      {decisionResult?.status === "REJECTED" && <p role="status">已拒绝该候选，未发布新修订。</p>}
    </div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
