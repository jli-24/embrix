import { useEffect, useRef, useState } from "react";
import { fetchSelectionParentPreview, fetchSelectionCurrent, fetchSelectionHistory, proposeSelectionRevision,
  decideSelectionRevision, requestHardwareModelCandidateV2, type ConversationDetail,
  type SelectionPreviewV3, type SelectionChoice, type SelectionRevisionHead, type SelectionRevisionView } from "../api/projectClient";

import { buildModelConsumptionSelector } from "../api/modelConsumptionClient";
import { ModelConsumptionPanel, type ConsumptionAuthorization } from "./ModelConsumptionPanel";

type Props = { conversation: ConversationDetail; disabled?: boolean; onRefresh: () => Promise<void> };
export function SelectionRevisionPanel({ conversation: c, disabled = false, onRefresh }: Props) {
  const identity = JSON.stringify([disabled,c.tenant_id,c.requester,c.project_id,c.conversation_id,c.fingerprint,c.selection_head?.revision_id,
    c.selection_head?.selection_fingerprint,c.selection_head?.decision_fingerprint,c.selection_head?.currentness,c.selection_head?.reason]);
  const scope = useRef(identity); scope.current = identity;
  const mounted = useRef(true), lock = useRef(false);
  const [authorization,setAuthorization]=useState<ConsumptionAuthorization|null>(null);
  const [mutation,setMutation]=useState(false);
  const [busy,setBusy] = useState(false), [error,setError] = useState<string|null>(null);
  const [preview,setPreview] = useState<SelectionPreviewV3|null>(null);
  const [parents,setParents] = useState<SelectionChoice[]>([]);
  const [treeDirty,setTreeDirty] = useState(false);
  const treeGeneration=useRef(0);
  const [choices,setChoices] = useState<SelectionChoice[]>([]);
  const [head,setHead] = useState<SelectionRevisionHead|null>(c.selection_head ?? null);
  const [history,setHistory] = useState<SelectionRevisionView[]>([]);
  useEffect(() => { mounted.current=true; return () => { mounted.current=false; }; },[]);
  useEffect(() => { setPreview(null); setParents([]); setTreeDirty(false); treeGeneration.current++; setChoices([]); setHistory([]); setHead(c.selection_head ?? null); setError(null); setBusy(false); setMutation(false); lock.current=false; },[identity]);
  const valid = (captured: string) => mounted.current && `${scope.current}:${treeGeneration.current}` === captured;
  async function action(work: (captured: string) => Promise<void>,changesState=false) {
    if (disabled || lock.current) return;
    const captured=`${identity}:${treeGeneration.current}`; lock.current=true; setBusy(true); setMutation(changesState); setError(null);
    try { await work(captured); } catch { if(valid(captured)) setError("SELECTION_REVISION_UNAVAILABLE"); }
    finally { if(valid(captured)) { lock.current=false; setBusy(false); setMutation(false); } }
  }
  async function load(captured: string) {
    const current=await fetchSelectionCurrent(c.project_id,c.conversation_id);
    if (!valid(captured)) return;
    setHead(current.kind === "REVISION" ? current.head : null);
    const next=await fetchSelectionParentPreview(c.project_id,c.conversation_id);
    if (!valid(captured)) return;
    acceptTree(next);
  }
  function acceptTree(next:SelectionPreviewV3) {
    setPreview(next);setParents(next.selected_parents);setTreeDirty(false);
    setChoices(next.required_slots.map(slot=>next.selected_parents.find(x=>x.slot_id===slot)??next.choices.find(x=>x.slot_id===slot)!));
  }
  function changeParent(slot:string,source:string) {
    const next=preview?.choices.find(x=>x.slot_id===slot&&x.source_id===source);if(!next)return;
    treeGeneration.current++;setParents(old=>old.map(x=>x.slot_id===slot?next:x));
    setChoices([]);setTreeDirty(true);setBusy(false);lock.current=false;setError(null);
  }
  async function readTree(captured:string) {
    const next=await fetchSelectionParentPreview(c.project_id,c.conversation_id,parents);
    if(valid(captured))acceptTree(next);
  }
  async function propose(captured: string) {
    if(!preview || treeDirty || choices.length!==preview.required_slots.length || choices.some(x=>!x)) return;
    const result=await proposeSelectionRevision(c.project_id,c.conversation_id,preview,choices,`selection-${crypto.randomUUID()}`);
    if(!valid(captured)) return;
    if(result.kind!=="REVISION" || result.head.status!=="REVIEW") throw new Error("SELECTION_RESPONSE_INVALID");
    setHead(result.head); setPreview(null); await onRefresh();
  }
  async function decide(captured: string, decision: "APPROVED"|"REJECTED"|"REVOKED") {
    if(!head) return;
    const result=await decideSelectionRevision(c.project_id,c.conversation_id,head,decision);
    if(!valid(captured)) return;
    if(result.kind!=="REVISION" || result.head.revision_id!==head.revision_id || result.head.selection_fingerprint!==head.selection_fingerprint) throw new Error("SELECTION_RESPONSE_INVALID");
    setHead(result.head); setPreview(null); await onRefresh();
  }
  const consumptionScope=JSON.stringify([identity,head,preview?.expected_head,preview?.preview_fingerprint,parents,treeDirty]);
  const authorized=authorization?.scopeKey===consumptionScope&&Date.parse(authorization.notAfter)>Date.now();
  const binding=c.engineering_plan?.model_operation_bindings?.filter(x=>x.operation_id==="HARDWARE_SELECTION_PROPOSAL" && x.stage_id==="HARDWARE_SELECTION");
  if(c.architecture_review?.status!=="APPROVED" || c.engineering_plan_review?.status!=="APPROVED") return null;
  return <section aria-label="Selection 替换与审批" className="ide-review-card">
    <p>Selection：{head?.status ?? c.hardware_selection?.status ?? "UNAVAILABLE"} / {head?.currentness ?? "UNVERIFIED"}{head?.reason ? ` (${head.reason})` : ""}</p>
    <button disabled={disabled||busy} onClick={()=>void action(load)}>读取替换候选</button>
    <button disabled={disabled||busy} onClick={()=>void action(async captured=>{ const result=await fetchSelectionHistory(c.project_id,c.conversation_id); if(valid(captured)) setHistory(result.revisions); })}>读取 Selection 历史</button>
    {preview && <><p>新候选必须独立审批；旧方案与审批保留。</p>{preview.root_slots.map(slot=><label key={slot}>父件 {slot}<select aria-label={slot} disabled={disabled||mutation} value={parents.find(x=>x.slot_id===slot)?.source_id??""} onChange={event=>changeParent(slot,event.target.value)}>{preview.choices.filter(x=>x.slot_id===slot).map(x=><option key={`${x.source_id}:${x.source_fingerprint}`} value={x.source_id}>{x.source_id} / {x.source_fingerprint}</option>)}</select></label>)}
      {treeDirty&&<><p>父件已变更；旧外围候选已撤下。</p><button disabled={disabled||busy} onClick={()=>void action(readTree)}>读取所选父件的外围候选</button></>}
      {!treeDirty&&preview.required_slots.filter(slot=>!preview.root_slots.includes(slot)).map(slot=><label key={slot}>{slot}<select aria-label={slot} disabled={busy||disabled} value={choices.find(x=>x.slot_id===slot)?.source_id ?? ""} onChange={event=>{const choice=preview.choices.find(x=>x.slot_id===slot && x.source_id===event.target.value);if(choice)setChoices(old=>old.map(x=>x.slot_id===slot?choice:x));}}>
      {preview.choices.filter(x=>x.slot_id===slot).map(x=><option key={`${x.source_id}:${x.source_fingerprint}`} value={x.source_id}>{x.source_id} / {x.source_fingerprint}</option>)}
    </select></label>)}<button disabled={disabled||busy||treeDirty||choices.length!==preview.required_slots.length||choices.some(x=>!x)} onClick={()=>void action(propose,true)}>提交新 Selection 候选</button>
    {binding?.length===1 && !treeDirty && <><button disabled={disabled||busy||!authorized} onClick={()=>void action(async captured=>{ if(!authorization)return;if(authorization.scopeKey!==consumptionScope||!(Date.parse(authorization.notAfter)>Date.now())){setAuthorization(null);return;}const requestId=authorization.requestId;setAuthorization(null);const result=await requestHardwareModelCandidateV2(c.project_id,c.conversation_id,preview,{request_id:requestId,plan_id:c.engineering_plan!.plan_id,phase_id:binding[0].phase_id,plan_task_id:binding[0].plan_task_id});if(valid(captured)){if(result.status!=="REVIEW")throw new Error("MODEL_CANDIDATE_UNAVAILABLE");await onRefresh();}},true)}>请求绑定当前预览的模型候选</button><ModelConsumptionPanel scopeKey={consumptionScope} projectId={c.project_id} conversationId={c.conversation_id} selector={buildModelConsumptionSelector(preview,{plan_id:c.engineering_plan!.plan_id,phase_id:binding[0].phase_id,plan_task_id:binding[0].plan_task_id})} disabled={disabled} onAuthorizationChange={setAuthorization}/></>}</>}
    {head?.status==="REVIEW" && <><button disabled={disabled||busy||head.currentness!=="CURRENT"} onClick={()=>void action(captured=>decide(captured,"APPROVED"),true)}>独立批准新 Selection</button><button disabled={disabled||busy||head.currentness!=="CURRENT"} onClick={()=>void action(captured=>decide(captured,"REJECTED"),true)}>拒绝新 Selection</button></>}
    {head?.status==="APPROVED" && <button disabled={disabled||busy} onClick={()=>void action(captured=>decide(captured,"REVOKED"),true)}>撤销当前 Selection 审批</button>}
    {history.map(item=><p key={item.head.revision_id}>历史 {item.head.revision_number}：{item.head.status} / {item.head.currentness}</p>)}
    {error && <p role="alert">{error}</p>}
  </section>;
}
