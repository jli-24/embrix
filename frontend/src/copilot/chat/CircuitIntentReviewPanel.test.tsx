import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";
import { CircuitIntentReviewPanel } from "./CircuitIntentReviewPanel";

const receiptFingerprint = `sha256:${"a".repeat(64)}`;
const proposalFingerprint = `sha256:${"b".repeat(64)}`;

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const value = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label));
  if (!value) throw new Error(`missing button ${label}`);
  return value;
}

function setText(element: HTMLTextAreaElement, value: string): void {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

const turns = [{ turn_id: "turn-design", route: "GREENFIELD_DESIGN", status: "COMPLETED", question: "设计电源" }];
const baseProps = {
  mode: "ERC_PUBLICATION" as const, projectId: "project-1", conversationId: "conversation-1", ercOperationId: "erc-1",
  turns, projection: null as DesktopWorkspaceProjection | null, busy: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

it("submits only the Owner ERC operation id and re-reads Owner before showing publication", async () => {
  const requests: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    requests.push({ url: path, body });
    if (path.includes("/status")) return response({
      operation_id: "erc-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_ERC",
      status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: receiptFingerprint,
    });
    if (path.endsWith("/preview")) return response({
      project_id: "project-1", conversation_id: "conversation-1", turn_id: "turn-design",
      proposal_id: "proposal-1", proposal_fingerprint: proposalFingerprint, status: "CREATED",
      object_id: "circuit-1", revision: "r1", base_revision: null, base_fingerprint: null,
      context_fingerprint: receiptFingerprint, source_fingerprint: receiptFingerprint,
      evidence_receipt_id: "erc-1", evidence_receipt_fingerprint: receiptFingerprint,
      circuit_intent: { function: "稳压", topology: ["输入到稳压器"], parameters: ["3.3V"], calculation: ["功耗核算"], assumptions: ["输入稳定"], risks: ["过热"] },
      created_at: "2026-09-29T00:00:00Z",
    });
    if (path.endsWith("/decision")) return response({
      project_id: "project-1", proposal_id: "proposal-1", proposal_fingerprint: proposalFingerprint,
      status: "APPLIED", object_id: "circuit-1", revision: "r1",
      object_fingerprint: receiptFingerprint, approval_id: "approval-1", approval_fingerprint: receiptFingerprint,
    });
    throw new Error(`unexpected request ${path}`);
  }) as typeof fetch;
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("crypto", { randomUUID: () => "idempotency-1" });
  const ownerRefresh = vi.fn(async () => {});
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} onOwnerRefresh={ownerRefresh} />); await Promise.resolve(); });
  await act(async () => { setText(container.querySelector("textarea")!, "稳压"); });
  await act(async () => { button(container, "生成审核预览").click(); await Promise.resolve(); });
  expect(requests[1].body).toMatchObject({ erc_operation_id: "erc-1", conversation_id: "conversation-1", turn_id: "turn-design" });
  expect(requests[1].body).not.toHaveProperty("evidence_reference");
  expect(container.textContent).toContain(proposalFingerprint);
  expect(container.querySelector('pre[aria-label="待批准 Circuit 规格"]')?.textContent).toContain("功耗核算");
  expect(container.textContent).toContain("上下文指纹");
  expect(ownerRefresh).not.toHaveBeenCalled();
  await act(async () => { button(container, "确认并发布").click(); await Promise.resolve(); });
  expect(requests[2].body).toMatchObject({ proposal_id: "proposal-1", evidence_receipt_fingerprint: receiptFingerprint, decision: "APPROVE" });
  expect(ownerRefresh).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("发布已提交");
  expect(container.textContent).toContain("Owner 状态：UNAVAILABLE");
  const projection = { authoritative_facts: [{
    domain: "CIRCUIT", project_id: "project-1", object_id: "circuit-1", revision: "r1",
    conversation_id: null, currentness: "CURRENT", reason_code: "SOURCE_BINDING_CURRENT",
  }] } as DesktopWorkspaceProjection;
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} projection={projection} onOwnerRefresh={ownerRefresh} />); });
  expect(container.textContent).toContain("Owner 状态：CURRENT");
  const published = container.querySelector('section[aria-label="已发布 Circuit"]')!;
  expect(published.textContent).toContain("circuit-1");
  expect(published.textContent).not.toContain("暂无");
  await act(async () => root.unmount());
});

it("lets the reviewer select an existing current Circuit as the revision base", async () => {
  const requests: Array<Record<string, unknown>> = [];
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url).includes("/status")) return response({
      operation_id: "erc-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_ERC",
      status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: receiptFingerprint,
    });
    if (String(url).endsWith("/preview")) {
      requests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return response({
        project_id: "project-1", conversation_id: "conversation-1", turn_id: "turn-design",
        proposal_id: "proposal-2", proposal_fingerprint: proposalFingerprint, status: "CREATED",
        object_id: "circuit-1", revision: "r2", base_revision: "r1", base_fingerprint: receiptFingerprint,
        context_fingerprint: receiptFingerprint, source_fingerprint: receiptFingerprint,
        evidence_receipt_id: "erc-1", evidence_receipt_fingerprint: receiptFingerprint,
        circuit_intent: { function: "稳压修订", topology: [], parameters: [], calculation: [], assumptions: [], risks: [] },
      });
    }
    throw new Error("unexpected request");
  }) as typeof fetch;
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("crypto", { randomUUID: () => "revision-key" });
  const projection = { authoritative_facts: [{
    domain: "CIRCUIT", project_id: "project-1", object_id: "circuit-1",
    revision: "r1", currentness: "CURRENT",
  }] } as DesktopWorkspaceProjection;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} projection={projection} onOwnerRefresh={async () => {}} />); await Promise.resolve(); });
  await act(async () => {
    const target = container.querySelector("select")!;
    target.value = "circuit-1";
    target.dispatchEvent(new Event("change", { bubbles: true }));
    setText(container.querySelector("textarea")!, "稳压修订");
  });
  await act(async () => { button(container, "生成审核预览").click(); await Promise.resolve(); });
  expect(requests[0]).toMatchObject({ object_id: "circuit-1", erc_operation_id: "erc-1" });
  expect(container.textContent).toContain("r2");
  await act(async () => root.unmount());
});

it("keeps review disabled for an unrelated or non ERC operation", async () => {
  const fetcher = vi.fn(async () => response({
    operation_id: "erc-1", project_id: "project-other", operation_type: "BUILD",
    status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: receiptFingerprint,
  })) as typeof fetch;
  vi.stubGlobal("fetch", fetcher);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} onOwnerRefresh={async () => {}} />); await Promise.resolve(); });
  await act(async () => { setText(container.querySelector("textarea")!, "稳压"); });
  expect(button(container, "生成审核预览").disabled).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => root.unmount());
});

it.each([null, "conversation-1"])("shows persisted Owner Circuit scope %s after remount and explicitly refreshes stale status", async (conversationId) => {
  const fetcher = vi.fn(async () => response({
    operation_id: "erc-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_ERC",
    status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: receiptFingerprint,
  })) as typeof fetch;
  vi.stubGlobal("fetch", fetcher);
  const ownerRefresh = vi.fn(async () => {});
  const fact = {
    domain: "CIRCUIT" as const, owner: "ProjectCatalog", project_id: "project-1",
    conversation_id: conversationId, object_id: "circuit-1", revision: "r1",
    fingerprint: proposalFingerprint, source_reference: "turn-design",
    source_fingerprint: receiptFingerprint, currentness: "CURRENT" as const,
    reason_code: "SOURCE_BINDING_CURRENT",
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} projection={{ authoritative_facts: [fact] } as DesktopWorkspaceProjection} onOwnerRefresh={ownerRefresh} />); await Promise.resolve(); });
  expect(container.textContent).toContain("circuit-1");
  expect(container.textContent).toContain("r1");
  expect(container.textContent).toContain("CURRENT");
  expect(container.textContent).toContain("turn-design");
  await act(async () => root.unmount());

  const reopened = createRoot(container);
  await act(async () => { reopened.render(<CircuitIntentReviewPanel {...baseProps} projection={{ authoritative_facts: [fact] } as DesktopWorkspaceProjection} onOwnerRefresh={ownerRefresh} />); await Promise.resolve(); });
  expect(container.textContent).toContain("circuit-1");
  await act(async () => { button(container, "从 Owner 重新读取").click(); await Promise.resolve(); });
  expect(ownerRefresh).toHaveBeenCalledTimes(1);
  await act(async () => { reopened.render(<CircuitIntentReviewPanel {...baseProps} projection={{ authoritative_facts: [{ ...fact, currentness: "STALE", reason_code: "SOURCE_FILE_CHANGED" }] } as DesktopWorkspaceProjection} onOwnerRefresh={ownerRefresh} />); });
  expect(container.textContent).toContain("STALE");
  expect(container.textContent).toContain("SOURCE_FILE_CHANGED");
  expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => reopened.unmount());
});

it("does not display another project's Circuit or an unavailable placeholder as a publication", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response({
    operation_id: "erc-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_ERC",
    status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: receiptFingerprint,
  })));
  const projection = { authoritative_facts: [
    { domain: "CIRCUIT", project_id: "project-other", conversation_id: null, object_id: "other-project-circuit", revision: "r1", currentness: "CURRENT" },
    { domain: "CIRCUIT", project_id: "project-1", conversation_id: "conversation-other", object_id: "other-conversation-circuit", revision: "r1", currentness: "CURRENT" },
    { domain: "CIRCUIT", project_id: "project-1", conversation_id: null, object_id: null, revision: null, currentness: "UNAVAILABLE" },
  ] } as DesktopWorkspaceProjection;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} projection={projection} onOwnerRefresh={async () => {}} />); });
  const published = container.querySelector('section[aria-label="已发布 Circuit"]')!;
  expect(published.textContent).not.toContain("other-project-circuit");
  expect(published.textContent).not.toContain("other-conversation-circuit");
  expect(published.textContent).toContain("暂无");
  await act(async () => root.unmount());
});

it.each(["refresh-fails", "decision-fails", "rejected"] as const)("keeps decision and Owner refresh outcomes distinct: %s", async (scenario) => {
  let decisionCalls = 0;
  vi.stubGlobal("crypto", { randomUUID: () => "outcome-key" });
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL) => {
    if (String(url).includes("/status")) return response({
      operation_id: "erc-1", project_id: "project-1", operation_type: "KICAD_SCHEMATIC_ERC",
      status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: receiptFingerprint,
    });
    if (String(url).endsWith("/preview")) return response({
      project_id: "project-1", conversation_id: "conversation-1", turn_id: "turn-design",
      proposal_id: "proposal-1", proposal_fingerprint: proposalFingerprint,
      object_id: "circuit-1", revision: "r1", base_revision: null, base_fingerprint: null,
      context_fingerprint: receiptFingerprint, evidence_receipt_id: "erc-1",
      evidence_receipt_fingerprint: receiptFingerprint, circuit_intent: { function: "稳压" },
    });
    if (String(url).endsWith("/decision")) {
      decisionCalls += 1;
      if (scenario === "decision-fails") return response({ detail: "CIRCUIT_SOURCE_CHANGED" }, 409);
      return response({
        project_id: "project-1", proposal_id: "proposal-1", proposal_fingerprint: proposalFingerprint,
        status: scenario === "rejected" ? "REJECTED" : "APPLIED",
        object_id: scenario === "rejected" ? null : "circuit-1", revision: scenario === "rejected" ? null : "r1",
        object_fingerprint: receiptFingerprint, approval_id: "approval-1", approval_fingerprint: receiptFingerprint,
      });
    }
    throw new Error("unexpected request");
  }));
  const refresh = vi.fn(async () => { if (scenario === "refresh-fails") throw new Error("CIRCUIT_OWNER_REFRESH_UNAVAILABLE"); });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} onOwnerRefresh={refresh} />); });
  await act(async () => { setText(container.querySelector("textarea")!, "稳压"); });
  await act(async () => { button(container, "生成审核预览").click(); });
  await act(async () => { button(container, scenario === "rejected" ? "拒绝" : "确认并发布").click(); });
  expect(decisionCalls).toBe(1);
  if (scenario === "decision-fails") {
    expect(refresh).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).not.toContain("发布已提交");
  } else {
    expect(refresh).toHaveBeenCalledTimes(1);
    expect([...container.querySelectorAll("button")].some((item) => item.textContent === "确认并发布")).toBe(false);
    expect(container.textContent).toContain(scenario === "rejected" ? "已拒绝该候选" : "发布已提交");
    if (scenario === "refresh-fails") expect(container.textContent).toContain("CIRCUIT_OWNER_REFRESH_UNAVAILABLE");
  }
  await act(async () => root.unmount());
});

const sourceExpectations = {
  requirement_context_fingerprint: receiptFingerprint, architecture_id: "arch-approved",
  architecture_fingerprint: proposalFingerprint, hardware_selection_approval_id: "hardware-approval",
  hardware_truth_fingerprint: receiptFingerprint,
};
const approvedPredesignSources = {
  requirements: [{ requirement_id: "req-power", label: "Approved power requirement" }, { requirement_id: "req-can", label: "Approved CAN requirement" }],
  architectureComponents: [{ component_id: "component-power", label: "Power component" }, { component_id: "component-can", label: "CAN component" }],
};

it("requires explicit S02 turn, user-authored block and claim; submits a design-only preview without ERC", async () => {
  const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
  vi.stubGlobal("crypto", { randomUUID: () => "predesign-key" });
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    requests.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {} });
    if (String(url).endsWith("/preview")) return response({
      project_id: "project-1", conversation_id: "conversation-1", turn_id: "turn-design",
      proposal_id: "proposal-s02", proposal_fingerprint: proposalFingerprint, status: "CREATED",
      purpose: "PREDESIGN_APPROVAL", completion: "DESIGN_ONLY", object_id: "circuit-s02", revision: "r1",
      base_revision: null, base_fingerprint: null, source_fingerprint: receiptFingerprint,
      source_snapshot: { requirement_context_fingerprint: receiptFingerprint },
      circuit_intent: { function: "用户填写设计", blocks: [{ block_id: "power-block", function: "用户填写 block", verification_refs: ["claim-1"] }], verification_requirements: [{ verification_id: "claim-1", claim: "输入负载条件下满足输出要求", status: "PLANNED" }] },
    });
    if (String(url).endsWith("/decision")) return response({
      project_id: "project-1", proposal_id: "proposal-s02", proposal_fingerprint: proposalFingerprint,
      purpose: "PREDESIGN_APPROVAL", completion: "DESIGN_ONLY", status: "APPLIED", object_id: "circuit-s02", revision: "r1",
      object_fingerprint: receiptFingerprint, approval_id: "approval-s02", approval_fingerprint: receiptFingerprint,
    });
    throw new Error(`unexpected request ${String(url)}`);
  }));
  const refresh = vi.fn(async () => {});
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} mode="PREDESIGN_APPROVAL" ercOperationId={undefined} predesignSourceExpectations={sourceExpectations} approvedPredesignSources={approvedPredesignSources} onOwnerRefresh={refresh} />); });
  expect((container.querySelector('select[aria-label=""]') as HTMLSelectElement | null)?.value ?? container.querySelector("select")?.value).toBe("");
  expect(button(container, "生成审核预览").disabled).toBe(true);
  expect(container.textContent).toContain("仅已批准且可解析的约束可用于设计");
  expect(container.textContent).toContain("仅已批准且可解析的约束可用于设计");
  expect(container.textContent).not.toContain("CAN bitrate");
  expect(container.textContent).not.toContain("AMS1117");
  expect(container.textContent).toContain("请显式填写至少一个设计 block");
  expect(requests).toHaveLength(0);
  const selects = container.querySelectorAll("select");
  await act(async () => {
    selects[1].value = "turn-design"; selects[1].dispatchEvent(new Event("change", { bubbles: true }));
    const areas = container.querySelectorAll("textarea");
    setText(areas[0], "用户填写设计"); setText(areas[1], "用户填写拓扑");
    const inputs = container.querySelectorAll("input");
    for (const [index, value] of ["power-block", "用户填写 block", "claim-1", "输入负载条件下满足输出要求", "电气分析"].entries()) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(inputs[index], value);
      inputs[index].dispatchEvent(new Event("input", { bubbles: true }));
    }
    selects[0].value = "NEW"; selects[0].dispatchEvent(new Event("change", { bubbles: true }));
    selects[2].value = "power-block"; selects[2].dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(button(container, "生成审核预览").disabled).toBe(true);
  await act(async () => {
    const method = container.querySelectorAll("input")[5];
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(method, "独立负载测量");
    method.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    (container.querySelector('[aria-label="Requirement req-power for power-block"]') as HTMLInputElement).click();
    (container.querySelector('[aria-label="Architecture component-power for power-block"]') as HTMLInputElement).click();
  });
  expect(button(container, "生成审核预览").disabled).toBe(false);
  await act(async () => { button(container, "生成审核预览").click(); await Promise.resolve(); });
  expect(requests).toHaveLength(1);
  expect(requests[0].url).toContain("/circuit-intents/preview");
  expect(requests[0].body).toMatchObject({ purpose: "PREDESIGN_APPROVAL", turn_id: "turn-design", source_expectations: sourceExpectations });
  expect(requests[0].body).not.toHaveProperty("object_id");
  expect(requests[0].body).not.toHaveProperty("erc_operation_id");
  expect((requests[0].body.circuit_intent as { blocks: Array<{ verification_refs: string[] }> }).blocks[0].verification_refs).toEqual(["claim-1"]);
  expect((requests[0].body.circuit_intent as { blocks: Array<{ requirement_refs: string[]; architecture_refs: string[] }> }).blocks[0]).toMatchObject({ requirement_refs: ["req-power"], architecture_refs: ["component-power"] });
  expect((requests[0].body.circuit_intent as { verification_requirements: Array<{ method: string }> }).verification_requirements[0].method).toBe("独立负载测量");
  expect(container.textContent).toContain("DESIGN_ONLY");
  expect(container.textContent).toContain("输入负载条件下满足输出要求");
  await act(async () => { button(container, "批准设计提案").click(); await Promise.resolve(); });
  expect(requests[1].body).toMatchObject({ purpose: "PREDESIGN_APPROVAL", source_fingerprint: receiptFingerprint, decision: "APPROVE" });
  expect(container.textContent).toContain("未触发 Worker 或原理图验证");
  expect(container.textContent).not.toContain("schematic verified");
  expect(requests.some(({ url }) => url.includes("schematic-verifications"))).toBe(false);
  expect(refresh).toHaveBeenCalledTimes(1);
  await act(async () => root.unmount());
});

it("blocks S02 preview when Owner has no approved Requirement or Architecture source options", async () => {
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} mode="PREDESIGN_APPROVAL" ercOperationId={undefined} predesignSourceExpectations={sourceExpectations} onOwnerRefresh={async () => {}} />); });
  const selects = container.querySelectorAll("select");
  const inputs = container.querySelectorAll("input");
  await act(async () => {
    selects[0].value = "NEW"; selects[0].dispatchEvent(new Event("change", { bubbles: true }));
    selects[1].value = "turn-design"; selects[1].dispatchEvent(new Event("change", { bubbles: true }));
    for (const [index, value] of ["block-1", "block function", "claim-1", "claim text", "verification type", "user method"].entries()) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(inputs[index], value);
      inputs[index].dispatchEvent(new Event("input", { bubbles: true }));
    }
    container.querySelectorAll("select")[2].value = "block-1";
    container.querySelectorAll("select")[2].dispatchEvent(new Event("change", { bubbles: true }));
    setText(container.querySelector("textarea")!, "design");
  });
  expect(container.textContent).toContain("没有提供已批准的 Requirement 与 Architecture 来源选项");
  expect(button(container, "生成审核预览").disabled).toBe(true);
  await act(async () => root.unmount());
});

it("requires explicit target and valid block mapping, and submits multiple block/claim relationships", async () => {
  const requests: Array<Record<string, unknown>> = [];
  vi.stubGlobal("crypto", { randomUUID: () => "predesign-multi" });
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (!String(url).endsWith("/preview")) throw new Error("unexpected request");
    requests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
    return response({ project_id: "project-1", conversation_id: "conversation-1", turn_id: "turn-design",
      purpose: "PREDESIGN_APPROVAL", completion: "DESIGN_ONLY", proposal_id: "multi", proposal_fingerprint: proposalFingerprint,
      status: "CREATED", object_id: "new-circuit", revision: "r1", base_revision: null, base_fingerprint: null,
      source_fingerprint: receiptFingerprint, source_snapshot: {}, circuit_intent: requests[0].circuit_intent });
  }));
  const projection = { authoritative_facts: [{ domain: "CIRCUIT", project_id: "project-1", object_id: "existing", revision: "r7", currentness: "CURRENT" }] } as DesktopWorkspaceProjection;
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} mode="PREDESIGN_APPROVAL" ercOperationId={undefined} predesignSourceExpectations={sourceExpectations} approvedPredesignSources={approvedPredesignSources} projection={projection} onOwnerRefresh={async () => {}} />); });
  const selects = container.querySelectorAll("select");
  expect(selects[0].value).toBe("");
  expect([...selects[0].options].map((item) => item.textContent)).toEqual(["请选择发布目标", "新建 Circuit", "existing · r7"]);
  await act(async () => { selects[1].value = "turn-design"; selects[1].dispatchEvent(new Event("change", { bubbles: true }));
    const textareas = container.querySelectorAll("textarea"); setText(textareas[0], "multi design");
    button(container, "添加 Block").click(); button(container, "添加 Claim").click(); });
  const inputs = container.querySelectorAll("input");
  const values = ["b1", "block one", "b2", "block two", "c1", "claim one", "test", "measure one", "c2", "claim two", "test", "measure two"];
  await act(async () => { for (const [index, value] of values.entries()) { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(inputs[index], value); inputs[index].dispatchEvent(new Event("input", { bubbles: true })); } });
  expect(button(container, "生成审核预览").disabled).toBe(true);
  const ownerSelects = container.querySelectorAll("select");
  await act(async () => { ownerSelects[0].value = "NEW"; ownerSelects[0].dispatchEvent(new Event("change", { bubbles: true }));
    ownerSelects[2].value = "b1"; ownerSelects[2].dispatchEvent(new Event("change", { bubbles: true }));
    ownerSelects[3].value = "b2"; ownerSelects[3].dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => {
    (container.querySelector('[aria-label="Requirement req-power for b1"]') as HTMLInputElement).click();
    (container.querySelector('[aria-label="Architecture component-power for b1"]') as HTMLInputElement).click();
    (container.querySelector('[aria-label="Requirement req-can for b2"]') as HTMLInputElement).click();
    (container.querySelector('[aria-label="Architecture component-can for b2"]') as HTMLInputElement).click();
  });
  expect(button(container, "生成审核预览").disabled).toBe(false);
  await act(async () => { button(container, "生成审核预览").click(); await Promise.resolve(); });
  expect(requests).toHaveLength(1);
  expect(requests[0].object_id).toBeUndefined();
  const intent = requests[0].circuit_intent as { blocks: Array<{ block_id: string; verification_refs: string[]; requirement_refs: string[]; architecture_refs: string[] }>; verification_requirements: Array<{ verification_id: string; owner_ref: string; status: string; method: string }> };
  expect(intent.blocks).toEqual([{ block_id: "b1", function: "block one", requirement_refs: ["req-power"], architecture_refs: ["component-power"], verification_refs: ["c1"], status: "DRAFT" }, { block_id: "b2", function: "block two", requirement_refs: ["req-can"], architecture_refs: ["component-can"], verification_refs: ["c2"], status: "DRAFT" }]);
  expect(intent.verification_requirements.map(({ owner_ref, status, method }) => [owner_ref, status, method])).toEqual([["b1", "PLANNED", "measure one"], ["b2", "PLANNED", "measure two"]]);
  await act(async () => root.unmount());
});

it("keeps an APPLIED S02 receipt visible when Owner refresh fails without claiming completion beyond DESIGN_ONLY", async () => {
  vi.stubGlobal("crypto", { randomUUID: () => "predesign-refresh-failure" });
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL) => response(String(url).endsWith("/preview") ? {
    project_id: "project-1", conversation_id: "conversation-1", turn_id: "turn-design", proposal_id: "proposal-refresh",
    proposal_fingerprint: proposalFingerprint, status: "CREATED", purpose: "PREDESIGN_APPROVAL", completion: "DESIGN_ONLY",
    object_id: "circuit-s02", revision: "r1", base_revision: null, base_fingerprint: null, source_fingerprint: receiptFingerprint,
    source_snapshot: { revision: "approved-source" }, circuit_intent: { function: "candidate", blocks: [{ block_id: "b1" }], verification_requirements: [{ claim: "claim remains unverified" }] },
  } : {
    project_id: "project-1", proposal_id: "proposal-refresh", proposal_fingerprint: proposalFingerprint,
    purpose: "PREDESIGN_APPROVAL", completion: "DESIGN_ONLY", status: "APPLIED", object_id: "circuit-s02", revision: "r1",
    object_fingerprint: receiptFingerprint, approval_id: "approval-refresh", approval_fingerprint: receiptFingerprint,
  })));
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  const refresh = vi.fn(async () => { throw new Error("OWNER_REFRESH_FAILED"); });
  await act(async () => { root.render(<CircuitIntentReviewPanel {...baseProps} mode="PREDESIGN_APPROVAL" ercOperationId={undefined} predesignSourceExpectations={sourceExpectations} approvedPredesignSources={approvedPredesignSources} onOwnerRefresh={refresh} />); });
  const selects = container.querySelectorAll("select");
  await act(async () => { selects[1].value = "turn-design"; selects[1].dispatchEvent(new Event("change", { bubbles: true }));
    const inputs = container.querySelectorAll("input");
    for (const [index, value] of ["b1", "block", "c1", "claim", "test", "measure"].entries()) { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(inputs[index], value); inputs[index].dispatchEvent(new Event("input", { bubbles: true })); }
    selects[0].value = "NEW"; selects[0].dispatchEvent(new Event("change", { bubbles: true }));
    selects[2].value = "b1"; selects[2].dispatchEvent(new Event("change", { bubbles: true }));
    (container.querySelector('[aria-label="Requirement req-power for b1"]') as HTMLInputElement).click();
    (container.querySelector('[aria-label="Architecture component-power for b1"]') as HTMLInputElement).click();
    setText(container.querySelector("textarea")!, "design"); });
  await act(async () => { button(container, "生成审核预览").click(); await Promise.resolve(); });
  await act(async () => { button(container, "批准设计提案").click(); await Promise.resolve(); });
  expect(container.textContent).toContain("设计提案已批准（DESIGN_ONLY）");
  expect(container.textContent).toContain("OWNER_REFRESH_FAILED");
  expect(container.textContent).toContain("来源快照");
  expect(container.textContent).toContain("approved-source");
  expect(container.textContent).not.toContain("原理图已验证");
  await act(async () => root.unmount());
});
