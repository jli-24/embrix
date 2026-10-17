import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect,it,vi } from "vitest";
import { SelectionRevisionPanel } from "./SelectionRevisionPanel";
import type { ConversationDetail } from "../api/projectClient";
import { markDesktopSessionActive } from "../api/sessionState";
(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
const fp=`sha256:${"1".repeat(64)}`;
const choice={slot_id:"root",source_id:"parent",source_fingerprint:fp};
const head={revision_id:"r",revision_number:1,selection_id:"s",selection_fingerprint:fp,fingerprint:fp,status:"APPROVED",decision_fingerprint:fp,currentness:"CURRENT",reason:null};
const preview={kind:"PREVIEW",schema_version:3,project_id:"p",conversation_id:"c",expected_head:["r","s",fp],preview_fingerprint:fp,root_slots:["root"],selected_parents:[choice],required_slots:["root"],choices:[choice]};
const conversation={project_id:"p",conversation_id:"c",tenant_id:"t",requester:"owner",fingerprint:fp,selection_head:head,engineering_plan:{plan_id:"plan",status:"APPROVED",model_operation_bindings:[{operation_id:"HARDWARE_SELECTION_PROPOSAL",stage_id:"HARDWARE_SELECTION",phase_id:"phase",plan_task_id:"task"}]},engineering_plan_review:{status:"APPROVED"},architecture_review:{status:"APPROVED"}} as ConversationDetail;
const grant={schema_version:1,grant_id:"grant",preview_id:"consumption",preview_fingerprint:fp,scope_fingerprint:fp,status:"ACTIVE",revision:1,not_after:"2099-01-01T00:00:00Z",counter_status:"UNKNOWN",generation_status:"UNKNOWN",total_external_request_count:null};
const response=(value:unknown)=>new Response(JSON.stringify(value));
function button(host:HTMLElement,label:string){return Array.from(host.querySelectorAll("button")).find(x=>x.textContent===label)!;}
it.each(["generate","owner-change"])("binds v3 tree and consent request id without automatic model dispatch: %s",async mode=>{
  markDesktopSessionActive();
  const fetcher=vi.fn<typeof fetch>().mockImplementation(async(url,init)=>{
    const path=String(url);
    if(path.endsWith("/currentness"))return response({kind:"REVISION",schema_version:2,head,selection:null});
    if(path.includes("currentness?"))return response(preview);
    if(path.endsWith("/model-consumption/preview"))return response({schema_version:1,status:"READY",blockers:[],preview:{schema_version:1,preview_id:"consumption",fingerprint:fp,not_after:grant.not_after,scope:{project_id:"p",conversation_id:"c",request_id:JSON.parse(String(init?.body)).request_id,selected_model:{model_id:"gpt-5.6-luna"}}}});
    if(path.endsWith("/grants"))return response(grant);
    return response({status:"REVIEW",operation_id:"HARDWARE_SELECTION_PROPOSAL"});
  });vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),refresh=vi.fn(async()=>{});
  try{
    await act(async()=>root.render(<SelectionRevisionPanel conversation={conversation} onRefresh={refresh}/>));
    expect(fetcher).not.toHaveBeenCalled();await act(async()=>button(host,"读取替换候选").click());
    expect(button(host,"请求绑定当前预览的模型候选").disabled).toBe(true);
    await act(async()=>button(host,"准备本次消费预览").click());
    await act(async()=>host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await act(async()=>button(host,"确认本次消费授权").click());
    expect(fetcher).toHaveBeenCalledTimes(4);expect(refresh).not.toHaveBeenCalled();
    const prepared=JSON.parse(String(fetcher.mock.calls[2][1]?.body));
    expect(prepared).toMatchObject({schema_version:1,selection_schema_version:3,selection_preview_fingerprint:fp,parent_choices:[choice],expected_head_revision_id:"r",expected_head_selection_id:"s",expected_head_selection_fingerprint:fp});
    if(mode==="owner-change"){
      await act(async()=>root.render(<SelectionRevisionPanel conversation={{...conversation,requester:"other"}} onRefresh={refresh}/>));
      expect(host.textContent).not.toContain("授权状态：ACTIVE");expect(fetcher).toHaveBeenCalledTimes(4);
    }else{
      await act(async()=>button(host,"请求绑定当前预览的模型候选").click());
      expect(fetcher).toHaveBeenCalledTimes(5);const sent=JSON.parse(String(fetcher.mock.calls[4][1]?.body));
      expect(sent.request_id).toBe(prepared.request_id);expect(sent.parent_choices).toEqual(prepared.parent_choices);
      expect(sent.schema_version).toBe(3);expect(sent.preview_fingerprint).toBe(fp);expect(refresh).toHaveBeenCalledTimes(1);
    }
  }finally{await act(async()=>root.unmount());vi.unstubAllGlobals();}
});
