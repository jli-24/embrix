import type { ContextReference, RuntimePanel } from "../api/chatClient";
import type { ConversationDetail, ConversationSummary, ConversationTurn } from "../api/projectClient";
import type { EngineeringMemoryProjection } from "../memory/memoryViewModel";
import type { EngineeringContextProjection, ProjectWorkspaceIdentity, ProjectWorkspaceNavigationItem } from "../projectWorkspaceIdentity";
import { createWorkflowStages, workflowIdentity, type WorkflowView } from "../workflow/engineeringWorkflowModel";

const projectId = "car-001";
const conversationId = "conversation-workflow-i2c";
const fingerprint = (value: string) => `sha256:${value.repeat(64).slice(0, 64)}`;

export const activeProjectIdentity: ProjectWorkspaceIdentity = {
  id: projectId,
  name: "循迹智能小车",
  type: "STM32",
  platform: "STM32",
  version: null,
  branch: null,
  lifecycle: "ready",
  conversationState: "completed",
};

export const navigationItem: ProjectWorkspaceNavigationItem = {
  identity: activeProjectIdentity,
  lastActivityAt: "2026-08-12T14:20:00Z",
};

const references: ContextReference[] = [
  "Firmware",
  "Hardware",
  "Documents",
  "Engineering Memory",
  "Firmware Evidence",
  "Hardware Evidence",
  "Memory",
].map((label, index) => ({
  tenant_id: "acceptance-tenant",
  project_id: projectId,
  reference_id: `workflow-reference-${index + 1}`,
  reference_kind: label.endsWith("Evidence") || label === "Memory" ? "EVIDENCE" : label === "Engineering Memory" ? "MEMORY" : "WORKSPACE",
  source_fingerprint: fingerprint(`workflow-source-${index}`),
  display_label: label,
  fingerprint: fingerprint(`workflow-reference-${index}`),
}));

const evidence: ConversationTurn["evidence"] = [
  { source_type: "Firmware", label: "Firmware Evidence", reference_id: "workflow-reference-5", status: "Observed", summary: "已有 Firmware 证据引用", source_fingerprint: fingerprint("workflow-source-4"), observed_at: "2026-08-12T14:15:00Z" },
  { source_type: "EDA", label: "Hardware Evidence", reference_id: "workflow-reference-6", status: "Reviewed", summary: "已有 Hardware 证据引用", source_fingerprint: fingerprint("workflow-source-5"), observed_at: "2026-08-12T14:16:00Z" },
  { source_type: "Memory", label: "Memory", reference_id: "workflow-reference-7", status: "Observed", summary: "已有工程记忆引用", source_fingerprint: fingerprint("workflow-source-6"), observed_at: "2026-08-12T14:17:00Z" },
];

const conversationTurn: ConversationTurn = {
  turn_id: "turn-workflow-i2c",
  request_id: "request-workflow-i2c",
  status: "COMPLETED",
  question: "为什么 I2C 读取失败？",
  answer_text: "当前问题已整理为只读工程分析：优先核对 Firmware I2C 超时配置、Hardware 总线信号质量与已有证据之间的对应关系。以下建议只用于工程复核，不代表硬件已经验证通过。",
  evidence,
  recommendations: [
    { review: "复核 Firmware I2C 超时配置", reason: "对应已有 Firmware Evidence。", status: "Proposal", reference_id: "workflow-reference-5", source_fingerprint: fingerprint("workflow-source-4") },
    { review: "检查 Hardware 总线连接与信号质量", reason: "对应已有 Hardware Evidence。", status: "Proposal", reference_id: "workflow-reference-6", source_fingerprint: fingerprint("workflow-source-5") },
  ],
  context_references: references,
  error_code: null,
  created_at: "2026-08-12T14:10:00Z",
  completed_at: "2026-08-12T14:20:00Z",
};

export const conversation: ConversationDetail = {
  conversation_id: conversationId,
  project_id: projectId,
  title: "I2C 读取失败分析",
  turns: [conversationTurn],
  created_at: "2026-08-12T14:10:00Z",
  updated_at: "2026-08-12T14:20:00Z",
  fingerprint: fingerprint("workflow-conversation"),
};

export const conversations: ConversationSummary[] = [{
  conversation_id: conversationId,
  project_id: projectId,
  title: conversation.title,
  turn_count: conversation.turns.length,
  created_at: conversation.created_at,
  updated_at: conversation.updated_at,
  fingerprint: conversation.fingerprint,
}];

export const contextProjection: EngineeringContextProjection = {
  projectId,
  references: [
    { category: "Firmware", count: 1 },
    { category: "Hardware", count: 1 },
    { category: "Documents", count: 1 },
    { category: "Engineering Memory", count: 1 },
  ],
  evidenceSources: [
    { source: "Firmware", count: 1 },
    { source: "Hardware", count: 1 },
    { source: "Memory", count: 1 },
  ],
  resources: [
    { capability: "Project Files", status: "AVAILABLE" },
    { capability: "Knowledge", status: "AVAILABLE" },
    { capability: "HIL", status: "UNAVAILABLE" },
    { capability: "EDA", status: "AVAILABLE" },
  ],
};

export const memoryProjection: EngineeringMemoryProjection = {
  projectId,
  state: "available",
  items: [{
    id: "memory-workflow-001",
    title: "I2C 读取失败摘要",
    summary: "本次对话形成只读工程摘要。",
    source: "conversation",
    createdAt: "2026-08-12",
    confidence: "candidate",
    relatedEvidenceIds: ["workflow-reference-5", "workflow-reference-6"],
  }],
};

const stages = createWorkflowStages("finished");
stages[0].description = "识别当前工程问题类型：Debug";
stages[1].description = "已整理当前系统边界与架构约束";
stages[2].description = "已关联：Firmware · Hardware · Documents";
stages[3].description = "已关联 Firmware 与工程上下文";
stages[4].description = "已形成只读验证投影";

export const workflowProjection: WorkflowView = {
  id: workflowIdentity(projectId, conversationId),
  taskTitle: "I2C读取失败分析",
  status: "finished",
  stages,
};

export const runtimePanel: RuntimePanel = {
  hil_status: "UNAVAILABLE",
  eda_status: "OBSERVED",
  memory_statuses: ["OBSERVED"],
  runtime_health: [],
};

export const workflowWorkspaceFixture = {
  activeProjectIdentity,
  navigationItem,
  projectId,
  conversationId,
  conversation,
  conversations,
  contextProjection,
  memoryProjection,
  workflowProjection,
  references,
  runtimePanel,
};
