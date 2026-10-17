import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { HardwareModelCandidatePanel } from "./HardwareModelCandidatePanel";
import { SelectionRevisionPanel } from "./SelectionRevisionPanel";
import type { ConversationDetail } from "../api/projectClient";
import { markDesktopSessionActive } from "../api/sessionState";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const fp=`sha256:${"1".repeat(64)}`;
const base={project_id:"p",conversation_id:"c",tenant_id:"t",requester:"owner",fingerprint:fp,
  engineering_plan:{plan_id:"plan",status:"APPROVED",model_operation_bindings:[{operation_id:"HARDWARE_SELECTION_PROPOSAL",stage_id:"HARDWARE_SELECTION",phase_id:"phase",plan_task_id:"task"}]},
  engineering_plan_review:{status:"APPROVED"},architecture_review:{status:"APPROVED"}} as ConversationDetail;
const head={revision_id:"r",revision_number:1,selection_id:"s",selection_fingerprint:fp,fingerprint:fp,status:"APPROVED",decision_fingerprint:fp,currentness:"CURRENT",reason:null};
const parent={slot_id:"root",source_id:"source",source_fingerprint:fp};
const tree={kind:"PREVIEW",schema_version:3,project_id:"p",conversation_id:"c",expected_head:["r","s",fp],preview_fingerprint:fp,root_slots:["root"],selected_parents:[parent],required_slots:["root"],choices:[parent]};
const expiry="2099-01-01T00:00:00Z";
const grant={schema_version:1,grant_id:"grant",preview_id:"preview",preview_fingerprint:fp,scope_fingerprint:fp,status:"ACTIVE",revision:1,not_after:expiry,counter_status:"UNKNOWN",generation_status:"UNKNOWN",total_external_request_count:null};
const response=(value:unknown)=>new Response(JSON.stringify(value));
const button=(host:HTMLElement,label:string)=>Array.from(host.querySelectorAll("button")).find(x=>x.textContent===label)!;

it.each(["initial","replacement"])("does not post model candidate after grant expires without another render: %s",async mode=>{
  markDesktopSessionActive();
  const fetcher=vi.fn<typeof fetch>().mockImplementation(async(url,init)=>{
    const path=String(url);
    if(path.endsWith("/currentness"))return response({kind:"REVISION",schema_version:2,head,selection:null});
    if(path.includes("currentness?"))return response(tree);
    if(path.endsWith("/model-consumption/preview"))return response({schema_version:1,status:"READY",blockers:[],preview:{schema_version:1,preview_id:"preview",fingerprint:fp,not_after:expiry,scope:{project_id:"p",conversation_id:"c",request_id:JSON.parse(String(init?.body)).request_id,selected_model:{model_id:"gpt-5.6-luna"}}}});
    if(path.endsWith("/grants"))return response(grant);
    return response({status:"REVIEW",operation_id:"HARDWARE_SELECTION_PROPOSAL"});
  });
  vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),refresh=vi.fn(async()=>{});
  try{
    await act(async()=>root.render(mode==="initial"?<HardwareModelCandidatePanel conversation={base} onRefresh={refresh}/>:<SelectionRevisionPanel conversation={{...base,selection_head:head} as ConversationDetail} onRefresh={refresh}/>));
    if(mode==="replacement")await act(async()=>button(host,"读取替换候选").click());
    await act(async()=>button(host,"准备本次消费预览").click());
    await act(async()=>host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await act(async()=>button(host,"确认本次消费授权").click());
    const before=fetcher.mock.calls.length;
    vi.spyOn(Date,"now").mockReturnValue(Date.parse(expiry)+1);
    await act(async()=>button(host,mode==="initial"?"请求硬件候选":"请求绑定当前预览的模型候选").click());
    expect(fetcher).toHaveBeenCalledTimes(before);
    expect(refresh).not.toHaveBeenCalled();
  }finally{await act(async()=>root.unmount());vi.restoreAllMocks();vi.unstubAllGlobals();}
});
