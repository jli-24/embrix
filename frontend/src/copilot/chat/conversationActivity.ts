import type { ChatEvidence } from "../api/projectClient";
import type { ChatTraceEvent } from "../api/chatClient";
import type { ExecutionOperationEvent } from "../api/executionClient";

export type ActivityKind = "PLANNING" | "RESEARCH" | "TOOL" | "EXECUTION" | "VERIFICATION" | "ARTIFACT" | "APPROVAL" | "WARNING" | "ERROR";
export type ActivityStatus = "PENDING" | "RUNNING" | "PASS" | "WARNING" | "FAIL" | "BLOCKED" | "AWAITING_APPROVAL" | "STALE";
export type ActivityResultState = "VERIFIED" | "UNKNOWN" | "NOT_APPLICABLE" | "STALE" | "UNAVAILABLE" | "FAILED" | null;

export type ConversationActivityItem = {
  id: string;
  kind: ActivityKind;
  title: string;
  status: ActivityStatus;
  result_state: ActivityResultState;
  not_applicable: boolean;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  capability: string | null;
  tool: string | null;
  operation: string | null;
  summary: string | null;
  evidence_refs: string[];
  artifact_refs: string[];
  approval: { status: ActivityStatus; label: string } | null;
  children: ConversationActivityItem[];
  developer_detail: string | null;
};

type TraceGroup = {
  id: string;
  kind: ActivityKind;
  title: string;
  capability: string;
  stages: string[];
};

const traceGroups: TraceGroup[] = [
  { id: "route", kind: "PLANNING", title: "选择处理路径", capability: "任务理解", stages: ["UI_SUBMIT", "CHAT_REQUEST_RECEIVED", "PROJECT_RESOLVE", "CONVERSATION_RESOLVE", "ROUTE_SELECT"] },
  { id: "context", kind: "PLANNING", title: "准备工程上下文", capability: "工程上下文", stages: ["CONTEXT_BUILD", "SOURCE_SNAPSHOT"] },
  { id: "evidence", kind: "RESEARCH", title: "检索工程证据", capability: "工程证据", stages: ["FIRMWARE_PARSE", "EVIDENCE_BUILD", "EVIDENCE_GATE"] },
  { id: "answer", kind: "EXECUTION", title: "生成回答", capability: "对话回答", stages: ["APPLICATION_RUNTIME_ENTER", "PROVIDER_RESOLVE", "MODEL_GATEWAY", "ANSWER_PERSIST", "SSE_TERMINAL", "UI_PROJECTION"] },
  { id: "transmission-approval", kind: "APPROVAL", title: "等待外发审批", capability: "外部知识检索", stages: ["TRANSMISSION_APPROVAL"] },
];

const stageLabels: Record<string, string> = {
  UI_SUBMIT: "接收工程问题",
  CHAT_REQUEST_RECEIVED: "接收聊天请求",
  PROJECT_RESOLVE: "确认工程范围",
  CONVERSATION_RESOLVE: "加载对话上下文",
  ROUTE_SELECT: "选择处理路径",
  CONTEXT_BUILD: "构建工程上下文",
  SOURCE_SNAPSHOT: "读取工程快照",
  FIRMWARE_PARSE: "解析固件证据",
  EVIDENCE_BUILD: "整理工程证据",
  EVIDENCE_GATE: "检查证据充分性",
  TRANSMISSION_APPROVAL: "检查外发审批",
  APPLICATION_RUNTIME_ENTER: "进入工程分析",
  PROVIDER_RESOLVE: "确认分析服务",
  MODEL_GATEWAY: "调用分析服务",
  ANSWER_PERSIST: "保存回答结果",
  SSE_TERMINAL: "完成回答流",
  UI_PROJECTION: "投影到对话",
};

function upper(value: string | null | undefined): string {
  return (value ?? "").trim().toUpperCase();
}

function resultStateForTrace(event: ChatTraceEvent): ActivityResultState {
  const gate = upper(event.gate_result);
  const source = upper(event.source_status);
  const reason = upper(event.reason_code);
  if (gate.includes("FAIL") || event.terminal_event === "ANSWER_FAILED") return "FAILED";
  if (gate === "UNKNOWN" || source === "UNKNOWN" || reason.includes("UNKNOWN")) return "UNKNOWN";
  if (gate === "STALE" || source === "STALE" || reason.includes("STALE")) return "STALE";
  if (gate === "UNAVAILABLE" || source === "UNAVAILABLE") return "UNAVAILABLE";
  if (gate === "NOT_APPLICABLE" || source === "NOT_APPLICABLE" || reason === "NOT_APPLICABLE") return "NOT_APPLICABLE";
  const greenfield = ["GREENFIELD", "GREENFIELD_DISCOVERY", "GREENFIELD_DESIGN"].includes(upper(event.route));
  if (greenfield && (
    (event.stage === "SOURCE_SNAPSHOT" && gate === "EMPTY_PROJECT") ||
    (["FIRMWARE_PARSE", "EVIDENCE_GATE"].includes(event.stage) && gate === "NOT_APPLICABLE_FOR_GREENFIELD") ||
    (event.stage === "EVIDENCE_BUILD" && gate === "GREENFIELD_CONTEXT")
  )) return "NOT_APPLICABLE";
  if (gate === "PASS" || (event.stage === "PROVIDER_RESOLVE" && gate === "AVAILABLE") ||
    (event.stage === "SSE_TERMINAL" && gate === "TERMINAL" && event.terminal_event === "ANSWER_COMPLETED")) return "VERIFIED";
  return null;
}

function statusForTrace(event: ChatTraceEvent, resultState: ActivityResultState): ActivityStatus {
  const reason = upper(event.reason_code);
  if (event.stage === "TRANSMISSION_APPROVAL" && (reason.includes("APPROVAL") || upper(event.gate_result) === "FAIL")) return "AWAITING_APPROVAL";
  if (resultState === "STALE") return "STALE";
  if (resultState === "UNKNOWN" || resultState === "UNAVAILABLE") return "WARNING";
  if (resultState === "FAILED") return "FAIL";
  if (resultState === "NOT_APPLICABLE" || resultState === "VERIFIED") return "PASS";
  return "PENDING";
}

function statusRank(status: ActivityStatus): number {
  return { FAIL: 7, STALE: 6, AWAITING_APPROVAL: 5, BLOCKED: 5, WARNING: 4, RUNNING: 3, PENDING: 2, PASS: 1 }[status];
}

function traceChild(group: TraceGroup, event: ChatTraceEvent): ConversationActivityItem {
  const resultState = resultStateForTrace(event);
  return {
    id: `${group.id}-${event.stage}`,
    kind: group.kind,
    title: stageLabels[event.stage] ?? "工程步骤",
    status: statusForTrace(event, resultState),
    result_state: resultState,
    not_applicable: resultState === "NOT_APPLICABLE",
    started_at: event.timestamp,
    completed_at: event.timestamp,
    duration_ms: null,
    capability: group.capability,
    tool: null,
    operation: null,
    summary: event.evidence_count === null ? null : `关联 ${event.evidence_count} 项工程证据`,
    evidence_refs: [],
    artifact_refs: [],
    approval: statusForTrace(event, resultState) === "AWAITING_APPROVAL" ? { status: "AWAITING_APPROVAL", label: "等待你的批准" } : null,
    children: [],
    developer_detail: event.service_identity ?? event.agent_identity,
  };
}

function traceGroupItem(group: TraceGroup, events: ChatTraceEvent[]): ConversationActivityItem | null {
  if (!events.length) return null;
  const children = events.map((event) => traceChild(group, event));
  const latest = events.at(-1)!;
  const status = children.reduce<ActivityStatus>((current, item) => statusRank(item.status) > statusRank(current) ? item.status : current, "PASS");
  const resultState = children.some((item) => item.result_state === "FAILED") ? "FAILED" : children.some((item) => item.result_state === "STALE") ? "STALE" : children.some((item) => item.result_state === "UNKNOWN") ? "UNKNOWN" : children.some((item) => item.result_state === "UNAVAILABLE") ? "UNAVAILABLE" : children.some((item) => item.result_state === null) ? null : children.every((item) => item.result_state === "NOT_APPLICABLE") ? "NOT_APPLICABLE" : "VERIFIED";
  return {
    id: group.id,
    kind: group.kind,
    title: group.title,
    status,
    result_state: resultState,
    not_applicable: resultState === "NOT_APPLICABLE",
    started_at: events[0].timestamp,
    completed_at: latest.terminal_event ? latest.timestamp : null,
    duration_ms: null,
    capability: group.capability,
    tool: null,
    operation: null,
    summary: status === "PASS" && resultState === "NOT_APPLICABLE" ? "当前工程不适用此项" : children.some((item) => item.evidence_refs.length > 0) ? "已关联工程证据" : null,
    evidence_refs: [],
    artifact_refs: [],
    approval: children.find((item) => item.approval)?.approval ?? null,
    children,
    developer_detail: latest.service_identity ?? latest.agent_identity,
  };
}

function operationTitle(operationType: string | undefined): { title: string; capability: string; tool: string | null; operation: string } {
  switch (operationType) {
    case "KICAD_SCHEMATIC_ERC": return { title: "原理图验证", capability: "Schematic Verification", tool: "KiCad", operation: "sch erc" };
    case "KICAD_SCHEMATIC_WRITE": return { title: "生成原理图", capability: "Schematic Generation", tool: "KiCad", operation: "schematic write" };
    case "BUILD": return { title: "固件构建", capability: "Firmware Build", tool: null, operation: "build" };
    case "TEST": return { title: "工程验证", capability: "Verification", tool: null, operation: "test" };
    case "FLASH": return { title: "固件烧录", capability: "Firmware Flash", tool: null, operation: "flash" };
    case "HIL": return { title: "硬件在环验证", capability: "HIL Validation", tool: null, operation: "HIL" };
    case "UART_OBSERVATION": return { title: "设备观测", capability: "Device Observation", tool: null, operation: "UART observation" };
    default: return { title: "受控工程操作", capability: "Controlled Execution", tool: null, operation: "UNAVAILABLE" };
  }
}

function executionStatus(event: ExecutionOperationEvent): ActivityStatus {
  switch (event.event_type) {
    case "OPERATION_STARTED": return "RUNNING";
    case "OPERATION_SUCCEEDED": return "PASS";
    case "OPERATION_FAILED":
    case "OPERATION_TIMED_OUT": return "FAIL";
    case "APPROVAL_ISSUED": return "AWAITING_APPROVAL";
    case "RECOVERY_REQUIRED": return "BLOCKED";
    case "OPERATION_CANCELLED": return "WARNING";
    default: return "PENDING";
  }
}

function executionChild(parent: ConversationActivityItem, event: ExecutionOperationEvent): ConversationActivityItem {
  const status = executionStatus(event);
  return {
    id: `${parent.id}-${event.event_id ?? event.event_type}`,
    kind: parent.kind,
    title: event.event_type === "OPERATION_STARTED" ? "开始执行" : event.event_type === "OPERATION_SUCCEEDED" ? "执行完成" : event.event_type === "OPERATION_FAILED" ? "执行失败" : event.event_type === "APPROVAL_ISSUED" ? "等待审批" : "执行状态更新",
    status,
    result_state: status === "PASS" ? "VERIFIED" : status === "FAIL" ? "FAILED" : null,
    not_applicable: false,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    capability: parent.capability,
    tool: parent.tool,
    operation: parent.operation,
    summary: null,
    evidence_refs: event.receipt_fingerprint ? [event.receipt_fingerprint] : [],
    artifact_refs: [],
    approval: status === "AWAITING_APPROVAL" ? { status, label: "等待你的批准" } : null,
    children: [],
    developer_detail: event.reason_code,
  };
}

export function projectExecutionActivities(events: readonly ExecutionOperationEvent[]): ConversationActivityItem[] {
  const groups = new Map<string, ExecutionOperationEvent[]>();
  for (const event of events) {
    if (!event.operation_id) continue;
    groups.set(event.operation_id, [...(groups.get(event.operation_id) ?? []), event]);
  }
  return [...groups.entries()].map(([operationId, operationEvents]) => {
    const latest = operationEvents.at(-1)!;
    const descriptor = operationTitle(latest.operation_type ?? operationEvents.find((item) => item.operation_type)?.operation_type);
    const status = executionStatus(latest);
    const parent: ConversationActivityItem = {
      id: `execution-${operationId}`,
      kind: status === "FAIL" ? "ERROR" : status === "AWAITING_APPROVAL" ? "APPROVAL" : "EXECUTION",
      title: descriptor.title,
      status,
      result_state: status === "PASS" ? "VERIFIED" : status === "FAIL" ? "FAILED" : null,
      not_applicable: false,
      started_at: null,
      completed_at: status === "PASS" || status === "FAIL" ? null : null,
      duration_ms: null,
      capability: descriptor.capability,
      tool: descriptor.tool,
      operation: descriptor.operation,
      summary: status === "PASS" ? "工程操作已完成" : status === "FAIL" ? "工程操作未通过" : status === "AWAITING_APPROVAL" ? "等待你的批准" : null,
      evidence_refs: latest.receipt_fingerprint ? [latest.receipt_fingerprint] : [],
      artifact_refs: [],
      approval: status === "AWAITING_APPROVAL" ? { status, label: "等待你的批准" } : null,
      children: [],
      developer_detail: operationId,
    };
    parent.children = operationEvents.map((event) => executionChild(parent, event));
    return parent;
  });
}

function evidenceState(status: string): { status: ActivityStatus; result_state: ActivityResultState; not_applicable: boolean } {
  const normalized = upper(status);
  if (normalized === "UNKNOWN") return { status: "WARNING", result_state: "UNKNOWN", not_applicable: false };
  if (["STALE", "SUPERSEDED", "INVALIDATED"].includes(normalized)) return { status: "STALE", result_state: "STALE", not_applicable: false };
  if (["NOT_APPLICABLE", "N/A"].includes(normalized)) return { status: "PASS", result_state: "NOT_APPLICABLE", not_applicable: true };
  if (["FAIL", "FAILED", "REJECTED"].includes(normalized)) return { status: "FAIL", result_state: "FAILED", not_applicable: false };
  if (["UNAVAILABLE"].includes(normalized)) return { status: "WARNING", result_state: "UNAVAILABLE", not_applicable: false };
  return { status: "PASS", result_state: "VERIFIED", not_applicable: false };
}

export function projectEvidenceActivity(evidence: readonly ChatEvidence[]): ConversationActivityItem | null {
  if (!evidence.length) return null;
  const children = evidence.map((item) => {
    const state = evidenceState(item.status);
    return {
      id: `evidence-${item.reference_id}`,
      kind: "VERIFICATION" as const,
      title: item.label,
      status: state.status,
      result_state: state.result_state,
      not_applicable: state.not_applicable,
      started_at: item.observed_at,
      completed_at: item.observed_at,
      duration_ms: null,
      capability: item.source_type,
      tool: null,
      operation: null,
      summary: item.summary,
      evidence_refs: [item.reference_id, item.source_fingerprint],
      artifact_refs: [],
      approval: null,
      children: [],
      developer_detail: item.source_uri ?? null,
    } satisfies ConversationActivityItem;
  });
  const parentStatus = children.reduce<ActivityStatus>((current, item) => statusRank(item.status) > statusRank(current) ? item.status : current, "PASS");
  const parentResult = children.some((item) => item.result_state === "FAILED") ? "FAILED" : children.some((item) => item.result_state === "STALE") ? "STALE" : children.some((item) => item.result_state === "UNKNOWN") ? "UNKNOWN" : children.some((item) => item.result_state === "UNAVAILABLE") ? "UNAVAILABLE" : children.every((item) => item.not_applicable) ? "NOT_APPLICABLE" : "VERIFIED";
  return {
    id: "engineering-evidence",
    kind: parentStatus === "FAIL" ? "ERROR" : "VERIFICATION",
    title: "工程证据",
    status: parentStatus,
    result_state: parentResult,
    not_applicable: parentResult === "NOT_APPLICABLE",
    started_at: evidence[0].observed_at,
    completed_at: evidence.at(-1)?.observed_at ?? null,
    duration_ms: null,
    capability: "Engineering Evidence",
    tool: null,
    operation: null,
    summary: `已关联 ${evidence.length} 项工程证据`,
    evidence_refs: evidence.map((item) => item.reference_id),
    artifact_refs: [],
    approval: null,
    children,
    developer_detail: null,
  };
}

function projectArtifacts(artifactRefs: readonly string[]): ConversationActivityItem | null {
  if (!artifactRefs.length) return null;
  const children = artifactRefs.map((reference) => ({
    id: `artifact-${reference}`,
    kind: "ARTIFACT" as const,
    title: reference.split(/[\\/]/u).at(-1) ?? reference,
    status: "PASS" as const,
    result_state: "VERIFIED" as const,
    not_applicable: false,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    capability: "Engineering Artifact",
    tool: null,
    operation: null,
    summary: null,
    evidence_refs: [],
    artifact_refs: [reference],
    approval: null,
    children: [],
    developer_detail: reference,
  } satisfies ConversationActivityItem));
  return {
    id: "engineering-artifacts",
    kind: "ARTIFACT",
    title: "工程产物",
    status: "PASS",
    result_state: "VERIFIED",
    not_applicable: false,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    capability: "Engineering Artifact",
    tool: null,
    operation: null,
    summary: `已关联 ${artifactRefs.length} 个工程产物`,
    evidence_refs: [],
    artifact_refs: [...artifactRefs],
    approval: null,
    children,
    developer_detail: null,
  };
}

export function projectConversationActivities(input: {
  traceEvents: readonly ChatTraceEvent[];
  executionEvents: readonly ExecutionOperationEvent[];
  evidence: readonly ChatEvidence[];
  artifactRefs: readonly string[];
  pending?: boolean;
  streamStatus?: "idle" | "started" | "streaming" | "clarification" | "completed" | "failed" | "resync";
  awaitingApproval?: boolean;
}): ConversationActivityItem[] {
  const activities = traceGroups
    .map((group) => traceGroupItem(group, input.traceEvents.filter((event) => group.stages.includes(event.stage))))
    .filter((item): item is ConversationActivityItem => item !== null);
  const execution = projectExecutionActivities(input.executionEvents);
  const evidenceItem = projectEvidenceActivity(input.evidence);
  const artifactItem = projectArtifacts(input.artifactRefs);
  const result = [...activities, ...execution, ...(evidenceItem ? [evidenceItem] : []), ...(artifactItem ? [artifactItem] : [])];
  if (input.awaitingApproval && !result.some((item) => item.status === "AWAITING_APPROVAL")) {
    result.push({ id: "approval-pending", kind: "APPROVAL", title: "等待你的批准", status: "AWAITING_APPROVAL", result_state: null, not_applicable: false, started_at: null, completed_at: null, duration_ms: null, capability: null, tool: null, operation: null, summary: "继续执行前需要你的确认", evidence_refs: [], artifact_refs: [], approval: { status: "AWAITING_APPROVAL", label: "等待你的批准" }, children: [], developer_detail: null });
  }
  if (input.pending && (input.streamStatus === "started" || input.streamStatus === "streaming")) {
    const current = result.find((item) => item.id === "answer");
    if (current) {
      current.status = "RUNNING";
      current.result_state = null;
      current.completed_at = null;
      current.summary = "正在生成回答";
    } else {
      result.push({ id: "answer", kind: "EXECUTION", title: "生成回答", status: "RUNNING", result_state: null, not_applicable: false, started_at: null, completed_at: null, duration_ms: null, capability: "对话回答", tool: null, operation: null, summary: "正在生成回答", evidence_refs: [], artifact_refs: [], approval: null, children: [], developer_detail: null });
    }
  }
  if (input.pending && input.streamStatus === "clarification") {
    result.push({ id: "clarification", kind: "WARNING", title: "等待补充信息", status: "BLOCKED", result_state: "UNKNOWN", not_applicable: false, started_at: null, completed_at: null, duration_ms: null, capability: "任务理解", tool: null, operation: null, summary: "尚不能确定需要分析的工程范围", evidence_refs: [], artifact_refs: [], approval: null, children: [], developer_detail: null });
  }
  if (input.pending && (input.streamStatus === "failed" || input.streamStatus === "resync")) {
    result.push({ id: "stream-error", kind: "ERROR", title: "执行未完成", status: "FAIL", result_state: "FAILED", not_applicable: false, started_at: null, completed_at: null, duration_ms: null, capability: null, tool: null, operation: null, summary: "当前执行未能完成", evidence_refs: [], artifact_refs: [], approval: null, children: [], developer_detail: null });
  }
  return result;
}
