import type { DesktopSafeItem, DesktopWorkspaceProjection } from "./api/desktopWorkspaceClient";
import type { ConversationDetail } from "./api/projectClient";
import type { EngineeringMemoryProjection } from "./memory/memoryViewModel";
import type { EngineeringTaskHistorySummary, EngineeringTaskProjection, EngineeringTaskStatus, ConversationTaskStatus } from "./task/engineeringTaskModel";
import { conversationTaskStatus, engineeringTaskStatus, taskIdentity } from "./task/engineeringTaskModel";
import type { WorkflowView, WorkflowStageStatus } from "./workflow/engineeringWorkflowModel";
import { workflowIdentity, WORKFLOW_STAGES, WORKFLOW_STAGE_TITLES } from "./workflow/engineeringWorkflowModel";

function taskStatus(status: string): EngineeringTaskStatus {
  if (status === "FAILED" || status === "VALIDATION_FAILED") return "failed";
  if (["EVIDENCE_COLLECTED", "VALIDATION_RECORDED", "FINDING_CREATED", "WAIT_REVIEW", "APPROVED", "CHANGE_PREVIEW_READY", "APPLY_APPROVAL_REQUIRED", "APPLIED"].includes(status)) return "completed";
  if (status === "UNAVAILABLE" || status === "NO_PROJECT" || status === "EXECUTION_UNAVAILABLE") return "created";
  return "running";
}

function conversationStatus(status: EngineeringTaskStatus): ConversationTaskStatus {
  return status === "completed" ? "completed" : status === "running" ? "active" : "not_started";
}

function itemTitle(item: DesktopSafeItem | undefined, fallback: string): string {
  return item?.summary?.trim() || fallback;
}

export function projectMemoryProjection(projection: DesktopWorkspaceProjection): EngineeringMemoryProjection {
  const items = projection.memory.items.map((item) => ({
    id: item.reference_id,
    title: itemTitle(item, "Engineering memory"),
    summary: item.summary ?? "Memory projection unavailable.",
    source: "evidence" as const,
    createdAt: projection.generated_at,
    fingerprint: item.fingerprint ?? undefined,
    confidence: item.confidence !== null && item.confidence >= 0.7 ? "confirmed" as const : "candidate" as const,
    relatedEvidenceIds: item.source_references,
  }));
  const stale = projection.memory.items.some((item) => item.status === "STALE");
  return { projectId: projection.identity.project_id, state: items.length ? stale ? "stale" : "available" : "empty", items };
}

export function projectTaskProjection(projection: DesktopWorkspaceProjection, conversationId: string | null, conversation: ConversationDetail | null = null): EngineeringTaskProjection | null {
  const reference = conversationId ? projection.conversations.items.find((item) => item.reference_id === conversationId) : undefined;
  const status = conversation ? engineeringTaskStatus(conversation) : taskStatus(projection.workflow_summary.status);
  if (!conversationId) return null;
  const activeConversationId = conversationId ?? projection.workflow_summary.current_reference ?? "workspace";
  return {
    identity: taskIdentity(projection.identity.project_id, activeConversationId, itemTitle(reference, "工程任务"), status, { updatedAt: projection.generated_at }),
    conversationStatus: conversation ? conversationTaskStatus(conversation) : conversationStatus(status),
    evidenceCount: projection.evidence.items.length,
    memoryCount: projection.memory.items.length,
  };
}

export function projectTaskSummaries(projection: DesktopWorkspaceProjection): EngineeringTaskHistorySummary[] {
  const status = taskStatus(projection.workflow_summary.status);
  return projection.conversations.items.map((item) => ({
    projectId: projection.identity.project_id,
    conversationId: item.reference_id,
    title: itemTitle(item, "新对话"),
    status: conversationStatus(status),
    evidenceCount: projection.evidence.items.length,
    memoryCount: projection.memory.items.length,
    updatedAt: projection.generated_at,
  }));
}

export function projectWorkflow(projection: DesktopWorkspaceProjection): WorkflowView {
  const workflowStatus: WorkflowView["status"] = projection.workflow_summary.status === "APPLIED" ? "finished" : "idle";
  const stageStatus: WorkflowStageStatus = workflowStatus === "finished" ? "finished" : "pending";
  const counts: Record<string, number> = { hardware: projection.evidence.items.length, verification: projection.validations.items.length };
  return {
    id: workflowIdentity(projection.identity.project_id, projection.workflow_summary.current_reference ?? "workspace"),
    taskTitle: "工程任务",
    status: workflowStatus,
    stages: WORKFLOW_STAGES.map((stage) => ({ stage, title: WORKFLOW_STAGE_TITLES[stage], status: stageStatus, ...(counts[stage] ? { relatedCount: counts[stage] } : {}) })),
  };
}
