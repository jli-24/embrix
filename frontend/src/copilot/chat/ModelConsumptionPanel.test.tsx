import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, expect, it, vi } from "vitest";
import { buildModelConsumptionSelector } from "../api/modelConsumptionClient";
import { markDesktopSessionActive } from "../api/sessionState";
import { ModelConsumptionPanel } from "./ModelConsumptionPanel";
(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
const fp=`sha256:${"1".repeat(64)}`;
const selector=buildModelConsumptionSelector(null,{plan_id:"plan",phase_id:"phase",plan_task_id:"task"});
const grant={schema_version:1,grant_id:"grant",preview_id:"preview",preview_fingerprint:fp,scope_fingerprint:fp,status:"ACTIVE",revision:1,not_after:"2099-01-01T00:00:00Z",counter_status:"UNKNOWN",generation_status:"UNKNOWN",total_external_request_count:null};
const ready=(requestId:string)=>({schema_version:1,status:"READY",blockers:[],preview:{schema_version:1,preview_id:"preview",fingerprint:fp,not_after:"2099-01-01T00:00:00Z",scope:{project_id:"p",conversation_id:"c",request_id:requestId,selected_model:{model_id:"gpt-5.6-luna"}}}});
function button(host:HTMLElement,label:string){return Array.from(host.querySelectorAll("button")).find(x=>x.textContent===label)!;}
const response=(value:unknown)=>new Response(JSON.stringify(value));
beforeEach(()=>markDesktopSessionActive());

it("requires separate preparation consent confirmation GET and revoke; never generates or polls",async()=>{
  const fetcher=vi.fn<typeof fetch>().mockImplementation(async (url,init)=>{
    if(String(url).endsWith("/preview"))return response(ready(JSON.parse(String(init?.body)).request_id));
    if(String(url).endsWith("/revoke"))return response({...grant,status:"REVOKED",revision:2});
    return response(grant);
  });vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),changed=vi.fn();
  try{
    await act(async()=>root.render(<ModelConsumptionPanel projectId="p" conversationId="c" scopeKey="owner-head" selector={selector} onAuthorizationChange={changed}/>));
    expect(fetcher).not.toHaveBeenCalled();
    await act(async()=>button(host,"准备本次消费预览").click());
    expect(fetcher).toHaveBeenCalledTimes(1);expect(button(host,"确认本次消费授权").disabled).toBe(true);
    await act(async()=>host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async()=>button(host,"确认本次消费授权").click());
    expect(fetcher).toHaveBeenCalledTimes(2);
    const requestId=JSON.parse(String(fetcher.mock.calls[0][1]?.body)).request_id;
    expect(changed).toHaveBeenLastCalledWith({scopeKey:"owner-head",requestId,notAfter:grant.not_after});
    expect(host.textContent).toContain("UNKNOWN");expect(host.textContent).toContain("USD 0.10");
    await act(async()=>button(host,"读取授权状态").click());expect(fetcher).toHaveBeenCalledTimes(3);
    await act(async()=>button(host,"撤销本次消费授权").click());expect(fetcher).toHaveBeenCalledTimes(4);
    expect(changed).toHaveBeenLastCalledWith(null);expect(host.textContent).toContain("REVOKED");
    expect(fetcher.mock.calls.every(x=>String(x[0]).includes("/model-consumption/"))).toBe(true);
    await act(async()=>new Promise(resolve=>setTimeout(resolve,20)));expect(fetcher).toHaveBeenCalledTimes(4);
  }finally{await act(async()=>root.unmount());vi.unstubAllGlobals();}
});

it.each(["owner","head","disabled","unmount"])("drops late preparation after %s changes",async change=>{
  let finish!:(v:Response)=>void;const pending=new Promise<Response>(r=>{finish=r;});
  const fetcher=vi.fn<typeof fetch>().mockReturnValue(pending);vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),changed=vi.fn();let unmounted=false;
  try{
    const render=(scopeKey="scope",disabled=false)=>root.render(<ModelConsumptionPanel projectId="p" conversationId="c" scopeKey={scopeKey} selector={selector} disabled={disabled} onAuthorizationChange={changed}/>);
    await act(async()=>render());await act(async()=>button(host,"准备本次消费预览").click());
    const requestId=JSON.parse(String(fetcher.mock.calls[0][1]?.body)).request_id;
    if(change==="unmount"){await act(async()=>root.unmount());unmounted=true;}
    else await act(async()=>render(change==="disabled"?"scope":change,change==="disabled"));
    await act(async()=>finish(response(ready(requestId))));
    expect(fetcher).toHaveBeenCalledTimes(1);expect(changed.mock.calls.every(x=>x[0]===null)).toBe(true);
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  }finally{if(!unmounted)await act(async()=>root.unmount());vi.unstubAllGlobals();}
});

it("withdraws consent on late confirm and never reuses another head's authorization",async()=>{
  let finish!:(v:Response)=>void;const pending=new Promise<Response>(r=>{finish=r;});
  const fetcher=vi.fn<typeof fetch>().mockImplementationOnce(async(_,init)=>response(ready(JSON.parse(String(init?.body)).request_id))).mockReturnValueOnce(pending);vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),changed=vi.fn();
  try{
    const render=(scopeKey:string)=>root.render(<ModelConsumptionPanel projectId="p" conversationId="c" scopeKey={scopeKey} selector={selector} onAuthorizationChange={changed}/>);
    await act(async()=>render("head-a"));await act(async()=>button(host,"准备本次消费预览").click());
    await act(async()=>host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await act(async()=>button(host,"确认本次消费授权").click());
    await act(async()=>render("head-b"));await act(async()=>finish(response(grant)));
    expect(changed.mock.calls.every(x=>x[0]===null)).toBe(true);expect(host.textContent).not.toContain("授权状态：ACTIVE");
  }finally{await act(async()=>root.unmount());vi.unstubAllGlobals();}
});

it.each(["BLOCKED","HTTP_ERROR","EXPIRED"])("keeps %s at zero confirmation and zero generation",async kind=>{
  const fetcher=vi.fn<typeof fetch>().mockImplementation(async(_,init)=>kind==="HTTP_ERROR"?new Response(JSON.stringify({detail:"secret/private body"}),{status:503}):response(kind==="BLOCKED"?{schema_version:1,status:"BLOCKED",preview:null,blockers:["ACCOUNT_AUTHORITY_UNAVAILABLE"]}:{...ready(JSON.parse(String(init?.body)).request_id),preview:{...ready("x").preview,scope:ready(JSON.parse(String(init?.body)).request_id).preview.scope,not_after:"2000-01-01T00:00:00Z"}}));vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),changed=vi.fn();
  try{
    await act(async()=>root.render(<ModelConsumptionPanel projectId="p" conversationId="c" scopeKey="scope" selector={selector} onAuthorizationChange={changed}/>));
    await act(async()=>button(host,"准备本次消费预览").click());
    expect(fetcher).toHaveBeenCalledTimes(1);expect(host.textContent).not.toContain("secret/private body");
    expect(changed.mock.calls.every(x=>x[0]===null)).toBe(true);
    expect(button(host,"确认本次消费授权")?.disabled ?? true).toBe(true);
  }finally{await act(async()=>root.unmount());vi.unstubAllGlobals();}
});

it("restores a grant only on explicit GET and does not authorize generation from history alone",async()=>{
  const fetcher=vi.fn<typeof fetch>().mockResolvedValue(response(grant));vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),changed=vi.fn();
  try{
    await act(async()=>root.render(<ModelConsumptionPanel projectId="p" conversationId="c" scopeKey="scope" selector={selector} onAuthorizationChange={changed}/>));
    const field=host.querySelector<HTMLInputElement>('input[aria-label="授权 ID"]')!;
    await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(field,"grant");field.dispatchEvent(new Event("input",{bubbles:true}));});
    expect(fetcher).not.toHaveBeenCalled();await act(async()=>button(host,"读取授权状态").click());
    expect(fetcher).toHaveBeenCalledTimes(1);expect(fetcher.mock.calls[0][1]?.method).toBe("GET");
    expect(host.textContent).toContain("尚未绑定本界面的当前消费预览");
    expect(changed.mock.calls.every(x=>x[0]===null)).toBe(true);expect(host.textContent).toContain("UNKNOWN");
  }finally{await act(async()=>root.unmount());vi.unstubAllGlobals();}
});

it("serializes double preparation clicks without retries",async()=>{
  let finish!:(v:Response)=>void;const pending=new Promise<Response>(r=>{finish=r;});
  const fetcher=vi.fn<typeof fetch>().mockReturnValue(pending);vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host);
  try{
    await act(async()=>root.render(<ModelConsumptionPanel projectId="p" conversationId="c" scopeKey="scope" selector={selector} onAuthorizationChange={()=>{}}/>));
    await act(async()=>{button(host,"准备本次消费预览").click();button(host,"准备本次消费预览").click();});
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async()=>finish(response(ready(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).request_id))));
    expect(fetcher).toHaveBeenCalledTimes(1);
  }finally{await act(async()=>root.unmount());vi.unstubAllGlobals();}
});
