import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ModelInvocationReceiptPanel } from "./ModelInvocationReceiptPanel";
import { ChatPanel } from "./ChatPanel";

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

const receipt = (projectId: string, requestId: string, connectionId: string | null = "connection-1", usage: unknown = null) => ({ request_id: requestId, trace_id: "trace-1", project_id: projectId, stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: connectionId, selected_model: { provider_id: "deepseek", endpoint_id: "endpoint-1", model_profile_id: "profile-1", model_id: "deepseek-chat" }, usage, result: "SUCCEEDED", error_code: null, started_at: "2026-09-29T00:00:00Z", completed_at: "2026-09-29T00:00:01Z", fingerprint: "sha256:receipt" });

async function mounted(fetcher: typeof fetch, projectId = "project-1", requestId = "request-1"): Promise<{ container: HTMLDivElement; root: Root }> {
  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => root.render(<ModelInvocationReceiptPanel projectId={projectId} chatRequestId={requestId} fetcher={fetcher} />));
  return { container, root };
}

async function open(container: HTMLDivElement): Promise<void> {
  await act(async () => { (container.querySelector("button") as HTMLButtonElement).click(); await Promise.resolve(); });
}

describe("engineering model invocation receipt panel", () => {
  it("places the read-only entry beside a real turn using its chat request id", () => {
    const markup = renderToStaticMarkup(<ChatPanel title="Chat" activeProjectIdentity={{ id: "project-1", name: "Project", lifecycle: "ready", conversationState: "completed" }} sessionReady turns={[{ turn_id: "answer-1", request_id: "request-1", status: "COMPLETED", question: "Question", answer_text: "Answer", evidence: [], recommendations: [], context_references: [], error_code: null, created_at: "2026-09-29T00:00:00Z", completed_at: "2026-09-29T00:00:01Z" }]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="compact" onToggleReference={() => {}} onAttach={() => {}} onSubmit={() => {}} onRetry={() => {}} />);
    expect(markup).toContain('data-model-invocation-receipt="true"');
    expect(markup).toContain("查看模型调用凭证");
  });

  it("loads only on click and shows the selected model and policy source", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ items: [receipt("project-1", "request-1")], total: 1, limit: 20, offset: 0 })).mockResolvedValue(response({}, 404));
    const { container, root } = await mounted(fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    await open(container);
    expect(container.textContent).toContain("模型调用凭证");
    expect(container.textContent).toContain("PROJECT_DEFAULT");
    expect(container.textContent).toContain("deepseek-chat");
    expect(container.textContent).toContain("connection-1");
    expect(container.textContent).toContain("request-1");
    expect(container.textContent).toContain("历史调用记录，不代表当前连接健康");
    expect(container.textContent).toContain("用量UNKNOWN");
    expect(container.textContent).toContain("额度UNKNOWN");
    expect(container.textContent).toContain("费用UNKNOWN");
    expect(container.textContent).not.toContain("Connection Test Receipt");
    await act(async () => root.unmount());
  });

  it("marks an old receipt's connection provenance as unknown", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ items: [{ ...receipt("project-1", "request-1", null), invocation_mode: "LEGACY_UNBOUND", gateway_response_observed: null }], total: 1, limit: 20, offset: 0 })).mockResolvedValue(response({}, 404));
    const { container, root } = await mounted(fetcher);
    await open(container);
    expect(container.textContent).toContain("Connection ID未知（旧记录）");
    expect(container.textContent).toContain("Gateway 响应UNKNOWN");
    await act(async () => root.unmount());
  });

  it("shows provider-reported tokens without inventing quota or cost", async () => {
    const usage = { input_tokens: 3, output_tokens: 2, total_tokens: 5, cached_input_tokens: null, cache_write_tokens: null, reasoning_tokens: null, usage_source: "PROVIDER" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ items: [receipt("project-1", "request-1", "connection-1", usage)], total: 1, limit: 20, offset: 0 })).mockResolvedValue(response({}, 404));
    const { container, root } = await mounted(fetcher);
    await open(container);
    expect(container.textContent).toContain("用量输入 3 / 输出 2 / 总计 5 tokens（PROVIDER）");
    expect(container.textContent).toContain("额度UNKNOWN");
    expect(container.textContent).toContain("费用UNKNOWN");
    await act(async () => root.unmount());
  });

  it("shows an uncertain persisted attempt when no receipt exists", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ items: [], total: 0, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(response({ request_id: "request-request-1", project_id: "project-1", stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: "connection-1", status: "OUTCOME_UNKNOWN", confirmed_provider_request_count: null, receipt_fingerprint: null }));
    const { container, root } = await mounted(fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    await open(container);
    expect(container.textContent).toContain("OUTCOME_UNKNOWN");
    expect(container.textContent).toContain("可能已发送；UNKNOWN 结果不会自动重试");
    expect(container.textContent).toContain("connection-1");
    expect(container.textContent).toContain("用量UNKNOWN");
    expect(container.textContent).toContain("费用UNKNOWN");
    expect(container.textContent).not.toContain("暂无模型调用凭证");
    await act(async () => root.unmount());
  });

  it("separates a completed gateway response from unknown transport sends", async () => {
    const authority = { tenant_id: "tenant-1", project_id: "project-1", engineering_task_id: "task-1", task_request_fingerprint: "sha256:task", coordination_fingerprint: "sha256:coordination", plan_id: "plan-1", plan_content_fingerprint: "sha256:plan", review_fingerprint: "sha256:review", phase_id: "phase-1", phase_fingerprint: "sha256:phase", plan_task_id: "plan-task-1", operation_id: "HARDWARE_SELECTION_PROPOSAL", operation_binding_fingerprint: "sha256:binding" };
    const attempt = { request_id: "request-request-1", project_id: "project-1", stage_id: "HARDWARE_SELECTION", policy_source: "STAGE_POLICY", connection_id: "connection-1", status: "COMPLETED", confirmed_provider_request_count: null, receipt_fingerprint: "sha256:receipt", invocation_mode: "TASK", authority_reference: authority, gateway_call_count: 1, gateway_response_observed: true, transport_send_count: null };
    const currentReceipt = { ...receipt("project-1", "request-request-1"), stage_id: "HARDWARE_SELECTION", invocation_mode: "TASK", authority_reference: attempt.authority_reference, gateway_call_count: 1, gateway_response_observed: true, transport_send_count: null };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ items: [currentReceipt], total: 1, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(response(attempt));
    const { container, root } = await mounted(fetcher);
    await open(container);
    expect(container.textContent).toContain("Gateway 调用1 次");
    expect(container.textContent).toContain("Gateway 响应已观察");
    expect(container.textContent).toContain("Transport 发送UNKNOWN");
    expect(container.textContent).toContain("用量UNKNOWN");
    expect(container.textContent).not.toContain("已确认 1 次");
    await act(async () => root.unmount());
  });

  it("does not describe a prepared attempt as sent", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ items: [], total: 0, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(response({ request_id: "request-request-1", project_id: "project-1", stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: "connection-1", status: "PREPARED", confirmed_provider_request_count: null, receipt_fingerprint: null }));
    const { container, root } = await mounted(fetcher);
    await open(container);
    expect(container.textContent).toContain("PREPARED");
    expect(container.textContent).toContain("尚未进入发送流程");
    expect(container.textContent).not.toContain("可能已发送");
    await act(async () => root.unmount());
  });

  it("shows uncertainty alongside a failed receipt", async () => {
    const failedReceipt = { ...receipt("project-1", "request-request-1"), result: "FAILED", error_code: "MODEL_PROVIDER_REQUEST_FAILED" };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ items: [failedReceipt], total: 1, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(response({ request_id: "request-request-1", project_id: "project-1", stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: "connection-1", status: "OUTCOME_UNKNOWN", confirmed_provider_request_count: null, receipt_fingerprint: null }));
    const { container, root } = await mounted(fetcher);
    await open(container);
    expect(container.textContent).toContain("MODEL_PROVIDER_REQUEST_FAILED");
    expect(container.textContent).toContain("可能已发送；UNKNOWN 结果不会自动重试");
    await act(async () => root.unmount());
  });

  it("separates empty, failed, forbidden, and not-found states", async () => {
    for (const [reply, expected] of [
      [response({ items: [], total: 0, limit: 20, offset: 0 }), "暂无模型调用凭证"],
      [response({}, 500), "读取模型调用凭证失败"],
      [response({}, 403), "无权查看模型调用凭证"],
      [response({}, 404), "模型调用凭证记录不存在"],
    ] as const) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(reply).mockResolvedValue(response({}, 404));
      const { container, root } = await mounted(fetcher);
      await open(container);
      expect(container.textContent).toContain(expected);
      await act(async () => root.unmount());
    }
  });

  it("does not display a late response after switching project or turn", async () => {
    let resolveOld!: (response: Response) => void;
    const oldResponse = new Promise<Response>((resolve) => { resolveOld = resolve; });
    const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(() => oldResponse).mockResolvedValueOnce(response({ items: [receipt("project-2", "request-2")], total: 1, limit: 20, offset: 0 })).mockResolvedValue(response({}, 404));
    const { container, root } = await mounted(fetcher);
    await open(container);
    await act(async () => root.render(<ModelInvocationReceiptPanel projectId="project-2" chatRequestId="request-2" fetcher={fetcher} />));
    await open(container);
    await act(async () => resolveOld(response({ items: [receipt("project-1", "request-1")], total: 1, limit: 20, offset: 0 })));
    expect(container.textContent).toContain("request-2");
    expect(container.textContent).not.toContain("request-1");
    await act(async () => root.unmount());
  });
});


it("shows the v6 estimate separately and never turns unknown billing into zero", async () => {
  const authority = Object.fromEntries(["tenant_id", "engineering_task_id", "task_request_fingerprint", "coordination_fingerprint", "plan_id", "plan_content_fingerprint", "review_fingerprint", "phase_id", "phase_fingerprint", "plan_task_id", "operation_id", "operation_binding_fingerprint"].map((key) => [key, "offline-binding"]));
  const entry = { ...receipt("project-1", "request-request-1", "connection-1", { input_tokens: 2, output_tokens: 3, total_tokens: 5, usage_source: "PROVIDER_REPORTED", estimated_cost: 0.000005, currency: "USD" }), invocation_mode: "TASK", authority_reference: { ...authority, project_id: "project-1" }, gateway_call_count: 1, gateway_response_observed: true, transport_send_count: 1, budget_envelope_id: "offline-one-envelope", budget_evidence_fingerprint: "sha256:" + "3".repeat(64), budget_reservation_id: "budget-reservation", budget_reconciliation_state: "ESTIMATED" };
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ items: [entry], total: 1, limit: 20, offset: 0 })).mockResolvedValue(response({}, 404));
  const { container, root } = await mounted(fetcher);
  await open(container);
  expect(container.textContent).toContain("绑定已保存");
  expect(container.textContent).toContain("估算 USD");
  expect(container.textContent).not.toContain("Provider 报告");
  await act(async () => root.unmount());
});
