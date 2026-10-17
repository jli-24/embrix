import type { ConversationDetail } from "../api/projectClient";

export type EngineeringTaskStatus = "created" | "running" | "discovery" | "refining" | "ready" | "completed" | "failed";
export type ConversationTaskStatus = "not_started" | "active" | "discovery" | "refining" | "ready" | "clarification" | "completed" | "unavailable" | "failed";

export interface EngineeringTaskIdentity {
  projectId: string;
  conversationId: string;
  title: string;
  status: EngineeringTaskStatus;
  createdAt?: string;
  updatedAt?: string;
}

export interface EngineeringTaskProjection {
  identity: EngineeringTaskIdentity;
  conversationStatus: ConversationTaskStatus;
  evidenceCount?: number;
  memoryCount?: number;
}

export interface EngineeringTaskHistorySummary {
  projectId: string;
  conversationId: string;
  title: string;
  status?: ConversationTaskStatus;
  evidenceCount?: number;
  memoryCount?: number;
  updatedAt?: string;
  turnCount?: number;
}

export function taskIdentity(projectId: string, conversationId: string, title: string, status: EngineeringTaskStatus = "created", timestamps?: Pick<EngineeringTaskIdentity, "createdAt" | "updatedAt">): EngineeringTaskIdentity {
  return { projectId, conversationId, title, status, ...timestamps };
}

export function conversationTaskStatus(conversation: ConversationDetail | null, pending = false): ConversationTaskStatus {
  if (!conversation) return "not_started";
  if (pending) return "active";
  const latest = conversation.turns[conversation.turns.length - 1];
  if (latest?.status === "PENDING") return "active";
  if (latest?.analysis_state === "DISCOVERY_STARTED" || latest?.analysis_state === "REQUIREMENTS_PARTIAL") return "discovery";
  if (latest?.analysis_state === "REQUIREMENTS_REFINING") return "refining";
  if (latest?.analysis_state === "REQUIREMENTS_READY" || latest?.analysis_state === "DESIGN_READY") return "ready";
  if (latest?.status === "WAITING_FOR_CLARIFICATION" || latest?.analysis_state === "WAITING_FOR_CLARIFICATION") return "clarification";
  if (latest?.analysis_state === "PROVIDER_UNAVAILABLE") return "unavailable";
  if (latest?.analysis_state === "INSUFFICIENT_EVIDENCE") return "failed";
  if (latest?.status === "FAILED") return "failed";
  if (latest?.status === "COMPLETED" || latest?.analysis_state === "COMPLETED") return "completed";
  return "not_started";
}

export function engineeringTaskStatus(conversation: ConversationDetail | null, pending = false): EngineeringTaskStatus {
  if (!conversation) return "created";
  if (pending) return "running";
  const latest = conversation.turns[conversation.turns.length - 1];
  if (latest?.status === "PENDING") return "running";
  if (latest?.analysis_state === "DISCOVERY_STARTED" || latest?.analysis_state === "REQUIREMENTS_PARTIAL") return "discovery";
  if (latest?.analysis_state === "REQUIREMENTS_REFINING") return "refining";
  if (latest?.analysis_state === "REQUIREMENTS_READY" || latest?.analysis_state === "DESIGN_READY") return "ready";
  if (latest?.status === "WAITING_FOR_CLARIFICATION" || latest?.analysis_state === "WAITING_FOR_CLARIFICATION") return "created";
  if (latest?.status === "FAILED") return "failed";
  if (latest?.status === "COMPLETED" || latest?.analysis_state === "COMPLETED") return "completed";
  return "created";
}

export function conversationEvidenceCount(conversation: ConversationDetail | null): number | undefined {
  if (!conversation) return undefined;
  return conversation.turns.filter((turn) => turn.status === "COMPLETED").reduce((count, turn) => count + turn.evidence.length, 0);
}

export function taskStatusLabel(status: ConversationTaskStatus): string {
  if (status === "active") return "● 分析中";
  if (status === "clarification") return "等待需求澄清";
  if (status === "discovery") return "需求探索中";
  if (status === "refining") return "需求完善中";
  if (status === "ready") return "已准备，可继续";
  if (status === "completed") return "✓ 已完成";
  if (status === "unavailable") return "服务暂不可用";
  if (status === "failed") return "处理失败";
  return "等待任务";
}

export function taskSummaryFromConversation(projectId: string, conversation: ConversationDetail, memoryCount?: number): EngineeringTaskHistorySummary {
  return {
    projectId,
    conversationId: conversation.conversation_id,
    title: conversation.title,
    status: conversationTaskStatus(conversation),
    evidenceCount: conversationEvidenceCount(conversation),
    memoryCount,
    updatedAt: conversation.updated_at,
    turnCount: conversation.turns.length,
  };
}
