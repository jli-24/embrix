import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ConversationTurn } from "../api/projectClient";
import type { ChatTraceEvent } from "../api/chatClient";
import { ChatPanel } from "./ChatPanel";

const turn: ConversationTurn = {
  turn_id: "turn-1", request_id: "request-1", trace_id: "trace-1", status: "COMPLETED",
  question: "如何分析需求？", answer_text: "需求分析已完成。", evidence: [], recommendations: [],
  context_references: [], error_code: null, created_at: "2026-09-29T00:00:00Z", completed_at: "2026-09-29T00:00:01Z",
};

function panel(traceByTurn?: Record<string, ChatTraceEvent[] | null>) {
  return <ChatPanel title="Chat" chatSessionId="session-2" sessionReady turns={[turn]} pendingQuestion={null}
    availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="compact"
    onToggleReference={() => {}} onAttach={() => {}} onSubmit={() => {}} onRetry={() => {}}
    traceByTurn={traceByTurn} />;
}

afterEach(() => vi.unstubAllGlobals());

describe("chat trace record states", () => {
  it("keeps a successful answer distinct from a trace read failure", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("trace store unavailable"));
    vi.stubGlobal("fetch", fetcher);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(panel()));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(String(fetcher.mock.calls[0][0])).toContain("/sessions/session-2/trace?trace_id=trace-1");
    expect(container.textContent).toContain("需求分析已完成。");
    expect(container.textContent).toContain("调用记录读取失败，回答结果不受影响");
    expect(container.querySelector('[data-activity-id="trace-unavailable"]')).toBeNull();
    await act(async () => root.unmount());
  });

  it("distinguishes an empty record and a loading record from failed execution", () => {
    const empty = renderToStaticMarkup(panel({ "turn-1": [] }));
    const loading = renderToStaticMarkup(panel({}));
    expect(empty).toContain("本次调用记录为空");
    expect(loading).toContain("正在读取调用记录");
    expect(empty).not.toContain("执行未通过");
  });

  it("shows normal trace evidence without inventing engineering execution", () => {
    const event: ChatTraceEvent = {
      event_id: "event-1", trace_id: "trace-1", request_id: "request-1", project_id: "project-1",
      conversation_id: "session-1", stage: "UI_PROJECTION", route: "KNOWLEDGE",
      agent_identity: null, service_identity: "DesktopProjection", source_status: "AVAILABLE",
      evidence_count: 0, gate_result: "PASS", provider_configured: true,
      gateway_invocation_count: 1, reason_code: "ANSWER_COMPLETED", terminal_event: "ANSWER_COMPLETED",
      timestamp: "2026-09-29T00:00:01Z", fingerprint: "sha256:" + "a".repeat(64),
    };
    const markup = renderToStaticMarkup(panel({ "turn-1": [event] }));
    expect(markup).toContain("已完成 1 个步骤");
    expect(markup).not.toContain("固件构建");
    expect(markup).not.toContain("固件烧录");
    expect(markup).not.toContain("调用记录读取失败");
  });
});
