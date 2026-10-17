import { describe, expect, it, vi } from "vitest";

import {
  bindChatAttachment,
  closeChatSession,
  eventUrl,
  fetchChatTrace,
  reduceChatStreamEvent,
  submitQuestion,
  streamChatEvents,
} from "../api/chatClient";
import {
  fetchEnvironmentAudit,
  fetchEnvironmentMemory,
  fetchEnvironmentProjects,
} from "../api/environmentClient";
import { ProjectCatalogRequestError, confirmProject, createConversation, decidePredesignCircuitIntent, deleteConversation, deleteCatalogProject, fetchProjectDirectories, fetchProjectLocations, listSchematicVerifications, previewPredesignCircuitIntent, previewProject, recordSchematicVerification, registerProjectLocation, requestSchematicModelCandidate } from "../api/projectClient";
import { createTransmissionApproval } from "../api/providerClient";
import type { FrontendProviderDiagnosticEvent } from "../api/webviewSessionDiagnostic";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("copilot API clients", () => {
  it("submits only explicit Plan binding IDs and preserves typed schematic BLOCKED reason", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      status: "BLOCKED", operation_id: "SCHEMATIC_DESIGN_PROPOSAL",
      blocking_reasons: ["POWER_SOURCE_UNRESOLVED"], reason_code: "POWER_SOURCE_UNRESOLVED",
      trace_id: "trace-test",
    }), { status: 409, headers: { "Content-Type": "application/json" } }));
    const input = { request_id: "request-1", plan_id: "plan-1", phase_id: "phase-1", plan_task_id: "task-1" };
    await expect(requestSchematicModelCandidate("project-1", "conversation-1", input, fetcher)).rejects.toMatchObject({
      code: "POWER_SOURCE_UNRESOLVED", blockingReason: "POWER_SOURCE_UNRESOLVED",
    } satisfies Partial<ProjectCatalogRequestError>);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/model-candidate-proposal");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(input);
  });
  it("does not trust malformed schematic BLOCKED projections", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      status: "BLOCKED", operation_id: "SCHEMATIC_DESIGN_PROPOSAL",
      blocking_reasons: ["UNTRUSTED_REASON"], reason_code: "UNTRUSTED_REASON",
    }), { status: 409, headers: { "Content-Type": "application/json" } }));
    await expect(requestSchematicModelCandidate("project-1", "conversation-1", {
      request_id: "request-1", plan_id: "plan-1", phase_id: "phase-1", plan_task_id: "task-1",
    }, fetcher)).rejects.toMatchObject({ blockingReason: null });
  });
  it("sends explicit design source and verification selectors without client PASS claims", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse({ completion: "DESIGN_ONLY" }));
    const fingerprint = `sha256:${"a".repeat(64)}`;
    await previewPredesignCircuitIntent("project-1", {
      purpose: "PREDESIGN_APPROVAL", conversation_id: "conversation-1", turn_id: "turn-selected",
      circuit_intent: { function: "Power", topology: ["VIN -> 3V3"], parameters: [], calculation: [], assumptions: [], risks: [], blocks: [{ block_id: "block-power", function: "Power conversion", requirement_refs: ["requirement-1"], architecture_refs: ["component-1"], verification_refs: ["verify-power"] }], verification_requirements: [{ verification_id: "verify-power", owner_ref: "block-power", claim: "Check power", verification_type: "DESIGN_REVIEW", method: "Review" }] },
      source_expectations: { requirement_context_fingerprint: fingerprint, architecture_id: "architecture-1", architecture_fingerprint: fingerprint, hardware_selection_approval_id: "approval-1", hardware_truth_fingerprint: fingerprint },
      idempotency_key: "request-1",
    }, fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/project-1/circuit-intents/preview");
    const previewBody = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(previewBody.turn_id).toBe("turn-selected");
    expect(previewBody.erc_operation_id).toBeUndefined();
    await decidePredesignCircuitIntent("project-1", { purpose: "PREDESIGN_APPROVAL", proposal_id: "proposal-1", proposal_fingerprint: fingerprint, source_fingerprint: fingerprint, decision: "APPROVE" }, fetcher);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body)).decision).toBe("APPROVE");
    await recordSchematicVerification("project-1", "conversation-1", { intent_id: "circuit-1", intent_revision: "r1", intent_fingerprint: fingerprint, plan_id: "plan-1", plan_fingerprint: fingerprint, artifact_fingerprint: fingerprint, generation_operation_id: "write-1", erc_operation_id: "erc-1" }, fetcher);
    expect(fetcher.mock.calls[2][0]).toBe("/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-verifications");
    expect(String(fetcher.mock.calls[2][1]?.body)).not.toContain("aggregate_status");
    await listSchematicVerifications("project-1", "conversation-1", 10, fetcher);
    expect(fetcher.mock.calls[3][0]).toBe("/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-verifications?limit=10");
  });
  it("reads the existing backend chat trace projection without exposing private diagnostics", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([{ stage: "UI_PROJECTION", fingerprint: `sha256:${"a".repeat(64)}` }]));
    const result = await fetchChatTrace("session-1", "trace-1", fetcher);
    expect(result[0].stage).toBe("UI_PROJECTION");
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/chat/sessions/session-1/trace?trace_id=trace-1");
    expect(JSON.stringify(fetcher.mock.calls[0][1])).not.toContain("prompt");
  });

  it("deletes a conversation through the project catalog contract", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ conversation_id: "conversation-1", deleted: true }));
    await expect(deleteConversation("project-1", "conversation-1", fetcher)).resolves.toEqual({ conversation_id: "conversation-1", deleted: true });
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/project-1/conversations/conversation-1");
    expect(fetcher.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("emits a redacted approval HTTP trace around the single receipt request", async () => {
    const events: string[] = [];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      receipt_id: "approval-1",
      decision: "APPROVED",
      context_fingerprint: `sha256:${"a".repeat(64)}`,
      provider_configuration_fingerprint: `sha256:${"b".repeat(64)}`,
      expires_at: "2026-08-23T00:05:00Z",
      fingerprint: `sha256:${"c".repeat(64)}`,
    }));

    await createTransmissionApproval({
      session_id: "session-1",
      project_id: "project-1",
      request_id: "request-1",
      context_fingerprints: [],
      decision: "APPROVED",
    }, fetcher, (event) => events.push(event.stage));

    expect(events).toEqual([
      "C3_APPROVAL_HTTP_REQUEST_SENT",
      "C4_APPROVAL_HTTP_RESPONSE",
      "C5_APPROVAL_RECEIPT_CREATED",
    ]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/desktop/system/provider/transmission-approvals");
    expect(fetcher.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.stringify(events)).not.toContain("session-1");
    expect(JSON.stringify(events)).not.toContain("project-1");
    expect(JSON.stringify(events)).not.toContain("request-1");
  });

  it("emits a failed response trace without fabricating a receipt", async () => {
    const events: Array<{ stage: string; status: string }> = [];
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("offline"));

    await expect(createTransmissionApproval({
      session_id: "session-2",
      project_id: "project-2",
      request_id: "request-2",
      context_fingerprints: [],
      decision: "APPROVED",
    }, fetcher, (event) => events.push({ stage: event.stage, status: event.status }))).rejects.toThrow();

    expect(events).toEqual([
      { stage: "C3_APPROVAL_HTTP_REQUEST_SENT", status: "PASS" },
      { stage: "C4_APPROVAL_HTTP_RESPONSE", status: "FAIL" },
    ]);
    expect(events.some((event) => event.stage === "C5_APPROVAL_RECEIPT_CREATED")).toBe(false);
  });

  it("requires the backend conversation identity and never creates one locally", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      conversation: { conversation_id: "conversation-from-backend" },
      session_id: "session-from-backend",
      chat_available: true,
    }));

    const result = await createConversation("project-1", fetcher);

    expect(result.conversation.conversation_id).toBe("conversation-from-backend");
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/project-1/conversations");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({});
  });

  it("keeps the chat success gate on SSE completion and fails closed", () => {
    const initial = { status: "idle" as const, answer: "" };
    const started = reduceChatStreamEvent(initial, { event_id: "1", event_type: "CHAT_STARTED", answer_chunk: null, answer_fingerprint: null });
    const streamed = reduceChatStreamEvent(started, { event_id: "2", event_type: "ANSWER_CHUNK", answer_chunk: "Evidence-backed ", answer_fingerprint: null });
    const completed = reduceChatStreamEvent(streamed, { event_id: "3", event_type: "ANSWER_COMPLETED", answer_chunk: null, answer_fingerprint: "sha256:x" });
    const failed = reduceChatStreamEvent(streamed, { event_id: "4", event_type: "ANSWER_FAILED", answer_chunk: null, answer_fingerprint: null });

    expect(completed).toEqual({ status: "completed", answer: "Evidence-backed " });
    expect(failed).toEqual({ status: "failed", answer: "Evidence-backed " });
    expect(reduceChatStreamEvent(failed, { event_id: "5", event_type: "ANSWER_CHUNK", answer_chunk: "must not complete", answer_fingerprint: null })).toEqual(failed);
  });

  it("closes only the backend-issued session identity", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ session_id: "session-1", status: "CLOSED" }));

    await closeChatSession("session-1", fetcher);

    const [closeUrl, closeInit] = fetcher.mock.calls[0];
    expect(closeUrl).toBe("/api/v1/copilot/chat/sessions/session-1");
    expect(closeInit?.method).toBe("DELETE");
  });

  it("binds attachment metadata without file content or a local path", async () => {
    const reference = { reference_id: "attachment-1", reference_kind: "WORKSPACE" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(reference));

    await bindChatAttachment("session-1", { name: "board.png", size: 1024, type: "image/png" }, fetcher);

    const [url, init] = fetcher.mock.calls[0];
    const body = JSON.parse(String(init?.body));
    expect(url).toBe("/api/v1/copilot/chat/sessions/session-1/attachments");
    expect(body).toMatchObject({ type: "IMAGE", basename: "board.png", size_bytes: 1024 });
    expect(body).not.toHaveProperty("content");
    expect(body).not.toHaveProperty("path");
  });

  it("maps answer detail to the existing constraints field", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({}));
    const project = { tenant_id: "tenant", project_id: "gateway", project_name_reference: "Gateway", workspace_reference: "workspace", fingerprint: `sha256:${"1".repeat(64)}` };

    await submitQuestion("session-1", project, "Explain the issue", [], "detailed", false, fetcher);

    const body = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(body.application_question.constraints).toEqual(["answer_detail:detailed"]);
    expect(body.external_search).toBe(false);
  });

  it("previews and confirms a project without exposing a workspace operation", async () => {
    const preview = {
      preview_id: "preview-1",
      name: "Sensor Gateway",
      location: { location_id: "default-project-root", display_label: "Embedded Projects", availability: "AVAILABLE", fingerprint: `sha256:${"b".repeat(64)}` },
      existing_directory: null,
      directory_name: "Sensor Gateway",
      creation_mode: "CREATE_NEW" as const,
      relative_path: "sensor-gateway",
      project_type: "ESP_IDF" as const,
      mode: "CREATE" as const,
      expires_at: "2026-08-11T12:00:00Z",
      write_summary: ["Create project directory"],
      fingerprint: `sha256:${"a".repeat(64)}`,
    };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(preview))
      .mockResolvedValueOnce(jsonResponse({ project_id: "project-1" }));

    await previewProject({ name: preview.name, location: { location_id: preview.location.location_id, fingerprint: preview.location.fingerprint }, project_type: preview.project_type, creation_mode: "CREATE_NEW", existing_directory: null }, fetcher);
    await confirmProject(preview, fetcher);

    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/previews");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ name: "Sensor Gateway", location: { location_id: "default-project-root", fingerprint: preview.location.fingerprint }, project_type: "ESP_IDF", creation_mode: "CREATE_NEW", existing_directory: null });
    expect(fetcher.mock.calls[1][0]).toBe("/api/v1/copilot/projects");
    const confirmation = JSON.parse(String(fetcher.mock.calls[1][1]?.body));
    expect(confirmation).toMatchObject({ preview_id: "preview-1", preview_fingerprint: preview.fingerprint });
    expect(JSON.stringify(confirmation)).not.toContain("workspace");
  });

  it("sends the controlled parent location for the new project flow", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({}));
    const parentLocation = { location_id: "default-project-root", fingerprint: `sha256:${"c".repeat(64)}` };

    await previewProject({ name: "循迹智能小车", parent_location: parentLocation, project_type: "STM32", creation_mode: "CREATE_NEW", existing_directory: null }, fetcher);

    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      name: "循迹智能小车",
      parent_location: parentLocation,
      project_type: "STM32",
      creation_mode: "CREATE_NEW",
      existing_directory: null,
    });
    expect(JSON.stringify(fetcher.mock.calls[0][1]?.body)).not.toContain("path");
  });

  it("loads opaque project locations and immediate directories", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse([])).mockResolvedValueOnce(jsonResponse({ items: [] }));
    await fetchProjectLocations(fetcher);
    await fetchProjectDirectories("default-project-root", fetcher);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(["/api/v1/copilot/projects/locations", "/api/v1/copilot/projects/locations/default-project-root/directories"]);
    const requestHeaders = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
    expect(requestHeaders.has("X-Embedded-Copilot-Native-Handoff")).toBe(false);
  });

  it("records Project Locations frontend resolution through the existing diagnostic sink", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse([]));

    await fetchProjectLocations(fetcher, (event) => { events.push(event); });

    expect(events.map((event) => event.stage)).toEqual([
      "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_BEGIN",
      "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_PROMISE_RESOLVED",
      "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_SUCCESS",
    ]);
    expect(events.every((event) => event.route === "PROJECT_LOCATIONS")).toBe(true);
    expect(events[1]?.http_status).toBe(200);
    expect(events[2]?.http_status).toBe(200);
    expect(events[2]).toMatchObject({
      project_locations_attempt_started_at: expect.any(String),
      project_locations_attempt_finished_at: expect.any(String),
      project_locations_attempt_duration_ms: expect.any(Number),
      frontend_fetch_resolved: true,
      frontend_http_status: 200,
      final_result: "SUCCESS",
    });
  });

  it("records a Project Locations HTTP failure without changing the existing error flow", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ code: "UNAVAILABLE" }), { status: 503 }));

    await expect(fetchProjectLocations(fetcher, (event) => { events.push(event); })).rejects.toThrow();

    expect(events.map((event) => event.stage)).toEqual([
      "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_BEGIN",
      "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_PROMISE_RESOLVED",
      "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_FAILURE",
    ]);
    expect(events.at(-1)).toMatchObject({
      route: "PROJECT_LOCATIONS",
      project_locations_attempt_started_at: expect.any(String),
      project_locations_attempt_finished_at: expect.any(String),
      project_locations_attempt_duration_ms: expect.any(Number),
      frontend_fetch_resolved: true,
      frontend_http_status: 503,
      final_result: "FAILURE",
      fetch_failure_kind: "HTTP_STATUS",
      fetch_failure_stage: "HTTP_STATUS",
      http_status: 503,
    });
  });

  it("records a Project Locations response-body failure and rethrows the original error", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response("not-json", { status: 200 }));

    await expect(fetchProjectLocations(fetcher, (event) => { events.push(event); })).rejects.toBeInstanceOf(SyntaxError);

    expect(events.at(-1)).toMatchObject({
      route: "PROJECT_LOCATIONS",
      project_locations_attempt_started_at: expect.any(String),
      project_locations_attempt_finished_at: expect.any(String),
      project_locations_attempt_duration_ms: expect.any(Number),
      frontend_fetch_resolved: true,
      frontend_http_status: 200,
      final_result: "FAILURE",
      fetch_failure_kind: "RESPONSE_BODY",
      fetch_failure_stage: "RESPONSE_BODY",
    });
    expect(events.at(-1)).not.toHaveProperty("fetch_rejection_error_name");
    expect(events.at(-1)).not.toHaveProperty("fetch_rejection_message_category");
  });

  it("registers a project location with the standard identity headers", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      location_id: "location-stm32",
      display_label: "STM32 Projects",
      availability: "AVAILABLE",
      fingerprint: `sha256:${"d".repeat(64)}`,
    }));

    await registerProjectLocation("D:\\STM32Projects", "STM32 Projects", fetcher);

    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/locations");
    expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({
      "X-Copilot-Requester": "desktop-user",
      "X-Environment-Tenant": "desktop-tenant",
    });
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      path: "D:\\STM32Projects",
      display_label: "STM32 Projects",
    });
  });

  it("deletes only the catalog project through its fingerprint", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ operation: "PROJECT_DELETED" }));
    await deleteCatalogProject("project/1", `sha256:${"e".repeat(64)}`, fetcher);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/copilot/projects/project%2F1");
    expect(init?.method).toBe("DELETE");
    expect(init?.headers).toMatchObject({ "X-Project-Fingerprint": `sha256:${"e".repeat(64)}` });
  });

  it("uses fixed environment routes and an encoded project filter", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse([]));

    await fetchEnvironmentProjects(fetcher);
    await fetchEnvironmentAudit("gateway.alpha", fetcher);
    await fetchEnvironmentMemory("gateway.alpha", fetcher);

    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/environment/projects",
      "/api/v1/environment/audit?project_id=gateway.alpha",
      "/api/v1/environment/memory-view?project_id=gateway.alpha",
    ]);
    for (const [, init] of fetcher.mock.calls) {
      expect(init?.headers).toMatchObject({
        "X-Copilot-Requester": "desktop-user",
        "X-Environment-Tenant": "desktop-tenant",
      });
    }
  });

  it("streams split SSE frames with a cursor and reports resync", async () => {
    const encoder = new TextEncoder();
    const payloads = [
      "id: chat-1\nevent: CHAT_STARTED\ndata: {}\n\nid: chat-2\nevent: ANSWER_CH",
      "UNK\ndata: {\"answer_chunk\":\"Projected answer\"}\n\nevent: RESYNC_REQUIRED\ndata: {}\n\n",
    ];
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const payload of payloads) controller.enqueue(encoder.encode(payload));
        controller.close();
      },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }));
    const events: string[] = [];

    const cursor = await streamChatEvents(
      "session-1",
      "chat-0",
      (event) => events.push(`${event.event_type}:${event.answer_chunk ?? ""}`),
      fetcher,
    );

    expect(eventUrl("session-1")).toBe("/api/v1/copilot/chat/events?session_id=session-1&requester=desktop-user&tenant=desktop-tenant");
    expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({
      "Last-Event-ID": "chat-0",
      "X-Copilot-Requester": "desktop-user",
      "X-Environment-Tenant": "desktop-tenant",
    });
    expect(events).toEqual([
      "CHAT_STARTED:",
      "ANSWER_CHUNK:Projected answer",
      "RESYNC_REQUIRED:",
    ]);
    expect(cursor).toBe("chat-2");
  });
});
