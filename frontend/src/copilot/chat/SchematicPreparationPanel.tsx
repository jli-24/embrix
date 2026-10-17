import { useEffect, useRef, useState } from "react";
import { fetchProjectSupply, proposeProjectSupply, reviewProjectSupply, fetchSchematicPlanHistory, proposeSchematicPlanV2, decideSchematicPlanV2, type SchematicPlanHistoryView, fetchStandardApplicability, proposeStandardApplicability, reviewStandardApplicability, fetchLibraryTerminalMaps, proposeLibraryTerminalMap, reviewLibraryTerminalMap, type ConversationDetail, type ProjectSupplyView, type EvidenceView, type StandardApplicabilityInput, type LibraryTerminalMapInput, type DeviceConditions } from "../api/projectClient";
import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";

type Props = { conversation: ConversationDetail; projection: DesktopWorkspaceProjection | null; disabled?: boolean; onRefresh: () => Promise<void> };
const toggle = (values: string[], id: string) => values.includes(id) ? values.filter((x) => x !== id) : [...values, id];
export function SchematicPreparationPanel({ conversation: c, projection, disabled = false, onRefresh }: Props) {
  const scope = JSON.stringify([c.project_id, c.conversation_id, c.fingerprint,
    c.engineering_plan?.fingerprint, c.hardware_selection?.proposal?.fingerprint,
    c.design_truth?.fingerprint, c.schematic_planning?.proposal?.fingerprint,
    c.schematic_planning?.approved_plan?.fingerprint, c.schematic_production?.generation_receipt?.fingerprint,
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
  const [supply, setSupply] = useState<ProjectSupplyView | null>(null);
  const [planHistory, setPlanHistory] = useState<SchematicPlanHistoryView | null>(null);
  const [standards, setStandards] = useState<EvidenceView<StandardApplicabilityInput>[]>([]);
  const [libraries, setLibraries] = useState<EvidenceView<LibraryTerminalMapInput>[]>([]);
  const [option, setOption] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [consumers, setConsumers] = useState<string[]>([]);
  const [evidence, setEvidence] = useState<string[]>([]);
  const [nominal, setNominal] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [netIds, setNetIds] = useState<string[]>([]);
  const [targetIds, setTargetIds] = useState<string[]>([]);
  const [conditions, setConditions] = useState<Record<string, Record<string, string>>>({});
  const [limit, setLimit] = useState<"frequency_max_hz" | "rise_time_max_s" | "cbus_max_f">("frequency_max_hz");
  const [libraryInstance, setLibraryInstance] = useState("");
  const [terminalNets, setTerminalNets] = useState<Record<string, string>>({});
  const selection = c.hardware_selection;
  const plan = c.engineering_plan;
  const truth = c.design_truth;
  const physical = c.schematic_planning?.proposal;
  const approved = c.schematic_planning?.status === "APPROVED" ? c.schematic_planning.approved_plan : null;
  const facts = projection?.authoritative_facts?.filter((f) => f.domain === "CIRCUIT" && f.currentness === "CURRENT" && f.project_id === c.project_id && f.owner === "ProjectCatalog" && f.object_id && f.fingerprint) ?? [];
  const circuit = facts.length === 1 ? facts[0] : null;
  const ready = Boolean((!c.selection_head || (c.selection_head.status === "APPROVED" && c.selection_head.currentness === "CURRENT")) && projection?.identity.project_id === c.project_id && circuit && plan?.status === "APPROVED" && c.engineering_plan_review?.status === "APPROVED" && selection?.status === "APPROVED" && selection.proposal && truth);
  const selected = selection?.proposal?.selected_components ?? [];
  const components = truth?.components ?? [];
  const evidenceRefs = [...new Set(truth?.evidence_bindings.map((b) => b.binding_id) ?? [])];
  const instances = physical?.component_instances ?? [];
  const nets = physical?.nets ?? [];
  function checkScope(value: { tenant_id: string; project_id: string; conversation_id: string }) {
    if (value.project_id !== c.project_id || value.conversation_id !== c.conversation_id || value.tenant_id !== projection?.identity.tenant_id) throw new Error("EVIDENCE_SCOPE_MISMATCH");
  }
  async function action(work: () => Promise<void>) {
    if (!live.current.mounted || live.current.disabled || live.current.scope !== scope || lock.current) return;
    requestGeneration.current = live.current.generation;
    lock.current = true; setBusy(true); setError(null);
    try { await work(); requireCurrent(); } catch { if (isCurrent()) setError("BLOCKED：证据不可用或来源已变化，请刷新 Owner 后重新读取。"); }
    finally { lock.current = false; if (live.current.mounted) setBusy(false); }
  }
  async function read() {
    setSupply(null); setStandards([]); setLibraries([]); setOption("");
    const [p, s, l] = await currentRequest(() => (Promise.all([fetchProjectSupply(c.project_id,c.conversation_id), fetchStandardApplicability(c.project_id,c.conversation_id), fetchLibraryTerminalMaps(c.project_id,c.conversation_id)])));
    if (p.candidate) checkScope(p.candidate);
    for (const item of [...s,...l]) checkScope(item.evidence);
    setSupply(p); setStandards(s); setLibraries(l);
  }
  async function readPlanHistory() {
    setPlanHistory(null);
    const history = await currentRequest(() => fetchSchematicPlanHistory(c.project_id,c.conversation_id));
    setPlanHistory(history);
  }
  async function proposeReplacement() {
    if (!ready || !supply || supply.status !== "APPROVED" || supply.candidate?.schema_version !== 3 || supply.review?.decision !== "APPROVED" || !planHistory) throw new Error("PLAN_REPLACEMENT_UNAVAILABLE");
    const fresh = await currentRequest(() => fetchSchematicPlanHistory(c.project_id,c.conversation_id));
    const expected = planHistory.head ?? planHistory.legacy;
    const liveExpected = fresh.head ?? fresh.legacy;
    if (JSON.stringify([expected?.revision_id ?? null,expected?.plan_id ?? null,expected?.plan_fingerprint ?? null]) !== JSON.stringify([liveExpected?.revision_id ?? null,liveExpected?.plan_id ?? null,liveExpected?.plan_fingerprint ?? null])) throw new Error("PLAN_HEAD_CHANGED");
    await currentRequest(() => proposeSchematicPlanV2(c.project_id,c.conversation_id,{request_id:crypto.randomUUID(),expected_head_revision_id:expected?.revision_id ?? null,expected_head_plan_id:expected?.plan_id ?? null,expected_head_plan_fingerprint:expected?.plan_fingerprint ?? null}));
    await currentRequest(readPlanHistory);
    await currentRequest(onRefresh);
  }
  async function decideReplacement(decision: "APPROVED" | "REJECTED") {
    const head = planHistory?.head;
    if (!head || !head.is_current || head.currentness !== "CURRENT" || head.planning.status !== "REVIEW") throw new Error("PLAN_DECISION_UNAVAILABLE");
    const fresh = await currentRequest(() => fetchSchematicPlanHistory(c.project_id,c.conversation_id));
    if (fresh.head?.revision_id !== head.revision_id || fresh.head.plan_id !== head.plan_id || fresh.head.plan_fingerprint !== head.plan_fingerprint || fresh.head.currentness !== "CURRENT" || fresh.head.planning.status !== "REVIEW") throw new Error("PLAN_HEAD_CHANGED");
    await currentRequest(() => decideSchematicPlanV2(c.project_id,c.conversation_id,{revision_id:head.revision_id,plan_id:head.plan_id,plan_fingerprint:head.plan_fingerprint,decision}));
    await currentRequest(readPlanHistory);
    await currentRequest(onRefresh);
  }
  async function submitSupply() {
    const choice = supply?.current_options?.find((o) => o.power_domain_id === option);
    const approvedSelection = selection?.proposal;
    if (!ready || !choice || !plan || !truth || !approvedSelection || !circuit || !sources.length || !consumers.length || !evidence.length) throw new Error("EVIDENCE_BINDING_UNAVAILABLE");
    if ([...sources,...consumers].some((id) => !components.some((x) => x.component_id === id)) || evidence.some((id) => !evidenceRefs.includes(id))) throw new Error("EVIDENCE_BINDING_UNAVAILABLE");
    const historical = supply?.candidate;
    const baseTruth = historical && historical.selection_id === approvedSelection.proposal_id
      && historical.selection_fingerprint === approvedSelection.fingerprint
      && historical.circuit_id === circuit.object_id && historical.circuit_fingerprint === circuit.fingerprint
      && historical.plan_id === plan.plan_id && historical.plan_fingerprint === plan.fingerprint
      ? { truth_id: historical.design_truth_id, fingerprint: historical.design_truth_fingerprint } : truth;
    const result = await currentRequest(() => (proposeProjectSupply(c.project_id,c.conversation_id,{design_truth_id:baseTruth.truth_id, design_truth_fingerprint:baseTruth.fingerprint,selection_id:approvedSelection.proposal_id,selection_fingerprint:approvedSelection.fingerprint,circuit_id:circuit.object_id!,circuit_fingerprint:circuit.fingerprint!,plan_id:plan.plan_id,plan_fingerprint:plan.fingerprint,relations:[{power_domain_id:choice.power_domain_id,supply_net_ref:choice.supply_net_ref,ground_net_ref:choice.ground_net_ref,source_component_refs:sources,consumer_component_refs:consumers,nominal_voltage_v:nominal,evidence_refs:evidence}]})));
    checkScope(result.candidate); await currentRequest(() => (read())); await currentRequest(() => (onRefresh()));
  }
  async function decideSupply(decision: "APPROVED" | "REJECTED") {
    const candidate = supply?.candidate;
    if (!candidate || supply?.status !== "REVIEW" || supply.review) throw new Error("EVIDENCE_NOT_REVIEWABLE");
    const current = await currentRequest(() => (fetchProjectSupply(c.project_id,c.conversation_id)));
    if (current.status !== "REVIEW" || current.candidate?.fingerprint !== candidate.fingerprint || current.review) throw new Error("EVIDENCE_NOT_REVIEWABLE");
    checkScope(current.candidate);
    await currentRequest(() => (reviewProjectSupply(c.project_id,c.conversation_id,candidate.candidate_id,candidate.fingerprint,decision)));
    await currentRequest(() => (read())); await currentRequest(() => (onRefresh()));
  }
  async function submitStandard() {
    const requirement = c.requirement_context;
    if (!ready || !physical || !plan || !requirement || !targetIds.length || !netIds.length) throw new Error("EVIDENCE_BINDING_UNAVAILABLE");
    const targets = targetIds.map((id) => {
      const instance = instances.find((x) => x.instance_id === id);
      const source = selected.find((x) => x.slot_id === instance?.hardware_slot_id)?.source;
      const input = conditions[id];
      if (!instance || !source || !input) throw new Error("EVIDENCE_BINDING_UNAVAILABLE");
      const envelope: DeviceConditions = {voltage_min:input.voltage_min,voltage_max:input.voltage_max,temperature_min:input.temperature_min,temperature_max:input.temperature_max,mode:"I2C_STANDARD",package:source.package};
      return {component_instance_id:id,source_record_id:source.source_id,source_record_fingerprint:source.fingerprint,device_conditions:envelope};
    });
    if (netIds.some((id) => !nets.some((n) => n.net_id === id))) throw new Error("EVIDENCE_BINDING_UNAVAILABLE");
    const result = await currentRequest(() => (proposeStandardApplicability(c.project_id,c.conversation_id,{document_uri:draft.standard_uri ?? "",document_revision:draft.standard_revision ?? "",content_sha256:draft.standard_hash ?? "",locator:{page:Number(draft.standard_page),table:draft.standard_table,row:draft.standard_row},mode:"I2C_STANDARD",[limit]:draft.standard_value ?? "",target_net_ids:netIds,target_components:targets,requirement_id:plan.requirement_id,requirement_fingerprint:requirement.fingerprint,plan_id:plan.plan_id,plan_fingerprint:plan.fingerprint})));
    checkScope(result.evidence); await currentRequest(() => (read())); await currentRequest(() => (onRefresh()));
  }
  async function submitLibrary() {
    const instance = approved?.component_instances?.find((x) => x.instance_id === libraryInstance);
    const source = selected.find((x) => x.slot_id === instance?.hardware_slot_id)?.source;
    if (!ready || !approved || !instance || !source) throw new Error("EVIDENCE_BINDING_UNAVAILABLE");
    const bindings = ["1","2","3","4","EP"].map((terminal) => {
      const logical = terminal === "EP" ? "5" : terminal;
      const net = approved.nets?.find((n) => n.net_id === terminalNets[terminal]);
      if (!net) throw new Error("EVIDENCE_BINDING_UNAVAILABLE");
      return {manufacturer_terminal:terminal,manufacturer_locator: terminal === "EP" ? {section:"4 Interface Specifications",clause:"CENTER_PAD_INTERNAL_VSS_AND_OPTIONAL_PCB_GND"} : {page:6,table:"Table 7",row:terminal},logical_symbol_pin:logical,physical_pad_ids:[logical],net_id:net.net_id};
    });
    const result = await currentRequest(() => (proposeLibraryTerminalMap(c.project_id,c.conversation_id,{component_instance_id:instance.instance_id,source_record_id:source.source_id,source_record_fingerprint:source.fingerprint,plan_id:approved.plan_id,plan_fingerprint:approved.fingerprint,symbol_library_id:instance.symbol,symbol_uri:draft.symbol_uri ?? "",symbol_sha256:draft.symbol_hash ?? "",footprint_library_id:instance.footprint,footprint_uri:draft.footprint_uri ?? "",footprint_sha256:draft.footprint_hash ?? "",terminal_bindings:bindings})));
    checkScope(result.evidence); await currentRequest(() => (read())); await currentRequest(() => (onRefresh()));
  }
  async function decideEvidence(kind: "standard" | "library", id: string, fp: string, decision: "APPROVED" | "REJECTED") {
    const rows = kind === "standard" ? await currentRequest(() => (fetchStandardApplicability(c.project_id,c.conversation_id))) : await currentRequest(() => (fetchLibraryTerminalMaps(c.project_id,c.conversation_id)));
    const item = rows.find((x) => x.evidence.evidence_id === id && x.evidence.fingerprint === fp);
    if (!item || item.currentness !== "REVIEW" || item.review) throw new Error("EVIDENCE_NOT_REVIEWABLE");
    checkScope(item.evidence);
    if (kind === "standard") await currentRequest(() => (reviewStandardApplicability(c.project_id,id,fp,decision)));
    else await currentRequest(() => (reviewLibraryTerminalMap(c.project_id,id,fp,decision)));
    await currentRequest(() => (read())); await currentRequest(() => (onRefresh()));
  }
  const field = (key: string,label: string) => <label key={key}>{label}<input value={draft[key] ?? ""} maxLength={1024} onChange={(e) => setDraft({...draft,[key]:e.target.value})} /></label>;
  const reviews = (kind: "standard" | "library", rows: EvidenceView<StandardApplicabilityInput>[] | EvidenceView<LibraryTerminalMapInput>[]) => rows.map((item) => <div key={item.evidence.evidence_id}><p>{item.evidence.evidence_id} · {item.currentness ?? "UNKNOWN"} · {item.evidence.fingerprint}</p><details><summary>核对原件与目标绑定</summary><pre>{JSON.stringify(item.evidence,null,2)}</pre></details>{item.review ? <p>审核 {item.review.decision} · {item.review.fingerprint}</p> : ["APPROVED","REJECTED"].map((d) => <button key={d} type="button" disabled={disabled || busy || item.currentness !== "REVIEW"} onClick={() => void action(() => decideEvidence(kind,item.evidence.evidence_id,item.evidence.fingerprint,d as "APPROVED"|"REJECTED"))}>{d === "APPROVED" ? "批准" : "拒绝"} {item.evidence.evidence_id}</button>)}</div>);
  return <section className="ide-review-card" aria-label="原理图生成前证据">
    <h3>原理图生成前证据</h3>
    {!ready && <p role="status">BLOCKED：需要当前 EngineeringPlan、Selection、DesignTruth 和唯一 CURRENT Circuit。请刷新 Owner。</p>}
    <button type="button" disabled={disabled || busy} onClick={() => void action(onRefresh)}>刷新 Owner</button>
    <button type="button" disabled={disabled || busy || !ready} onClick={() => void action(read)}>读取当前待审依据</button>
    <p>供电：{supply?.status ?? "UNKNOWN"}；规范记录：{standards.length}；库记录：{libraries.length}。提交候选与人工审核分别进行。</p>
    {circuit && <p>Circuit {circuit.object_id} · {circuit.fingerprint}</p>}
    <details><summary>P1 供电来源与关系</summary>
      <label>当前供电域<select value={option} onChange={(e) => setOption(e.target.value)}><option value="">请选择</option>{supply?.current_options?.map((o) => <option key={o.power_domain_id} value={o.power_domain_id}>{o.domain_name} · {o.supply_net_ref} / {o.ground_net_ref}</option>)}</select></label>
      <label>标称电压 V<input value={nominal} onChange={(e) => setNominal(e.target.value)} /></label>
      {components.map((x) => <div key={x.component_id}><label><input type="checkbox" checked={sources.includes(x.component_id)} onChange={() => setSources(toggle(sources,x.component_id))} />供电来源 {x.name}</label><label><input type="checkbox" checked={consumers.includes(x.component_id)} onChange={() => setConsumers(toggle(consumers,x.component_id))} />用电器件 {x.name}</label></div>)}
      {evidenceRefs.map((id) => <label key={id}><input type="checkbox" checked={evidence.includes(id)} onChange={() => setEvidence(toggle(evidence,id))} />依据 {id}</label>)}
      <button type="button" disabled={disabled || busy || !ready || !option || !sources.length || !consumers.length || !evidence.length || !nominal} onClick={() => void action(submitSupply)}>提交供电候选</button>
      {supply?.candidate && <><p>{supply.candidate.candidate_id} · {supply.candidate.fingerprint}</p><pre>{JSON.stringify(supply.candidate.relations,null,2)}</pre>{supply.review ? <p>审核 {supply.review.decision} · {supply.review.fingerprint}</p> : ["APPROVED","REJECTED"].map((d) => <button key={d} type="button" disabled={disabled || busy || supply.status !== "REVIEW"} onClick={() => void action(() => decideSupply(d as "APPROVED"|"REJECTED"))}>{d === "APPROVED" ? "批准供电关系" : "拒绝供电关系"}</button>)}</>}
    </details>
    <details><summary>原理图计划修订与历史</summary>
      <button type="button" disabled={disabled || busy} onClick={() => void action(readPlanHistory)}>读取计划头与历史</button>
      {planHistory && <>
        <p>当前计划：{planHistory.head?.plan_id ?? planHistory.legacy?.plan_id ?? "无"} · {planHistory.head?.currentness ?? planHistory.legacy?.currentness ?? "UNAVAILABLE"}</p>
        {planHistory.legacy && <p>旧计划 {planHistory.legacy.plan_id} · STALE · 收据引用 {planHistory.legacy.receipt_references.length}</p>}
        {planHistory.history.map((item) => <p key={item.revision_id}>修订 {item.revision_number} · {item.plan_id} · {item.currentness} · 收据引用 {item.receipt_references.length}</p>)}
        <button type="button" disabled={disabled || busy || !ready || supply?.status !== "APPROVED" || supply.candidate?.schema_version !== 3 || supply.review?.decision !== "APPROVED"} onClick={() => void action(proposeReplacement)}>提交替换计划提案</button>
        {planHistory.head?.planning.status === "REVIEW" && ["APPROVED","REJECTED"].map((d) => <button key={d} type="button" disabled={disabled || busy || planHistory.head?.currentness !== "CURRENT"} onClick={() => void action(() => decideReplacement(d as "APPROVED"|"REJECTED"))}>{d === "APPROVED" ? "批准替换计划" : "拒绝替换计划"}</button>)}
      </>}
    </details>
    <details><summary>P2 规范适用性</summary>
      {!physical && <p>BLOCKED：需要当前 SchematicPlanning proposal 的物理 net/instance 选择器。</p>}
      {field("standard_uri","规范 URI")}{field("standard_revision","规范修订")}{field("standard_hash","原件 SHA256")}{field("standard_page","物理页")}{field("standard_table","表")}{field("standard_row","行")}
      <label>规范上限<select value={limit} onChange={(e) => setLimit(e.target.value as typeof limit)}><option value="frequency_max_hz">频率 Hz</option><option value="rise_time_max_s">上升时间 s</option><option value="cbus_max_f">总线电容 F</option></select></label>{field("standard_value","上限值")}
      {nets.map((n) => <label key={n.net_id}><input type="checkbox" checked={netIds.includes(n.net_id)} onChange={() => setNetIds(toggle(netIds,n.net_id))} />目标 net {n.name} · {n.net_id}</label>)}
      {instances.map((i) => <div key={i.instance_id}><label><input type="checkbox" checked={targetIds.includes(i.instance_id)} onChange={() => setTargetIds(toggle(targetIds,i.instance_id))} />目标 {i.manufacturer} {i.mpn} · {i.instance_id}</label>{targetIds.includes(i.instance_id) && ["voltage_min","voltage_max","temperature_min","temperature_max"].map((key) => <label key={key}>{key}<input value={conditions[i.instance_id]?.[key] ?? ""} placeholder="UNKNOWN" onChange={(e) => setConditions({...conditions,[i.instance_id]:{...conditions[i.instance_id],[key]:e.target.value}})} /></label>)}</div>)}
      <button type="button" disabled={disabled || busy || !ready || !physical || !targetIds.length || !netIds.length} onClick={() => void action(submitStandard)}>提交规范候选</button>{reviews("standard",standards)}
    </details>
    <details><summary>P3 原厂端子与私有库映射</summary>
      {!approved && <p>BLOCKED：需要获批 SchematicPlan，库映射应在写入前审核。</p>}
      <label>已批准器件<select value={libraryInstance} onChange={(e) => {setLibraryInstance(e.target.value);setTerminalNets({});}}><option value="">请选择</option>{approved?.component_instances?.map((i) => <option key={i.instance_id} value={i.instance_id}>{i.manufacturer} {i.mpn} · {i.instance_id}</option>)}</select></label>
      {field("symbol_uri","Symbol 原件 URI")}{field("symbol_hash","Symbol SHA256")}{field("footprint_uri","Footprint 原件 URI")}{field("footprint_hash","Footprint SHA256")}
      <p>有限 SHTC3_EP：原厂 1–4 对应 logical/pad 1–4；未编号 EP 对应私有 logical/pad 5（VSS/GND）。</p>
      {["1","2","3","4","EP"].map((t) => <label key={t}>原厂端子 {t} 的批准 net<select value={terminalNets[t] ?? ""} onChange={(e) => setTerminalNets({...terminalNets,[t]:e.target.value})}><option value="">请选择</option>{approved?.nets?.map((n) => <option key={n.net_id} value={n.net_id}>{n.name} · {n.net_id}</option>)}</select></label>)}
      <button type="button" disabled={disabled || busy || !ready || !approved || !libraryInstance} onClick={() => void action(submitLibrary)}>提交库映射候选</button>{reviews("library",libraries)}
    </details>
    {error && <p role="alert">{error}</p>}
  </section>;
}