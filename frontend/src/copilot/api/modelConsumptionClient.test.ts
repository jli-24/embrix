import { beforeEach, expect, it, vi } from "vitest";
import { markDesktopSessionActive, markDesktopSessionExpired } from "./sessionState";
import { buildModelConsumptionSelector, confirmModelConsumption, prepareModelConsumption,
  readModelConsumptionGrant, revokeModelConsumptionGrant } from "./modelConsumptionClient";

const fp = `sha256:${"1".repeat(64)}`;
const task = {plan_id:"plan",phase_id:"phase",plan_task_id:"task"};
const grant = {schema_version:1,grant_id:"grant/1",preview_id:"preview",preview_fingerprint:fp,
  scope_fingerprint:fp,status:"ACTIVE",revision:1,not_after:"2099-01-01T00:00:00Z",
  counter_status:"UNKNOWN",generation_status:"UNKNOWN",total_external_request_count:null};
beforeEach(() => markDesktopSessionActive());

it.each([1,2,3] as const)("projects exact fixed schema1 selectors for selection v%s and drops extra input", async version => {
  const tree = version===1 ? null : {schema_version:version,expected_head:["r","s",fp],preview_fingerprint:fp,
    ...(version===3?{selected_parents:[{slot_id:"root",source_id:"part",source_fingerprint:fp}]}:{})};
  const selector=buildModelConsumptionSelector(tree as Parameters<typeof buildModelConsumptionSelector>[0],task);
  const fetcher=vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({schema_version:1,status:"BLOCKED",preview:null,blockers:["ACCOUNT_AUTHORITY_UNAVAILABLE"]})));
  await prepareModelConsumption("p/1","c/1",{...selector,request_id:"request",model:"forbidden",stage_id:"forbidden"} as typeof selector & {request_id:string},fetcher);
  const body=JSON.parse(String(fetcher.mock.calls[0][1]?.body));
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/p%2F1/conversations/c%2F1/model-consumption/preview");
  expect(body).toEqual({schema_version:1,selection_schema_version:version,operation_id:"HARDWARE_SELECTION_PROPOSAL",...task,request_id:"request",
    expected_head_revision_id:version===1?null:"r",expected_head_selection_id:version===1?null:"s",
    expected_head_selection_fingerprint:version===1?null:fp,selection_preview_fingerprint:version===1?null:fp,
    parent_choices:version===3?[{slot_id:"root",source_id:"part",source_fingerprint:fp}]:[]});
  expect(fetcher.mock.calls[0][1]?.credentials).toBe("same-origin");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("uses separate explicit confirm GET and revision-only revoke without invocation",async()=>{
  const fetcher=vi.fn<typeof fetch>().mockImplementation(async()=>new Response(JSON.stringify(grant)));
  await confirmModelConsumption("p","c","preview",fp,fetcher);
  await readModelConsumptionGrant("p","c","grant/1",fetcher);
  await revokeModelConsumptionGrant("p","c","grant/1",1,fetcher);
  expect(fetcher.mock.calls.map(x=>x[1]?.method)).toEqual(["POST","GET","POST"]);
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({preview_id:"preview",preview_fingerprint:fp,consent:true});
  expect(String(fetcher.mock.calls[1][0])).toMatch(/\/model-consumption\/grants\/grant%2F1$/);
  expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({expected_revision:1});
  expect(fetcher.mock.calls.every(x=>!String(x[0]).includes("model-candidate"))).toBe(true);
});

it.each(["foreign","bad-ready","unknown-total"])("rejects malformed or foreign response %s",async kind=>{
  const input={...buildModelConsumptionSelector(null,task),request_id:"request"};
  const response=kind==="unknown-total"?{...grant,total_external_request_count:"0"}:{schema_version:1,status:"READY",blockers:[],preview:kind==="bad-ready"?null:{schema_version:1,preview_id:"preview",fingerprint:fp,not_after:"2099-01-01T00:00:00Z",scope:{project_id:"foreign",conversation_id:"c",request_id:"request",selected_model:{model_id:"model"}}}};
  const fetcher=vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response)));
  await expect(kind==="unknown-total"?readModelConsumptionGrant("p","c","grant/1",fetcher):prepareModelConsumption("p","c",input,fetcher)).rejects.toThrow("MODEL_CONSUMPTION");
});

it("preserves UNKNOWN usage and refuses expired session without a request",async()=>{
  const fetcher=vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(grant)));
  expect((await readModelConsumptionGrant("p","c","grant/1",fetcher)).total_external_request_count).toBeNull();
  markDesktopSessionExpired();
  await expect(readModelConsumptionGrant("p","c","grant/1",fetcher)).rejects.toThrow("DESKTOP_SESSION_EXPIRED");
  expect(fetcher).toHaveBeenCalledTimes(1);
});
