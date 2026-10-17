export type GovernanceScope = "GLOBAL" | "PROJECT" | "STAGE";
export type GovernanceSelectionMode = "AUTO" | "FIXED_MODEL" | "CANDIDATE_GROUP";
export type GovernanceStageId = "REQUIREMENT_ANALYSIS" | "TECH_RESEARCH" | "SYSTEM_ARCHITECTURE" | "HARDWARE_SELECTION" | "SCHEMATIC_DESIGN" | "PCB_REVIEW" | "FIRMWARE_DEVELOPMENT" | "DEBUGGING" | "SIMULATION" | "DOCUMENTATION";

export type GovernanceModel = {
  model_profile_id: string;
  provider_id: string;
  provider_display_name?: string;
  connection_id?: string | null;
  connection_name?: string | null;
  endpoint_id: string;
  protocol_family?: string;
  credential_ref: string;
  model_id: string;
  display_name: string;
  billing_class: string;
  billing_provenance: string;
  billing_source?: string | null;
  availability: string;
  availability_status: string;
  availability_provenance: string;
  identity_status: "VALID" | "STALE" | "INVALID" | "UNKNOWN" | string;
  identity_status_reason?: string | null;
  record_provenance: "OFFICIAL_CATALOG" | "ENDPOINT_DISCOVERY" | "EXPLICIT_CONFIGURATION" | "PROVIDER_METADATA" | "UNKNOWN" | string;
  verification_status: "VERIFIED" | "UNVERIFIED" | "UNKNOWN" | string;
  discovery_status: string;
  /** Optional when the discovery adapter exposes the event timestamp. */
  discovered_at?: string | null;
  metadata_source?: string | null;
  metadata_updated_at?: string | null;
  verified_at?: string | null;
  credential_revision?: string | null;
  endpoint_revision?: string | null;
  capability_provenance: Array<{
    capability: string;
    provenance: string;
    status: string;
    source?: string | null;
    observed_at?: string | null;
    provider_id?: string | null;
    endpoint_id?: string | null;
    credential_ref?: string | null;
    model_profile_id?: string | null;
    model_id?: string | null;
    credential_revision?: string | null;
    endpoint_revision?: string | null;
  }>;
  can_enable: boolean;
  enable_block_reason?: string | null;
  capabilities: string[];
  allowed: boolean;
  policy_status?: string;
  policy_block_reason?: string | null;
  policy_id?: string | null;
  policy_scope?: GovernanceScope | null;
  policy_project_id?: string | null;
  policy_stage_id?: GovernanceStageId | null;
  capability_warning?: string | null;
};

export type OfficialCatalogModel = {
  provider_id: string;
  model_id: string;
  display_name: string;
  billing_class: string;
  billing_provenance: string;
  billing_source?: string | null;
  capabilities: string[];
  metadata_source?: string | null;
  metadata_updated_at?: string | null;
  deprecated: boolean;
  record_type: "OFFICIAL_CATALOG";
  availability_status: "UNVERIFIED";
  verification_status: "UNVERIFIED";
  can_enable: false;
  enable_block_reason: string;
};

export type GovernanceIntegrityIssue = {
  record_type: string;
  record_id: string;
  provider_id?: string | null;
  endpoint_id?: string | null;
  credential_ref?: string | null;
  model_profile_id?: string | null;
  model_id?: string | null;
  identity_status: "VALID" | "STALE" | "INVALID" | "UNKNOWN" | string;
  reason: string;
  detected_at: string;
};

export type GovernanceStage = {
  stage_id: GovernanceStageId;
  display_name: string;
  required_capabilities: string[];
};

export type GovernanceModelIdentity = {
  provider_id: string;
  endpoint_id: string;
  credential_ref: string;
  model_profile_id: string;
  model_id: string;
};

export type GovernanceStagePolicy = {
  id: string;
  project_id: string;
  stage_id: GovernanceStageId;
  selection_mode: GovernanceSelectionMode;
  fixed_model: GovernanceModelIdentity | null;
  candidate_models: GovernanceModelIdentity[];
  created_at: string;
  updated_at: string;
};

export type GovernanceProjectDefaultPolicy = {
  id: string;
  project_id: string;
  selection_mode: GovernanceSelectionMode;
  fixed_model: GovernanceModelIdentity | null;
  candidate_models: GovernanceModelIdentity[];
  created_at: string;
  updated_at: string;
};

export class ModelGovernanceRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "ModelGovernanceRequestError";
  }
}

async function call<T>(path: string, init: RequestInit = {}, fetcher: typeof fetch = fetch): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(`/api/v1/model-gateway/governance${path}`, {
      credentials: "same-origin",
      ...init,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch {
    throw new ModelGovernanceRequestError("MODEL_GOVERNANCE_NETWORK_UNAVAILABLE");
  }
  if (!response.ok) {
    let code = "MODEL_GOVERNANCE_UNAVAILABLE";
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) code = body.detail;
    } catch {
      // Keep a stable error code for non-JSON responses.
    }
    throw new ModelGovernanceRequestError(code);
  }
  if (response.status === 204) return undefined as T;
  try {
    return await response.json() as T;
  } catch {
    throw new ModelGovernanceRequestError("MODEL_GOVERNANCE_INVALID_RESPONSE");
  }
}

export function fetchGovernanceStages(fetcher: typeof fetch = fetch): Promise<GovernanceStage[]> {
  return call<GovernanceStage[]>("/stages", {}, fetcher);
}

export function fetchGovernanceModels(
  fetcher: typeof fetch = fetch,
  scope: GovernanceScope = "GLOBAL",
  projectId?: string,
  stageId?: GovernanceStageId,
): Promise<GovernanceModel[]> {
  const query = new URLSearchParams({ scope });
  if (projectId) query.set("project_id", projectId);
  if (stageId) query.set("stage_id", stageId);
  return call<GovernanceModel[]>(`/available-models?${query.toString()}`, {}, fetcher);
}

export function fetchOfficialCatalog(
  fetcher: typeof fetch = fetch,
  providerId?: string,
): Promise<OfficialCatalogModel[]> {
  const query = providerId ? `?provider_id=${encodeURIComponent(providerId)}` : "";
  return call<OfficialCatalogModel[]>(`/official-catalog${query}`, {}, fetcher);
}

export function fetchGovernanceIntegrityIssues(fetcher: typeof fetch = fetch): Promise<GovernanceIntegrityIssue[]> {
  return call<GovernanceIntegrityIssue[]>("/integrity-issues", {}, fetcher);
}

export function setGovernanceModelEnabled(fetcher: typeof fetch, policyId: string, enabled: boolean): Promise<unknown> {
  return call(`/allowed-models/${encodeURIComponent(policyId)}/enabled?enabled=${enabled ? "true" : "false"}`, { method: "PATCH" }, fetcher);
}

export function createGovernanceModelPolicy(
  fetcher: typeof fetch,
  model: GovernanceModel,
  scope: GovernanceScope,
  projectId?: string,
  stageId?: GovernanceStageId,
  enabled = true,
): Promise<unknown> {
  return call("/allowed-models", {
    method: "POST",
    body: JSON.stringify({
      scope,
      project_id: scope === "GLOBAL" ? null : projectId ?? null,
      stage_id: scope === "STAGE" ? stageId ?? null : null,
      provider_id: model.provider_id,
      endpoint_id: model.endpoint_id,
      credential_ref: model.credential_ref,
      model_profile_id: model.model_profile_id,
      model_id: model.model_id,
      enabled,
      priority: 100,
    }),
  }, fetcher);
}

export function fetchStagePolicies(fetcher: typeof fetch, projectId: string): Promise<GovernanceStagePolicy[]> {
  return call<GovernanceStagePolicy[]>(`/stage-policies/${encodeURIComponent(projectId)}`, {}, fetcher);
}

export function saveStagePolicy(
  fetcher: typeof fetch,
  projectId: string,
  stageId: GovernanceStageId,
  payload: Pick<GovernanceStagePolicy, "selection_mode" | "fixed_model" | "candidate_models">,
): Promise<GovernanceStagePolicy> {
  return call<GovernanceStagePolicy>(`/stage-policies/${encodeURIComponent(projectId)}/${encodeURIComponent(stageId)}`, { method: "PUT", body: JSON.stringify(payload) }, fetcher);
}

export async function fetchProjectDefaultPolicy(fetcher: typeof fetch, projectId: string): Promise<GovernanceProjectDefaultPolicy | null> {
  try {
    return await call<GovernanceProjectDefaultPolicy>(`/project-defaults/${encodeURIComponent(projectId)}`, {}, fetcher);
  } catch (reason: unknown) {
    if (reason instanceof ModelGovernanceRequestError && reason.code === "PROJECT_DEFAULT_POLICY_NOT_FOUND") return null;
    throw reason;
  }
}

export function saveProjectDefaultPolicy(
  fetcher: typeof fetch,
  projectId: string,
  payload: Pick<GovernanceProjectDefaultPolicy, "selection_mode" | "fixed_model" | "candidate_models">,
): Promise<GovernanceProjectDefaultPolicy> {
  return call<GovernanceProjectDefaultPolicy>(`/project-defaults/${encodeURIComponent(projectId)}`, { method: "PUT", body: JSON.stringify(payload) }, fetcher);
}

export function deleteProjectDefaultPolicy(fetcher: typeof fetch, projectId: string): Promise<unknown> {
  return call(`/project-defaults/${encodeURIComponent(projectId)}`, { method: "DELETE" }, fetcher);
}
