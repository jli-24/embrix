import { describe, expect, it } from "vitest";

import type { ChatEvidence } from "../api/projectClient";
import type { ChatTraceEvent } from "../api/chatClient";
import type { ExecutionOperationEvent } from "../api/executionClient";
import {
  projectConversationActivities,
  projectExecutionActivities,
  projectEvidenceActivity,
} from "./conversationActivity";

const trace = (stage: string, overrides: Partial<ChatTraceEvent> = {}): ChatTraceEvent => ({
  event_id: `event-${stage}`,
  trace_id: "trace-1",
  request_id: "request-1",
  project_id: "project-1",
  conversation_id: "conversation-1",
  stage,
  route: "KNOWLEDGE",
  agent_identity: "KnowledgeAgent",
  service_identity: "EngineeringContextService",
  source_status: "AVAILABLE",
  evidence_count: 1,
  gate_result: "PASS",
  provider_configured: true,
  gateway_invocation_count: 1,
  reason_code: "COMPLETED",
  terminal_event: null,
  timestamp: "2026-09-20T10:00:00Z",
  fingerprint: "sha256:" + "b".repeat(64),
  ...overrides,
});

const executionEvent = (event_type: ExecutionOperationEvent["event_type"], overrides: Partial<ExecutionOperationEvent> = {}): ExecutionOperationEvent => ({
  event_id: `event-${event_type}`,
  event_type,
  operation_id: "operation-1",
  operation_type: "KICAD_SCHEMATIC_ERC",
  status: event_type === "OPERATION_SUCCEEDED" ? "SUCCEEDED" : "RUNNING",
  recovery_state: "NONE",
  receipt_fingerprint: event_type === "OPERATION_SUCCEEDED" ? "sha256:receipt" : null,
  reason_code: event_type,
  ...overrides,
});

const evidence = (status: string): ChatEvidence => ({
  source_type: "Verification",
  label: "Return Path",
  reference_id: "evidence-1",
  status,
  summary: "Reference-plane assignment",
  source_uri: null,
  source_fingerprint: "sha256:" + "a".repeat(64),
  observed_at: "2026-09-20T10:00:00Z",
});

describe("conversation activity projection", () => {
  it("projects completed greenfield discovery gates without claiming source retrieval", () => {
    const greenfield = (stage: string, gate_result: string, reason_code: string): ChatTraceEvent => trace(stage, {
      route: "GREENFIELD_DISCOVERY",
      source_status: ["ANSWER_PERSIST", "SSE_TERMINAL", "UI_PROJECTION"].includes(stage) ? "AVAILABLE" : "EMPTY_PROJECT",
      evidence_count: 0, gate_result, reason_code,
      gateway_invocation_count: ["MODEL_GATEWAY", "ANSWER_PERSIST", "SSE_TERMINAL", "UI_PROJECTION"].includes(stage) ? 1 : 0,
    });
    const activities = projectConversationActivities({
      traceEvents: [
        greenfield("ROUTE_SELECT", "PASS", "GREENFIELD"),
        greenfield("CONTEXT_BUILD", "PASS", "GREENFIELD_CONTEXT_READY"),
        greenfield("SOURCE_SNAPSHOT", "EMPTY_PROJECT", "EMPTY_PROJECT"),
        greenfield("FIRMWARE_PARSE", "NOT_APPLICABLE_FOR_GREENFIELD", "GREENFIELD"),
        greenfield("EVIDENCE_BUILD", "GREENFIELD_CONTEXT", "GREENFIELD_CONTEXT_PRESENT"),
        greenfield("EVIDENCE_GATE", "NOT_APPLICABLE_FOR_GREENFIELD", "GREENFIELD"),
        greenfield("PROVIDER_RESOLVE", "AVAILABLE", "PROVIDER_READY"),
        greenfield("MODEL_GATEWAY", "PASS", "ANSWER_COMPLETED"),
        greenfield("ANSWER_PERSIST", "PASS", "ANSWER_COMPLETED"),
        greenfield("SSE_TERMINAL", "TERMINAL", "ANSWER_COMPLETED"),
        greenfield("UI_PROJECTION", "PASS", "ANSWER_COMPLETED"),
      ].map((event) => ({ ...event, terminal_event: event.stage === "SSE_TERMINAL" || event.stage === "UI_PROJECTION" ? "ANSWER_COMPLETED" : null })),
      executionEvents: [], evidence: [], artifactRefs: [],
    });
    expect(activities.map(({ id, status, result_state }) => [id, status, result_state])).toEqual([
      ["route", "PASS", "VERIFIED"],
      ["context", "PASS", "VERIFIED"],
      ["evidence", "PASS", "NOT_APPLICABLE"],
      ["answer", "PASS", "VERIFIED"],
    ]);
    expect(activities.find((item) => item.id === "context")?.children[1].result_state).toBe("NOT_APPLICABLE");
    expect(activities.find((item) => item.id === "evidence")?.children.every((child) => child.not_applicable)).toBe(true);
  });

  it("does not let later completion hide failed or unknown gates", () => {
    for (const gate_result of ["FAIL", "UNKNOWN", "STALE", "UNAVAILABLE", "FUTURE_GATE"]) {
      const activities = projectConversationActivities({
        traceEvents: [
          trace("PROVIDER_RESOLVE", { gate_result, route: "GREENFIELD_DISCOVERY" }),
          trace("UI_PROJECTION", { terminal_event: "ANSWER_COMPLETED" }),
        ], executionEvents: [], evidence: [], artifactRefs: [],
      });
      const answer = activities.find((item) => item.id === "answer");
      expect(answer?.status).not.toBe("PASS");
      expect(answer?.result_state).not.toBe("VERIFIED");
    }
    const failed = projectConversationActivities({ traceEvents: [trace("UI_PROJECTION", { gate_result: "FAIL", terminal_event: "ANSWER_FAILED" })], executionEvents: [], evidence: [], artifactRefs: [] });
    expect(failed[0].result_state).toBe("FAILED");
    const unrecognizedTerminal = projectConversationActivities({ traceEvents: [trace("UI_PROJECTION", { gate_result: "FUTURE_GATE", terminal_event: "ANSWER_COMPLETED" })], executionEvents: [], evidence: [], artifactRefs: [] });
    expect(unrecognizedTerminal[0].status).toBe("PENDING");
    expect(unrecognizedTerminal[0].result_state).toBeNull();
    const otherRoute = projectConversationActivities({ traceEvents: [trace("SOURCE_SNAPSHOT", { route: "KNOWLEDGE", gate_result: "EMPTY_PROJECT" })], executionEvents: [], evidence: [], artifactRefs: [] });
    expect(otherRoute[0].status).toBe("PENDING");
  });
  it("aggregates runtime trace stages into a small collapsed-friendly activity list", () => {
    const activities = projectConversationActivities({
      traceEvents: [
        trace("ROUTE_SELECT"),
        trace("CONTEXT_BUILD"),
        trace("EVIDENCE_GATE"),
        trace("UI_PROJECTION", { service_identity: "DesktopProjection", terminal_event: "ANSWER_COMPLETED" }),
      ],
      executionEvents: [],
      evidence: [],
      artifactRefs: [],
    });

    expect(activities.map((item) => item.title)).toEqual([
      "选择处理路径",
      "准备工程上下文",
      "检索工程证据",
      "生成回答",
    ]);
    expect(activities.every((item) => item.children.length <= 3)).toBe(true);
    expect(JSON.stringify(activities)).not.toContain("chain-of-thought");
  });

  it("keeps one execution activity while backend events update its status", () => {
    const activities = projectExecutionActivities([
      executionEvent("OPERATION_STARTED"),
      executionEvent("OPERATION_SUCCEEDED"),
    ]);

    expect(activities).toHaveLength(1);
    expect(activities[0].id).toBe("execution-operation-1");
    expect(activities[0].status).toBe("PASS");
    expect(activities[0].tool).toBe("KiCad");
    expect(activities[0].children).toHaveLength(2);
  });

  it.each([
    ["UNKNOWN", "UNKNOWN"],
    ["STALE", "STALE"],
    ["NOT_APPLICABLE", "NOT_APPLICABLE"],
  ] as const)("preserves evidence state %s instead of treating it as PASS", (status, resultState) => {
    const item = projectEvidenceActivity([evidence(status)]);
    expect(item?.result_state).toBe(resultState);
    expect(item?.not_applicable).toBe(status === "NOT_APPLICABLE");
  });

  it("does not invent activities when no backend evidence exists", () => {
    expect(projectConversationActivities({ traceEvents: [], executionEvents: [], evidence: [], artifactRefs: [] })).toEqual([]);
  });

  it("projects a pure answer trace without engineering operations", () => {
    const activities = projectConversationActivities({ traceEvents: [trace("UI_PROJECTION", { terminal_event: "ANSWER_COMPLETED" })], executionEvents: [], evidence: [], artifactRefs: [] });
    expect(activities.map((item) => item.id)).toEqual(["answer"]);
    expect(activities.some((item) => item.id.startsWith("execution-"))).toBe(false);
  });

  it("projects failure, approval and artifact states from existing data", () => {
    const activities = projectConversationActivities({
      traceEvents: [trace("EVIDENCE_GATE", { gate_result: "FAIL", reason_code: "VERIFICATION_FAILED" })],
      executionEvents: [executionEvent("APPROVAL_ISSUED")],
      evidence: [evidence("FAIL")],
      artifactRefs: ["hardware/main.kicad_pcb"],
    });
    expect(activities.some((item) => item.status === "FAIL")).toBe(true);
    expect(activities.some((item) => item.status === "AWAITING_APPROVAL")).toBe(true);
    expect(activities.find((item) => item.id === "engineering-artifacts")?.children[0].title).toBe("main.kicad_pcb");
  });

  it("uses the same activity id for a live stream update", () => {
    const running = projectConversationActivities({ traceEvents: [], executionEvents: [], evidence: [], artifactRefs: [], pending: true, streamStatus: "streaming" });
    const completed = projectConversationActivities({ traceEvents: [trace("UI_PROJECTION", { terminal_event: "ANSWER_COMPLETED" })], executionEvents: [], evidence: [], artifactRefs: [] });
    expect(running.find((item) => item.id === "answer")?.status).toBe("RUNNING");
    expect(completed.find((item) => item.id === "answer")?.status).toBe("PASS");
  });
});
