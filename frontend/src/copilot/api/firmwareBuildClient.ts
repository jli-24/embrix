import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type FirmwareBuildContext = {
  status: string;
  reason_code: string;
  context_version: string | null;
  project: string | null;
  target: string | null;
  source_revision: string | null;
  workspace_fingerprint: string | null;
  configuration: string | null;
  toolchain: string | null;
  plan: string | null;
  proposal: string | null;
};

export type FirmwareBuildConfirmation = {
  status: string;
  message: string;
  task_id?: string | null;
  operation_id?: string | null;
};

export type FirmwareBuildReceipt = {
  operation_id: string;
  fingerprint: string;
  project_id: string;
  operation_type: string;
  status: string;
  tool_execution_status: string;
  flash_verification_status: string;
  target_runtime_verification_status: string;
  artifact: {
    artifact_id: string;
    target_id: string;
    workspace_fingerprint: string;
    build_configuration: string;
    build_operation_id: string;
  } | null;
  build_evidence: {
    result_status: string;
    toolchain_id: string;
    toolchain_version: string;
    source_fingerprints: string[];
    output_artifact_ids: string[];
    log_artifact_id: string | null;
    tool_observed: boolean;
    compiler_exit_code: number | null;
    linker_exit_code: number | null;
    elf_verified: boolean;
    bin_verified: boolean;
  } | null;
  source_revision: string | null;
  evidence_references: string[];
  safe_diagnostics_summary: string;
  recovery_state: string;
  created_at: string;
};

export class FirmwareBuildRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "FirmwareBuildRequestError";
  }
}

const headers = { "X-Copilot-Requester": "desktop-user", "X-Environment-Tenant": "desktop-tenant" };

async function request<T>(url: string, init: RequestInit | undefined, fetcher: typeof fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new FirmwareBuildRequestError(DESKTOP_SESSION_EXPIRED);
  let response: Response;
  try {
    response = await fetcher(url, { credentials: "same-origin", ...init, headers: { ...headers, ...(init?.headers ?? {}) } });
  } catch {
    throw new FirmwareBuildRequestError("FIRMWARE_BUILD_UNAVAILABLE");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new FirmwareBuildRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(body)) ?? "FIRMWARE_BUILD_REJECTED");
  if (!body || typeof body !== "object") throw new FirmwareBuildRequestError("FIRMWARE_BUILD_RESPONSE_INVALID");
  return body as T;
}

function requireScope(projectId: string, conversationId: string): void {
  if (!projectId.trim() || !conversationId.trim()) throw new FirmwareBuildRequestError("FIRMWARE_BUILD_CONTEXT_REQUIRED");
}

export function fetchFirmwareBuildContext(projectId: string, conversationId: string, fetcher: typeof fetch = fetch): Promise<FirmwareBuildContext> {
  requireScope(projectId, conversationId);
  const params = new URLSearchParams({ project_id: projectId, conversation_id: conversationId });
  return request<FirmwareBuildContext>(`/api/v1/copilot/firmware-build/context?${params}`, undefined, fetcher);
}

export function reviewFirmwareBuildProposal(projectId: string, conversationId: string, contextVersion: string, fetcher: typeof fetch = fetch): Promise<FirmwareBuildContext> {
  requireScope(projectId, conversationId);
  if (!contextVersion.trim()) throw new FirmwareBuildRequestError("FIRMWARE_BUILD_CONTEXT_REQUIRED");
  return request<FirmwareBuildContext>("/api/v1/copilot/firmware-build/proposals", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: projectId, conversation_id: conversationId, context_version: contextVersion, reviewed: true }),
  }, fetcher);
}

export function confirmFirmwareBuild(projectId: string, conversationId: string, contextVersion: string, fetcher: typeof fetch = fetch): Promise<FirmwareBuildConfirmation> {
  requireScope(projectId, conversationId);
  if (!contextVersion.trim()) throw new FirmwareBuildRequestError("FIRMWARE_BUILD_CONTEXT_REQUIRED");
  return request<FirmwareBuildConfirmation>("/api/v1/copilot/firmware-build/confirm", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: projectId, conversation_id: conversationId, context_version: contextVersion, confirmed: true }),
  }, fetcher);
}

export function fetchFirmwareBuildReceipt(projectId: string, operationId: string, fetcher: typeof fetch = fetch): Promise<FirmwareBuildReceipt> {
  if (!projectId.trim() || !operationId.trim()) throw new FirmwareBuildRequestError("FIRMWARE_BUILD_OPERATION_REQUIRED");
  return request<FirmwareBuildReceipt>(`/api/v1/copilot/operations/${encodeURIComponent(operationId)}/receipt?project_id=${encodeURIComponent(projectId)}`, undefined, fetcher);
}
