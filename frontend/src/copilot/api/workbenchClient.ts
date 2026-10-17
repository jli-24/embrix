import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type WorkbenchResourceNode = {
  resource_id: string;
  parent_id: string | null;
  name: string;
  relative_path: string;
  node_type: "DIRECTORY" | "FILE";
  category: string;
  language: string | null;
  source_fingerprint: string | null;
};

export type WorkbenchFinding = {
  finding_id: string;
  finding_type: string;
  severity: string;
  subject: string;
  summary: string;
  evidence_ids: string[];
  validation_id: string | null;
  confidence: number | null;
  limitations: string[];
  target_identity: string | null;
  target_fingerprint: string | null;
  finding_fingerprint: string | null;
};

export type WorkbenchRepairAlternative = {
  proposal_id: string;
  proposal_type: string;
  subject: string;
  summary: string;
  rationale: string | null;
  finding_ids: string[];
  evidence_ids: string[];
  proposed_changes: Array<{ file_reference: string | null; symbol: string | null; logical_change: string }>;
  risk: string | null;
  confidence: number | null;
  limitations: string[];
  review_status: string | null;
};

export type WorkbenchProjection = {
  schema_version: "workbench-v1";
  project_id: string;
  project_fingerprint: string;
  resources: WorkbenchResourceNode[];
  findings: WorkbenchFinding[];
  repair_alternatives: WorkbenchRepairAlternative[];
  truncated: boolean;
  generated_at: string;
  projection_fingerprint: string;
  /** Optional additive hint; absent means the directory state is unknown. */
  directory_state?: "EMPTY" | "NON_EMPTY";
};

export type WorkbenchResourceState = "EMPTY_DIRECTORY" | "LOADED_ZERO" | "LOADED";

/**
 * Keep a successful zero-resource response truthful.  Only an explicit
 * read-only projection hint can claim that the physical directory is empty;
 * without it the UI reports that no resources were projected.
 */
export function classifyWorkbenchResourceState(projection: WorkbenchProjection): WorkbenchResourceState {
  if (projection.resources.length > 0) return "LOADED";
  return projection.directory_state === "EMPTY" ? "EMPTY_DIRECTORY" : "LOADED_ZERO";
}

export class WorkbenchRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "WorkbenchRequestError";
  }
}

export async function fetchWorkbench(projectId: string, options: { limit?: number; fetcher?: typeof fetch; signal?: AbortSignal } = {}): Promise<WorkbenchProjection> {
  if (!projectId.trim()) throw new WorkbenchRequestError("PROJECT_ID_REQUIRED");
  if (isDesktopSessionExpired()) throw new WorkbenchRequestError(DESKTOP_SESSION_EXPIRED);
  const response = await (options.fetcher ?? fetch)(`/api/v1/copilot/desktop/projects/${encodeURIComponent(projectId)}/workbench?limit=${options.limit ?? 500}`, {
    credentials: "same-origin",
    headers: { "X-Copilot-Requester": "desktop-user", "X-Environment-Tenant": "desktop-tenant" },
    signal: options.signal,
  }).catch((error) => {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new WorkbenchRequestError("DESKTOP_WORKBENCH_UNAVAILABLE");
  });
  const payload = await response.json().catch(() => null) as WorkbenchProjection | { error?: string } | null;
  if (!response.ok) throw new WorkbenchRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "DESKTOP_WORKBENCH_REJECTED");
  if (!payload || !("schema_version" in payload) || payload.schema_version !== "workbench-v1") throw new WorkbenchRequestError("DESKTOP_WORKBENCH_RESPONSE_INVALID");
  return payload as WorkbenchProjection;
}
