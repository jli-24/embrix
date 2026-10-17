import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type SchematicApprovalContext = {
  fingerprint: string;
  requester_id: string;
  expires_at: string;
  conversation_id: string;
  schematic_artifact_fingerprint: string;
  source_operation_id: string;
  source_operation_receipt_fingerprint: string;
  source_parse_operation_id: string | null;
  source_parse_receipt_fingerprint: string | null;
  proposal_id: string;
  proposal_fingerprint: string;
};

export type ExecutionOperationStatus = {
  operation_id: string;
  tenant_id: string;
  project_id: string;
  operation_type: "BUILD" | "TEST" | "FLASH" | "UART_OBSERVATION" | "HIL" | "FIRMWARE_SOURCE_WRITE" | "KICAD_SCHEMATIC_WRITE" | "KICAD_SCHEMATIC_ERC" | "KICAD_SCHEMATIC_PARSE" | "KICAD_SCHEMATIC_READ_BACK" | "KICAD_PCB_DRC_BASELINE";
  status: string;
  approval_id: string | null;
  worker_id: string | null;
  recovery_state: string;
  cancellation_requested: boolean;
  updated_at: string;
  receipt_fingerprint: string | null;
  safe_detail: string;
  schematic_approval_context?: SchematicApprovalContext | null;
  correlation?: {
    project_id: string;
    operation_id: string | null;
    task_id: string | null;
  } | null;
};

export type ExecutionEventType =
  | "OPERATION_CREATED"
  | "APPROVAL_ISSUED"
  | "OPERATION_QUEUED"
  | "OPERATION_CLAIMED"
  | "OPERATION_STARTED"
  | "CANCELLATION_REQUESTED"
  | "CANCELLATION_PENDING"
  | "OPERATION_SUCCEEDED"
  | "OPERATION_FAILED"
  | "OPERATION_CANCELLED"
  | "OPERATION_TIMED_OUT"
  | "RECOVERY_REQUIRED"
  | "RECOVERY_RESOLVED"
  | "RESYNC_REQUIRED";

export type ExecutionOperationEvent = {
  event_id: string | null;
  event_type: ExecutionEventType;
  operation_id: string | null;
  operation_type?: ExecutionOperationStatus["operation_type"];
  status: string | null;
  recovery_state: string | null;
  receipt_fingerprint: string | null;
  reason_code: string;
};

export class ExecutionRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "ExecutionRequestError";
  }
}

const headers = {
  "X-Copilot-Requester": "desktop-user",
  "X-Environment-Tenant": "desktop-tenant",
};

async function request<T>(url: string, init: RequestInit | undefined, fetcher: typeof fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new ExecutionRequestError(DESKTOP_SESSION_EXPIRED);
  let response: Response;
  try {
    response = await fetcher(url, { credentials: "same-origin", ...init, headers: { ...headers, ...(init?.headers ?? {}) } });
  } catch {
    throw new ExecutionRequestError("EXECUTION_UNAVAILABLE");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ExecutionRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(body)) ?? "EXECUTION_REJECTED");
  return body as T;
}

export function fetchExecutionStatus(projectId: string, operationId: string, fetcher: typeof fetch = fetch): Promise<ExecutionOperationStatus> {
  if (!projectId.trim() || !operationId.trim()) throw new ExecutionRequestError("OPERATION_ID_REQUIRED");
  return request<ExecutionOperationStatus>(`/api/v1/copilot/operations/${encodeURIComponent(operationId)}/status?project_id=${encodeURIComponent(projectId)}`, undefined, fetcher);
}

export function cancelExecution(projectId: string, operationId: string, reason: string, fetcher: typeof fetch = fetch): Promise<ExecutionOperationStatus> {
  if (!projectId.trim() || !operationId.trim() || !reason.trim()) throw new ExecutionRequestError("CANCELLATION_INPUT_INVALID");
  return request<ExecutionOperationStatus>(`/api/v1/copilot/operations/${encodeURIComponent(operationId)}/cancel?project_id=${encodeURIComponent(projectId)}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ reason }) }, fetcher);
}

export type ExecutionApprovalResult = {
  grant_id: string;
  operation_id: string;
  status: string;
  fingerprint: string;
};

/** Issues the canonical backend approval and lets the worker own execution. */
export function approveExecution(
  projectId: string,
  operationId: string,
  reviewerId: string,
  nonce: string,
  expiresAt: string,
  fetcher: typeof fetch = fetch,
): Promise<ExecutionApprovalResult> {
  if (!projectId.trim() || !operationId.trim() || !reviewerId.trim() || !nonce.trim() || !expiresAt.trim()) {
    throw new ExecutionRequestError("APPROVAL_INPUT_INVALID");
  }
  return request<ExecutionApprovalResult>(
    `/api/v1/copilot/operations/${encodeURIComponent(operationId)}/approval?project_id=${encodeURIComponent(projectId)}`,
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ reviewer_id: reviewerId, nonce, expires_at: expiresAt }) },
    fetcher,
  );
}

const executionEventTypes = new Set<ExecutionEventType>([
  "OPERATION_CREATED",
  "APPROVAL_ISSUED",
  "OPERATION_QUEUED",
  "OPERATION_CLAIMED",
  "OPERATION_STARTED",
  "CANCELLATION_REQUESTED",
  "CANCELLATION_PENDING",
  "OPERATION_SUCCEEDED",
  "OPERATION_FAILED",
  "OPERATION_CANCELLED",
  "OPERATION_TIMED_OUT",
  "RECOVERY_REQUIRED",
  "RECOVERY_RESOLVED",
  "RESYNC_REQUIRED",
]);

function parseExecutionEventBlock(block: string): ExecutionOperationEvent | null {
  let eventType = "";
  let eventId: string | null = null;
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("id:")) eventId = line.slice(3).trim() || null;
    if (line.startsWith("event:")) eventType = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!executionEventTypes.has(eventType as ExecutionEventType)) return null;
  const payload = data.length
    ? JSON.parse(data.join("\n")) as Partial<ExecutionOperationEvent>
    : {};
  return {
    event_id: eventId,
    event_type: eventType as ExecutionEventType,
    operation_id: payload.operation_id ?? null,
    operation_type: payload.operation_type,
    status: payload.status ?? null,
    recovery_state: payload.recovery_state ?? null,
    receipt_fingerprint: payload.receipt_fingerprint ?? null,
    reason_code: payload.reason_code ?? eventType,
  };
}

export function projectExecutionEvent(
  current: ExecutionOperationStatus,
  event: ExecutionOperationEvent,
  selectedOperationId: string,
): ExecutionOperationStatus {
  if (
    event.event_type === "RESYNC_REQUIRED"
    || event.operation_id !== selectedOperationId
    || event.status === null
    || event.recovery_state === null
  ) return current;
  return {
    ...current,
    status: event.status,
    recovery_state: event.recovery_state,
    receipt_fingerprint: event.receipt_fingerprint,
    safe_detail: event.reason_code,
  };
}

export async function streamExecutionEvents(
  projectId: string,
  lastEventId: string | null,
  onEvent: (event: ExecutionOperationEvent) => void,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<string | null> {
  if (!projectId.trim()) throw new ExecutionRequestError("PROJECT_ID_REQUIRED");
  if (isDesktopSessionExpired()) throw new ExecutionRequestError(DESKTOP_SESSION_EXPIRED);
  const response = await fetcher(
    `/api/v1/copilot/operations/events/stream?project_id=${encodeURIComponent(projectId)}`,
    {
      credentials: "same-origin",
      headers: {
        ...headers,
        Accept: "text/event-stream",
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      },
      signal,
    },
  );
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new ExecutionRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(body)) ?? "EXECUTION_EVENT_STREAM_REJECTED");
  }
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
      const event = parseExecutionEventBlock(block);
      if (!event) continue;
      if (event.event_id) cursor = event.event_id;
      onEvent(event);
    }
    if (done) break;
  }
  const trailing = parseExecutionEventBlock(buffer);
  if (trailing) {
    if (trailing.event_id) cursor = trailing.event_id;
    onEvent(trailing);
  }
  return cursor;
}

export type ExecutionReceipt = {
  operation_id: string; project_id: string; operation_type: string; status: string; fingerprint: string;
  evidence_references: string[]; safe_diagnostics_summary: string; recovery_state: string;
  artifact: { artifact_id: string } | null;
};
export const fetchExecutionReceipt = (projectId: string, operationId: string, fetcher: typeof fetch = fetch) =>
  request<ExecutionReceipt>(`/api/v1/copilot/operations/${encodeURIComponent(operationId)}/receipt?project_id=${encodeURIComponent(projectId)}`, undefined, fetcher);
