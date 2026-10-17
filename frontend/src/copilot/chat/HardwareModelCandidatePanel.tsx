import { useEffect, useRef, useState } from "react";
import { requestHardwareModelCandidate, type ConversationDetail } from "../api/projectClient";

import { buildModelConsumptionSelector } from "../api/modelConsumptionClient";
import { ModelConsumptionPanel, type ConsumptionAuthorization } from "./ModelConsumptionPanel";

type Props = { conversation: ConversationDetail; disabled?: boolean; onRefresh: () => Promise<void> };
export function HardwareModelCandidatePanel({ conversation, disabled = false, onRefresh }: Props) {
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [state, setState] = useState<string | null>(null);
  const [authorization,setAuthorization]=useState<ConsumptionAuthorization|null>(null);
  const plan = conversation.engineering_plan;
  const bindings = plan?.model_operation_bindings?.filter((item) => item.operation_id === "HARDWARE_SELECTION_PROPOSAL" && item.stage_id === "HARDWARE_SELECTION") ?? [];
  const scopeKey=JSON.stringify([disabled,conversation.tenant_id,conversation.requester,conversation.project_id,conversation.conversation_id,conversation.fingerprint,plan,conversation.engineering_plan_review,conversation.selection_head]);
  const current=useRef(scopeKey);current.current=scopeKey;
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{setAuthorization(null);setState(null);setBusy(false);lock.current=false;},[scopeKey]);
  const authorized=authorization?.scopeKey===scopeKey&&Date.parse(authorization.notAfter)>Date.now();
  if (plan?.status !== "APPROVED" || conversation.engineering_plan_review?.status !== "APPROVED" || bindings.length !== 1 || conversation.hardware_selection || conversation.selection_head) return null;
  async function requestCandidate() {
    if (disabled || lock.current || !plan || !authorization) return;
    if (authorization.scopeKey !== scopeKey || !(Date.parse(authorization.notAfter) > Date.now())) { setAuthorization(null); return; }
    const captured=scopeKey,requestId=authorization.requestId;setAuthorization(null);
    lock.current = true; setBusy(true); setState(null);
    try {
      const result = await requestHardwareModelCandidate(conversation.project_id, conversation.conversation_id, {
        request_id: requestId, plan_id: plan.plan_id,
        phase_id: bindings[0].phase_id, plan_task_id: bindings[0].plan_task_id,
      });
      if(!mounted.current||current.current!==captured)return;
      if (result.status !== "REVIEW" || result.operation_id !== "HARDWARE_SELECTION_PROPOSAL") throw new Error("invalid candidate response");
      setState("REVIEW");
      await onRefresh();
    } catch { if(mounted.current&&current.current===captured)setState("HARDWARE_CANDIDATE_UNAVAILABLE"); }
    finally { if(mounted.current&&current.current===captured){lock.current = false; setBusy(false);} }
  }
  return <section aria-label="硬件模型候选" className="ide-review-card">
    <button type="button" disabled={disabled || busy || !authorized} onClick={() => void requestCandidate()}>请求硬件候选</button>
    <ModelConsumptionPanel scopeKey={scopeKey} projectId={conversation.project_id} conversationId={conversation.conversation_id} selector={buildModelConsumptionSelector(null,{plan_id:plan.plan_id,phase_id:bindings[0].phase_id,plan_task_id:bindings[0].plan_task_id})} disabled={disabled} onAuthorizationChange={setAuthorization}/>
    {state === "REVIEW" ? <p role="status">硬件候选已生成，待人工审核；尚未批准设计。</p> : state && <p role="alert">{state}</p>}
  </section>;
}
