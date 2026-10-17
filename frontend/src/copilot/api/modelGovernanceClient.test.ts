import { describe, expect, it, vi } from "vitest";

import {
  fetchProjectDefaultPolicy,
  fetchGovernanceModels,
  ModelGovernanceRequestError,
  deleteProjectDefaultPolicy,
  saveProjectDefaultPolicy,
  createGovernanceModelPolicy,
  type GovernanceModel,
} from "./modelGovernanceClient";

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("model governance client", () => {
  it("sends scope and project filters without exposing credentials", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response([]));
    await expect(fetchGovernanceModels(fetcher, "PROJECT", "project/a")).resolves.toEqual([]);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/model-gateway/governance/available-models?scope=PROJECT&project_id=project%2Fa",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("treats an absent project default as an empty policy", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ detail: "PROJECT_DEFAULT_POLICY_NOT_FOUND" }, 404));
    await expect(fetchProjectDefaultPolicy(fetcher, "project-a")).resolves.toBeNull();
  });

  it("maps project default writes to the scoped endpoint", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ selection_mode: "AUTO" }));
    await saveProjectDefaultPolicy(fetcher, "project-a", { selection_mode: "AUTO", fixed_model: null, candidate_models: [] });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/model-gateway/governance/project-defaults/project-a",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selection_mode: "AUTO", fixed_model: null, candidate_models: [] }) }),
    );
  });

  it("sends the complete bound model identity and never sends client enablement state", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ id: "policy-1" }));
    const model = {
      model_profile_id: "model-zhipu-glm-4.7-flash",
      provider_id: "zhipu",
      endpoint_id: "endpoint-zhipu",
      credential_ref: "credential:zhipu",
      model_id: "glm-4.7-flash",
      display_name: "GLM 4.7 Flash",
      billing_class: "FREE",
      billing_provenance: "OFFICIAL_CATALOG",
      availability: "AVAILABLE",
      availability_status: "AVAILABLE",
      availability_provenance: "PROVIDER_API",
      identity_status: "VALID",
      record_provenance: "ENDPOINT_DISCOVERY",
      verification_status: "VERIFIED",
      discovery_status: "DISCOVERED",
      capability_provenance: [],
      can_enable: true,
      capabilities: ["TEXT"],
      allowed: false,
    } as GovernanceModel;

    await createGovernanceModelPolicy(fetcher, model, "GLOBAL");

    const init = fetcher.mock.calls[0]?.[1];
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      provider_id: "zhipu",
      endpoint_id: "endpoint-zhipu",
      credential_ref: "credential:zhipu",
      model_profile_id: "model-zhipu-glm-4.7-flash",
      model_id: "glm-4.7-flash",
    });
    expect(body).not.toHaveProperty("can_enable");
  });

  it("keeps a stable error code for network failures", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("offline"));
    await expect(fetchGovernanceModels(fetcher)).rejects.toEqual(new ModelGovernanceRequestError("MODEL_GOVERNANCE_NETWORK_UNAVAILABLE"));
  });

  it("accepts the API's empty 204 delete response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    await expect(deleteProjectDefaultPolicy(fetcher, "project-a")).resolves.toBeUndefined();
  });
});
