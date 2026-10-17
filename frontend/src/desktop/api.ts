export type ProductSession = { product_session_id: string; workspace_reference: { fingerprint: string } };
export type Explorer = { items: Array<{ reference: { reference_id: string; source_domain: string; source_kind: string; status: string | null } }> };
export type Timeline = { entries: Array<{ reference_id: string; source_domain: string; source_kind: string; status: string | null }> };
export type Preferences = { theme: "SYSTEM" | "LIGHT" | "DARK"; sidebar_collapsed: boolean; time_format: "LOCAL" | "UTC"; notification_level: "ALL" | "IMPORTANT" | "NONE"; updated_at: string };
export type Notification = { notification_id: string; event_type: string; status: string | null; occurred_at: string };
export type ChatResponse = { display: { content: string } };

export class DesktopRequestError extends Error {
  constructor(readonly code: string) { super(code); this.name = "DesktopRequestError"; }
}

async function call<T>(path: string, requester: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1/desktop${path}`, { ...init, headers: { "Content-Type": "application/json", "X-Desktop-Requester": requester, ...(init?.headers ?? {}) } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { error?: string };
    throw new DesktopRequestError(body.error ?? "DESKTOP_PRODUCT_UNAVAILABLE");
  }
  return response.json() as Promise<T>;
}

export function openDesktopSession(requester: string, projectId: string): Promise<ProductSession> { return call("/sessions", requester, { method: "POST", body: JSON.stringify({ product_session_id: crypto.randomUUID(), project_id: projectId, expires_at: new Date(Date.now() + 3_600_000).toISOString() }) }); }
export function fetchExplorer(requester: string, sessionId: string) { return call<Explorer>(`/sessions/${sessionId}/explorer`, requester); }
export function fetchTimeline(requester: string, sessionId: string) { return call<Timeline>(`/sessions/${sessionId}/timeline`, requester); }
export function fetchNotifications(requester: string, sessionId: string) { return call<Notification[]>(`/sessions/${sessionId}/notifications`, requester); }
export function fetchPreferences(requester: string) { return call<Preferences>("/preferences", requester); }
export function savePreferences(requester: string, preferences: Preferences) { return call<Preferences>("/preferences", requester, { method: "PUT", body: JSON.stringify(preferences) }); }
export function askDesktop(requester: string, sessionId: string, question: string): Promise<ChatResponse> { return call(`/sessions/${sessionId}/chat`, requester, { method: "POST", body: JSON.stringify({ request_id: crypto.randomUUID(), trace_id: crypto.randomUUID(), question, constraints: [], requested_at: new Date().toISOString() }) }); }
