import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  analyzeFrontendDiagnosticSequences,
  classifyFetchRejectionErrorName,
  classifyFetchRejectionMessageCategory,
  classifyProviderOriginContext,
  classifyProviderFetcher,
  createProviderProjectionDiagnosticSession,
  FRONTEND_DIAGNOSTIC_BUILD_ID,
  recordFrontendProviderDiagnostic,
  type FrontendProviderDiagnosticEvent,
} from "./webviewSessionDiagnostic";
import { fetchProviderConfiguration, ProviderRequestError } from "./providerClient";
import { SettingsPanel } from "../settings/SettingsPanel";

const preferences = {
  theme: "system" as const,
  density: "comfortable" as const,
  answerDetail: "standard" as const,
  evidenceDisplay: "expanded" as const,
};

const configuredProjection = {
  provider: "deepseek" as const,
  model: "deepseek-chat",
  base_url: "https://api.deepseek.com/v1",
  credential_status: "CONFIGURED" as const,
  connection_status: "PROVIDER_CONNECTION_UNVERIFIED" as const,
  last_test_status: "PROVIDER_CONNECTION_UNVERIFIED" as const,
  tested_at: null,
  configuration_fingerprint: "FRONTEND_FETCH_FINGERPRINT_CANARY_DO_NOT_LEAK",
  tested_configuration_fingerprint: null,
  transmission_status: "NOT_APPROVED" as const,
  restart_required: false,
  fingerprint: "FRONTEND_FETCH_FINGERPRINT_CANARY_DO_NOT_LEAK",
};

const unconfiguredProjection = { ...configuredProjection, credential_status: "NOT_CONFIGURED" as const };

afterEach(() => {
  window.history.replaceState(null, "", "/desktop/");
  vi.unstubAllGlobals();
});

function enableDiagnosticMode(): void {
  window.history.replaceState(null, "", "/desktop/?webview_session_diagnostic=1");
}

function stages(events: FrontendProviderDiagnosticEvent[]): string[] {
  return events.map((event) => event.stage);
}

function assertAttemptSequence(events: FrontendProviderDiagnosticEvent[]): void {
  expect(new Set(events.map((event) => event.fetch_attempt_id)).size).toBe(1);
  expect(events.map((event) => event.event_sequence)).toEqual(events.map((_, index) => index + 1));
}

describe("frontend provider projection fetch boundary evidence", () => {
  it("classifies fetcher identity only by browser-global reference equality", () => {
    const browserFetch = globalThis.fetch;
    const expected = typeof window !== "undefined" && browserFetch === window.fetch ? "GLOBAL_BROWSER_FETCH" : "GLOBAL_THIS_FETCH";
    expect(classifyProviderFetcher(browserFetch)).toBe(expected);
    expect(classifyProviderFetcher((...args) => browserFetch(...args))).toBe("OTHER");
  });

  it("is disabled by default and creates no diagnostic request", () => {
    const transport = vi.fn<typeof fetch>();

    void recordFrontendProviderDiagnostic({
      stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
      result: "ENTERED",
      route: "PROVIDER_PROJECTION",
    }, transport);

    expect(transport).not.toHaveBeenCalled();
  });

  it("sends only the bounded safe event schema and never provider material", async () => {
    enableDiagnosticMode();
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    const canary = "FRONTEND_FETCH_API_KEY_CANARY_DO_NOT_LEAK";

    await expect(recordFrontendProviderDiagnostic({
      stage: "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
      result: "SUCCESS",
      route: "PROVIDER_PROJECTION",
      fetch_attempt_id: "provider-fetch-1",
      event_sequence: 6,
      projection_state: "CONFIGURED",
      credential_state: "CONFIGURED",
      provider: canary,
      response: { secret: canary },
    } as unknown as FrontendProviderDiagnosticEvent, transport)).resolves.toBe(true);

    const request = transport.mock.calls[0]?.[1];
    const body = String(request?.body);
    expect(request?.credentials).toBe("omit");
    expect(request?.headers).not.toHaveProperty("Cookie");
    expect(JSON.parse(body)).toEqual({
      build_id: FRONTEND_DIAGNOSTIC_BUILD_ID,
      stage: "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
      result: "SUCCESS",
      route: "PROVIDER_PROJECTION",
      fetch_attempt_id: "provider-fetch-1",
      event_sequence: 6,
      projection_state: "CONFIGURED",
      credential_state: "CONFIGURED",
    });
    expect(body).not.toContain(canary);
    expect(body).not.toContain("response");
  });

  it("proves fetch resolution before response inspection and keeps a monotonic attempt sequence", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { events.push(event); });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(configuredProjection), { status: 200 }),
    );

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).resolves.toEqual(configuredProjection);
    diagnostic.stateCommit(configuredProjection);
    diagnostic.fetchSuccess();

    expect(stages(events)).toEqual([
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
      "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN",
      "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_RESOLVED",
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_RESOLVED",
      "WEBVIEW_FRONTEND_PROVIDER_RESPONSE_RECEIVED",
      "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_SUCCESS",
      "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_SUCCESS",
    ]);
    expect(events[4]?.http_status).toBe(200);
    expect(events[7]?.projection_processing_result).toBe("NOT_APPLICABLE");
    expect(events.some((event) => event.stage.startsWith("WEBVIEW_FRONTEND_PROVIDER_NATIVE_FETCH"))).toBe(false);
    assertAttemptSequence(events);
  });

  it("uses a new process-local attempt id for each Provider fetch", async () => {
    const firstEvents: FrontendProviderDiagnosticEvent[] = [];
    const secondEvents: FrontendProviderDiagnosticEvent[] = [];
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(configuredProjection), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(unconfiguredProjection), { status: 200 }));

    await fetchProviderConfiguration(fetcher, createProviderProjectionDiagnosticSession((event) => { firstEvents.push(event); }));
    await fetchProviderConfiguration(fetcher, createProviderProjectionDiagnosticSession((event) => { secondEvents.push(event); }));

    expect(firstEvents[0]?.fetch_attempt_id).toMatch(/^provider-fetch-[1-9][0-9]*$/);
    expect(secondEvents[0]?.fetch_attempt_id).toMatch(/^provider-fetch-[1-9][0-9]*$/);
    expect(firstEvents[0]?.fetch_attempt_id).not.toBe(secondEvents[0]?.fetch_attempt_id);
  });

  it("records only the fetch Promise rejection in the transport catch", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { events.push(event); });
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("FETCH_REJECT_CANARY"));

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).rejects.toBeInstanceOf(TypeError);

    expect(stages(events)).toEqual([
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
      "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN",
      "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_REJECTED",
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_REJECTED",
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
    ]);
    expect(events[2]?.safe_error_class).toBe("FETCH_REJECTED");
    expect(events[2]?.failure_stage).toBe("FETCH");
    expect(events[4]?.production_fetcher_identity).toBe("OTHER");
    expect(events[4]?.fetch_rejection_error_class).toBe("TYPE_ERROR");
    expect(events[4]?.frontend_provider_same_origin).toBe(true);
    expect(events[4]?.provider_request_url_strategy).toBe("RELATIVE_SAME_ORIGIN");
    expect(events[4]?.abort_signal_attached_to_provider_fetch).toBe("NONE");
    expect(events[4]?.document_generation_at_rejection).toBe(events[4]?.document_generation_at_fetch);
    expect(events.some((event) => event.stage === "WEBVIEW_FRONTEND_PROVIDER_RESPONSE_RECEIVED")).toBe(false);
    assertAttemptSequence(events);
  });

  it("keeps localhost and 127.0.0.1 different origins even on the same port", () => {
    expect(classifyProviderOriginContext(
      "http://127.0.0.1:1420/desktop/",
      "http://localhost:1420/api/v1/copilot/desktop/system/provider",
      "ABSOLUTE_DIFFERENT_LOOPBACK",
    )).toEqual({
      frontend_origin_class: "LOOPBACK_HTTP",
      provider_target_origin_class: "LOOPBACK_CROSS_ORIGIN",
      frontend_provider_same_origin: false,
      provider_request_url_strategy: "ABSOLUTE_DIFFERENT_LOOPBACK",
    });
  });

  it("does not expose URL, origin, or raw rejection details in failure evidence", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { events.push(event); });
    const secretUrl = "https://127.0.0.1:3244/provider?token=SECRET_CANARY";
    const error = new TypeError(`Failed ${secretUrl} Authorization=Bearer SECRET`);
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(error);

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).rejects.toBe(error);

    const failure = events.at(-1);
    expect(JSON.stringify(failure)).not.toContain("SECRET_CANARY");
    expect(JSON.stringify(failure)).not.toContain("Authorization");
    expect(JSON.stringify(failure)).not.toContain("Bearer");
    expect(JSON.stringify(failure)).not.toContain(secretUrl);
    expect(failure?.fetch_rejection_error_class).toBe("TYPE_ERROR");
    expect(failure?.fetch_rejection_error_name).toBe("TypeError");
    expect(failure?.fetch_rejection_message_category).toBe("OTHER_SANITIZED");
    expect(failure?.fetch_failure_kind).toBe("PROMISE_REJECTION");
    expect(failure?.fetch_failure_stage).toBe("FETCH_CALL");
  });

  it("classifies only the fixed safe rejection error names", () => {
    expect(classifyFetchRejectionErrorName(new TypeError("type"))).toBe("TypeError");
    expect(classifyFetchRejectionErrorName(new DOMException("abort", "AbortError"))).toBe("AbortError");
    expect(classifyFetchRejectionErrorName({ name: "NetworkError" })).toBe("NetworkError");
    expect(classifyFetchRejectionErrorName({ name: "SecurityError" })).toBe("SecurityError");
    expect(classifyFetchRejectionErrorName({ name: "InvalidStateError" })).toBe("InvalidStateError");
    expect(classifyFetchRejectionErrorName({ name: "NotAllowedError" })).toBe("NotAllowedError");
    expect(classifyFetchRejectionErrorName(new Error("unknown"))).toBe("Other");
    expect(classifyFetchRejectionErrorName(null)).toBe("Unknown");
    expect(classifyFetchRejectionErrorName(undefined)).toBe("Unknown");
  });

  it("classifies rejection messages into bounded categories without exposing text", () => {
    expect(classifyFetchRejectionMessageCategory(new Error("Failed to fetch"))).toBe("FAILED_TO_FETCH");
    expect(classifyFetchRejectionMessageCategory(new Error("Load failed"))).toBe("LOAD_FAILED");
    expect(classifyFetchRejectionMessageCategory(new Error("Illegal invocation"))).toBe("ILLEGAL_INVOCATION");
    expect(classifyFetchRejectionMessageCategory(new Error("net::ERR_CONNECTION_REFUSED"))).toBe("CONNECTION_REFUSED");
    expect(classifyFetchRejectionMessageCategory(new Error("net::ERR_CONNECTION_RESET"))).toBe("CONNECTION_RESET");
    expect(classifyFetchRejectionMessageCategory(new Error("Network error"))).toBe("NETWORK_ERROR");
    expect(classifyFetchRejectionMessageCategory(new Error("blocked by policy"))).toBe("BLOCKED_BY_CLIENT_OR_POLICY");
    expect(classifyFetchRejectionMessageCategory(new Error(""))).toBe("EMPTY");
    expect(classifyFetchRejectionMessageCategory(null)).toBe("UNKNOWN");
    expect(classifyFetchRejectionMessageCategory(new Error("Authorization=Bearer SECRET_CANARY"))).toBe("OTHER_SANITIZED");
  });

  it("keeps the final failure self-contained even when no begin event was delivered", () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { events.push(event); });

    diagnostic.fetchFailure("FETCH_REJECTED", "FETCH", undefined, {
      fetchRejectionErrorClass: "TYPE_ERROR",
      fetchRejectionErrorName: "TypeError",
      fetchRejectionMessageCategory: "FAILED_TO_FETCH",
      fetchFailureKind: "PROMISE_REJECTION",
      fetchFailureStage: "FETCH_CALL",
    });

    const failure = events.at(-1);
    expect(failure?.fetch_attempt_id).toMatch(/^provider-fetch-[1-9][0-9]*$/);
    expect(failure?.fetch_failure_kind).toBe("PROMISE_REJECTION");
    expect(failure?.fetch_failure_stage).toBe("FETCH_CALL");
    expect(failure?.fetch_rejection_error_name).toBe("TypeError");
    expect(failure?.fetch_rejection_message_category).toBe("FAILED_TO_FETCH");
    expect(failure?.production_fetcher_identity).toBe("UNKNOWN");
    expect(failure?.request_credentials_mode).toBe("SAME_ORIGIN");
    expect(failure?.abort_signal_attached_to_provider_fetch).toBe("NONE");
    expect(failure?.fetch_attempt_started_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(failure?.fetch_attempt_finished_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(failure?.fetch_attempt_duration_ms).toEqual(expect.any(Number));
  });

  it("keeps both Provider terminal outcomes self-contained without a delivered begin event", () => {
    const failureEvents: FrontendProviderDiagnosticEvent[] = [];
    const failureDiagnostic = createProviderProjectionDiagnosticSession((event) => { failureEvents.push(event); });
    failureDiagnostic.fetchFailure("FETCH_REJECTED", "FETCH", undefined, {
      fetchRejectionErrorClass: "TYPE_ERROR",
      fetchRejectionErrorName: "TypeError",
      fetchRejectionMessageCategory: "FAILED_TO_FETCH",
      fetchFailureKind: "PROMISE_REJECTION",
      fetchFailureStage: "FETCH_CALL",
    });

    const successEvents: FrontendProviderDiagnosticEvent[] = [];
    const successDiagnostic = createProviderProjectionDiagnosticSession((event) => { successEvents.push(event); });
    successDiagnostic.fetchSuccess();

    expect(failureEvents.at(-1)).toMatchObject({ fetch_attempt_started_at: expect.any(String), fetch_attempt_finished_at: expect.any(String), fetch_attempt_duration_ms: expect.any(Number) });
    expect(successEvents.at(-1)).toMatchObject({ fetch_attempt_started_at: expect.any(String), fetch_attempt_finished_at: expect.any(String), fetch_attempt_duration_ms: expect.any(Number) });
  });

  it("classifies AbortError as REQUEST_ABORTED without inventing a response", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { events.push(event); });
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new DOMException("ABORT_CANARY", "AbortError"));

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).rejects.toBeInstanceOf(DOMException);

    expect(events[2]?.safe_error_class).toBe("REQUEST_ABORTED");
    expect(events[3]?.safe_error_class).toBe("REQUEST_ABORTED");
    expect(events[3]?.failure_stage).toBe("FETCH");
    expect(events[4]?.fetch_failure_kind).toBe("ABORT");
    expect(events[4]?.fetch_failure_stage).toBe("FETCH_CALL");
    expect(events[4]?.fetch_rejection_error_name).toBe("AbortError");
    expect(events[4]?.fetch_rejection_message_category).toBe("OTHER_SANITIZED");
    assertAttemptSequence(events);
  });

  it("keeps HTTP status failure separate from fetch rejection", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { events.push(event); });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ error: "PROVIDER_CONFIGURATION_UNAVAILABLE", provider: "FRONTEND_FETCH_SESSION_CANARY_DO_NOT_LEAK" }), { status: 503 }),
    );

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).rejects.toEqual(
      new ProviderRequestError("PROVIDER_CONFIGURATION_UNAVAILABLE"),
    );

    expect(stages(events)).toEqual([
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
      "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN",
      "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_RESOLVED",
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_RESOLVED",
      "WEBVIEW_FRONTEND_PROVIDER_RESPONSE_RECEIVED",
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
    ]);
    expect(events[5]?.safe_error_class).toBe("HTTP_STATUS_ERROR");
    expect(events[5]?.failure_stage).toBe("HTTP_STATUS");
    expect(events[5]?.fetch_failure_kind).toBe("HTTP_STATUS");
    expect(events[5]?.fetch_failure_stage).toBe("HTTP_STATUS");
    expect(events[5]?.fetch_rejection_error_name).toBe("Unknown");
    expect(events[5]?.fetch_rejection_message_category).toBe("EMPTY");
    expect(JSON.stringify(events)).not.toContain("FRONTEND_FETCH_SESSION_CANARY_DO_NOT_LEAK");
    assertAttemptSequence(events);
  });

  it("records JSON parse failure after a resolved HTTP 200", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { events.push(event); });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("not-json", { status: 200 }));

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).rejects.toBeInstanceOf(SyntaxError);

    expect(stages(events)).toEqual([
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
      "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN",
      "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_RESOLVED",
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_RESOLVED",
      "WEBVIEW_FRONTEND_PROVIDER_RESPONSE_RECEIVED",
      "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_FAILURE",
      "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
    ]);
    expect(events[5]?.safe_error_class).toBe("JSON_PARSE_ERROR");
    expect(events[6]?.failure_stage).toBe("JSON_PARSE");
    expect(events[6]?.fetch_failure_kind).toBe("RESPONSE_BODY");
    expect(events[6]?.fetch_failure_stage).toBe("RESPONSE_BODY");
    expect(events[6]?.fetch_rejection_error_name).toBe("Unknown");
    expect(events[6]?.fetch_rejection_message_category).toBe("EMPTY");
    assertAttemptSequence(events);
  });

  it("records setter invocation as observed, without claiming React commit success", () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { events.push(event); });

    diagnostic.stateCommit(configuredProjection);
    diagnostic.stateCommit(unconfiguredProjection);

    expect(events.map((event) => [event.projection_state, event.credential_state])).toEqual([
      ["CONFIGURED", "CONFIGURED"],
      ["UNCONFIGURED", "UNCONFIGURED"],
    ]);
    expect(events.map((event) => event.result)).toEqual(["RECEIVED", "RECEIVED"]);
    assertAttemptSequence(events);
  });

  it("keeps ProviderProjection behavior when diagnostic delivery fails", async () => {
    const diagnostic = createProviderProjectionDiagnosticSession(() => {
      throw new Error("FRONTEND_DIAGNOSTIC_SINK_CANARY");
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(unconfiguredProjection), { status: 200 }),
    );

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).resolves.toEqual(unconfiguredProjection);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("does not wait for a diagnostic delivery that remains pending", async () => {
    const diagnostic = createProviderProjectionDiagnosticSession(() => new Promise<boolean>(() => undefined));
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(unconfiguredProjection), { status: 200 }),
    );

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).resolves.toEqual(unconfiguredProjection);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("reports delivery status internally without making Provider fetch await it", async () => {
    enableDiagnosticMode();
    const successfulDelivery = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    const failedDelivery = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 500 }));
    const rejectedDelivery = vi.fn<typeof fetch>().mockRejectedValue(new Error("DIAGNOSTIC_DELIVERY_CANARY"));

    await expect(recordFrontendProviderDiagnostic({
      stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
      result: "ENTERED",
      route: "PROVIDER_PROJECTION",
    }, successfulDelivery)).resolves.toBe(true);
    await expect(recordFrontendProviderDiagnostic({
      stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
      result: "ENTERED",
      route: "PROVIDER_PROJECTION",
    }, failedDelivery)).resolves.toBe(false);
    await expect(recordFrontendProviderDiagnostic({
      stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
      result: "ENTERED",
      route: "PROVIDER_PROJECTION",
    }, rejectedDelivery)).resolves.toBe(false);
  });

  it("confirms a diagnostic event loss from a sequence gap while business fetch succeeds", async () => {
    const attempted: FrontendProviderDiagnosticEvent[] = [];
    const diagnostic = createProviderProjectionDiagnosticSession((event) => { attempted.push(event); });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(configuredProjection), { status: 200 }),
    );

    await expect(fetchProviderConfiguration(fetcher, diagnostic)).resolves.toEqual(configuredProjection);
    diagnostic.stateCommit(configuredProjection);
    diagnostic.fetchSuccess();

    const observed = attempted.filter((event) => [1, 3, 4].includes(event.event_sequence ?? 0));
    const analysis = analyzeFrontendDiagnosticSequences(observed);
    expect(analysis).toEqual([{
      fetch_attempt_id: attempted[0]?.fetch_attempt_id,
      observed_sequences: [1, 3, 4],
      missing_sequences: [2],
      diagnostic_event_loss_confirmed: true,
    }]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("commits state invocation, observes the next render, and preserves the connection predicate", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    const providerFetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ ...configuredProjection, model_pool: [{ provider_id: "deepseek", endpoint_id: "endpoint-deepseek", model_profile_id: "model-deepseek-deepseek-chat", model_id: "deepseek-chat", display_name: "DeepSeek Chat", availability: "AVAILABLE", availability_source: "PROVIDER_API", capabilities: ["TEXT"], verified_at: null }] }), { status: 200 }),
    );
    const modelGatewayFetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => String(input).endsWith("/model-gateway/providers")
      ? new Response(JSON.stringify([{ provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true }]), { status: 200 })
      : new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async () => new Response("[]", { status: 200 })));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(createElement(SettingsPanel, {
        preferences,
        onChange: () => undefined,
        onClose: () => undefined,
        diagnosticSink: (event) => { events.push(event); },
        providerFetcher,
        modelGatewayFetcher,
      }));
      await Promise.resolve();
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      (container.querySelector('button[data-provider-manage-id="deepseek"]') as HTMLButtonElement).click();
      await Promise.resolve();
    });

    const renderEvents = events.filter((event) => event.stage === "WEBVIEW_FRONTEND_PROVIDER_RENDER_STATE");
    expect(renderEvents.map((event) => event.projection_state)).toEqual(["NULL", "CONFIGURED"]);
    expect(renderEvents.map((event) => event.provider_present)).toEqual([false, true]);
    expect(renderEvents.every((event) => event.result === "SUCCESS")).toBe(true);
    expect(new Set(renderEvents.map((event) => event.fetch_attempt_id)).size).toBe(1);
    expect(events.find((event) => event.stage === "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT")?.credential_state).toBe("CONFIGURED");
    const stateCommitIndex = stages(events).indexOf("WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT");
    const configuredRenderIndex = events.findIndex((event) => event.stage === "WEBVIEW_FRONTEND_PROVIDER_RENDER_STATE" && event.projection_state === "CONFIGURED");
    expect(configuredRenderIndex).toBeGreaterThan(stateCommitIndex);
    expect(stages(events).indexOf("WEBVIEW_FRONTEND_PROVIDER_FETCH_SUCCESS"))
      .toBeLessThan(configuredRenderIndex);
    expect(events.filter((event) => event.stage === "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_SUCCESS"))
      .toHaveLength(1);
    expect(events.find((event) => event.stage === "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_SUCCESS"))
      .toEqual(expect.objectContaining({
        route: "PROJECT_LOCATIONS",
        frontend_fetch_resolved: true,
        frontend_http_status: 200,
        final_result: "SUCCESS",
      }));
    const connectionButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "测试连接");
    expect(connectionButton).toBeInstanceOf(HTMLButtonElement);
    expect((connectionButton as HTMLButtonElement).disabled).toBe(false);

    root.unmount();
    container.remove();
  });

  it("does not change the provider request when diagnostics are disabled", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(unconfiguredProjection), { status: 200 }),
    );

    await fetchProviderConfiguration(fetcher);

    expect(fetcher).toHaveBeenCalledWith("/api/v1/copilot/desktop/system/provider", expect.objectContaining({
      credentials: "same-origin",
      headers: expect.objectContaining({
        "Content-Type": "application/json",
        "X-Copilot-Requester": "desktop-user",
        "X-Environment-Tenant": "desktop-tenant",
      }),
    }));
  });
});
