import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { ConversationDetail } from "../api/projectClient";
import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";
import { SchematicPreparationPanel } from "./SchematicPreparationPanel";
const fp = `sha256:${"a".repeat(64)}`;
const conversation = {
  project_id:"p",conversation_id:"c",fingerprint:fp,
  engineering_plan:{plan_id:"engineering-plan",requirement_id:"requirement",fingerprint:fp,status:"APPROVED"},
  engineering_plan_review:{status:"APPROVED"},
  hardware_selection:{status:"APPROVED",proposal:{proposal_id:"selection",fingerprint:fp,selected_components:[]}},
  design_truth:{truth_id:"truth",fingerprint:fp,components:[{component_id:"source",name:"Connector"},{component_id:"consumer",name:"Sensor"}],evidence_bindings:[{binding_id:"binding",source_id:"source"}]},
} as unknown as ConversationDetail;
const circuit = {domain:"CIRCUIT",owner:"ProjectCatalog",project_id:"p",object_id:"circuit",fingerprint:fp,currentness:"CURRENT",conversation_id:null};
const projection = {identity:{project_id:"p",tenant_id:"t"},authoritative_facts:[circuit]} as unknown as DesktopWorkspaceProjection;
const candidate = {candidate_id:"candidate",fingerprint:fp,tenant_id:"t",project_id:"p",conversation_id:"c",relations:[]};
function response(body:unknown) {return new Response(JSON.stringify(body),{status:200});}
function button(host:HTMLElement,label:string) {const found=[...host.querySelectorAll("button")].find((b)=>b.textContent===label);if(!found)throw new Error(label);return found;}
function change(host:HTMLElement,label:string,value:string) {const item=[...host.querySelectorAll("label")].find((l)=>l.textContent?.startsWith(label));const input=item?.querySelector("input,select") as HTMLInputElement|HTMLSelectElement;const setter=Object.getOwnPropertyDescriptor(input instanceof HTMLSelectElement ? HTMLSelectElement.prototype:HTMLInputElement.prototype,"value")!.set!;setter.call(input,value);input.dispatchEvent(new Event(input instanceof HTMLSelectElement ? "change":"input",{bubbles:true}));}
async function render(fetcher:typeof fetch,project=projection,c=conversation) {vi.stubGlobal("fetch",fetcher);const host=document.createElement("div");document.body.append(host);const root=createRoot(host);const refresh=vi.fn(async()=>{});await act(async()=>root.render(<SchematicPreparationPanel conversation={c} projection={project} onRefresh={refresh}/>));return {host,root,refresh};}
afterEach(()=>{vi.unstubAllGlobals();document.body.replaceChildren();});
it("exists before generation; mount performs no request and first UNAVAILABLE options allow explicit candidate/review",async()=>{
  const calls:Array<{path:string;method:string;body:Record<string,unknown>|null}>=[];
  let proposed=false,reviewed=false;
  const options=[{power_domain_id:"domain",domain_name:"3V3",supply_net_ref:"supply",ground_net_ref:"ground"}];
  const fetcher=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url),method=init?.method??"GET",body=init?.body?JSON.parse(String(init.body)):null;calls.push({path,method,body});
    if(path.endsWith("/review")){reviewed=true;return response({candidate,review:{decision:"APPROVED",candidate_fingerprint:fp,fingerprint:fp}});}
    if(path.endsWith("/project-supply")){
      if(method==="POST"){proposed=true;return response({candidate,review:null});}
      return response({status:reviewed?"APPROVED":proposed?"REVIEW":"UNAVAILABLE",current_options:options,candidate:proposed?candidate:null,review:reviewed?{decision:"APPROVED",fingerprint:fp}:null});
    }
    return response([]);
  }) as typeof fetch;
  const {host,root,refresh}=await render(fetcher);
  expect(calls).toHaveLength(0);expect(host.textContent).toContain("原理图生成前证据");
  await act(async()=>button(host,"读取当前待审依据").click());
  expect(calls.every((x)=>x.method==="GET")).toBe(true);
  expect(button(host,"提交供电候选").disabled).toBe(true);
  await act(async()=>{change(host,"当前供电域","domain");change(host,"标称电压 V","3.3");});
  await act(async()=>{
    for(const label of ["供电来源 Connector","用电器件 Sensor","依据 binding"]){const l=[...host.querySelectorAll("label")].find((x)=>x.textContent===label)!;(l.querySelector("input") as HTMLInputElement).click();}
  });
  await act(async()=>button(host,"提交供电候选").click());
  const post=calls.find((x)=>x.method==="POST")!;
  expect(post.body).toMatchObject({selection_id:"selection",plan_id:"engineering-plan",circuit_id:"circuit",design_truth_id:"truth"});
  expect(post.body?.relations).toEqual([{power_domain_id:"domain",supply_net_ref:"supply",ground_net_ref:"ground",source_component_refs:["source"],consumer_component_refs:["consumer"],nominal_voltage_v:"3.3",evidence_refs:["binding"]}]);
  expect(calls.some((x)=>x.path.endsWith("/review"))).toBe(false);
  await act(async()=>button(host,"批准供电关系").click());
  expect(calls.find((x)=>x.path.endsWith("/review"))?.body).toEqual({candidate_fingerprint:fp,decision:"APPROVED"});
  expect(refresh).toHaveBeenCalledTimes(2);await act(async()=>root.unmount());
});
it.each([{facts:[]},{facts:[circuit,{...circuit,object_id:"other"}]},{facts:[{...circuit,currentness:"STALE"}]},{facts:[{...circuit,owner:"Projection"}]}])("fails closed for absent/ambiguous/stale/noncanonical circuit selector",async({facts})=>{
  const fetcher=vi.fn<typeof fetch>();
  const {host,root}=await render(fetcher,{...projection,authoritative_facts:facts} as DesktopWorkspaceProjection);
  expect(button(host,"读取当前待审依据").disabled).toBe(true);
  expect(button(host,"提交供电候选").disabled).toBe(true);
  expect(host.textContent).toContain("BLOCKED");expect(fetcher).not.toHaveBeenCalled();await act(async()=>root.unmount());
});
it("re-reads current candidate before approval and rejects source-generation drift",async()=>{
  let supplyReads=0;
  const fetcher=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith("/project-supply")?response({status:"REVIEW",candidate:{...candidate,fingerprint:++supplyReads===1?fp:`sha256:${"b".repeat(64)}`},review:null}):response([])) as typeof fetch;
  const {host,root}=await render(fetcher);
  await act(async()=>button(host,"读取当前待审依据").click());
  await act(async()=>button(host,"批准供电关系").click());
  expect(vi.mocked(fetcher).mock.calls.every((call)=>!String(call[0]).endsWith("/review"))).toBe(true);
  expect(host.textContent).toContain("来源已变化");await act(async()=>root.unmount());
});
it("P3 is gated by approved plan before any generation receipt exists",async()=>{
  const {host,root}=await render(vi.fn<typeof fetch>());
  expect(button(host,"提交库映射候选").disabled).toBe(true);expect(host.textContent).toContain("需要获批 SchematicPlan");await act(async()=>root.unmount());
});
const physicalInstance={instance_id:"sensor-instance",hardware_slot_id:"sensor-slot",manufacturer:"Sensirion",mpn:"SHTC3",symbol:"SHTC3_EP:SHTC3_EP",footprint:"SHTC3_EP:SHTC3_EP"};
const physicalPlan={plan_id:"physical-plan",fingerprint:fp,component_instances:[physicalInstance],nets:[{net_id:"net-ground",name:"GND"},{net_id:"net-signal",name:"SDA"}]};
const prepared={...conversation,requirement_context:{fingerprint:fp},hardware_selection:{status:"APPROVED",proposal:{proposal_id:"selection",fingerprint:fp,selected_components:[{slot_id:"sensor-slot",source:{source_id:"sensor-source",fingerprint:fp,package:"DFN-4 2x2 mm"}}]}},schematic_planning:{status:"APPROVED",proposal:physicalPlan,approved_plan:physicalPlan}} as unknown as ConversationDetail;
async function check(host:HTMLElement,label:string){await act(async()=>{const item=[...host.querySelectorAll("label")].find((l)=>l.textContent===label)!;(item.querySelector("input") as HTMLInputElement).click();});}
function flowFetcher(calls:Array<{path:string;method:string;body:Record<string,unknown>|null}>,kind:"standard-applicability"|"library-terminal-maps"){
  let value:Record<string,unknown>|null=null,reviewed=false;
  return vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url),method=init?.method??"GET",body=init?.body?JSON.parse(String(init.body)):null;calls.push({path,method,body});
    if(path.includes(kind)&&path.endsWith("/review")){reviewed=true;return response({evidence:value,review:{decision:"APPROVED",fingerprint:fp}});}
    if(path.endsWith(`/${kind}`)){
      if(method==="POST"){value={...body,evidence_id:"evidence",fingerprint:fp,tenant_id:"t",project_id:"p",conversation_id:"c"};return response({evidence:value,review:null});}
      return response(value?[{evidence:value,currentness:reviewed?"CURRENT":"REVIEW",review:reviewed?{decision:"APPROVED",fingerprint:fp}:null}]:[]);
    }
    return response(path.endsWith("/project-supply")?{status:"UNAVAILABLE",current_options:[]}:[]);
  }) as typeof fetch;
}
it("P2 submits explicit physical target selectors and operating conditions bound to current EngineeringPlan, then reviews separately",async()=>{
  const calls:Array<{path:string;method:string;body:Record<string,unknown>|null}>=[];
  const {host,root}=await render(flowFetcher(calls,"standard-applicability"),projection,prepared);
  expect(calls).toHaveLength(0);
  await check(host,"目标 net SDA · net-signal");await check(host,"目标 Sensirion SHTC3 · sensor-instance");
  for(const [label,value] of [["规范 URI","https://example.test/standard.pdf"],["规范修订","revision"],["原件 SHA256",fp],["物理页","7"],["表","Table"],["行","Row"],["上限值","100000"],["voltage_min","3.2"],["voltage_max","3.4"],["temperature_min","20"],["temperature_max","30"]])await act(async()=>change(host,label,value));
  await act(async()=>button(host,"提交规范候选").click());
  expect(calls.find((x)=>x.method==="POST")?.body).toMatchObject({plan_id:"engineering-plan",plan_fingerprint:fp,requirement_id:"requirement",requirement_fingerprint:fp,target_net_ids:["net-signal"],target_components:[{component_instance_id:"sensor-instance",source_record_id:"sensor-source",source_record_fingerprint:fp,device_conditions:{voltage_min:"3.2",voltage_max:"3.4",temperature_min:"20",temperature_max:"30",mode:"I2C_STANDARD",package:"DFN-4 2x2 mm"}}],frequency_max_hz:"100000"});
  expect(calls.some((x)=>x.path.endsWith("/review"))).toBe(false);
  await act(async()=>button(host,"批准 evidence").click());
  expect(calls.find((x)=>x.path.endsWith("/review"))?.body).toEqual({evidence_fingerprint:fp,decision:"APPROVED"});await act(async()=>root.unmount());
});
it("P3 submits five explicitly selected terminal nets under the approved physical plan before any write or Receipt",async()=>{
  const calls:Array<{path:string;method:string;body:Record<string,unknown>|null}>=[];
  const {host,root}=await render(flowFetcher(calls,"library-terminal-maps"),projection,prepared);
  await act(async()=>change(host,"已批准器件","sensor-instance"));
  for(const [label,value] of [["Symbol 原件 URI","project://symbol.kicad_sym"],["Symbol SHA256",fp],["Footprint 原件 URI","project://SHTC3_EP.kicad_mod"],["Footprint SHA256",fp]])await act(async()=>change(host,label,value));
  for(const terminal of ["1","2","3","4","EP"])await act(async()=>change(host,`原厂端子 ${terminal} 的批准 net`,terminal==="EP"?"net-ground":"net-signal"));
  await act(async()=>button(host,"提交库映射候选").click());
  const body=calls.find((x)=>x.method==="POST")?.body;
  expect(body).toMatchObject({plan_id:"physical-plan",plan_fingerprint:fp,component_instance_id:"sensor-instance",source_record_id:"sensor-source",symbol_library_id:"SHTC3_EP:SHTC3_EP",footprint_library_id:"SHTC3_EP:SHTC3_EP"});
  expect(body?.terminal_bindings).toEqual([...["1","2","3","4"].map((t)=>({manufacturer_terminal:t,manufacturer_locator:{page:6,table:"Table 7",row:t},logical_symbol_pin:t,physical_pad_ids:[t],net_id:"net-signal"})),{manufacturer_terminal:"EP",manufacturer_locator:{section:"4 Interface Specifications",clause:"CENTER_PAD_INTERNAL_VSS_AND_OPTIONAL_PCB_GND"},logical_symbol_pin:"5",physical_pad_ids:["5"],net_id:"net-ground"}]);
  expect(calls.some((x)=>x.path.endsWith("/review"))).toBe(false);
  await act(async()=>button(host,"批准 evidence").click());
  expect(calls.every((x)=>!x.path.includes("operations"))).toBe(true);await act(async()=>root.unmount());
});

it.each(["source", "selection"])("stale %s flows through fresh P1 review and independent exact v2 plan decision",async(changeKind)=>{
  vi.stubGlobal("crypto",{randomUUID:()=>"request-v2-1"});
  const calls:Array<{path:string;method:string;body:Record<string,unknown>|null}>=[];
  const projectedFingerprint=`sha256:${"b".repeat(64)}`;
  const newSelection = {...prepared.hardware_selection!, proposal:{...prepared.hardware_selection!.proposal!, proposal_id:"selection-new", fingerprint:projectedFingerprint}};
  const projected={...prepared,hardware_selection:changeKind==="selection"?newSelection:prepared.hardware_selection,design_truth:{...prepared.design_truth,truth_id:"projected-truth",fingerprint:projectedFingerprint}} as ConversationDetail;
  const oldCandidate={...candidate,schema_version:3,design_truth_id:"truth",design_truth_fingerprint:fp,selection_id:"selection",selection_fingerprint:fp,circuit_id:"circuit",circuit_fingerprint:fp,plan_id:"engineering-plan",plan_fingerprint:fp,source_publications:[{source_id:"source",generation:1,publication_fingerprint:fp}]};
  const freshCandidate={...oldCandidate,candidate_id:"candidate-fresh",...(changeKind==="selection"?{selection_id:"selection-new",selection_fingerprint:projectedFingerprint,design_truth_id:"projected-truth",design_truth_fingerprint:projectedFingerprint}:{}),source_publications:[{source_id:"source",generation:3,publication_fingerprint:projectedFingerprint}]};
  const headPlan={...physicalPlan,plan_id:"replacement-plan"};
  const legacy={schema_version:2,revision_id:null,revision_number:0,predecessor_revision_id:null,plan_id:"physical-plan",plan_fingerprint:fp,is_current:false,currentness:"STALE",planning:prepared.schematic_planning,receipt_references:[{kind:"GENERATION",receipt_id:"old-receipt",fingerprint:fp}]};
  const revision={schema_version:2,revision_id:"revision-1",revision_number:1,predecessor_revision_id:null,plan_id:"replacement-plan",plan_fingerprint:fp,is_current:true,currentness:"CURRENT",planning:{status:"REVIEW",proposal:headPlan,approved_plan:null},receipt_references:[]};
  let head:Record<string,unknown>|null=null,supplyStage:0|1|2=0;
  const options=[{power_domain_id:"domain",domain_name:"3V3",supply_net_ref:"supply",ground_net_ref:"ground"}];
  const fetcher=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url),method=init?.method??"GET",body=init?.body?JSON.parse(String(init.body)):null;calls.push({path,method,body});
    if(path.includes("/schematic-plan/currentness?schema_version=2"))return response({schema_version:2,head,legacy,history:head?[head]:[]});
    if(path.endsWith("/schematic-plan/proposal")){head=revision;return response(head);}
    if(path.endsWith("/schematic-plan/decision")){head={...revision,planning:{...revision.planning,status:"APPROVED",approved_plan:headPlan}};return response(head);}
    if(path.endsWith("/project-supply/candidate-fresh/review")){supplyStage=2;return response({candidate:freshCandidate,review:{decision:"APPROVED",fingerprint:fp}});}
    if(path.endsWith("/project-supply")){
      if(method==="POST"){supplyStage=1;return response({candidate:freshCandidate,review:null});}
      return response({status:supplyStage===0?"STALE":supplyStage===1?"REVIEW":"APPROVED",candidate:supplyStage===0?oldCandidate:freshCandidate,review:supplyStage===2?{decision:"APPROVED",fingerprint:fp}:null,current_options:options});
    }
    return response([]);
  }) as typeof fetch;
  const {host,root}=await render(fetcher,projection,projected);
  expect(calls).toHaveLength(0);
  await act(async()=>button(host,"读取当前待审依据").click());
  await act(async()=>{change(host,"当前供电域","domain");change(host,"标称电压 V","3.3");});
  await act(async()=>{
    for(const label of ["供电来源 Connector","用电器件 Sensor","依据 binding"]){const l=[...host.querySelectorAll("label")].find((x)=>x.textContent===label)!;(l.querySelector("input") as HTMLInputElement).click();}
  });
  await act(async()=>button(host,"提交供电候选").click());
  const freshP1=calls.find((item)=>item.path.endsWith("/project-supply")&&item.method==="POST")!;
  expect(freshP1.body).toMatchObject(changeKind==="selection"?{design_truth_id:"projected-truth",design_truth_fingerprint:projectedFingerprint,selection_id:"selection-new",selection_fingerprint:projectedFingerprint,circuit_id:"circuit"}:{design_truth_id:"truth",design_truth_fingerprint:fp,selection_id:"selection",circuit_id:"circuit"});
  expect(calls.some((item)=>item.path.endsWith("/schematic-plan/proposal"))).toBe(false);
  await act(async()=>button(host,"批准供电关系").click());
  expect(calls.find((item)=>item.path.endsWith("/project-supply/candidate-fresh/review"))?.body).toEqual({candidate_fingerprint:fp,decision:"APPROVED"});
  await act(async()=>button(host,"读取计划头与历史").click());
  expect(host.textContent).toContain("旧计划 physical-plan · STALE · 收据引用 1");
  await act(async()=>button(host,"提交替换计划提案").click());
  const proposal=calls.find((item)=>item.path.endsWith("/schematic-plan/proposal"))!;
  expect(proposal.body).toEqual({schema_version:2,request_id:"request-v2-1",expected_head_revision_id:null,expected_head_plan_id:"physical-plan",expected_head_plan_fingerprint:fp});
  expect(calls.some((item)=>item.path.endsWith("/schematic-plan/decision"))).toBe(false);
  await act(async()=>button(host,"批准替换计划").click());
  const decision=calls.find((item)=>item.path.endsWith("/schematic-plan/decision"))!;
  expect(decision.body).toEqual({schema_version:2,revision_id:"revision-1",plan_id:"replacement-plan",plan_fingerprint:fp,decision:"APPROVED"});
  expect(host.textContent).toContain("修订 1 · replacement-plan");
  await act(async()=>root.unmount());
});
