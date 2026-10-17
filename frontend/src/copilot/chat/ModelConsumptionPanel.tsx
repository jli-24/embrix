import { useEffect, useRef, useState } from "react";
import { confirmModelConsumption, prepareModelConsumption, readModelConsumptionGrant, revokeModelConsumptionGrant,
  type ModelConsumptionGrant, type ModelConsumptionPreparation, type ModelConsumptionSelector } from "../api/modelConsumptionClient";

export type ConsumptionAuthorization={scopeKey:string;requestId:string;notAfter:string};
type Props={projectId:string;conversationId:string;scopeKey:string;selector:ModelConsumptionSelector;disabled?:boolean;
  onAuthorizationChange:(value:ConsumptionAuthorization|null)=>void};
type Display={identity:string;preparation:ModelConsumptionPreparation|null;grant:ModelConsumptionGrant|null};
export function ModelConsumptionPanel({projectId,conversationId,scopeKey,selector,disabled=false,onAuthorizationChange}:Props){
  const identity=JSON.stringify([disabled,projectId,conversationId,scopeKey,selector]);
  const current=useRef(identity);current.current=identity;
  const mounted=useRef(true),lock=useRef(false),generation=useRef(0);
  const callback=useRef(onAuthorizationChange);callback.current=onAuthorizationChange;
  const [display,setDisplay]=useState<Display|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
  const [consent,setConsent]=useState(false),[grantId,setGrantId]=useState("");
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;generation.current++;};},[]);
  useEffect(()=>{generation.current++;lock.current=false;setBusy(false);setDisplay(null);setConsent(false);setGrantId("");setError(null);callback.current(null);},[identity]);
  const shown=display?.identity===identity?display:null;
  const preview=shown?.preparation?.preview??null,grant=shown?.grant??null;
  const unexpired=(expiry:string)=>Date.parse(expiry)>Date.now();
  const valid=(captured:string,epoch:number)=>mounted.current&&current.current===captured&&generation.current===epoch;
  function clearAuthorization(){callback.current(null);}
  function commitGrant(next:ModelConsumptionGrant,prepared:ModelConsumptionPreparation|null){
    setDisplay({identity,preparation:prepared,grant:next});setGrantId(next.grant_id);
    const p=prepared?.preview;
    if(next.status==="ACTIVE"&&unexpired(next.not_after)&&p&&next.preview_id===p.preview_id&&next.preview_fingerprint===p.fingerprint)
      callback.current({scopeKey,requestId:p.scope.request_id,notAfter:next.not_after});
    else clearAuthorization();
  }
  async function action(work:(captured:string,epoch:number)=>Promise<void>){
    if(disabled||lock.current)return;
    const captured=identity,epoch=generation.current;lock.current=true;setBusy(true);setError(null);
    // A failed or uncertain mutation/read cannot preserve a previously enabled send.
    clearAuthorization();
    try{await work(captured,epoch);}catch{if(valid(captured,epoch))setError("MODEL_CONSUMPTION_UNAVAILABLE");}
    finally{if(valid(captured,epoch)){lock.current=false;setBusy(false);}}
  }
  async function prepare(captured:string,epoch:number){
    setDisplay(null);setConsent(false);setGrantId("");
    const result=await prepareModelConsumption(projectId,conversationId,{...selector,request_id:`request-${crypto.randomUUID()}`});
    if(valid(captured,epoch))setDisplay({identity,preparation:result,grant:null});
  }
  async function confirm(captured:string,epoch:number){
    if(!consent||!preview||!unexpired(preview.not_after))throw new Error("PREVIEW_EXPIRED");
    const result=await confirmModelConsumption(projectId,conversationId,preview.preview_id,preview.fingerprint);
    if(valid(captured,epoch)){setConsent(false);commitGrant(result,shown?.preparation??null);}
  }
  async function read(captured:string,epoch:number){
    const id=grantId.trim();if(!id)return;
    const result=await readModelConsumptionGrant(projectId,conversationId,id);
    if(valid(captured,epoch))commitGrant(result,shown?.preparation??null);
  }
  async function revoke(captured:string,epoch:number){
    if(!grant)return;
    const result=await revokeModelConsumptionGrant(projectId,conversationId,grant.grant_id,grant.revision);
    if(valid(captured,epoch)){setConsent(false);commitGrant(result,shown?.preparation??null);}
  }
  return <section aria-label="本次模型消费授权" className="ide-review-card">
    <p>本次流程上限：USD 0.10；输入准入 8192；总输出 2048；最多 1 次计数和 1 次生成。零自动重试。</p>
    <p>准备与确认只管理本次授权；生成需单独点击硬件候选按钮。来源或权限不足时保持不可用。</p>
    <button type="button" disabled={disabled||busy} onClick={()=>void action(prepare)}>准备本次消费预览</button>
    {shown?.preparation?.status==="BLOCKED"&&<p role="status">消费预览受阻：{shown.preparation.blockers.join(" / ")||"UNAVAILABLE"}</p>}
    {preview&&<><p>模型：{preview.scope.model_id}；预览：{preview.fingerprint}；有效期：{preview.not_after}</p>
      <label><input type="checkbox" checked={consent} disabled={disabled||busy||!unexpired(preview.not_after)||grant!==null} onChange={event=>setConsent(event.target.checked)}/>我确认上述本次范围和费用上限，授权计数及生成各最多一次。</label>
      <button type="button" disabled={disabled||busy||!consent||!unexpired(preview.not_after)||grant!==null} onClick={()=>void action(confirm)}>确认本次消费授权</button></>}
    <label>授权 ID<input aria-label="授权 ID" value={grantId} disabled={disabled||busy} onChange={event=>{setGrantId(event.target.value);clearAuthorization();setDisplay(old=>old?{...old,grant:null}:null);}}/></label>
    <button type="button" disabled={disabled||busy||!grantId.trim()} onClick={()=>void action(read)}>读取授权状态</button>
    {grant&&<><p role="status">授权状态：{grant.status}；版本：{grant.revision}；有效期：{grant.not_after}</p>
      <p>计数：{grant.counter_status}；生成：{grant.generation_status}；外部请求总数：{grant.total_external_request_count??"UNKNOWN"}</p>
      {!preview&&<p>已恢复授权历史状态；尚未绑定本界面的当前消费预览。</p>}
      <button type="button" disabled={disabled||busy||grant.status!=="ACTIVE"} onClick={()=>void action(revoke)}>撤销本次消费授权</button></>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
