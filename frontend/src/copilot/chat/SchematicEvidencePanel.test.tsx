import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import type { ConversationDetail } from "../api/projectClient";
import { SchematicEvidencePanel } from "./SchematicEvidencePanel";

const fp = `sha256:${"a".repeat(64)}`;
const conversation = {
  conversation_id: "conversation-1", project_id: "project-1",
  schematic_planning: { status: "APPROVED", approved_plan: {
    plan_id: "plan-1", project_id: "project-1", conversation_id: "conversation-1", fingerprint: fp,
    circuit_intent_id: "intent-1", circuit_intent_revision: "r1", circuit_intent_fingerprint: fp,
  } },
  schematic_production: { status: "GENERATED", operation_id: "write-1", generation_receipt: {
    operation_id: "write-1", artifact_fingerprint: fp, artifact_path: "hardware/main.kicad_sch",
    schematic_plan_fingerprint: fp, design_truth_fingerprint: fp, validation_status: "UNAVAILABLE", fingerprint: fp,
  } },
} as ConversationDetail;

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label));
  if (!found) throw new Error(`missing button ${label}`);
  return found;
}

async function render(fetcher: typeof fetch) {
  vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<SchematicEvidencePanel conversation={conversation} projection={null} onRefresh={async () => {}} />));
  return { host, root };
}

afterEach(() => { vi.unstubAllGlobals(); document.body.replaceChildren(); });

it("mount does not mutate; creating parse remains WAIT_APPROVAL until an explicit separate approval", async () => {
  const calls: Array<{ url: string; method: string; body: Record<string, unknown> | null }> = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ url: path, method, body });
    if (path.endsWith("/schematic-evidence/operations")) return response({ operation_id: "parse-1", operation_type: "KICAD_SCHEMATIC_PARSE", tenant_id: "tenant-1", project_id: "project-1", conversation_id: "conversation-1", requester_id: "user-1", expires_at: "2099-01-01T00:00:00Z", fingerprint: fp });
    if (path.includes("/status")) return response({ operation_id: "parse-1", tenant_id: "tenant-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_PARSE", status: "WAIT_APPROVAL", recovery_state: "NONE", receipt_fingerprint: null });
    if (path.includes("/approval")) return response({ grant_id: "grant-1", operation_id: "parse-1", status: "ISSUED", fingerprint: fp });
    throw new Error(`unexpected ${path}`);
  }) as typeof fetch;
  vi.stubGlobal("crypto", { randomUUID: vi.fn().mockReturnValueOnce("nonce-1") });
  const { host, root } = await render(fetcher);
  expect(calls).toHaveLength(0);
  await act(async () => button(host, "创建 Parse 操作").click());
  expect(calls.filter((call) => call.url.includes("/approval"))).toHaveLength(0);
  expect(host.textContent).toContain("WAIT_APPROVAL");
  await act(async () => button(host, "批准 Parse").click());
  const approval = calls.find((call) => call.url.includes("/approval"));
  expect(approval?.body?.reviewer_id).toBe("user-1");
  expect(approval?.body?.nonce).toBe("nonce-1");
  expect(Date.parse(String(approval?.body?.expires_at))).toBeLessThanOrEqual(Date.parse("2099-01-01T00:00:00Z"));
  expect(calls.filter((call) => call.url.includes("/approval"))).toHaveLength(1);
  await act(async () => root.unmount());
});

it("never approves an operation whose canonical status has the wrong project", async () => {
  const calls: string[] = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url); calls.push(path);
    if (path.endsWith("/schematic-evidence/operations")) return response({ operation_id: "parse-1", operation_type: "KICAD_SCHEMATIC_PARSE", tenant_id: "tenant-1", project_id: "project-1", conversation_id: "conversation-1", requester_id: "user-1", expires_at: "2099-01-01T00:00:00Z", fingerprint: fp });
    if (path.includes("/status")) return response({ operation_id: "parse-1", tenant_id: "tenant-1", project_id: "other-project", operation_type: "KICAD_SCHEMATIC_PARSE", status: "WAIT_APPROVAL", recovery_state: "NONE" });
    throw new Error(`unexpected ${path}`);
  }) as typeof fetch;
  const { host, root } = await render(fetcher);
  await act(async () => button(host, "创建 Parse 操作").click());
  await act(async () => button(host, "批准 Parse").click());
  expect(calls.some((path) => path.includes("/approval"))).toBe(false);
  expect(host.textContent).toContain("OPERATION_SCOPE_MISMATCH");
  await act(async () => root.unmount());
});

it("records only Owner selectors and renders the server's fixed eight claims without client PASS input", async () => {
  const calls: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url), body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ url: path, body });
    if (path.includes("/schematic-verifications")) return response({ currentness: "CURRENT", record: {
      verification_id: "verification-1", project_id: "project-1", conversation_id: "conversation-1", intent_id: "intent-1", intent_revision: "r1", intent_fingerprint: fp, plan_id: "plan-1", plan_fingerprint: fp, artifact_fingerprint: fp, erc_status: "BLOCKED", aggregate_status: "BLOCKED", fingerprint: fp,
      claims: Array.from({ length: 8 }, (_, index) => ({ claim_id: `S02-I2C-0${index + 1}`, status: "BLOCKED", reason: "EVIDENCE_UNAVAILABLE", evidence_ids: [] })),
    } });
    throw new Error(`unexpected ${path}`);
  }) as typeof fetch;
  const { host, root } = await render(fetcher);
  await act(async () => button(host, "记录八项验证").click());
  const submitted = calls[0]?.body;
  expect(submitted).toMatchObject({ intent_id: "intent-1", plan_id: "plan-1", generation_operation_id: "write-1", artifact_fingerprint: fp });
  expect(submitted).not.toHaveProperty("claims");
  expect(submitted).not.toHaveProperty("required_claim_ids");
  expect(host.textContent).toContain("S02-I2C-08：BLOCKED");
  expect(host.textContent).not.toContain("SCHEMATIC_VERIFIED");
  await act(async () => root.unmount());
});

it("blocks stale production without creating operations or recording claims", async () => {
  const fetcher = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const stale = { ...conversation, schematic_production: { ...conversation.schematic_production!, status: "STALE" as const } };
  await act(async () => root.render(<SchematicEvidencePanel conversation={stale} onRefresh={async () => {}} />));
  expect(button(host, "创建 Parse 操作").disabled).toBe(true);
  expect(button(host, "记录八项验证").disabled).toBe(true);
  expect(host.textContent).toContain("BLOCKED");
  expect(fetcher).not.toHaveBeenCalled();
  await act(async () => root.unmount());
});

it("rejects a VERIFIED response with missing mandatory claims", async () => {
  const fetcher = vi.fn(async () => response({ currentness: "CURRENT", record: {
    project_id: "project-1", conversation_id: "conversation-1", intent_id: "intent-1", intent_revision: "r1", intent_fingerprint: fp,
    plan_id: "plan-1", plan_fingerprint: fp, artifact_fingerprint: fp, aggregate_status: "SCHEMATIC_VERIFIED", claims: [],
  } })) as typeof fetch;
  const { host, root } = await render(fetcher);
  await act(async () => button(host, "记录八项验证").click());
  expect(host.textContent).toContain("VERIFICATION_SCOPE_MISMATCH");
  expect(host.textContent).not.toContain("整体：SCHEMATIC_VERIFIED");
  await act(async () => root.unmount());
});

const ercContext = {
  fingerprint: fp, requester_id: "user-1", expires_at: "2099-01-01T00:00:00Z",
  conversation_id: "conversation-1", schematic_artifact_fingerprint: fp,
  source_operation_id: "write-1", source_operation_receipt_fingerprint: `sha256:${"b".repeat(64)}`,
  source_parse_operation_id: "parse-1", source_parse_receipt_fingerprint: `sha256:${"c".repeat(64)}`,
  proposal_id: "plan-1", proposal_fingerprint: fp,
};

function ercFixture(context: Record<string, unknown> | null = ercContext, status = "WAIT_APPROVAL") {
  return { operation_id: "erc-1", tenant_id: "tenant-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_ERC", status, recovery_state: "NONE", cancellation_requested: false, receipt_fingerprint: null, schematic_approval_context: context };
}

async function renderErc(fetcher: typeof fetch, writeReceipt: string | null = ercContext.source_operation_receipt_fingerprint) {
  vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const current = { ...conversation, schematic_production: { ...conversation.schematic_production!, proposal: { proposal_id: "schematic-proposal-1", fingerprint: `sha256:${"f".repeat(64)}` }, generation_receipt: { ...conversation.schematic_production!.generation_receipt!, operation_receipt_fingerprint: writeReceipt } } } as ConversationDetail;
  const projection = { identity: { tenant_id: "tenant-1", requester_id: "user-1", project_id: "project-1" }, operations: { items: [{ reference_id: "erc-1", status: "WAIT_APPROVAL" }] } } as unknown as import("../api/desktopWorkspaceClient").DesktopWorkspaceProjection;
  await act(async () => root.render(<SchematicEvidencePanel conversation={current} projection={projection} onRefresh={async () => {}} />));
  return { host, root };
}

it("reads canonical ERC, write, and parse status; only a separate click approves the bound operation", async () => {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | null }> = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url), method = init?.method ?? "GET", body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ path, method, body });
    if (path.includes("/erc-1/status")) return response(ercFixture());
    if (path.includes("/write-1/status")) return response({ operation_id: "write-1", tenant_id: "tenant-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_WRITE", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: ercContext.source_operation_receipt_fingerprint });
    if (path.includes("/parse-1/status")) return response({ operation_id: "parse-1", tenant_id: "tenant-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_PARSE", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: ercContext.source_parse_receipt_fingerprint });
    if (path.includes("/erc-1/approval")) return response({ grant_id: "grant-1", operation_id: "erc-1", status: "ISSUED", fingerprint: fp });
    throw new Error(`unexpected ${path}`);
  }) as typeof fetch;
  vi.stubGlobal("crypto", { randomUUID: vi.fn().mockReturnValue("erc-nonce") });
  const { host, root } = await renderErc(fetcher);
  expect(calls).toHaveLength(0);
  await act(async () => { host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.value = "erc-1"; host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => button(host, "读取 ERC 状态").click());
  expect(calls).toHaveLength(3);
  expect(calls.every((call) => call.method === "GET")).toBe(true);
  expect(button(host, "批准 ERC").disabled).toBe(false);
  await act(async () => button(host, "批准 ERC").click());
  expect(calls.filter((call) => call.path.includes("/approval"))).toHaveLength(1);
  expect(calls.find((call) => call.path.includes("/approval"))?.body).toMatchObject({ reviewer_id: "user-1", nonce: "erc-nonce" });
  expect(calls.filter((call) => call.path.includes("/status")).length).toBeGreaterThanOrEqual(6);
  await act(async () => root.unmount());
});

it.each([
  ["legacy response", null, ercContext.source_parse_receipt_fingerprint],
  ["wrong conversation", { ...ercContext, conversation_id: "other" }, ercContext.source_parse_receipt_fingerprint],
  ["wrong artifact", { ...ercContext, schematic_artifact_fingerprint: `sha256:${"e".repeat(64)}` }, ercContext.source_parse_receipt_fingerprint],
  ["wrong source write", { ...ercContext, source_operation_id: "other-write" }, ercContext.source_parse_receipt_fingerprint],
  ["wrong source receipt", { ...ercContext, source_operation_receipt_fingerprint: `sha256:${"e".repeat(64)}` }, ercContext.source_parse_receipt_fingerprint],
  ["wrong requester", { ...ercContext, requester_id: "other-user" }, ercContext.source_parse_receipt_fingerprint],
  ["wrong proposal", { ...ercContext, proposal_id: "other-plan" }, ercContext.source_parse_receipt_fingerprint],
  ["expired", { ...ercContext, expires_at: "2000-01-01T00:00:00Z" }, ercContext.source_parse_receipt_fingerprint],
  ["missing parse pair", { ...ercContext, source_parse_operation_id: null, source_parse_receipt_fingerprint: null }, ercContext.source_parse_receipt_fingerprint],
  ["incomplete parse pair", { ...ercContext, source_parse_receipt_fingerprint: null }, ercContext.source_parse_receipt_fingerprint],
  ["drifted parse receipt", ercContext, `sha256:${"d".repeat(64)}`],
])("keeps ERC approval disabled for %s", async (_name, context, parseReceipt) => {
  const calls: string[] = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url); calls.push(path);
    if (path.includes("/erc-1/status")) return response(ercFixture(context));
    if (path.includes("/write-1/status")) return response({ operation_id: "write-1", tenant_id: "tenant-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_WRITE", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: ercContext.source_operation_receipt_fingerprint });
    if (path.includes("/parse-1/status")) return response({ operation_id: "parse-1", tenant_id: "tenant-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_PARSE", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: parseReceipt });
    throw new Error(`unexpected ${path}`);
  }) as typeof fetch;
  const { host, root } = await renderErc(fetcher);
  await act(async () => { host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.value = "erc-1"; host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => button(host, "读取 ERC 状态").click());
  expect(button(host, "批准 ERC").disabled).toBe(true);
  expect(calls.some((path) => path.includes("/approval"))).toBe(false);
  await act(async () => root.unmount());
});


it("blocks approval when the canonical source receipt drifts between reading and clicking", async () => {
  let writeReads = 0;
  const calls: string[] = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url); calls.push(path);
    if (path.includes("/erc-1/status")) return response(ercFixture());
    if (path.includes("/write-1/status")) {
      writeReads += 1;
      return response({ operation_id: "write-1", tenant_id: "tenant-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_WRITE", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: writeReads === 1 ? ercContext.source_operation_receipt_fingerprint : `sha256:${"d".repeat(64)}` });
    }
    if (path.includes("/parse-1/status")) return response({ operation_id: "parse-1", tenant_id: "tenant-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_PARSE", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: ercContext.source_parse_receipt_fingerprint });
    throw new Error(`unexpected ${path}`);
  }) as typeof fetch;
  const { host, root } = await renderErc(fetcher);
  await act(async () => { host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.value = "erc-1"; host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => button(host, "读取 ERC 状态").click());
  expect(button(host, "批准 ERC").disabled).toBe(false);
  await act(async () => button(host, "批准 ERC").click());
  expect(calls.some((path) => path.includes("/approval"))).toBe(false);
  expect(button(host, "批准 ERC").disabled).toBe(true);
  expect(host.textContent).toContain("OPERATION_NOT_APPROVABLE");
  await act(async () => root.unmount());
});


it.each([
  ["cancelled", { cancellation_requested: true }],
  ["recovery", { recovery_state: "TARGET_STATE_UNKNOWN" }],
  ["not awaiting approval", { status: "CANCELLED" }],
])("keeps ERC approval disabled for %s status", async (_reason, patch) => {
  const calls: string[] = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url); calls.push(path);
    if (path.includes("/erc-1/status")) return response({ ...ercFixture(), ...patch });
    throw new Error(`unexpected ${path}`);
  }) as typeof fetch;
  const { host, root } = await renderErc(fetcher);
  await act(async () => { host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.value = "erc-1"; host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => button(host, "读取 ERC 状态").click());
  expect(button(host, "批准 ERC").disabled).toBe(true);
  expect(calls).toHaveLength(1);
  await act(async () => root.unmount());
});

it("requires the persisted generation operation receipt fingerprint", async () => {
  const calls: string[] = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url); calls.push(path);
    if (path.includes("/erc-1/status")) return response(ercFixture());
    throw new Error(`unexpected ${path}`);
  }) as typeof fetch;
  const { host, root } = await renderErc(fetcher, null);
  await act(async () => { host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.value = "erc-1"; host.querySelector<HTMLSelectElement>('select[aria-label="ERC 操作"]')!.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => button(host, "读取 ERC 状态").click());
  expect(button(host, "批准 ERC").disabled).toBe(true);
  expect(calls).toHaveLength(1);
  await act(async () => root.unmount());
});

it("readback uses a deliberately selected current APPROVED P3 pair and never auto-approves", async () => {
  const calls:Array<{path:string;body:Record<string,unknown>|null}>=[];
  const map={evidence:{evidence_id:"map-1",fingerprint:fp,tenant_id:"tenant-1",project_id:"project-1",conversation_id:"conversation-1",plan_id:"plan-1",plan_fingerprint:fp},review:{decision:"APPROVED",evidence_fingerprint:fp},currentness:"APPROVED"};
  const fetcher=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url),body=init?.body?JSON.parse(String(init.body)):null;calls.push({path,body});
    if(path.endsWith("/library-terminal-maps"))return response([map]);
    if(path.endsWith("/schematic-evidence/operations"))return response({operation_id:"readback-1",operation_type:"KICAD_SCHEMATIC_READ_BACK",tenant_id:"tenant-1",project_id:"project-1",conversation_id:"conversation-1",requester_id:"user-1",expires_at:"2099-01-01T00:00:00Z",fingerprint:fp,library_map_evidence_id:"map-1",library_map_evidence_fingerprint:fp});
    if(path.endsWith("/status"))return response({operation_id:"readback-1",operation_type:"KICAD_SCHEMATIC_READ_BACK",tenant_id:"tenant-1",project_id:"project-1",status:"WAIT_APPROVAL",recovery_state:"NONE"});
    throw new Error(path);
  }) as typeof fetch;
  vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
  const projection={identity:{tenant_id:"tenant-1",project_id:"project-1",requester_id:"user-1"},operations:{items:[]}} as unknown as import("../api/desktopWorkspaceClient").DesktopWorkspaceProjection;
  await act(async()=>root.render(<SchematicEvidencePanel conversation={conversation} projection={projection} onRefresh={async()=>{}}/>));
  expect(calls).toHaveLength(0);
  await act(async()=>button(host,"读取已审库映射选择器").click());
  const selector=[...host.querySelectorAll("label")].find((label)=>label.textContent?.startsWith("Read-back 库映射"))!.querySelector("select")!;
  expect(selector.value).toBe("");expect(calls.every((call)=>call.body===null)).toBe(true);
  await act(async()=>{selector.value="map-1";selector.dispatchEvent(new Event("change",{bubbles:true}));});
  await act(async()=>button(host,"创建 Read-back 操作").click());
  expect(calls.find((call)=>call.path.endsWith("/schematic-evidence/operations"))?.body).toEqual({kind:"READBACK",library_map_evidence_id:"map-1",library_map_evidence_fingerprint:fp});
  expect(calls.some((call)=>call.path.endsWith("/approval"))).toBe(false);await act(async()=>root.unmount());
});