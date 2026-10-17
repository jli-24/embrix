import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type DesktopSafeItem = {
  reference_id: string;
  kind: string;
  status: string | null;
  summary: string | null;
  fingerprint: string | null;
  source_references: string[];
  limitations: string[];
  confidence: number | null;
};

export type DesktopBoundedPage = {
  items: DesktopSafeItem[];
  next_cursor: string | null;
  limit: number;
  total_available: number | null;
};

export type DesktopEvidenceItem = {
  evidence_id: string;
  source_type: string;
  subject: string;
  summary: string | null;
  source_reference: string | null;
  fingerprint: string;
  trust_level: string | null;
  verification_status: string;
  confidence: string | null;
  observed_at: string | null;
  limitations: string[];
};

export type DesktopWorkspaceProjection = {
  schema_version: "v6.7";
  identity: { tenant_id: string; requester_id: string; project_id: string; project_fingerprint: string };
  project: { project_id: string; tenant_id: string; name: string; relative_path: string; status: string; fingerprint: string; initialization_status: string };
  context: DesktopSafeItem | null;
  conversations: DesktopBoundedPage;
  evidence: { items: DesktopEvidenceItem[]; next_cursor: string | null; limit: number; total_available: number | null };
  memory: DesktopBoundedPage;
  knowledge: DesktopBoundedPage;
  reviews: DesktopBoundedPage;
  validations: DesktopBoundedPage;
  findings: DesktopBoundedPage;
  proposals: DesktopBoundedPage;
  changes: DesktopBoundedPage;
  operations: DesktopBoundedPage;
  audit: DesktopBoundedPage;
  capabilities: Array<{ capability: string; status: "AVAILABLE" | "DEGRADED" | "UNAVAILABLE" | "UNVERIFIED"; reason_code: string; detail: string; provider_configured?: string | null; credential_saved?: string | null; runtime_status?: string | null; model_status?: string | null }>;
  authoritative_facts?: Array<{ domain: "REQUIREMENT" | "ARCHITECTURE" | "CIRCUIT" | "SCHEMATIC" | "PCB" | "FIRMWARE" | "EVIDENCE"; owner: string; project_id: string; conversation_id: string | null; object_id: string | null; revision: string | null; fingerprint: string | null; source_reference: string | null; source_fingerprint: string | null; currentness: "CURRENT" | "STALE" | "BLOCKED" | "UNAVAILABLE" | "UNKNOWN"; reason_code: string }>;
  workflow_summary: { status: string; current_reference: string | null; stages: string[]; source_references: string[] };
  generated_at: string;
  projection_fingerprint: string;
};

export class DesktopWorkspaceRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "DesktopWorkspaceRequestError";
  }
}

const headers = { "X-Copilot-Requester": "desktop-user", "X-Environment-Tenant": "desktop-tenant" };

export async function fetchDesktopWorkspace(projectId: string, options: { limit?: number; cursor?: string | null; conversationId?: string | null; taskId?: string | null; signal?: AbortSignal; fetcher?: typeof fetch } = {}): Promise<DesktopWorkspaceProjection> {
  if (isDesktopSessionExpired()) throw new DesktopWorkspaceRequestError(DESKTOP_SESSION_EXPIRED);
  const params = new URLSearchParams({ limit: String(options.limit ?? 20) });
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.conversationId) params.set("conversation_id", options.conversationId);
  if (options.taskId) params.set("task_id", options.taskId);
  let response: Response;
  try {
    response = await (options.fetcher ?? fetch)(`/api/v1/copilot/desktop/projects/${encodeURIComponent(projectId)}/workspace?${params.toString()}`, { credentials: "same-origin", headers, signal: options.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new DesktopWorkspaceRequestError("DESKTOP_WORKSPACE_UNAVAILABLE");
  }
  const payload = await response.json().catch(() => null) as { detail?: string; error?: string } | DesktopWorkspaceProjection | null;
  if (!response.ok) {
    throw new DesktopWorkspaceRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "DESKTOP_WORKSPACE_REJECTED");
  }
  if (!payload || !("schema_version" in payload) || payload.schema_version !== "v6.7") throw new DesktopWorkspaceRequestError("DESKTOP_WORKSPACE_RESPONSE_INVALID");
  return payload as DesktopWorkspaceProjection;
}
