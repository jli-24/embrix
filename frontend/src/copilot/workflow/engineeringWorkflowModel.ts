export const WORKFLOW_STAGES = [
  "requirement",
  "architecture",
  "hardware",
  "firmware",
  "verification",
] as const;

export type WorkflowStage = typeof WORKFLOW_STAGES[number];

export type WorkflowStatus =
  | "idle"
  | "running"
  | "finished"
  | "failed";

export type WorkflowStageStatus = "pending" | "ready" | "finished";

export interface WorkflowView {
  id: string;
  taskTitle: string;
  status: WorkflowStatus;
  stages: WorkflowStageView[];
}

export interface WorkflowStageView {
  stage: WorkflowStage;
  title: string;
  description?: string;
  status: WorkflowStageStatus;
  relatedCount?: number;
}

export const WORKFLOW_STAGE_TITLES: Record<WorkflowStage, string> = {
  requirement: "需求",
  architecture: "架构",
  hardware: "硬件",
  firmware: "固件",
  verification: "验证",
};

export function workflowIdentity(projectId: string, conversationId: string): string {
  return `workflow:${projectId}:${conversationId}`;
}

export function createWorkflowStages(status: WorkflowStageStatus = "pending", relatedCounts: Partial<Record<WorkflowStage, number>> = {}): WorkflowStageView[] {
  return WORKFLOW_STAGES.map((stage) => ({
    stage,
    title: WORKFLOW_STAGE_TITLES[stage],
    status,
    ...(relatedCounts[stage] !== undefined && relatedCounts[stage] > 0 ? { relatedCount: relatedCounts[stage] } : {}),
  }));
}
