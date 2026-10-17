import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DrcBaselinePanel } from "./DrcBaselinePanel";
import type { EdaImportRevision } from "../api/projectClient";
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const fp = `sha256:${"a".repeat(64)}`;
const revision: EdaImportRevision = { import_revision_id:"rev-1", import_revision_fingerprint:fp, active:true, created_at:"2026-10-11T00:00:00Z", backend:"KICAD", native_entry_relative_path:"board.kicad_pcb",native_format_family:"KICAD_PROJECT_DIRECTORY",native_format_versions:{"board.kicad_pcb":"20240108"},source_set_fingerprint:fp,parser_ruleset_id:"rules",supported_subset_id:"subset",source_currentness:"CURRENT",observation_currentness:"CURRENT",semantic_completeness:"PARTIAL",manifest:[{relative_path:"board.kicad_pcb",kind:"FILE",byte_size:5,sha256:"a".repeat(64),reference_digest:null,discovery_locator:null}],objects:[],issues:[],verified:false,can_modify:false };
const status = { operation_id:"baseline-op",project_id:"project-1",operation_type:"KICAD_PCB_DRC_BASELINE",status:"WAIT_APPROVAL",recovery_state:"NONE",receipt_fingerprint:null };
function button(box:HTMLElement,label:string) {const found=[...box.querySelectorAll<HTMLButtonElement>("button")].find(item=>item.textContent?.includes(label));if(!found)throw new Error(label);return found;}
function response(body:unknown,code=200) {return new Response(JSON.stringify(body),{status:code,headers:{"content-type":"application/json"}});}
function setup(mode:"normal"|"unavailable"="normal") {
 const calls:{url:string;method:string;body?:Record<string,unknown>}[]=[];let succeeded=false;
 const fetcher=vi.fn(async(url:unknown,init?:RequestInit)=>{
  const text=String(url);calls.push({url:text,method:init?.method??"GET",body:init?.body?JSON.parse(String(init.body)):undefined});
  if(text.includes("/eda-imports/"))return response(revision);
  if(text.includes("/drc-baseline-operations"))return mode==="unavailable"?response({error:"DRC_BASELINE_UNAVAILABLE"},409):response(status,201);
  if(text.includes("/approval")){succeeded=true;return response({grant_id:"grant",operation_id:"baseline-op",status:"ISSUED",fingerprint:fp});}
  if(text.includes("/status"))return response({...status,status:succeeded?"SUCCEEDED":"WAIT_APPROVAL",receipt_fingerprint:succeeded?fp:null});
  if(text.includes("/receipt"))return response({operation_id:"baseline-op",project_id:"project-1",operation_type:"KICAD_PCB_DRC_BASELINE",status:"SUCCEEDED",fingerprint:fp,evidence_references:["baseline-op-context","baseline-op-raw-report"],safe_diagnostics_summary:"DRC_BASELINE_OBSERVATION scope=BASELINE_COMPARISON_ONLY error_count=2 warning_count=3",artifact:{artifact_id:"baseline-op-context"}});
  throw new Error(text);
 });vi.stubGlobal("fetch",fetcher);
 const box=document.createElement("div");document.body.appendChild(box);const root=createRoot(box);
 return {calls,fetcher,box,root};
}
async function select(box:HTMLElement) {const picker=box.querySelector<HTMLSelectElement>('select[aria-label="baseline PCB"]')!;await act(async()=>{picker.value="board.kicad_pcb";picker.dispatchEvent(new Event("change",{bubbles:true}));});}
afterEach(()=>{vi.unstubAllGlobals();document.body.innerHTML="";});
describe("independent baseline Desktop",()=>{
 it("uses explicit PCB, exact three inputs, separate approve-queue and actual scoped observation counts",async()=>{
  const {box,root,calls}=setup();try{
   await act(async()=>root.render(<DrcBaselinePanel projectId="project-1" revision={revision} reviewerId="owner" reviewerScope="current"/>));
   expect(calls).toEqual([]);expect(button(box,"准备 baseline").disabled).toBe(true);
   await select(box);await act(async()=>button(box,"准备 baseline").click());
   expect(calls.filter(item=>item.method==="POST")).toHaveLength(1);
   expect(calls.find(item=>item.method==="POST")?.body).toEqual({import_revision_id:"rev-1",import_revision_fingerprint:fp,pcb_relative_path:"board.kicad_pcb"});
   expect(box.textContent).toContain("WAIT_APPROVAL");
   await act(async()=>button(box,"批准并排队").click());await act(async()=>button(box,"刷新 baseline").click());
   expect(calls.filter(item=>item.url.includes("/approval"))).toHaveLength(1);
   expect(box.textContent).toContain("errors 2；warnings 3");expect(box.textContent).toContain("BASELINE_COMPARISON_ONLY");expect(box.textContent).toContain("设计未验证");expect(box.textContent).not.toContain("board PASS");
  }finally{await act(async()=>root.unmount());}
 });
 it("unknown reviewer permits preparation and blocks approval",async()=>{
  const {box,root,calls}=setup();try{await act(async()=>root.render(<DrcBaselinePanel projectId="project-1" revision={revision}/>));await select(box);await act(async()=>button(box,"准备 baseline").click());expect(box.textContent).toContain("审批身份：UNKNOWN");expect(button(box,"批准并排队").disabled).toBe(true);expect(calls.some(item=>item.url.includes("/approval"))).toBe(false);}finally{await act(async()=>root.unmount());}
 });
 it("default unavailable is displayed as BLOCKED without an approval",async()=>{
  const {box,root,calls}=setup("unavailable");try{await act(async()=>root.render(<DrcBaselinePanel projectId="project-1" revision={revision} reviewerId="owner"/>));await select(box);await act(async()=>button(box,"准备 baseline").click());expect(box.textContent).toContain("BLOCKED：DRC_BASELINE_UNAVAILABLE");expect(calls.filter(item=>item.method==="POST")).toHaveLength(1);}finally{await act(async()=>root.unmount());}
 });
 for(const phase of ["prepare","approval"] as const)for(const drift of ["unmount","disabled","project","revision-aba","reviewer"] as const)it(`${phase} deferred GET then ${drift} never posts from old scope`,async()=>{
  const {box,root,calls,fetcher}=setup();let mounted=true;
  try{
   const render=(props:Partial<{projectId:string;revision:EdaImportRevision;reviewerId:string;disabled:boolean;reviewerScope:string}>={})=>root.render(<DrcBaselinePanel projectId="project-1" revision={revision} reviewerId="owner" reviewerScope="scope-1" {...props}/>);
   await act(async()=>render());await select(box);
   if(phase==="approval")await act(async()=>button(box,"准备 baseline").click());
   const before=calls.filter(item=>item.method==="POST").length;
   let release!:(response:Response)=>void;const deferred=new Promise<Response>(resolve=>{release=resolve;});fetcher.mockImplementationOnce(async()=>deferred);
   await act(async()=>button(box,phase==="prepare"?"准备 baseline":"批准并排队").click());
   if(drift==="unmount"){await act(async()=>root.unmount());mounted=false;}
   else if(drift==="disabled")await act(async()=>render({disabled:true}));
   else if(drift==="project")await act(async()=>render({projectId:"project-2"}));
   else if(drift==="reviewer")await act(async()=>render({reviewerId:"other",reviewerScope:"scope-2"}));
   else {await act(async()=>render({revision:{...revision,import_revision_id:"rev-2"}}));await act(async()=>render());}
   await act(async()=>release(response(phase==="prepare"?revision:status)));
   expect(calls.filter(item=>item.method==="POST")).toHaveLength(before);
  }finally{if(mounted)await act(async()=>root.unmount());}
 });
 for(const change of [{active:false},{source_currentness:"STALE" as const},{observation_currentness:"UNAVAILABLE" as const}])it("known inactive/stale C1 disables old WAIT_APPROVAL without POST",async()=>{
  const {box,root,calls}=setup();try{
   await act(async()=>root.render(<DrcBaselinePanel projectId="project-1" revision={revision} reviewerId="owner"/>));await select(box);await act(async()=>button(box,"准备 baseline").click());
   const before=calls.filter(item=>item.method==="POST").length;
   await act(async()=>root.render(<DrcBaselinePanel projectId="project-1" revision={{...revision,...change}} reviewerId="owner"/>));
   expect(button(box,"批准并排队").disabled).toBe(true);await act(async()=>button(box,"批准并排队").click());
   expect(calls.filter(item=>item.method==="POST")).toHaveLength(before);expect(button(box,"刷新 baseline").disabled).toBe(false);
  }finally{await act(async()=>root.unmount());}
 });

});
