import { useEffect, useRef, useState } from "react";
import { approveExecution, cancelExecution, fetchExecutionStatus, type ExecutionOperationStatus } from "../api/executionClient";
import { createSchematicEvidenceOperation, createLibraryReadbackOperation, fetchLibraryTerminalMaps, type EvidenceView, type LibraryTerminalMapInput, proposeSchematicParameter, readSchematicParameter, recordSchematicVerification, reviewSchematicParameter, type ConversationDetail, type SchematicEvidenceOperation, type SchematicParameterCandidate, type SchematicParameterView, type SchematicVerificationView } from "../api/projectClient";
import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";

type Props = { conversation: ConversationDetail; projection?: DesktopWorkspaceProjection | null; disabled?: boolean; onRefresh: () => Promise<void> };
type Kind = "PARSE" | "READBACK";
const labels: Record<Kind, string> = { PARSE: "Parse", READBACK: "Read-back" };
const units: Record<SchematicParameterCandidate["parameter_key"], SchematicParameterCandidate["unit"]> = {
  SUPPLY_MIN: "V", SUPPLY_MAX: "V", PULLUP_RESISTANCE: "ohm", PULLUP_TOLERANCE: "ratio", BUS_CAPACITANCE_MAX: "F", RISE_TIME_MAX: "s", BUS_FREQUENCY_MAX: "Hz", DECOUPLING_NOMINAL: "F", DECOUPLING_EFFECTIVE_MIN: "F", VOL_MAX: "V", VIL_MAX: "V", IOL_MIN: "A", LEAKAGE_MAX: "A",
};
const fields = [
  ["document_uri", "资料地址"], ["document_revision", "资料修订"], ["content_sha256", "原件 SHA-256"],
  ["page", "页码"], ["table", "表格"], ["row", "行"], ["section", "章节"], ["clause", "条款"],
  ["value_decimal", "参数值"], ["voltage_min", "适用电压下限 V"], ["voltage_max", "适用电压上限 V"],
  ["temperature_min", "适用温度下限 °C"], ["temperature_max", "适用温度上限 °C"],
] as const;
export function SchematicEvidencePanel({ conversation, projection, disabled = false, onRefresh }: Props) {
  const scope = JSON.stringify([conversation.project_id, conversation.conversation_id, conversation.fingerprint,
    conversation.engineering_plan?.fingerprint, conversation.hardware_selection?.proposal?.fingerprint,
    conversation.design_truth?.fingerprint, conversation.schematic_planning?.proposal?.fingerprint,
    conversation.schematic_planning?.approved_plan?.fingerprint, conversation.schematic_production?.generation_receipt?.fingerprint,
    projection?.identity.tenant_id, projection?.identity.project_id, projection?.projection_fingerprint]);
  const live = useRef({ mounted: true, scope, disabled, generation: 0 });
  if (live.current.scope !== scope || live.current.disabled !== disabled) live.current.generation += 1;
  live.current.scope = scope; live.current.disabled = disabled;
  const requestGeneration = useRef(live.current.generation);
  useEffect(() => {
    live.current.mounted = true;
    return () => { live.current.mounted = false; live.current.generation += 1; };
  }, []);
  const isCurrent = () => live.current.mounted && !live.current.disabled && live.current.scope === scope && live.current.generation === requestGeneration.current;
  function requireCurrent() { if (!isCurrent()) throw new Error("EVIDENCE_SCOPE_CHANGED"); }
  async function currentRequest<T>(work: () => Promise<T>): Promise<T> {
    requireCurrent();
    const result = await work();
    requireCurrent();
    return result;
  }
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [operations, setOperations] = useState<Partial<Record<Kind, SchematicEvidenceOperation>>>({});
  const [statuses, setStatuses] = useState<Partial<Record<Kind, ExecutionOperationStatus>>>({});
  const [libraryMaps, setLibraryMaps] = useState<EvidenceView<LibraryTerminalMapInput>[]>([]);
  const [libraryMapId, setLibraryMapId] = useState("");
  const [verification, setVerification] = useState<SchematicVerificationView | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [parameterKey, setParameterKey] = useState<SchematicParameterCandidate["parameter_key"]>("SUPPLY_MIN");
  const [bound, setBound] = useState<SchematicParameterCandidate["bound_kind"]>("MIN");
  const [mode, setMode] = useState<SchematicParameterCandidate["conditions"]["mode"]>("UNKNOWN");
  const [candidate, setCandidate] = useState<(SchematicParameterCandidate & { fingerprint: string }) | null>(null);
  const [decision, setDecision] = useState<string | null>(null);
  const [evidenceId, setEvidenceId] = useState("");
  const [parameter, setParameter] = useState<SchematicParameterView | null>(null);
  const [currentParameterIds, setCurrentParameterIds] = useState<string[]>([]);
  const [ercId, setErcId] = useState("");
  const [ercStatus, setErcStatus] = useState<ExecutionOperationStatus | null>(null);
  const [ercSourceChecked, setErcSourceChecked] = useState(false);
  const production = conversation.schematic_production;
  const plan = conversation.schematic_planning?.status === "APPROVED" ? conversation.schematic_planning.approved_plan : null;
  const generation = production?.generation_receipt;
  const sources = conversation.hardware_selection?.status === "APPROVED" ? conversation.hardware_selection.proposal?.selected_components ?? [] : [];
  const ready = Boolean(production?.status === "GENERATED" && plan && generation && production?.operation_id && plan.project_id === conversation.project_id && plan.conversation_id === conversation.conversation_id && plan.fingerprint === generation.schematic_plan_fingerprint);
  async function action(work: () => Promise<void>) {
    if (!live.current.mounted || live.current.disabled || live.current.scope !== scope || lock.current) return;
    requestGeneration.current = live.current.generation;
    lock.current = true; setBusy(true); setError(null);
    try { await work(); requireCurrent(); } catch (failure) {
      const code = failure instanceof Error && /^(OPERATION_SCOPE_MISMATCH|OPERATION_NOT_APPROVABLE|PARAMETER_SCOPE_MISMATCH|VERIFICATION_SCOPE_MISMATCH)$/.test(failure.message) ? failure.message : "SCHEMATIC_EVIDENCE_UNAVAILABLE";
      if (isCurrent()) setError(code);
    } finally { lock.current = false; if (live.current.mounted) setBusy(false); }
  }
  function checkStatus(op: SchematicEvidenceOperation, status: ExecutionOperationStatus) {
    if (status.project_id !== conversation.project_id || status.tenant_id !== op.tenant_id || status.operation_id !== op.operation_id || status.operation_type !== op.operation_type) throw new Error("OPERATION_SCOPE_MISMATCH");
  }
  async function create(kind: Kind) {
    let selectedMap: EvidenceView<LibraryTerminalMapInput> | undefined;
    if (kind === "READBACK" && libraryMapId) {
      const previous = libraryMaps.find((item) => item.evidence.evidence_id === libraryMapId);
      const current = await currentRequest(() => (fetchLibraryTerminalMaps(conversation.project_id, conversation.conversation_id)));
      selectedMap = current.find((item) => item.evidence.evidence_id === libraryMapId && item.evidence.fingerprint === previous?.evidence.fingerprint);
      if (!selectedMap || selectedMap.currentness !== "APPROVED" || selectedMap.review?.decision !== "APPROVED"
        || selectedMap.review.evidence_fingerprint !== selectedMap.evidence.fingerprint
        || selectedMap.evidence.project_id !== conversation.project_id || selectedMap.evidence.conversation_id !== conversation.conversation_id
        || selectedMap.evidence.tenant_id !== projection?.identity.tenant_id
        || selectedMap.evidence.plan_id !== plan?.plan_id || selectedMap.evidence.plan_fingerprint !== plan?.fingerprint) throw new Error("OPERATION_SCOPE_MISMATCH");
    }
    const op = selectedMap
      ? await currentRequest(() => (createLibraryReadbackOperation(conversation.project_id, conversation.conversation_id, selectedMap.evidence.evidence_id, selectedMap.evidence.fingerprint)))
      : await currentRequest(() => (createSchematicEvidenceOperation(conversation.project_id, conversation.conversation_id, kind)));
    if (selectedMap && (op.library_map_evidence_id !== selectedMap.evidence.evidence_id || op.library_map_evidence_fingerprint !== selectedMap.evidence.fingerprint)) throw new Error("OPERATION_SCOPE_MISMATCH");
    if (op.project_id !== conversation.project_id || op.conversation_id !== conversation.conversation_id || op.operation_type !== (kind === "PARSE" ? "KICAD_SCHEMATIC_PARSE" : "KICAD_SCHEMATIC_READ_BACK")) throw new Error("OPERATION_SCOPE_MISMATCH");
    setOperations((current) => ({ ...current, [kind]: op }));
    const status = await currentRequest(() => (fetchExecutionStatus(conversation.project_id, op.operation_id)));
    checkStatus(op, status); setStatuses((current) => ({ ...current, [kind]: status }));
  }
  async function refreshStatus(kind: Kind) {
    const op = operations[kind]; if (!op) return;
    const status = await currentRequest(() => (fetchExecutionStatus(conversation.project_id, op.operation_id)));
    checkStatus(op, status); setStatuses((current) => ({ ...current, [kind]: status }));
    await currentRequest(() => (onRefresh()));
  }
  async function approve(kind: Kind) {
    const op = operations[kind]; if (!op) return;
    const status = await currentRequest(() => (fetchExecutionStatus(conversation.project_id, op.operation_id)));
    checkStatus(op, status);
    const expires = Math.min(Date.parse(op.expires_at), Date.now() + 5 * 60 * 1000);
    if (status.status !== "WAIT_APPROVAL" || status.recovery_state !== "NONE" || !Number.isFinite(expires) || expires <= Date.now()) throw new Error("OPERATION_NOT_APPROVABLE");
    await currentRequest(() => (approveExecution(conversation.project_id, op.operation_id, op.requester_id, globalThis.crypto.randomUUID(), new Date(expires).toISOString())));
    await currentRequest(() => (refreshStatus(kind)));
  }
  async function submitParameter() {
    const source = sources.find((item) => item.source.source_id === sourceId)?.source;
    const operation = operations.PARSE ?? operations.READBACK;
    if (!source || !operation) throw new Error("PARAMETER_SCOPE_MISMATCH");
    setCandidate(null); setDecision(null); setParameter(null);
    const value = await currentRequest(() => (proposeSchematicParameter(conversation.project_id, conversation.conversation_id, {
      evidence_id: `parameter-${globalThis.crypto.randomUUID()}`, tenant_id: operation.tenant_id,
      project_id: conversation.project_id, conversation_id: conversation.conversation_id,
      source_record_id: source.source_id, source_record_fingerprint: source.fingerprint,
      manufacturer: source.manufacturer, mpn: source.mpn, package: source.package,
      document_uri: draft.document_uri ?? "", document_revision: draft.document_revision ?? "", content_sha256: draft.content_sha256 ?? "",
      locator: { page: draft.page ? Number(draft.page) : null, table: draft.table || null, row: draft.row || null, section: draft.section || null, clause: draft.clause || null },
      parameter_key: parameterKey, bound_kind: bound, value_decimal: draft.value_decimal ?? "", unit: units[parameterKey],
      conditions: { voltage_min: draft.voltage_min || null, voltage_max: draft.voltage_max || null, temperature_min: draft.temperature_min || null, temperature_max: draft.temperature_max || null, mode, package: source.package },
    })));
    if (value.project_id !== conversation.project_id || value.conversation_id !== conversation.conversation_id || value.tenant_id !== operation.tenant_id) throw new Error("PARAMETER_SCOPE_MISMATCH");
    setCandidate(value); setEvidenceId(value.evidence_id);
  }
  async function readParameter() {
    setParameter(null);
    setCurrentParameterIds((ids) => ids.filter((id) => id !== evidenceId.trim()));
    const value = await currentRequest(() => (readSchematicParameter(conversation.project_id, evidenceId.trim())));
    const found = value.evidence.candidate;
    if (found.project_id !== conversation.project_id || found.conversation_id !== conversation.conversation_id || found.evidence_id !== evidenceId.trim() || value.evidence.review.decision !== "APPROVED") throw new Error("PARAMETER_SCOPE_MISMATCH");
    setParameter(value);
    setCurrentParameterIds((ids) => value.currentness === "CURRENT" ? [...new Set([...ids, found.evidence_id])] : ids.filter((id) => id !== found.evidence_id));
  }
  async function review(value: "APPROVED" | "REJECTED") {
    if (!candidate || decision) return;
    const result = await currentRequest(() => (reviewSchematicParameter(conversation.project_id, candidate.evidence_id, candidate.fingerprint, value)));
    if (result.decision !== value) throw new Error("PARAMETER_SCOPE_MISMATCH");
    setDecision(result.decision);
  }
  async function record() {
    if (!plan || !generation || !production?.operation_id || !plan.circuit_intent_id || !plan.circuit_intent_revision || !plan.circuit_intent_fingerprint) throw new Error("VERIFICATION_SCOPE_MISMATCH");
    const { circuit_intent_id, circuit_intent_revision, circuit_intent_fingerprint } = plan;
    const generationOperationId = production.operation_id;
    setVerification(null);
    const result = await currentRequest(() => (recordSchematicVerification(conversation.project_id, conversation.conversation_id, {
      intent_id: circuit_intent_id, intent_revision: circuit_intent_revision, intent_fingerprint: circuit_intent_fingerprint,
      plan_id: plan.plan_id, plan_fingerprint: plan.fingerprint, artifact_fingerprint: generation.artifact_fingerprint,
      generation_operation_id: generationOperationId, parse_operation_id: operations.PARSE?.operation_id ?? null,
      readback_operation_id: operations.READBACK?.operation_id ?? null, erc_operation_id: ercStatus?.operation_type === "KICAD_SCHEMATIC_ERC" ? ercStatus.operation_id : production.erc_receipt?.operation_id ?? null,
      parameter_evidence_ids: currentParameterIds,
      ...(operations.READBACK?.library_map_evidence_id && operations.READBACK.library_map_evidence_fingerprint ? {
        library_map_evidence_ids: [operations.READBACK.library_map_evidence_id],
        library_map_evidence_fingerprints: [operations.READBACK.library_map_evidence_fingerprint],
      } : {}),
    })));
    if (result.record.project_id !== conversation.project_id || result.record.conversation_id !== conversation.conversation_id || result.record.plan_id !== plan.plan_id || result.record.plan_fingerprint !== plan.fingerprint || result.record.intent_id !== plan.circuit_intent_id || result.record.intent_revision !== plan.circuit_intent_revision || result.record.intent_fingerprint !== plan.circuit_intent_fingerprint || result.record.artifact_fingerprint !== generation.artifact_fingerprint || (result.record.aggregate_status === "SCHEMATIC_VERIFIED" && (result.currentness !== "CURRENT" || result.record.claims.length !== 8 || result.record.claims.some((claim) => claim.status !== "PASS")))) throw new Error("VERIFICATION_SCOPE_MISMATCH");
    const mapId = operations.READBACK?.library_map_evidence_id;
    const mapFp = operations.READBACK?.library_map_evidence_fingerprint;
    if (mapId && mapFp && (result.record.profile_version !== 2 || result.record.library_map_evidence_ids?.length !== 1
      || result.record.library_map_evidence_ids[0] !== mapId || result.record.library_map_evidence_fingerprints?.length !== 1
      || result.record.library_map_evidence_fingerprints[0] !== mapFp)) throw new Error("VERIFICATION_SCOPE_MISMATCH");
    setVerification(result); await currentRequest(() => (onRefresh()));
  }
  function ercContextMatches(status: ExecutionOperationStatus, selectedId: string): boolean {
    const context = status.schematic_approval_context;
    const writeReceipt = generation && "operation_receipt_fingerprint" in generation ? generation.operation_receipt_fingerprint : null;
    const expires = context ? Date.parse(context.expires_at) : NaN;
    return Boolean(
      ready && projection && context && generation && writeReceipt
      && status.operation_id === selectedId && status.operation_type === "KICAD_SCHEMATIC_ERC"
      && status.project_id === conversation.project_id && status.tenant_id === projection.identity.tenant_id
      && projection.identity.project_id === conversation.project_id
      && context.requester_id === projection.identity.requester_id
      && context.conversation_id === conversation.conversation_id
      && context.schematic_artifact_fingerprint === generation.artifact_fingerprint
      && context.source_operation_id === production?.operation_id
      && generation.operation_id === production.operation_id
      && context.source_operation_receipt_fingerprint === writeReceipt
      && context.source_parse_operation_id && context.source_parse_receipt_fingerprint
      && (!operations.PARSE || operations.PARSE.operation_id === context.source_parse_operation_id)
      && context.proposal_id === plan?.plan_id && context.proposal_fingerprint === plan.fingerprint
      && typeof context.fingerprint === "string" && context.fingerprint.length > 0
      && status.status === "WAIT_APPROVAL" && status.recovery_state === "NONE"
      && !status.cancellation_requested && Number.isFinite(expires) && expires > Date.now()
    );
  }
  async function readErcSource(status: ExecutionOperationStatus, selectedId: string): Promise<boolean> {
    if (!ercContextMatches(status, selectedId)) return false;
    const context = status.schematic_approval_context!;
    const write = await currentRequest(() => (fetchExecutionStatus(conversation.project_id, context.source_operation_id)));
    const parse = await currentRequest(() => (fetchExecutionStatus(conversation.project_id, context.source_parse_operation_id!)));
    return write.operation_id === context.source_operation_id
      && write.operation_type === "KICAD_SCHEMATIC_WRITE" && write.project_id === conversation.project_id
      && write.tenant_id === status.tenant_id && write.status === "SUCCEEDED" && write.recovery_state === "NONE"
      && write.receipt_fingerprint === context.source_operation_receipt_fingerprint
      && parse.operation_id === context.source_parse_operation_id
      && parse.operation_type === "KICAD_SCHEMATIC_PARSE" && parse.project_id === conversation.project_id
      && parse.tenant_id === status.tenant_id && parse.status === "SUCCEEDED" && parse.recovery_state === "NONE"
      && parse.receipt_fingerprint === context.source_parse_receipt_fingerprint;
  }
  async function readErc() {
    setErcSourceChecked(false); setErcStatus(null);
    const selectedId = ercId.trim();
    const status = await currentRequest(() => (fetchExecutionStatus(conversation.project_id, selectedId)));
    if (status.project_id !== conversation.project_id || status.operation_id !== selectedId || status.operation_type !== "KICAD_SCHEMATIC_ERC") throw new Error("OPERATION_SCOPE_MISMATCH");
    setErcStatus(status);
    try { setErcSourceChecked(await currentRequest(() => (readErcSource(status, selectedId)))); } catch (failure) { if (!isCurrent()) throw failure; setErcSourceChecked(false); }
  }
  async function approveErc() {
    const selectedId = ercId.trim();
    const previous = ercStatus?.schematic_approval_context;
    setErcSourceChecked(false);
    if (!previous || !ercSourceChecked || ercStatus?.operation_id !== selectedId) throw new Error("OPERATION_NOT_APPROVABLE");
    const status = await currentRequest(() => (fetchExecutionStatus(conversation.project_id, selectedId)));
    if (status.schematic_approval_context?.fingerprint !== previous.fingerprint || !(await currentRequest(() => (readErcSource(status, selectedId))))) throw new Error("OPERATION_NOT_APPROVABLE");
    const expires = Math.min(Date.parse(previous.expires_at), Date.now() + 5 * 60 * 1000);
    if (!Number.isFinite(expires) || expires <= Date.now()) throw new Error("OPERATION_NOT_APPROVABLE");
    await currentRequest(() => (approveExecution(conversation.project_id, selectedId, previous.requester_id, globalThis.crypto.randomUUID(), new Date(expires).toISOString())));
    await currentRequest(() => (readErc()));
    setErcSourceChecked(false);
  }
  return <section aria-label="原理图独立证据" className="ide-review-card">
    <h3>原理图独立证据</h3>
    <p>写入、Parse、ERC 与独立 Read-back 分项审批；操作成功不等于八项验证通过。</p>
    {!ready && <p role="status">BLOCKED：需要当前获批计划及生成产物。</p>}
    <button type="button" disabled={disabled || busy || !ready} onClick={() => void action(async () => {
      setLibraryMaps([]); setLibraryMapId("");
      const values = await currentRequest(() => (fetchLibraryTerminalMaps(conversation.project_id,conversation.conversation_id)));
      if (values.some((item) => item.evidence.project_id !== conversation.project_id || item.evidence.conversation_id !== conversation.conversation_id || item.evidence.tenant_id !== projection?.identity.tenant_id)) throw new Error("OPERATION_SCOPE_MISMATCH");
      setLibraryMaps(values);
    })}>读取已审库映射选择器</button>
    <label>Read-back 库映射<select value={libraryMapId} onChange={(event) => setLibraryMapId(event.target.value)}><option value="">旧版无库映射观察</option>{libraryMaps.filter((item) => item.currentness === "APPROVED" && item.review?.decision === "APPROVED").map((item) => <option key={item.evidence.evidence_id} value={item.evidence.evidence_id}>{item.evidence.evidence_id} · {item.evidence.fingerprint}</option>)}</select></label>
    {(["PARSE", "READBACK"] as Kind[]).map((kind) => <div key={kind}>
      <button type="button" disabled={disabled || busy || !ready} onClick={() => void action(() => create(kind))}>创建 {labels[kind]} 操作</button>
      {operations[kind] && <><p>{labels[kind]}：{operations[kind]!.operation_id} · {statuses[kind]?.status ?? "UNKNOWN"} · {statuses[kind]?.recovery_state ?? "UNKNOWN"}</p>
        <button type="button" disabled={disabled || busy || !ready || (statuses[kind]?.status !== "WAIT_APPROVAL" && statuses[kind] !== undefined)} onClick={() => void action(() => approve(kind))}>批准 {labels[kind]}</button>
        <button type="button" disabled={disabled || busy} onClick={() => void action(() => refreshStatus(kind))}>读取 {labels[kind]} 状态</button>
        {["RUNNING", "QUEUED"].includes(statuses[kind]?.status ?? "") && <button type="button" disabled={disabled || busy} onClick={() => void action(async () => { await currentRequest(() => (cancelExecution(conversation.project_id, operations[kind]!.operation_id, "USER_CANCELLED"))); await currentRequest(() => (refreshStatus(kind))); })}>取消 {labels[kind]}</button>}
      </>}
    </div>)}
    <label>ERC 操作<select aria-label="ERC 操作" value={ercId} onChange={(event) => { setErcId(event.target.value); setErcStatus(null); setErcSourceChecked(false); }}><option value="">选择已持久化操作</option>{projection?.operations.items.map((item) => <option key={item.reference_id} value={item.reference_id}>{item.reference_id} · {item.status ?? "UNKNOWN"}</option>)}</select></label>
    <button type="button" disabled={disabled || busy || !ercId} onClick={() => void action(readErc)}>读取 ERC 状态</button>
    {ercStatus && <><p>ERC：{ercStatus.status}；审批来源绑定 {ercSourceChecked && ercContextMatches(ercStatus, ercId.trim()) ? "CURRENT" : "UNVERIFIED"}。</p>
      <button type="button" disabled={disabled || busy || !ercSourceChecked || !ercContextMatches(ercStatus, ercId.trim())} onClick={() => void action(approveErc)}>批准 ERC</button>
    </>}
    <details><summary>参数来源与适用条件审核</summary>
      <p>先读取本会话的独立操作身份。仅核对已选 exact part 的原件参数；未批准条件保持 UNKNOWN。</p>
      <form onSubmit={(event) => { event.preventDefault(); void action(submitParameter); }}>
        <label>已批准器件<select value={sourceId} onChange={(event) => { setSourceId(event.target.value); setCandidate(null); setDecision(null); }}><option value="">请选择</option>{sources.map((item) => <option key={item.source.source_id} value={item.source.source_id}>{item.source.manufacturer} {item.source.mpn} · {item.source.package}</option>)}</select></label>
        {fields.map(([name, label]) => <label key={name}>{label}<input value={draft[name] ?? ""} maxLength={1023} onChange={(event) => { setDraft((old) => ({ ...old, [name]: event.target.value })); setCandidate(null); setDecision(null); }} /></label>)}
        <label>参数<select value={parameterKey} onChange={(event) => { setParameterKey(event.target.value as typeof parameterKey); setCandidate(null); setDecision(null); }}>{Object.keys(units).map((key) => <option key={key}>{key}</option>)}</select> · {units[parameterKey]}</label>
        <label>取值类型<select value={bound} onChange={(event) => { setBound(event.target.value as typeof bound); setCandidate(null); setDecision(null); }}>{["MIN", "MAX", "TYP", "EXACT"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>适用模式<select value={mode} onChange={(event) => { setMode(event.target.value as typeof mode); setCandidate(null); setDecision(null); }}>{["UNKNOWN", "DC", "I2C_STANDARD"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <button type="submit" disabled={disabled || busy || !sourceId || !(operations.PARSE || operations.READBACK)}>提交参数待审</button>
      </form>
      {candidate && <div><p>待审：{candidate.evidence_id} · {candidate.manufacturer}/{candidate.mpn}/{candidate.package} · {candidate.parameter_key}={candidate.value_decimal} {candidate.unit}</p><p>原件：{candidate.document_uri} · {candidate.document_revision} · {candidate.content_sha256}</p><p>定位：{Object.values(candidate.locator).filter(Boolean).join(" / ")}；适用条件：电压 {candidate.conditions.voltage_min ?? "UNKNOWN"}–{candidate.conditions.voltage_max ?? "UNKNOWN"} V，温度 {candidate.conditions.temperature_min ?? "UNKNOWN"}–{candidate.conditions.temperature_max ?? "UNKNOWN"} °C，模式 {candidate.conditions.mode}，封装 {candidate.conditions.package}；候选指纹：{candidate.fingerprint}</p>
        {decision ? <p>审核：{decision}</p> : <><button type="button" disabled={disabled || busy} onClick={() => void action(() => review("APPROVED"))}>批准参数</button><button type="button" disabled={disabled || busy} onClick={() => void action(() => review("REJECTED"))}>拒绝参数</button></>}
      </div>}
      <label>已审参数记录<input value={evidenceId} onChange={(event) => { setEvidenceId(event.target.value); setParameter(null); }} /></label>
      <button type="button" disabled={disabled || busy || !evidenceId.trim()} onClick={() => void action(readParameter)}>从 Owner 读回参数</button>
      {parameter && <p>参数：{parameter.evidence.candidate.parameter_key}={parameter.evidence.candidate.value_decimal} {parameter.evidence.candidate.unit} · {parameter.currentness}；{parameter.currentness === "STALE" ? "历史批准不能用于当前证明。" : "已绑定当前资料与选型。"}</p>}
      <p>已核对当前参数记录：{currentParameterIds.length}</p>
    </details>
    <button type="button" disabled={disabled || busy || !ready} onClick={() => void action(record)}>记录八项验证</button>
    {verification && <div><p>当前性：{verification.currentness}；整体：{verification.record.aggregate_status}{verification.currentness === "STALE" ? "（历史）" : ""}</p>{verification.record.claims.map((claim) => <p key={claim.claim_id}>{claim.claim_id}：{claim.status}（{claim.reason}）</p>)}</div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
