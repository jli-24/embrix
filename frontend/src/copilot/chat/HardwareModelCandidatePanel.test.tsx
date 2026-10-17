import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, expect, it, vi } from "vitest";
import type { ConversationDetail } from "../api/projectClient";
import { markDesktopSessionActive } from "../api/sessionState";
import { HardwareModelCandidatePanel } from "./HardwareModelCandidatePanel";
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const fp=`sha256:${"1".repeat(64)}`;
const conversation = { project_id: "p", conversation_id: "c", engineering_plan: { plan_id: "plan", status: "APPROVED", model_operation_bindings: [{ phase_id: "phase", plan_task_id: "task", operation_id: "HARDWARE_SELECTION_PROPOSAL", stage_id: "HARDWARE_SELECTION" }] }, engineering_plan_review: { status: "APPROVED" } } as ConversationDetail;
const grant={schema_version:1,grant_id:"grant",preview_id:"preview",preview_fingerprint:fp,scope_fingerprint:fp,status:"ACTIVE",revision:1,not_after:"2099-01-01T00:00:00Z",counter_status:"UNKNOWN",generation_status:"UNKNOWN",total_external_request_count:null};
const response=(value:unknown)=>new Response(JSON.stringify(value));
function click(host:HTMLElement,label:string){Array.from(host.querySelectorAll("button")).find(x=>x.textContent===label)!.click();}
async function consent(host:HTMLElement){await act(async()=>click(host,"准备本次消费预览"));await act(async()=>host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());await act(async()=>click(host,"确认本次消费授权"));}
function fetcherForCandidate(failed=false){return vi.fn<typeof fetch>().mockImplementation(async(url,init)=>{
  if(String(url).endsWith("/preview"))return response({schema_version:1,status:"READY",blockers:[],preview:{schema_version:1,preview_id:"preview",fingerprint:fp,not_after:grant.not_after,scope:{project_id:"p",conversation_id:"c",request_id:JSON.parse(String(init?.body)).request_id,selected_model:{model_id:"gpt-5.6-luna"}}}});
  if(String(url).endsWith("/grants"))return response(grant);
  return failed?new Response(JSON.stringify({error:"HARDWARE_SELECTION_REJECTED"}),{status:503}):response({status:"REVIEW",operation_id:"HARDWARE_SELECTION_PROPOSAL"});
});}
beforeEach(()=>markDesktopSessionActive());
it("requires explicit consent and a separate generate click with the same immutable request id", async () => {
  const fetcher=fetcherForCandidate();vi.stubGlobal("fetch",fetcher);
  const host = document.createElement("div"), root = createRoot(host), refresh = vi.fn(async () => {});
  try {
    await act(async () => root.render(<HardwareModelCandidatePanel conversation={conversation} onRefresh={refresh} />));
    expect(fetcher).not.toHaveBeenCalled();expect(host.querySelector("button")!.disabled).toBe(true);
    await consent(host);expect(fetcher).toHaveBeenCalledTimes(2);expect(refresh).not.toHaveBeenCalled();
    await act(async()=>click(host,"请求硬件候选"));expect(fetcher).toHaveBeenCalledTimes(3);
    expect(String(fetcher.mock.calls[2][0])).toContain("/hardware-selection/model-candidate-proposal");
    const prepared=JSON.parse(String(fetcher.mock.calls[0][1]?.body));expect(prepared.selection_schema_version).toBe(1);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({request_id:prepared.request_id,plan_id:"plan",phase_id:"phase",plan_task_id:"task"});
    expect(host.textContent).toContain("待人工审核");expect(refresh).toHaveBeenCalledTimes(1);
  } finally { await act(async () => root.unmount()); vi.unstubAllGlobals(); }
});
it("keeps unapproved, ambiguous and already selected contexts at zero calls", async () => {
  const fetcher = vi.fn<typeof fetch>(); vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"), root = createRoot(host);
  try {
    for (const detail of [
      { ...conversation, engineering_plan_review: { status: "PENDING" } },
      { ...conversation, engineering_plan: { ...conversation.engineering_plan, model_operation_bindings: [...conversation.engineering_plan!.model_operation_bindings!, ...conversation.engineering_plan!.model_operation_bindings!] } },
      { ...conversation, hardware_selection: { status: "APPROVED" } },
    ]) {
      await act(async () => root.render(<HardwareModelCandidatePanel conversation={detail as ConversationDetail} onRefresh={async () => {}} />));
      expect(host.querySelector("button")).toBeNull();
    }
    expect(fetcher).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); vi.unstubAllGlobals(); }
});
it("does not turn unavailable generation into REVIEW or automatically retry it", async () => {
  const fetcher=fetcherForCandidate(true);vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"), root = createRoot(host);
  try {
    await act(async () => root.render(<HardwareModelCandidatePanel conversation={conversation} onRefresh={async () => {}} />));
    await consent(host);await act(async()=>click(host,"请求硬件候选"));
    expect(fetcher).toHaveBeenCalledTimes(3);expect(host.textContent).toContain("HARDWARE_CANDIDATE_UNAVAILABLE");expect(host.textContent).not.toContain("待人工审核");
  } finally { await act(async () => root.unmount()); vi.unstubAllGlobals(); }
});
