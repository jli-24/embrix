import { afterEach, describe, expect, it } from "vitest";

import {
  deleteProviderConnection,
  fetchProviderConnections,
  fetchProviderConnectionRuntimeHealth,
  fetchProviderConnectionVerifications,
  patchProviderConnection,
  refreshProviderConnectionModels,
  saveProviderConnectionCredential,
  testProviderConnectionModel,
} from "./providerConnectionClient";
import { getDesktopSessionState, markDesktopSessionActive } from "./sessionState";

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("provider connection API client", () => {
  afterEach(() => markDesktopSessionActive());

  it("marks an expired Desktop session and stops later quota reads", async () => {
    let calls = 0;
    const fetcher: typeof fetch = async () => {
      calls += 1;
      return response({ detail: "DESKTOP_SESSION_EXPIRED" }, 401);
    };
    await expect(fetchProviderConnections(fetcher)).rejects.toMatchObject({ code: "DESKTOP_SESSION_EXPIRED" });
    expect(getDesktopSessionState()).toBe("EXPIRED");
    await expect(fetchProviderConnections(fetcher)).rejects.toMatchObject({ code: "DESKTOP_SESSION_EXPIRED" });
    expect(calls).toBe(1);
  });
  it("reads sanitized verification history and runtime health by connection_id", async () => {
    const requests: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      requests.push(String(input));
      return response([]);
    };
    await fetchProviderConnectionVerifications("conn/a", fetcher);
    await expect(fetchProviderConnectionRuntimeHealth("conn/a", fetcher)).resolves.toEqual([]);
    expect(requests).toEqual([
      "/api/v1/model-gateway/provider-connections/conn%2Fa/verifications",
      "/api/v1/model-gateway/provider-connections/conn%2Fa/runtime-health",
    ]);
  });

  it("keeps rename bound to the same connection_id", async () => {
    let request: [string, RequestInit | undefined] | undefined;
    await patchProviderConnection("conn-a", { connection_name: "公司 API" }, async (input, init) => {
      request = [String(input), init];
      return response({ connection_id: "conn-a" });
    });
    expect(request?.[0]).toBe("/api/v1/model-gateway/provider-connections/conn-a");
    expect(request?.[1]?.method).toBe("PATCH");
    expect(request?.[1]?.body).toBe(JSON.stringify({ connection_name: "公司 API" }));
  });

  it("routes credential, refresh, verification and delete through the canonical connection path", async () => {
    const requests: Array<[string, RequestInit | undefined]> = [];
    const fetcher: typeof fetch = async (input, init) => { requests.push([String(input), init]); return response({}); };
    await saveProviderConnectionCredential("conn-a", "secret-value", fetcher);
    await refreshProviderConnectionModels("conn-a", fetcher);
    await testProviderConnectionModel("conn-a", { model_profile_id: "profile-a", model_id: "model-a" }, fetcher);
    await deleteProviderConnection("conn-a", fetcher);
    expect(requests.map(([url]) => url)).toEqual([
      "/api/v1/model-gateway/provider-connections/conn-a/credential",
      "/api/v1/model-gateway/provider-connections/conn-a/models/refresh",
      "/api/v1/model-gateway/provider-connections/conn-a/test-connection",
      "/api/v1/model-gateway/provider-connections/conn-a",
    ]);
    expect(requests[0]?.[1]?.body).toBe(JSON.stringify({ secret: "secret-value" }));
  });
});
