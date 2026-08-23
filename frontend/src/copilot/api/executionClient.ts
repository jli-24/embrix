export type ExecutionOperationStatus = {
  operation_id: string;
  tenant_id: string;
  project_id: string;
  operation_type: "BUILD" | "TEST" | "FLASH" | "UART_OBSERVATION" | "HIL";
  status: string;
  approval_id: string | null;
  worker_id: string | null;
  recovery_state: string;
  cancellation_requested: boolean;
  updated_at: string;
  receipt_fingerprint: string | null;
  safe_detail: string;
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
  let response: Response;
  try {
    response = await fetcher(url, { ...init, headers: { ...headers, ...(init?.headers ?? {}) } });
  } catch {
    throw new ExecutionRequestError("EXECUTION_UNAVAILABLE");
  }
  const body = await response.json().catch(() => ({})) as { error?: string };
  if (!response.ok) throw new ExecutionRequestError(body.error ?? "EXECUTION_REJECTED");
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
  const response = await fetcher(
    `/api/v1/copilot/operations/events/stream?project_id=${encodeURIComponent(projectId)}`,
    {
      headers: {
        ...headers,
        Accept: "text/event-stream",
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      },
      signal,
    },
  );
  if (!response.ok || !response.body) {
    throw new ExecutionRequestError("EXECUTION_EVENT_STREAM_REJECTED");
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
