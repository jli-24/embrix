import { describe, expect, it, vi } from "vitest";

import {
  projectExecutionEvent,
  streamExecutionEvents,
  type ExecutionOperationStatus,
} from "./executionClient";

describe("execution event replay client", () => {
  it("parses split SSE chunks and carries the scoped cursor headers", async () => {
    const encoder = new TextEncoder();
    const chunks = [
      "id: operation-event-2\nevent: OPERATION_QUEUED\ndata: {\"operation_id\":\"operation-test-1\",\"status\":\"QUEUED\",\"recovery_state\":\"NONE\",\"reason_code\":\"APPROVAL_BOUND\"}\n\nid: operation-event-3\nevent: OPERATION_STA",
      "RTED\ndata: {\"operation_id\":\"operation-test-1\",\"status\":\"RUNNING\",\"recovery_state\":\"NONE\",\"reason_code\":\"APPROVAL_CONSUMED\"}\n\nevent: RESYNC_REQUIRED\ndata: {\"reason_code\":\"RESYNC_REQUIRED\"}\n\n",
    ];
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
        controller.close();
      },
    });
    const requests: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push([input, init]);
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }) as unknown as typeof fetch;
    const events: string[] = [];

    const cursor = await streamExecutionEvents(
      "project-1",
      "operation-event-1",
      (event) => events.push(`${event.event_type}:${event.operation_id ?? ""}`),
      fetcher,
    );

    expect(requests[0][0]).toBe(
      "/api/v1/copilot/operations/events/stream?project_id=project-1",
    );
    expect(requests[0][1]?.headers).toMatchObject({
      Accept: "text/event-stream",
      "Last-Event-ID": "operation-event-1",
      "X-Copilot-Requester": "desktop-user",
      "X-Environment-Tenant": "desktop-tenant",
    });
    expect(events).toEqual([
      "OPERATION_QUEUED:operation-test-1",
      "OPERATION_STARTED:operation-test-1",
      "RESYNC_REQUIRED:",
    ]);
    expect(cursor).toBe("operation-event-3");
  });

  it("projects only events bound to the selected operation", () => {
    const current: ExecutionOperationStatus = {
      operation_id: "operation-test-1",
      tenant_id: "tenant-1",
      project_id: "project-1",
      operation_type: "TEST",
      status: "QUEUED",
      approval_id: "approval-test-1",
      worker_id: null,
      recovery_state: "NONE",
      cancellation_requested: false,
      updated_at: "2026-01-01T00:00:00Z",
      receipt_fingerprint: null,
      safe_detail: "queued",
    };
    const matching = projectExecutionEvent(current, {
      event_id: "operation-event-2",
      event_type: "OPERATION_STARTED",
      operation_id: "operation-test-1",
      status: "RUNNING",
      recovery_state: "NONE",
      receipt_fingerprint: null,
      reason_code: "APPROVAL_CONSUMED",
    }, current.operation_id);
    const foreign = projectExecutionEvent(matching, {
      event_id: "operation-event-3",
      event_type: "OPERATION_FAILED",
      operation_id: "operation-other",
      status: "FAILED",
      recovery_state: "NONE",
      receipt_fingerprint: null,
      reason_code: "ADAPTER_FAILED",
    }, current.operation_id);

    expect(matching.status).toBe("RUNNING");
    expect(foreign).toBe(matching);
  });
});
