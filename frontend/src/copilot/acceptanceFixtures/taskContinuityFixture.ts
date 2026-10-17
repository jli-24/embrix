import { createWorkflowStages, workflowIdentity, type WorkflowView } from "../workflow/engineeringWorkflowModel";
import type { EngineeringContextProjection, ProjectWorkspaceIdentity, ProjectWorkspaceNavigationItem } from "../projectWorkspaceIdentity";
import type { EngineeringTaskHistorySummary, EngineeringTaskProjection } from "../task/engineeringTaskModel";

const projectId = "car-001";
const conversationId = "conversation-i2c-001";
const taskTitle = "为什么 I2C 读取失败？";

const projectIdentity: ProjectWorkspaceIdentity = {
  id: projectId,
  name: "循迹智能小车",
  type: "STM32",
  platform: "STM32",
  version: null,
  branch: null,
  lifecycle: "ready",
  conversationState: "completed",
};

const navigationItem: ProjectWorkspaceNavigationItem = {
  identity: projectIdentity,
  lastActivityAt: "2026-08-12T10:00:00Z",
};

const taskIdentity = {
  projectId,
  conversationId,
  title: taskTitle,
  status: "completed" as const,
  createdAt: "2026-08-12T09:30:00Z",
  updatedAt: "2026-08-12T10:00:00Z",
};

const taskProjection: EngineeringTaskProjection = {
  identity: taskIdentity,
  conversationStatus: "completed",
  evidenceCount: 3,
  memoryCount: 1,
};

const workflowProjection: WorkflowView = {
  id: workflowIdentity(projectId, conversationId),
  taskTitle,
  status: "finished",
  stages: createWorkflowStages("finished", { hardware: 3, verification: 1 }).map((stage) => ({
    ...stage,
    description: stage.stage === "hardware" ? "已关联工程证据" : stage.stage === "verification" ? "已形成验证投影" : undefined,
  })),
};

const taskSummaries: EngineeringTaskHistorySummary[] = [
  { projectId, conversationId, title: taskTitle, status: "completed", evidenceCount: 3, memoryCount: 1, updatedAt: "2026-08-12T10:00:00Z", turnCount: 1 },
  { projectId, conversationId: "conversation-can-002", title: "CAN 通讯异常", status: "active", updatedAt: "2026-08-12T09:15:00Z", turnCount: 1 },
];

const contextProjection: EngineeringContextProjection = {
  projectId,
  references: [
    { category: "Firmware", count: 2 },
    { category: "Hardware", count: 1 },
    { category: "Documents", count: 1 },
  ],
  evidenceSources: [
    { source: "HIL", count: 2 },
    { source: "Firmware", count: 3 },
    { source: "Memory", count: 1 },
  ],
  resources: [
    { capability: "Project Files", status: "AVAILABLE" },
    { capability: "Knowledge", status: "AVAILABLE" },
    { capability: "HIL", status: "OBSERVED" },
    { capability: "EDA", status: "UNAVAILABLE" },
  ],
};

export const taskContinuityWorkspaceFixture = {
  projectId,
  conversationId,
  projectIdentity,
  navigationItem,
  taskIdentity,
  taskProjection,
  workflowProjection,
  taskSummaries,
  contextProjection,
  memoryCount: 1,
};
