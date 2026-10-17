import { describe, expect, it, vi } from "vitest";

import { fetchModelGatewayProviders, fetchModelInvocationAttempt, fetchModelInvocationReceipts, ModelGatewayRequestError } from "./modelGatewayClient";

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("model gateway client", () => {
  it("loads validated provider metadata without credential fields", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response([{ provider_id: "openai", display_name: "OpenAI", provider_kind: "openai", supported_protocols: ["OPENAI_RESPONSES"], preset_status: "PRESET_PARTIAL", enabled: true }]));
    await expect(fetchModelGatewayProviders(fetcher)).resolves.toEqual([{ provider_id: "openai", display_name: "OpenAI", provider_kind: "openai", supported_protocols: ["OPENAI_RESPONSES"], preset_status: "PRESET_PARTIAL", enabled: true }]);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/model-gateway/providers", { credentials: "same-origin" });
  });

  it("maps unavailable and malformed responses to fixed errors", async () => {
    await expect(fetchModelGatewayProviders(vi.fn<typeof fetch>().mockResolvedValue(response({ detail: "MODEL_GATEWAY_UNAVAILABLE" }, 503)))).rejects.toEqual(new ModelGatewayRequestError("MODEL_GATEWAY_UNAVAILABLE"));
    await expect(fetchModelGatewayProviders(vi.fn<typeof fetch>().mockResolvedValue(response({ unexpected: true })))).rejects.toEqual(new ModelGatewayRequestError("MODEL_GATEWAY_INVALID_RESPONSE"));
  });

  it("loads project-scoped invocation receipts and discards extra fields", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ items: [{ request_id: "request-request-1", trace_id: "trace-1", project_id: "project-1", stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: "connection-1", selected_model: { provider_id: "deepseek", endpoint_id: "endpoint-1", model_profile_id: "profile-1", model_id: "deepseek-chat", credential_ref: "hidden" }, usage: { input_tokens: 3, output_tokens: 2, total_tokens: 5, cached_input_tokens: null, cache_write_tokens: null, reasoning_tokens: null, usage_source: "PROVIDER", credential_ref: "hidden" }, result: "SUCCEEDED", error_code: null, started_at: "2026-09-29T00:00:00Z", completed_at: "2026-09-29T00:00:01Z", fingerprint: "sha256:receipt", prompt: "hidden" }], total: 1, limit: 20, offset: 0, credential_ref: "hidden" }));
    const result = await fetchModelInvocationReceipts("project-1", "request-1", fetcher);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/model-gateway/invocations/projects/project-1/receipts?chat_request_id=request-1&limit=20&offset=0", { credentials: "same-origin" });
    expect(result).toEqual({ items: [{ request_id: "request-request-1", trace_id: "trace-1", project_id: "project-1", stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: "connection-1", selected_model: { provider_id: "deepseek", endpoint_id: "endpoint-1", model_profile_id: "profile-1", model_id: "deepseek-chat" }, usage: { input_tokens: 3, output_tokens: 2, total_tokens: 5, cached_input_tokens: null, cache_write_tokens: null, reasoning_tokens: null, usage_source: "PROVIDER" }, result: "SUCCEEDED", error_code: null, started_at: "2026-09-29T00:00:00Z", completed_at: "2026-09-29T00:00:01Z", fingerprint: "sha256:receipt", invocation_mode: "LEGACY_UNBOUND", authority_reference: null, gateway_call_count: null, gateway_response_observed: null, transport_send_count: null }], total: 1, limit: 20, offset: 0 });
    expect(JSON.stringify(result)).not.toContain("hidden");
  });

  it("preserves unknown provenance for old receipts and rejects malformed connection ids", async () => {
    const old = { request_id: "request-old", trace_id: null, project_id: "project-1", stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: null, selected_model: null, usage: null, result: "REJECTED", error_code: "MODEL_UNAVAILABLE", started_at: "2026-09-29T00:00:00Z", completed_at: null, fingerprint: "sha256:old" };
    const oldFetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ items: [old], total: 1, limit: 20, offset: 0 }));
    await expect(fetchModelInvocationReceipts("project-1", "old", oldFetcher)).resolves.toMatchObject({ items: [{ connection_id: null }] });
    const malformedFetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ items: [{ ...old, connection_id: { credential_ref: "hidden" } }], total: 1, limit: 20, offset: 0 }));
    await expect(fetchModelInvocationReceipts("project-1", "old", malformedFetcher)).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_INVALID_RESPONSE"));
  });

  it("rejects inconsistent or negative reported usage", async () => {
    const base = { request_id: "request-1", trace_id: null, project_id: "project-1", stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: null, selected_model: null, result: "SUCCESS", error_code: null, started_at: "2026-09-29T00:00:00Z", completed_at: "2026-09-29T00:00:01Z", fingerprint: "sha256:receipt" };
    for (const usage of [
      { input_tokens: 3, output_tokens: 2, total_tokens: 4, usage_source: "PROVIDER" },
      { input_tokens: -1, output_tokens: 2, total_tokens: 1, usage_source: "PROVIDER" },
    ]) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ items: [{ ...base, usage }], total: 1, limit: 20, offset: 0 }));
      await expect(fetchModelInvocationReceipts("project-1", "request-1", fetcher)).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_INVALID_RESPONSE"));
    }
  });

  it("loads a project-scoped attempt without claiming an unconfirmed provider send", async () => {
    const body = { request_id: "request-request-1", project_id: "project-1", stage_id: "REQUIREMENT_ANALYSIS", policy_source: "PROJECT_DEFAULT", connection_id: "connection-1", status: "OUTCOME_UNKNOWN", confirmed_provider_request_count: null, receipt_fingerprint: null, invocation_mode: "LEGACY_UNBOUND", gateway_response_observed: null, credential_ref: "hidden" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(body));
    await expect(fetchModelInvocationAttempt("project-1", "request-1", fetcher)).resolves.toEqual({ request_id: body.request_id, project_id: body.project_id, stage_id: body.stage_id, policy_source: body.policy_source, connection_id: body.connection_id, status: body.status, confirmed_provider_request_count: null, receipt_fingerprint: null, invocation_mode: "LEGACY_UNBOUND", authority_reference: null, gateway_call_count: null, gateway_response_observed: null, transport_send_count: null });
    expect(fetcher).toHaveBeenCalledWith("/api/v1/model-gateway/invocations/projects/project-1/attempts/request-request-1", { credentials: "same-origin" });
    expect(await fetchModelInvocationAttempt("project-1", "request-1", vi.fn<typeof fetch>().mockResolvedValue(response({}, 404)))).toBeNull();
    await expect(fetchModelInvocationAttempt("project-1", "request-1", vi.fn<typeof fetch>().mockResolvedValue(response({}, 403)))).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_FORBIDDEN"));
    await expect(fetchModelInvocationAttempt("project-1", "request-1", vi.fn<typeof fetch>().mockResolvedValue(response({ ...body, project_id: "project-2" })))).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE"));
    await expect(fetchModelInvocationAttempt("project-1", "request-1", vi.fn<typeof fetch>().mockResolvedValue(response({ ...body, invocation_mode: "CHAT" })))).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE"));
  });

  it("keeps forbidden, not-found, network, and invalid responses distinct", async () => {
    await expect(fetchModelInvocationReceipts("p", "r", vi.fn<typeof fetch>().mockResolvedValue(response({}, 403)))).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_FORBIDDEN"));
    await expect(fetchModelInvocationReceipts("p", "r", vi.fn<typeof fetch>().mockResolvedValue(response({}, 404)))).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_NOT_FOUND"));
    await expect(fetchModelInvocationReceipts("p", "r", vi.fn<typeof fetch>().mockRejectedValue(new Error("offline")))).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_NETWORK_UNAVAILABLE"));
    await expect(fetchModelInvocationReceipts("p", "r", vi.fn<typeof fetch>().mockResolvedValue(response({ items: [], total: 0 })))).rejects.toEqual(new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_INVALID_RESPONSE"));
  });
});
