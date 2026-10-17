import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type EnvironmentProject = {
  environment: { tenant_id: string; status: string };
  project: { project_id: string; project_name_reference: string; workspace_reference: string; status: string };
};

export type RuntimeHealth = { component: string; status: string; observed_at: string };
export type AuditView = { event_type: string; object_reference: string; status: string; timestamp: string };
export type MemoryView = { memory_reference: string; status: string };

export class EnvironmentRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "EnvironmentRequestError";
  }
}

const tenant = "desktop-tenant";

async function read<T>(path: string, fetcher: typeof fetch = fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new EnvironmentRequestError(DESKTOP_SESSION_EXPIRED);
  const response = await fetcher(`/api/v1/environment${path}`, {
    credentials: "same-origin",
    headers: {
      "X-Copilot-Requester": "desktop-user",
      "X-Environment-Tenant": tenant,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ error: "ENVIRONMENT_UNAVAILABLE" }));
    throw new EnvironmentRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "ENVIRONMENT_UNAVAILABLE");
  }
  return response.json() as Promise<T>;
}

export const fetchEnvironmentProjects = (fetcher: typeof fetch = fetch) => read<EnvironmentProject[]>("/projects", fetcher);
export const fetchEnvironmentHealth = (fetcher: typeof fetch = fetch) => read<RuntimeHealth[]>("/runtime-health", fetcher);
export const fetchEnvironmentAudit = (projectId: string, fetcher: typeof fetch = fetch) => read<AuditView[]>(`/audit?project_id=${encodeURIComponent(projectId)}`, fetcher);
export const fetchEnvironmentMemory = (projectId: string, fetcher: typeof fetch = fetch) => read<MemoryView[]>(`/memory-view?project_id=${encodeURIComponent(projectId)}`, fetcher);
