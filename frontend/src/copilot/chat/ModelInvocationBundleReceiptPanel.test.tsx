import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { ModelInvocationReceiptPanel } from "./ModelInvocationReceiptPanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fp = "sha256:" + "a".repeat(64);
const authority = {
  tenant_id: "tenant-1", project_id: "project-1", engineering_task_id: "task-1",
  task_request_fingerprint: fp, coordination_fingerprint: fp,
  plan_id: "plan-1", plan_content_fingerprint: fp, review_fingerprint: fp,
  phase_id: "phase-1", phase_fingerprint: fp, plan_task_id: "plan-task-1",
  operation_id: "HARDWARE_SELECTION_PROPOSAL", operation_binding_fingerprint: fp,
};

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json" },
  });
}

function receipt() {
  return {
    request_id: "request-request-1", trace_id: "trace-1", project_id: "project-1",
    stage_id: "HARDWARE_SELECTION", policy_source: "STAGE_POLICY",
    connection_id: "connection-1", selected_model: null, usage: null,
    result: "FAILED", error_code: "BUDGET_EVIDENCE_UNAVAILABLE",
    started_at: "2026-10-15T00:00:00Z", completed_at: "2026-10-15T00:00:01Z",
    fingerprint: fp, invocation_mode: "TASK", authority_reference: authority,
    gateway_call_count: 1, gateway_response_observed: false,
    budget_envelope_id: "envelope-1", budget_evidence_fingerprint: null,
    budget_reservation_id: "reservation-1", budget_reconciliation_state: "UNKNOWN",
    budget_phase: "TERMINAL", counter_admission_fingerprint: fp,
    counter_observation_fingerprint: fp, counter_status: "HTTP_ERROR",
    counter_transport_send_count: 1, counter_response_observed: true,
    generation_transport_send_count: 0, total_external_request_count: 1,
    counter_http_status: 500, counter_input_tokens: null, transport_send_count: 0,
  };
}

async function showReceipt(value: unknown): Promise<{ container: HTMLDivElement; unmount: () => Promise<void> }> {
  const fetcher = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(response({ items: [value], total: 1, limit: 20, offset: 0 }))
    .mockResolvedValue(response({}, 404));
  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => root.render(
    <ModelInvocationReceiptPanel projectId="project-1" chatRequestId="request-1" fetcher={fetcher} />,
  ));
  await act(async () => {
    (container.querySelector("button") as HTMLButtonElement).click();
    await Promise.resolve();
  });
  return { container, unmount: async () => { await act(async () => root.unmount()); } };
}

function displayed(container: HTMLElement, label: string): string | null {
  const list = container.querySelector("dl");
  const term = [...(list?.querySelectorAll("dt") ?? [])].find((item) => item.textContent === label);
  return term?.nextElementSibling?.textContent ?? null;
}

describe("two-role model invocation receipt panel", () => {
  it.each([
    ["HTTP_ERROR", 500, null, "HTTP 500 / 无有效 token 数"],
    ["COUNTED", 200, 8193, "HTTP 200 / 8193"],
  ])("shows counter-only %s without a generation send", async (status, http, tokens, expected) => {
    const view = await showReceipt({
      ...receipt(), counter_status: status, counter_http_status: http,
      counter_input_tokens: tokens,
    });
    expect(displayed(view.container, "计数操作")).toBe(`${status} / 1 次`);
    expect(displayed(view.container, "计数响应")).toBe(expected);
    expect(displayed(view.container, "生成操作")).toBe("0 次");
    expect(displayed(view.container, "外部请求合计")).toBe("1 次");
    await view.unmount();
  });

  it("renders uncertain counter intent as UNKNOWN, never zero", async () => {
    const view = await showReceipt({
      ...receipt(), counter_status: "OUTCOME_UNKNOWN",
      counter_transport_send_count: null, counter_response_observed: false,
      counter_observation_fingerprint: null, counter_http_status: null,
      total_external_request_count: null,
    });
    expect(displayed(view.container, "计数操作")).toBe("OUTCOME_UNKNOWN / UNKNOWN");
    expect(displayed(view.container, "生成操作")).toBe("0 次");
    expect(displayed(view.container, "外部请求合计")).toBe("UNKNOWN");
    await view.unmount();
  });

  it("renders a completed one-count plus one-generation bundle as two", async () => {
    const view = await showReceipt({
      ...receipt(), result: "SUCCESS", error_code: null,
      counter_status: "COUNTED", counter_http_status: 200,
      counter_input_tokens: 8192, generation_transport_send_count: 1,
      total_external_request_count: 2, transport_send_count: 1,
      budget_evidence_fingerprint: fp,
    });
    expect(displayed(view.container, "计数操作")).toBe("COUNTED / 1 次");
    expect(displayed(view.container, "生成操作")).toBe("1 次");
    expect(displayed(view.container, "外部请求合计")).toBe("2 次");
    await view.unmount();
  });

  it("keeps legacy receipts visible without adding bundle labels", async () => {
    const old: Record<string, unknown> = receipt();
    for (const name of [
      "budget_phase", "counter_admission_fingerprint", "counter_observation_fingerprint",
      "counter_status", "counter_transport_send_count", "counter_response_observed",
      "generation_transport_send_count", "total_external_request_count",
      "counter_http_status", "counter_input_tokens",
    ]) delete old[name];
    old.budget_evidence_fingerprint = fp;
    const view = await showReceipt(old);
    expect(view.container.textContent).toContain("request-request-1");
    expect(displayed(view.container, "预算角色阶段")).toBeNull();
    expect(displayed(view.container, "外部请求合计")).toBeNull();
    await view.unmount();
  });
});
