import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";
import type { FirmwareBuildConfirmation, FirmwareBuildContext } from "./firmwareBuildClient";

export type BrownfieldBuildConfiguration = {
  target_kind: string;
  build_system: "CMAKE";
  toolchain_id: string;
  toolchain_version: string;
  profile: string;
  build_directory: string;
  expected_artifact_types: Array<"ELF" | "BIN" | "HEX" | "MAP">;
  output_stem: string;
  compiler_flags: string[];
  linker_script: string | null;
  cmake_project: string;
  cmake_toolchain_file: string;
  startup_source: string;
  system_source: string;
  cmsis_include: string;
  device_include: string;
};

export type BrownfieldBuildPreview = {
  project_id: string;
  project_fingerprint: string;
  workspace_fingerprint: string;
  workspace_target_id: string;
  workspace_target_fingerprint: string;
  source_files: string[];
};

export type BrownfieldBuildContext = {
  project_id: string;
  project_fingerprint: string;
  workspace_fingerprint: string;
  workspace_target_id: string;
  workspace_target_fingerprint: string;
  plan: { plan_id: string; title: string; status: string };
  review: { status: string } | null;
  firmware_spec: { mcu_exact_part: string; source_layout: string[] };
  configuration: BrownfieldBuildConfiguration;
  proposal_fingerprint: string;
  fingerprint: string;
};

export type BrownfieldBuildProposal = {
  workspace_fingerprint: string;
  target_platform: string;
  mcu_family: string;
  mcu_exact_part: string;
  source_layout: string[];
  configuration: BrownfieldBuildConfiguration;
};

export class BrownfieldBuildRequestError extends Error {
  constructor(readonly code: string) { super(code); this.name = "BrownfieldBuildRequestError"; }
}

const headers = { "X-Copilot-Requester": "desktop-user", "X-Environment-Tenant": "desktop-tenant" };

function base(projectId: string): string {
  if (!projectId.trim()) throw new BrownfieldBuildRequestError("PROJECT_REQUIRED");
  return `/api/v1/copilot/projects/${encodeURIComponent(projectId)}/brownfield-build`;
}

async function request<T>(url: string, init: RequestInit | undefined, fetcher: typeof fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new BrownfieldBuildRequestError(DESKTOP_SESSION_EXPIRED);
  let response: Response;
  try { response = await fetcher(url, { credentials: "same-origin", ...init, headers: { ...headers, ...(init?.headers ?? {}) } }); }
  catch { throw new BrownfieldBuildRequestError("BROWNFIELD_BUILD_UNAVAILABLE"); }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new BrownfieldBuildRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(body)) ?? "BROWNFIELD_BUILD_REJECTED");
  if (!body || typeof body !== "object") throw new BrownfieldBuildRequestError("BROWNFIELD_BUILD_RESPONSE_INVALID");
  return body as T;
}

export function fetchBrownfieldBuildPreview(projectId: string, fetcher: typeof fetch = fetch): Promise<BrownfieldBuildPreview> {
  return request<BrownfieldBuildPreview>(`${base(projectId)}/preview`, undefined, fetcher);
}

export async function fetchBrownfieldBuildContext(projectId: string, fetcher: typeof fetch = fetch): Promise<BrownfieldBuildContext | null> {
  const url = base(projectId);
  if (isDesktopSessionExpired()) throw new BrownfieldBuildRequestError(DESKTOP_SESSION_EXPIRED);
  let response: Response;
  try { response = await fetcher(url, { credentials: "same-origin", headers }); }
  catch { throw new BrownfieldBuildRequestError("BROWNFIELD_BUILD_UNAVAILABLE"); }
  if (response.status === 404) return null;
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new BrownfieldBuildRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(body)) ?? "BROWNFIELD_BUILD_REJECTED");
  if (!body || typeof body !== "object") throw new BrownfieldBuildRequestError("BROWNFIELD_BUILD_RESPONSE_INVALID");
  return body as BrownfieldBuildContext;
}

export function createBrownfieldBuildProposal(projectId: string, proposal: BrownfieldBuildProposal, fetcher: typeof fetch = fetch): Promise<BrownfieldBuildContext> {
  return request<BrownfieldBuildContext>(`${base(projectId)}/proposal`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(proposal) }, fetcher);
}

export function decideBrownfieldBuildPlan(projectId: string, proposalFingerprint: string, decision: "APPROVED" | "REJECTED", fetcher: typeof fetch = fetch): Promise<BrownfieldBuildContext> {
  if (!proposalFingerprint.trim()) throw new BrownfieldBuildRequestError("BUILD_PROPOSAL_REQUIRED");
  return request<BrownfieldBuildContext>(`${base(projectId)}/decision`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ proposal_fingerprint: proposalFingerprint, decision }) }, fetcher);
}

function firmwareBase(): string { return "/api/v1/copilot/firmware-build/brownfield"; }

export function fetchBrownfieldFirmwareBuildContext(projectId: string, fetcher: typeof fetch = fetch): Promise<FirmwareBuildContext> {
  if (!projectId.trim()) throw new BrownfieldBuildRequestError("PROJECT_REQUIRED");
  return request<FirmwareBuildContext>(`${firmwareBase()}/context?project_id=${encodeURIComponent(projectId)}`, undefined, fetcher);
}

export function reviewBrownfieldFirmwareBuildProposal(projectId: string, contextVersion: string, fetcher: typeof fetch = fetch): Promise<FirmwareBuildContext> {
  if (!projectId.trim() || !contextVersion.trim()) throw new BrownfieldBuildRequestError("FIRMWARE_BUILD_CONTEXT_REQUIRED");
  return request<FirmwareBuildContext>(`${firmwareBase()}/proposals`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: projectId, context_version: contextVersion, reviewed: true }),
  }, fetcher);
}

export function confirmBrownfieldFirmwareBuild(projectId: string, contextVersion: string, fetcher: typeof fetch = fetch): Promise<FirmwareBuildConfirmation> {
  if (!projectId.trim() || !contextVersion.trim()) throw new BrownfieldBuildRequestError("FIRMWARE_BUILD_CONTEXT_REQUIRED");
  return request<FirmwareBuildConfirmation>(`${firmwareBase()}/confirm`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: projectId, context_version: contextVersion, confirmed: true }),
  }, fetcher);
}
