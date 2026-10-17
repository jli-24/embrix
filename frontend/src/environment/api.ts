export type EnvironmentProject = {
  environment: { tenant_id: string; status: string };
  project: { project_id: string; project_name_reference: string; workspace_reference: string; status: string };
};

export type Collaboration = {
  team: { team_id: string };
  active_reviewer_references: string[];
  pending_review_references: string[];
  recent_activity_references: string[];
};

export type RuntimeHealth = { component: string; status: string; observed_at: string };
export type AuditView = { event_type: string; object_reference: string; status: string; timestamp: string };
export type MemoryView = { memory_reference: string; status: string };

export class EnvironmentRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

const tenant = "desktop-tenant";

async function read<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1/environment${path}`, {
    headers: { "X-Environment-Tenant": tenant },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ error: "ENVIRONMENT_UNAVAILABLE" }));
    throw new EnvironmentRequestError(payload.error ?? "ENVIRONMENT_UNAVAILABLE");
  }
  return response.json() as Promise<T>;
}

export const fetchProjects = () => read<EnvironmentProject[]>("/projects");
export const fetchTeam = () => read<Collaboration>("/team");
export const fetchRuntimeHealth = () => read<RuntimeHealth[]>("/runtime-health");
export const fetchAudit = () => read<AuditView[]>("/audit");
export const fetchMemoryViews = () => read<MemoryView[]>("/memory-view");
