import type { SelectionChoice, SelectionTreePreview } from "./projectClient";
import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError,
  readDesktopSessionErrorCode } from "./sessionState";

export type ModelConsumptionTask = {plan_id:string;phase_id:string;plan_task_id:string};
export type ModelConsumptionSelector = ModelConsumptionTask & {
  schema_version:1; selection_schema_version:1|2|3; operation_id:"HARDWARE_SELECTION_PROPOSAL";
  expected_head_revision_id:string|null; expected_head_selection_id:string|null;
  expected_head_selection_fingerprint:string|null; selection_preview_fingerprint:string|null;
  parent_choices:SelectionChoice[];
};
export type ModelConsumptionPreviewRequest = ModelConsumptionSelector & {request_id:string};
// Deliberately project only the fields the UI needs. Account evidence, prompts,
// credentials, provider objects and the complete backend scope are not retained.
export type ModelConsumptionPreview = {preview_id:string;fingerprint:string;not_after:string;
  scope:{project_id:string;conversation_id:string;request_id:string;model_id:string}};
export type ModelConsumptionPreparation = {schema_version:1;status:"READY"|"BLOCKED";
  preview:ModelConsumptionPreview|null;blockers:string[]};
export type ModelConsumptionGrant = {schema_version:1;grant_id:string;preview_id:string;
  preview_fingerprint:string;scope_fingerprint:string;status:"ACTIVE"|"REVOKED"|"EXPIRED"|"STALE";
  revision:number;not_after:string;counter_status:string;generation_status:string;
  total_external_request_count:number|null};

export function buildModelConsumptionSelector(preview:SelectionTreePreview|null,task:ModelConsumptionTask):ModelConsumptionSelector {
  return {schema_version:1,selection_schema_version:preview?.schema_version??1,
    operation_id:"HARDWARE_SELECTION_PROPOSAL",plan_id:task.plan_id,phase_id:task.phase_id,plan_task_id:task.plan_task_id,
    expected_head_revision_id:preview?.expected_head[0]??null,
    expected_head_selection_id:preview?.expected_head[1]??null,
    expected_head_selection_fingerprint:preview?.expected_head[2]??null,
    selection_preview_fingerprint:preview?.preview_fingerprint??null,
    parent_choices:preview?.schema_version===3?preview.selected_parents.map(x=>({slot_id:x.slot_id,source_id:x.source_id,source_fingerprint:x.source_fingerprint})):[]};
}
const invalid=():never=>{throw new Error("MODEL_CONSUMPTION_RESPONSE_INVALID");};
function record(value:unknown):Record<string,unknown>{if(!value||typeof value!=="object"||Array.isArray(value))return invalid();return value as Record<string,unknown>;}
function text(value:unknown):string{if(typeof value!=="string"||!value.trim()||value.length>512)return invalid();return value;}
function fingerprint(value:unknown):string{const result=text(value);if(!/^sha256:[a-f0-9]{64}$/.test(result))return invalid();return result;}
function date(value:unknown):string{const result=text(value);if(!Number.isFinite(Date.parse(result)))return invalid();return result;}
function code(value:unknown):string{const result=text(value);if(!/^[A-Z][A-Z0-9_:-]{0,127}$/.test(result))return invalid();return result;}
const base=(p:string,c:string)=>`/api/v1/copilot/projects/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/model-consumption`;
async function request(path:string,method:"GET"|"POST",body:object|undefined,fetcher:typeof fetch):Promise<unknown>{
  if(isDesktopSessionExpired())throw new Error(DESKTOP_SESSION_EXPIRED);
  let response:Response;
  try{response=await fetcher(path,{method,credentials:"same-origin",headers:{"Content-Type":"application/json"},...(body?{body:JSON.stringify(body)}:{})});}
  catch{throw new Error("MODEL_CONSUMPTION_UNAVAILABLE");}
  if(!response.ok){
    if(response.status===401){
      let payload:unknown=null;try{payload=await response.json();}catch{/* Fixed failure below. */}
      const error=mapDesktopSessionError(response.status,readDesktopSessionErrorCode(payload));
      if(error===DESKTOP_SESSION_EXPIRED)throw new Error(DESKTOP_SESSION_EXPIRED);
    }
    throw new Error("MODEL_CONSUMPTION_UNAVAILABLE");
  }
  try{return await response.json();}catch{throw new Error("MODEL_CONSUMPTION_RESPONSE_INVALID");}
}
function parseGrant(value:unknown,expectedId?:string):ModelConsumptionGrant{
  const x=record(value),status=code(x.status);
  if(x.schema_version!==1||!["ACTIVE","REVOKED","EXPIRED","STALE"].includes(status)
    ||typeof x.revision!=="number"||!Number.isSafeInteger(x.revision)||x.revision<1
    ||(x.total_external_request_count!==null&&(typeof x.total_external_request_count!=="number"
      ||!Number.isInteger(x.total_external_request_count)||x.total_external_request_count<0||x.total_external_request_count>2)))return invalid();
  const result:ModelConsumptionGrant={schema_version:1,grant_id:text(x.grant_id),preview_id:text(x.preview_id),
    preview_fingerprint:fingerprint(x.preview_fingerprint),scope_fingerprint:fingerprint(x.scope_fingerprint),
    status:status as ModelConsumptionGrant["status"],revision:x.revision,not_after:date(x.not_after),
    counter_status:code(x.counter_status),generation_status:code(x.generation_status),
    total_external_request_count:x.total_external_request_count as number|null};
  if(expectedId!==undefined&&result.grant_id!==expectedId)return invalid();
  return result;
}
export async function prepareModelConsumption(p:string,c:string,input:ModelConsumptionPreviewRequest,fetcher:typeof fetch=fetch):Promise<ModelConsumptionPreparation>{
  // Explicit projection prevents extra runtime caller keys becoming authority.
  const body={schema_version:1,operation_id:"HARDWARE_SELECTION_PROPOSAL",selection_schema_version:input.selection_schema_version,
    request_id:input.request_id,plan_id:input.plan_id,phase_id:input.phase_id,plan_task_id:input.plan_task_id,
    expected_head_revision_id:input.expected_head_revision_id,expected_head_selection_id:input.expected_head_selection_id,
    expected_head_selection_fingerprint:input.expected_head_selection_fingerprint,selection_preview_fingerprint:input.selection_preview_fingerprint,
    parent_choices:input.parent_choices.map(x=>({slot_id:x.slot_id,source_id:x.source_id,source_fingerprint:x.source_fingerprint}))};
  const x=record(await request(`${base(p,c)}/preview`,"POST",body,fetcher));
  if(x.schema_version!==1||!["READY","BLOCKED"].includes(String(x.status))||!Array.isArray(x.blockers))return invalid();
  const blockers=x.blockers.map(code);
  if(x.status==="BLOCKED"){
    if(x.preview!==null)return invalid();
    return {schema_version:1,status:"BLOCKED",preview:null,blockers};
  }
  const v=record(x.preview),scope=record(v.scope),model=record(scope.selected_model);
  if(v.schema_version!==1||scope.project_id!==p||scope.conversation_id!==c||scope.request_id!==input.request_id||blockers.length)return invalid();
  return {schema_version:1,status:"READY",blockers,preview:{preview_id:text(v.preview_id),fingerprint:fingerprint(v.fingerprint),
    not_after:date(v.not_after),scope:{project_id:p,conversation_id:c,request_id:input.request_id,model_id:text(model.model_id)}}};
}
export async function confirmModelConsumption(p:string,c:string,id:string,fp:string,fetcher:typeof fetch=fetch):Promise<ModelConsumptionGrant>{
  const result=parseGrant(await request(`${base(p,c)}/grants`,"POST",{preview_id:id,preview_fingerprint:fp,consent:true},fetcher));
  if(result.preview_id!==id||result.preview_fingerprint!==fp)return invalid();
  return result;
}
export async function readModelConsumptionGrant(p:string,c:string,id:string,fetcher:typeof fetch=fetch):Promise<ModelConsumptionGrant>{
  return parseGrant(await request(`${base(p,c)}/grants/${encodeURIComponent(id)}`,"GET",undefined,fetcher),id);
}
export async function revokeModelConsumptionGrant(p:string,c:string,id:string,revision:number,fetcher:typeof fetch=fetch):Promise<ModelConsumptionGrant>{
  return parseGrant(await request(`${base(p,c)}/grants/${encodeURIComponent(id)}/revoke`,"POST",{expected_revision:revision},fetcher),id);
}
