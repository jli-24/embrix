import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect,it,vi } from "vitest";
import { SelectionRevisionPanel } from "./SelectionRevisionPanel";
import type { ConversationDetail } from "../api/projectClient";
(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
const fp="sha256:"+"1".repeat(64),nextFp="sha256:"+"2".repeat(64);
const head={revision_id:"revision-a",revision_number:1,selection_id:"selection-a",selection_fingerprint:fp,fingerprint:fp,status:"REVIEW",decision_fingerprint:null,currentness:"CURRENT",reason:null};
const conversation={project_id:"p",conversation_id:"c",fingerprint:fp,architecture_review:{status:"APPROVED"},engineering_plan_review:{status:"APPROVED"},selection_head:head} as ConversationDetail;
const preview={kind:"PREVIEW",schema_version:3,project_id:"p",conversation_id:"c",expected_head:["revision-a","selection-a",fp],preview_fingerprint:fp,root_slots:["slot"],selected_parents:[{slot_id:"slot",source_id:"source",source_fingerprint:fp}],required_slots:["slot"],choices:[{slot_id:"slot",source_id:"source",source_fingerprint:fp}]};
function click(host:HTMLElement,label:string){Array.from(host.querySelectorAll("button")).find(x=>x.textContent===label)!.click();}
it.each(["same-conversation-head","conversation","disabled","unmount"])("drops late preview after %s and never writes or refreshes",async change=>{
  let resolve!:(value:Response)=>void;
  const pending=new Promise<Response>(r=>{resolve=r;});
  const fetcher=vi.fn<typeof fetch>().mockReturnValueOnce(pending);
  vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),refresh=vi.fn(async()=>{});
  let unmounted=false;
  try{
    await act(async()=>root.render(<SelectionRevisionPanel conversation={conversation} onRefresh={refresh}/>));
    expect(fetcher).not.toHaveBeenCalled();
    await act(async()=>click(host,"读取替换候选"));
    if(change==="unmount"){await act(async()=>root.unmount());unmounted=true;}
    else {
      const next=change==="conversation"?{...conversation,conversation_id:"other"}:change==="same-conversation-head"?{...conversation,fingerprint:nextFp,selection_head:{...conversation.selection_head!,revision_id:"revision-b",selection_id:"selection-b",selection_fingerprint:nextFp}}:conversation;
      await act(async()=>root.render(<SelectionRevisionPanel conversation={next} disabled={change==="disabled"} onRefresh={refresh}/>));
    }
    await act(async()=>resolve(new Response(JSON.stringify({kind:"REVISION",schema_version:2,head,selection:null}))));
    expect(fetcher).toHaveBeenCalledTimes(1);expect(refresh).not.toHaveBeenCalled();
    expect(host.querySelector("select")).toBeNull();
  }finally{if(!unmounted)await act(async()=>root.unmount());vi.unstubAllGlobals();}
});
it("sends exact immutable revision selector and never approves from a new preview",async()=>{
  const fetcher=vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({kind:"REVISION",schema_version:2,head,selection:null}))).mockResolvedValueOnce(new Response(JSON.stringify(preview))).mockResolvedValueOnce(new Response(JSON.stringify({kind:"REVISION",schema_version:2,head:{...head,status:"REVIEW",revision_id:"revision-b",selection_id:"selection-b",selection_fingerprint:nextFp},selection:null})));
  vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"),root=createRoot(host),refresh=vi.fn(async()=>{});
  try{
    await act(async()=>root.render(<SelectionRevisionPanel conversation={conversation} onRefresh={refresh}/>));
    await act(async()=>click(host,"读取替换候选"));
    expect(fetcher).toHaveBeenCalledTimes(2);expect(host.querySelector("select")).not.toBeNull();
    await act(async()=>click(host,"提交新 Selection 候选"));
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toMatchObject({schema_version:3,parent_choices:preview.selected_parents,expected_head_revision_id:"revision-a",expected_head_selection_id:"selection-a",expected_head_selection_fingerprint:fp,preview_fingerprint:fp,selections:preview.choices});
    expect(String(fetcher.mock.calls[2][0])).toContain("/proposal");expect(refresh).toHaveBeenCalledTimes(1);
  }finally{await act(async()=>root.unmount());vi.unstubAllGlobals();}
});

it("withdraws old child tree on parent change and drops late selected-parent preview",async()=>{
  const rootA={slot_id:"root",source_id:"parent-a",source_fingerprint:fp},rootB={slot_id:"root",source_id:"parent-b",source_fingerprint:nextFp};
  const childA={slot_id:"child-a",source_id:"cap",source_fingerprint:fp};
  const tree={...preview,root_slots:["root"],selected_parents:[rootA],required_slots:["root","child-a"],choices:[rootA,rootB,childA]};
  let resolve!:(value:Response)=>void;const pending=new Promise<Response>(r=>{resolve=r;});
  const fetcher=vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({kind:"REVISION",schema_version:2,head,selection:null}))).mockResolvedValueOnce(new Response(JSON.stringify(tree))).mockReturnValueOnce(pending);
  vi.stubGlobal("fetch",fetcher);const host=document.createElement("div"),root=createRoot(host),refresh=vi.fn(async()=>{});
  const change=async(value:string)=>{await act(async()=>{const select=host.querySelector('[aria-label="root"]') as HTMLSelectElement;select.value=value;select.dispatchEvent(new Event("change",{bubbles:true}));});};
  try{
    await act(async()=>root.render(<SelectionRevisionPanel conversation={conversation} onRefresh={refresh}/>));
    await act(async()=>click(host,"读取替换候选"));expect(host.querySelector('[aria-label="child-a"]')).not.toBeNull();
    await change("parent-b");expect(host.querySelector('[aria-label="child-a"]')).toBeNull();
    expect(Array.from(host.querySelectorAll("button")).find(x=>x.textContent==="提交新 Selection 候选")!.disabled).toBe(true);
    await act(async()=>click(host,"读取所选父件的外围候选"));
    const query=new URL(String(fetcher.mock.calls[2][0]),"http://offline").searchParams;
    expect(JSON.parse(query.get("parent_choices")!)).toEqual([rootB]);
    await change("parent-a");
    await act(async()=>resolve(new Response(JSON.stringify({...tree,selected_parents:[rootB],required_slots:["root","child-b"],choices:[rootA,rootB,{...childA,slot_id:"child-b"}]}))));
    expect(host.querySelector('[aria-label="child-b"]')).toBeNull();expect(host.querySelector('[aria-label="child-a"]')).toBeNull();
    expect(refresh).not.toHaveBeenCalled();expect(fetcher).toHaveBeenCalledTimes(3);
  }finally{await act(async()=>root.unmount());vi.unstubAllGlobals();}
});
