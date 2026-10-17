import { describe, expect, it, vi } from "vitest";

import {
  fetchModelInvocationAttempt,
  fetchModelInvocationReceipts,
  ModelGatewayRequestError,
} from "./modelGatewayClient";

const fp = "sha256:" + "a".repeat(64);
const authority = {
  tenant_id: "tenant-1", project_id: "project-1", engineering_task_id: "task-1",
  task_request_fingerprint: fp, coordination_fingerprint: fp,
  plan_id: "plan-1", plan_content_fingerprint: fp, review_fingerprint: fp,
  phase_id: "phase-1", phase_fingerprint: fp, plan_task_id: "plan-task-1",
  operation_id: "HARDWARE_SELECTION_PROPOSAL", operation_binding_fingerprint: fp,
};

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
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

async function parseReceipt(value: unknown) {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({
    items: [value], total: 1, limit: 20, offset: 0,
  }));
  return (await fetchModelInvocationReceipts("project-1", "request-1", fetcher)).items[0];
}

describe("two-role invocation API projection", () => {
  it("preserves a counter-only HTTP error and an over-limit count without inventing generation", async () => {
    const failed = await parseReceipt(receipt());
    expect(failed).toMatchObject({
      counter_status: "HTTP_ERROR", counter_http_status: 500,
      counter_input_tokens: null, counter_transport_send_count: 1,
      generation_transport_send_count: 0, total_external_request_count: 1,
    });
    const overLimit = await parseReceipt({
      ...receipt(), counter_status: "COUNTED", counter_http_status: 200,
      counter_input_tokens: 8193,
    });
    expect(overLimit).toMatchObject({
      counter_input_tokens: 8193, generation_transport_send_count: 0,
      total_external_request_count: 1,
    });
  });

  it("preserves UNKNOWN counter intent and a successful 1 + 1 = 2 bundle", async () => {
    const uncertain = await parseReceipt({
      ...receipt(), counter_status: "OUTCOME_UNKNOWN",
      counter_transport_send_count: null, counter_response_observed: false,
      counter_observation_fingerprint: null, counter_http_status: null,
      total_external_request_count: null,
    });
    expect(uncertain.counter_transport_send_count).toBeNull();
    expect(uncertain.total_external_request_count).toBeNull();
    const complete = await parseReceipt({
      ...receipt(), result: "SUCCESS", error_code: null,
      counter_status: "COUNTED", counter_http_status: 200,
      counter_input_tokens: 8192, generation_transport_send_count: 1,
      total_external_request_count: 2, transport_send_count: 1,
      budget_evidence_fingerprint: fp,
    });
    expect(complete).toMatchObject({
      counter_transport_send_count: 1, generation_transport_send_count: 1,
      total_external_request_count: 2,
    });
  });

  it("rejects malformed totals and keeps old receipts without role fields", async () => {
    await expect(parseReceipt({ ...receipt(), total_external_request_count: 0 }))
      .rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_INVALID_RESPONSE"));
    const old = receipt() as Record<string, unknown>;
    for (const name of [
      "budget_phase", "counter_admission_fingerprint", "counter_observation_fingerprint",
      "counter_status", "counter_transport_send_count", "counter_response_observed",
      "generation_transport_send_count", "total_external_request_count",
      "counter_http_status", "counter_input_tokens",
    ]) delete old[name];
    old.budget_evidence_fingerprint = fp;
    const parsed = await parseReceipt(old);
    expect(parsed.budget_phase).toBeUndefined();
    expect(parsed.invocation_mode).toBe("TASK");
  });

  it("projects a persisted counter-intent attempt with UNKNOWN send count", async () => {
    const body = {
      request_id: "request-request-1", project_id: "project-1",
      stage_id: "HARDWARE_SELECTION", policy_source: "STAGE_POLICY",
      connection_id: "connection-1", status: "DISPATCH_INTENT",
      confirmed_provider_request_count: null, receipt_fingerprint: null,
      invocation_mode: "TASK", authority_reference: authority,
      gateway_call_count: null, gateway_response_observed: false,
      // API projects the known unsent generation separately from UNKNOWN counter.
      transport_send_count: 0, budget_envelope_id: "envelope-1",
      budget_evidence_fingerprint: null, budget_reservation_id: "reservation-1",
      budget_phase: "COUNTER_INTENT", counter_admission_fingerprint: fp,
      counter_observation_fingerprint: null, counter_status: "DISPATCH_INTENT",
      counter_transport_send_count: null, counter_response_observed: false,
      generation_transport_send_count: 0, total_external_request_count: null,
      counter_http_status: null, counter_input_tokens: null,
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(body));
    const attempt = await fetchModelInvocationAttempt("project-1", "request-1", fetcher);
    expect(attempt?.counter_transport_send_count).toBeNull();
    expect(attempt?.total_external_request_count).toBeNull();
    const failedCounter = { ...body, status: "COMPLETED", receipt_fingerprint: fp,
      budget_phase: "TERMINAL", counter_status: "HTTP_ERROR", counter_transport_send_count: 1,
      counter_response_observed: true, counter_observation_fingerprint: fp,
      counter_http_status: 429, total_external_request_count: 1 };
    const failedFetcher = vi.fn<typeof fetch>().mockResolvedValue(response(failedCounter));
    expect((await fetchModelInvocationAttempt("project-1", "request-1", failedFetcher))?.counter_status).toBe("HTTP_ERROR");
    const uncertain = { ...body, status: "OUTCOME_UNKNOWN", receipt_fingerprint: fp,
      budget_phase: "TERMINAL", counter_status: "OUTCOME_UNKNOWN" };
    const uncertainFetcher = vi.fn<typeof fetch>().mockResolvedValue(response(uncertain));
    expect((await fetchModelInvocationAttempt("project-1", "request-1", uncertainFetcher))?.total_external_request_count).toBeNull();
  });
});
