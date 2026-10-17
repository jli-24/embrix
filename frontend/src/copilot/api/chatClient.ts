import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type ProjectReference = {
  tenant_id: string;
  project_id: string;
  project_name_reference: string;
  workspace_reference: string;
  fingerprint: string;
};

export type ContextReference = {
  tenant_id: string;
  project_id: string;
  reference_id: string;
  reference_kind: "WORKSPACE" | "TIMELINE" | "EVIDENCE" | "PROPOSAL" | "MEMORY";
  source_fingerprint: string;
  display_label: string;
  fingerprint: string;
};

export type ChatAnswer = {
  answer_id: string;
  status: "COMPLETED";
  answer_text: string;
  response_type: "CHAT" | "DOCUMENT" | "ARTIFACT";
  artifact_refs: string[];
  evidence_references: ContextReference[];
  evidence_details: Array<{ source_type: string; label: string; reference_id: string; status: string; summary: string | null; source_uri: string | null; source_fingerprint: string; observed_at: string; fingerprint: string }>;
  recommendations: Array<{ review: string; reason: string | null; status: string; reference_id: string; source_fingerprint: string; fingerprint: string }>;
  created_at: string;
  fingerprint: string;
};

export type RuntimePanel = {
  hil_status: "OBSERVED" | "FAILED" | "UNAVAILABLE";
  eda_status: "OBSERVED" | "FAILED" | "UNAVAILABLE";
  memory_statuses: Array<"REVIEW_REQUIRED" | "APPROVED" | "OBSERVED">;
  runtime_health: Array<{ component: string; status: string; observed_at: string }>;
};

export type ChatEventType = "CHAT_STARTED" | "ANSWER_CHUNK" | "ANSWER_COMPLETED" | "ANSWER_FAILED" | "RESYNC_REQUIRED";

export type ChatEvent = {
  event_id: string | null;
  event_type: ChatEventType;
  answer_chunk: string | null;
  answer_fingerprint: string | null;
  request_id?: string | null;
  trace_id?: string | null;
  analysis_state?: import("./projectClient").AnalysisState | null;
  reason_code?: string | null;
  terminal_event?: string | null;
};

export type ChatTraceEvent = {
  event_id: string;
  trace_id: string;
  request_id: string;
  project_id: string;
  conversation_id: string;
  stage: string;
  route: string | null;
  agent_identity: string | null;
  service_identity: string | null;
  source_status: string | null;
  evidence_count: number | null;
  gate_result: string | null;
  provider_configured: boolean | null;
  gateway_invocation_count: number;
  reason_code: string | null;
  terminal_event: string | null;
  timestamp: string;
  fingerprint: string;
};

export type ChatStreamProjection = {
  status: "idle" | "started" | "streaming" | "clarification" | "completed" | "failed" | "resync";
  answer: string;
  errorCode?: string | null;
  analysisState?: ChatEvent["analysis_state"];
  traceId?: string | null;
};

export function reduceChatStreamEvent(
  state: ChatStreamProjection,
  event: ChatEvent,
): ChatStreamProjection {
  if (event.analysis_state === "WAITING_FOR_CLARIFICATION") {
    return { ...state, status: "clarification", errorCode: "CLARIFICATION_REQUIRED", analysisState: event.analysis_state, traceId: event.trace_id ?? state.traceId };
  }
  if (state.status === "failed" || state.status === "resync" || state.status === "clarification") return state;
  switch (event.event_type) {
    case "CHAT_STARTED":
      return { ...state, status: "started", errorCode: event.reason_code ?? state.errorCode, analysisState: event.analysis_state ?? state.analysisState, traceId: event.trace_id ?? state.traceId };
    case "ANSWER_CHUNK":
      return {
        status: "streaming",
        answer: state.answer + (event.answer_chunk ?? ""),
        errorCode: event.reason_code ?? state.errorCode,
        analysisState: event.analysis_state ?? state.analysisState,
        traceId: event.trace_id ?? state.traceId,
      };
    case "ANSWER_COMPLETED":
      return { ...state, status: "completed", errorCode: event.reason_code ?? state.errorCode, analysisState: event.analysis_state ?? state.analysisState, traceId: event.trace_id ?? state.traceId };
    case "ANSWER_FAILED":
      return { ...state, status: "failed", errorCode: event.reason_code ?? state.errorCode, analysisState: event.analysis_state ?? state.analysisState, traceId: event.trace_id ?? state.traceId };
    case "RESYNC_REQUIRED":
      return { ...state, status: "resync", errorCode: event.reason_code ?? state.errorCode, analysisState: event.analysis_state ?? state.analysisState, traceId: event.trace_id ?? state.traceId };
    default:
      return state;
  }
}

const chatEventTypes = new Set<ChatEventType>([
  "CHAT_STARTED",
  "ANSWER_CHUNK",
  "ANSWER_COMPLETED",
  "ANSWER_FAILED",
  "RESYNC_REQUIRED",
]);

export class CopilotChatRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "CopilotChatRequestError";
  }
}

export function isRetryableChatErrorCode(code: string): boolean {
  return new Set([
    "COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED",
    "COPILOT_CHAT_UNAVAILABLE",
    "PROVIDER_TRANSPORT_FAILED",
  ]).has(code);
}

const requester = "desktop-user";
const tenant = "desktop-tenant";

function headers(): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-Copilot-Requester": requester,
    "X-Environment-Tenant": tenant,
  };
}

async function call<T>(path: string, init: RequestInit = {}, fetcher: typeof fetch = fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new CopilotChatRequestError(DESKTOP_SESSION_EXPIRED);
  const response = await fetcher(`/api/v1/copilot/chat${path}`, {
    credentials: "same-origin",
    ...init,
    headers: { ...headers(), ...(init.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const code = mapDesktopSessionError(response.status, readDesktopSessionErrorCode(body)) ?? "COPILOT_CHAT_UNAVAILABLE";
    throw new CopilotChatRequestError(code);
  }
  return response.json() as Promise<T>;
}

export function closeChatSession(sessionId: string, fetcher: typeof fetch = fetch): Promise<unknown> {
  return call(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }, fetcher);
}

export const fetchProject = (sessionId: string, fetcher: typeof fetch = fetch) => call<ProjectReference>(`/sessions/${encodeURIComponent(sessionId)}/project`, {}, fetcher);
export const fetchReferences = (sessionId: string, fetcher: typeof fetch = fetch) => call<ContextReference[]>(`/sessions/${encodeURIComponent(sessionId)}/references`, {}, fetcher);
export const fetchRuntimePanel = (sessionId: string, fetcher: typeof fetch = fetch) => call<RuntimePanel>(`/sessions/${encodeURIComponent(sessionId)}/runtime-panel`, {}, fetcher);
export const fetchChatTrace = (sessionId: string, traceId: string, fetcher: typeof fetch = fetch): Promise<ChatTraceEvent[]> => {
  if (!sessionId.trim() || !traceId.trim()) throw new CopilotChatRequestError("TRACE_ID_REQUIRED");
  return call<ChatTraceEvent[]>(`/sessions/${encodeURIComponent(sessionId)}/trace?trace_id=${encodeURIComponent(traceId)}`, {}, fetcher);
};

export function bindChatAttachment(sessionId: string, file: Pick<File, "name" | "size" | "type">, fetcher: typeof fetch = fetch): Promise<ContextReference> {
  return call(`/sessions/${encodeURIComponent(sessionId)}/attachments`, {
    method: "POST",
    body: JSON.stringify({
      reference_id: `attachment-${crypto.randomUUID()}`,
      type: file.type.startsWith("image/") ? "IMAGE" : "FILE",
      basename: file.name,
      summary: "User-selected read-only attachment reference",
      size_bytes: file.size,
      created_at: new Date().toISOString(),
    }),
  }, fetcher);
}

function questionPayload(project: ProjectReference, question: string, references: ContextReference[], answerDetail: "concise" | "standard" | "detailed", externalSearch: boolean, requestId: string) {
  return {
      project_reference: project,
      application_question: {
        request_id: requestId,
        trace_id: crypto.randomUUID(),
        project_id: project.project_id,
        requester,
        question,
        constraints: [`answer_detail:${answerDetail}`],
        external_search: externalSearch,
        requested_at: new Date().toISOString(),
      },
      external_search: externalSearch,
      context_references: references,
      requested_at: new Date().toISOString(),
  };
}

export function classifyQuestion(sessionId: string, project: ProjectReference, question: string, references: ContextReference[], answerDetail: "concise" | "standard" | "detailed" = "standard", externalSearch = false, fetcher: typeof fetch = fetch, requestId = crypto.randomUUID()): Promise<{ route: import("./projectClient").CanonicalRoute | "GREENFIELD" | "BROWNFIELD" | "FIRMWARE" | "DEBUG" | "UNKNOWN"; reason_code: string; request_id: string; request_fingerprint: string; fingerprint: string }> {
  return call(`/sessions/${encodeURIComponent(sessionId)}/classify`, {
    method: "POST",
    body: JSON.stringify(questionPayload(project, question, references, answerDetail, externalSearch, requestId)),
  }, fetcher);
}

export function submitQuestion(sessionId: string, project: ProjectReference, question: string, references: ContextReference[], answerDetail: "concise" | "standard" | "detailed" = "standard", externalSearch = false, fetcher: typeof fetch = fetch, requestId = crypto.randomUUID()): Promise<ChatAnswer> {
  return call(`/sessions/${encodeURIComponent(sessionId)}/requests`, {
    method: "POST",
    body: JSON.stringify(questionPayload(project, question, references, answerDetail, externalSearch, requestId)),
  }, fetcher);
}

export function eventUrl(sessionId: string): string {
  const query = new URLSearchParams({ session_id: sessionId, requester, tenant });
  return `/api/v1/copilot/chat/events?${query.toString()}`;
}

function parseEventBlock(block: string): ChatEvent | null {
  let eventType = "";
  let eventId: string | null = null;
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("id:")) eventId = line.slice(3).trim() || null;
    if (line.startsWith("event:")) eventType = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!chatEventTypes.has(eventType as ChatEventType)) return null;
  const payload = data.length ? JSON.parse(data.join("\n")) as Partial<ChatEvent> : {};
  return {
    event_id: eventId,
    event_type: eventType as ChatEventType,
    answer_chunk: payload.answer_chunk ?? null,
    answer_fingerprint: payload.answer_fingerprint ?? null,
    request_id: payload.request_id ?? null,
    trace_id: payload.trace_id ?? null,
    analysis_state: payload.analysis_state ?? null,
    reason_code: payload.reason_code ?? null,
    terminal_event: payload.terminal_event ?? null,
  };
}

export async function streamChatEvents(
  sessionId: string,
  lastEventId: string | null,
  onEvent: (event: ChatEvent) => void,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<string | null> {
  if (isDesktopSessionExpired()) throw new CopilotChatRequestError(DESKTOP_SESSION_EXPIRED);
  const response = await fetcher(eventUrl(sessionId), {
    credentials: "same-origin",
    headers: {
      ...headers(),
      Accept: "text/event-stream",
      ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
    },
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const code = mapDesktopSessionError(response.status, readDesktopSessionErrorCode(body)) ?? "COPILOT_CHAT_UNAVAILABLE";
    throw new CopilotChatRequestError(code);
  }
  if (!response.body) throw new CopilotChatRequestError("COPILOT_CHAT_UNAVAILABLE");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let cursor = lastEventId;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = parseEventBlock(block);
      if (!event) continue;
      if (event.event_id) cursor = event.event_id;
      onEvent(event);
    }
    if (done) break;
  }
  const trailing = parseEventBlock(buffer);
  if (trailing) {
    if (trailing.event_id) cursor = trailing.event_id;
    onEvent(trailing);
  }
  return cursor;
}
