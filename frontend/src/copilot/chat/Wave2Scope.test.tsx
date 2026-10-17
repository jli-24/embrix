import {StrictMode,act} from "react";
import {createRoot} from "react-dom/client";
import {afterEach,it,expect,vi} from "vitest";
import {SchematicPreparationPanel} from "./SchematicPreparationPanel";
import {SchematicEvidencePanel} from "./SchematicEvidencePanel";
import type {ConversationDetail} from "../api/projectClient";
import type {DesktopWorkspaceProjection} from "../api/desktopWorkspaceClient";
const fp=`sha256:${"a".repeat(64)}`;
const c={project_id:"p",conversation_id:"c",fingerprint:fp,engineering_plan:{plan_id:"ep",fingerprint:fp,status:"APPROVED"},engineering_plan_review:{status:"APPROVED"},hardware_selection:{status:"APPROVED",proposal:{proposal_id:"selection",fingerprint:fp,selected_components:[]}},design_truth:{truth_id:"truth",fingerprint:fp,components:[],evidence_bindings:[]},schematic_planning:{status:"APPROVED",approved_plan:{plan_id:"plan",fingerprint:fp,project_id:"p",conversation_id:"c"}},schematic_production:{status:"GENERATED",operation_id:"write",generation_receipt:{schematic_plan_fingerprint:fp,fingerprint:fp}}} as unknown as ConversationDetail;
const projection={identity:{tenant_id:"t",project_id:"p"},authoritative_facts:[{domain:"CIRCUIT",owner:"ProjectCatalog",project_id:"p",currentness:"CURRENT",object_id:"circuit",fingerprint:fp}],operations:{items:[]}} as unknown as DesktopWorkspaceProjection;
const candidate={candidate_id:"candidate",fingerprint:fp,tenant_id:"t",project_id:"p",conversation_id:"c",relations:[]};
const evidence={evidence_id:"evidence",fingerprint:fp,tenant_id:"t",project_id:"p",conversation_id:"c",plan_id:"plan",plan_fingerprint:fp};
function reply(value:unknown){return new Response(JSON.stringify(value),{status:200});}
function button(host:HTMLElement,label:string){return [...host.querySelectorAll("button")].find((b)=>b.textContent===label)!;}
afterEach(()=>{vi.unstubAllGlobals();document.body.replaceChildren();});
it.each(["supply","standard","readback","approval"].flatMap(kind=>["unmount","disabled","switch","rerender"].map(change=>({kind,change}))))("blocks pending $kind mutation after $change without publishing obsolete Owner refresh",async({kind,change})=>{
  let reads=0;let resume!:(r:Response)=>void;const pending=new Promise<Response>(resolve=>{resume=resolve;});const posts:string[]=[];const refresh=vi.fn(async()=>{});
  const current=()=>kind==="approval"?reply({operation_id:"parse",operation_type:"KICAD_SCHEMATIC_PARSE",tenant_id:"t",project_id:"p",status:"WAIT_APPROVAL",recovery_state:"NONE"}):kind==="supply"?reply({status:"REVIEW",candidate,review:null,current_options:[]}):reply([{evidence,currentness:kind==="readback"?"APPROVED":"REVIEW",review:kind==="readback"?{decision:"APPROVED",evidence_fingerprint:fp}:null}]);
  const fetcher=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url);
    if(init?.method==="POST"){
      posts.push(path);
      if(kind==="approval")return reply(path.endsWith("/approval")?{grant_id:"grant",fingerprint:fp}:{operation_id:"parse",operation_type:"KICAD_SCHEMATIC_PARSE",tenant_id:"t",project_id:"p",conversation_id:"c",requester_id:"u",expires_at:"2099-01-01T00:00:00Z",fingerprint:fp});
      if(kind==="readback")return reply({operation_id:"readback",operation_type:"KICAD_SCHEMATIC_READ_BACK",tenant_id:"t",project_id:"p",conversation_id:"c",requester_id:"u",expires_at:"2099-01-01T00:00:00Z",fingerprint:fp,library_map_evidence_id:"evidence",library_map_evidence_fingerprint:fp});
      return reply(kind==="supply"?{candidate,review:{decision:"APPROVED",fingerprint:fp}}:{evidence,review:{decision:"APPROVED",fingerprint:fp}});
    }
    const selected=path.split("?")[0].endsWith(kind==="supply"?"/project-supply":kind==="standard"?"/standard-applicability":kind==="approval"?"/parse/status":"/library-terminal-maps");
    if(selected)return ++reads===2?pending:current();
    if(path.split("?")[0].endsWith("/status"))return reply({operation_id:"readback",operation_type:"KICAD_SCHEMATIC_READ_BACK",tenant_id:"t",project_id:"p",status:"WAIT_APPROVAL",recovery_state:"NONE"});
    return reply(path.endsWith("/project-supply")?{status:"UNAVAILABLE",current_options:[]}:[]);
  }) as typeof fetch;
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);vi.stubGlobal("crypto",{randomUUID:()=>"nonce"});vi.stubGlobal("fetch",fetcher);const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
  const Panel=kind==="readback"||kind==="approval"?SchematicEvidencePanel:SchematicPreparationPanel;
  await act(async()=>root.render(<Panel key="old" conversation={c} projection={projection} onRefresh={refresh}/>));
  await act(async()=>button(host,kind==="readback"?"读取已审库映射选择器":kind==="approval"?"创建 Parse 操作":"读取当前待审依据").click());
  if(kind==="readback")await act(async()=>{const select=[...host.querySelectorAll("label")].find(l=>l.textContent?.startsWith("Read-back 库映射"))!.querySelector("select")!;select.value="evidence";select.dispatchEvent(new Event("change",{bubbles:true}));});
  const previousPosts=[...posts];
  await act(async()=>button(host,kind==="supply"?"批准供电关系":kind==="standard"?"批准 evidence":kind==="approval"?"批准 Parse":"创建 Read-back 操作").click());
  expect(reads).toBe(2);expect(posts).toEqual(previousPosts);
  if(change==="unmount")await act(async()=>root.unmount());
  else await act(async()=>root.render(<Panel key={change==="switch"?"new":"old"} conversation={change==="switch"||change==="rerender"?{...c,project_id:"p-new",conversation_id:"c-new"}:c} projection={projection} disabled={change==="disabled"} onRefresh={refresh}/>));
  await act(async()=>resume(await current()));
  expect(posts).toEqual(previousPosts);expect(refresh).not.toHaveBeenCalled();
  if(change!=="unmount")await act(async()=>root.unmount());
});

it("accepts a new explicit request after StrictMode mount and after disabling then re-enabling",async()=>{
  const calls:string[]=[];vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);vi.stubGlobal("fetch",vi.fn(async(url:RequestInfo|URL)=>{calls.push(String(url));return reply(String(url).endsWith("/project-supply")?{status:"UNAVAILABLE",current_options:[]}:[]);}) as typeof fetch);
  const host=document.createElement("div");document.body.append(host);const root=createRoot(host);const refresh=async()=>{};
  await act(async()=>root.render(<StrictMode><SchematicPreparationPanel conversation={c} projection={projection} onRefresh={refresh}/></StrictMode>));
  await act(async()=>button(host,"读取当前待审依据").click());expect(calls).toHaveLength(3);
  await act(async()=>root.render(<StrictMode><SchematicPreparationPanel conversation={c} projection={projection} disabled onRefresh={refresh}/></StrictMode>));
  await act(async()=>root.render(<StrictMode><SchematicPreparationPanel conversation={c} projection={projection} onRefresh={refresh}/></StrictMode>));
  await act(async()=>button(host,"读取当前待审依据").click());expect(calls).toHaveLength(6);await act(async()=>root.unmount());
});
