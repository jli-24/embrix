import type { ContextReference, ProjectReference, RuntimePanel } from "../api/chatClient";
import type {
  ConversationDetail,
  ConversationSummary,
  ConversationTurn,
  ProjectContext,
  ProjectView,
} from "../api/projectClient";

export type AcceptanceWorkspaceFixture = {
  projects: ProjectView[];
  activeProjectId: string;
  project: ProjectView | null;
  context: ProjectContext | null;
  conversations: ConversationSummary[];
  conversation: ConversationDetail | null;
  chatProject: ProjectReference | null;
  references: ContextReference[];
  runtimePanel: RuntimePanel | null;
  sessionId: string | null;
  chatAvailable: boolean;
  catalogError: string | null;
};

const fingerprint = (value: string) => `sha256:${value.repeat(64).slice(0, 64)}`;

const activeProject: ProjectView = {
  project_id: "acceptance-project-esp32-s3",
  tenant_id: "acceptance-tenant",
  name: "ESP32-S3 Sensor Gateway",
  location_id: "acceptance-location",
  directory_name: "esp32-s3-sensor-gateway",
  creation_mode: "ATTACH_EXISTING",
  relative_path: "esp32-s3-sensor-gateway",
  project_type: "ESP_IDF",
  initialization_status: "READY",
  platform: "ESP32-S3",
  version: "v2.4.1",
  branch: "main",
  failure_code: null,
  created_at: "2026-08-10T08:30:00Z",
  updated_at: "2026-08-10T14:14:00Z",
  last_activity_at: "2026-08-10T14:14:00Z",
  fingerprint: fingerprint("a"),
};

const projects: ProjectView[] = [
  activeProject,
  { ...activeProject, project_id: "acceptance-project-smart-robot", name: "Smart Robot Controller", directory_name: "smart-robot-controller", relative_path: "smart-robot-controller", platform: "STM32", version: null, last_activity_at: "2026-08-09T18:00:00Z", updated_at: "2026-08-09T18:00:00Z", fingerprint: fingerprint("b") },
  { ...activeProject, project_id: "acceptance-project-power-monitor", name: "Power Monitor System", directory_name: "power-monitor-system", relative_path: "power-monitor-system", platform: "Arduino", version: null, last_activity_at: "2026-08-07T11:00:00Z", updated_at: "2026-08-07T11:00:00Z", fingerprint: fingerprint("c") },
];

const evidence: ConversationTurn["evidence"] = [
  { source_type: "Firmware", label: "sensor_i2c.c", reference_id: "fw_ref_0012", status: "Observed", summary: "I2C driver timeout configuration", source_fingerprint: fingerprint("d"), observed_at: "2026-08-10T14:10:00Z" },
  { source_type: "HIL", label: "I2C timeout observation", reference_id: "hil_obs_20250810", status: "Observed", summary: "I2C timeout observed on ESP32-S3", source_fingerprint: fingerprint("e"), observed_at: "2026-08-10T14:08:00Z" },
  { source_type: "EDA", label: "Power integrity review", reference_id: "eda_003", status: "Reviewed", summary: "Power integrity review reference", source_fingerprint: fingerprint("f"), observed_at: "2026-08-10T13:52:00Z" },
];

const references: ContextReference[] = evidence.map((item) => ({
  tenant_id: activeProject.tenant_id,
  project_id: activeProject.project_id,
  reference_id: item.reference_id,
  reference_kind: "EVIDENCE",
  source_fingerprint: item.source_fingerprint,
  display_label: item.label,
  fingerprint: item.source_fingerprint,
}));

const conversationTurn: ConversationTurn = {
  turn_id: "acceptance-turn-i2c",
  request_id: "acceptance-request-i2c",
  status: "COMPLETED",
  question: "为什么 I2C 读取经常失败？",
  answer_text: "根据当前工程上下文，I2C timeout 可能来自三个区域：Firmware driver timeout、Hardware signal quality 和 bus configuration。建议先对照证据确认驱动超时配置与实际总线波形，再判断是否需要调整采样策略。",
  evidence,
  recommendations: [
    { review: "检查 sensor_i2c.c 的 timeout 配置", reason: "Firmware reference 显示驱动超时设置需要复核。", status: "Proposal", reference_id: "fw_ref_0012", source_fingerprint: fingerprint("d") },
    { review: "复核 SDA/SCL 上拉与信号质量", reason: "HIL observation 与 EDA review 需要联合核对。", status: "Proposal", reference_id: "hil_obs_20250810", source_fingerprint: fingerprint("e") },
    { review: "确认总线配置与传感器启动时序", reason: "在缺少更多观测前保持只读建议。", status: "Proposal", reference_id: "eda_003", source_fingerprint: fingerprint("f") },
  ],
  context_references: references,
  error_code: null,
  created_at: "2026-08-10T14:10:00Z",
  completed_at: "2026-08-10T14:14:00Z",
};

const conversation: ConversationDetail = {
  conversation_id: "acceptance-conversation-i2c",
  project_id: activeProject.project_id,
  title: "为什么 I2C 读取经常失败？",
  turns: [conversationTurn],
  created_at: "2026-08-10T14:10:00Z",
  updated_at: "2026-08-10T14:14:00Z",
  fingerprint: fingerprint("g"),
};

const conversations: ConversationSummary[] = [
  { conversation_id: conversation.conversation_id, project_id: activeProject.project_id, title: conversation.title, turn_count: 1, created_at: conversation.created_at, updated_at: conversation.updated_at, fingerprint: conversation.fingerprint },
  { conversation_id: "acceptance-conversation-drc", project_id: activeProject.project_id, title: "PCB DRC 错误分析", turn_count: 2, created_at: "2026-08-09T10:00:00Z", updated_at: "2026-08-09T11:03:00Z", fingerprint: fingerprint("h") },
  { conversation_id: "acceptance-conversation-camera", project_id: activeProject.project_id, title: "摄像头传输优化", turn_count: 1, created_at: "2026-08-08T16:00:00Z", updated_at: "2026-08-08T16:42:00Z", fingerprint: fingerprint("i") },
];

const context: ProjectContext = {
  project_id: activeProject.project_id,
  status: "READY",
  groups: [
    { category: "Firmware", references: [{ reference_id: "firmware-sensor-i2c", category: "Firmware", display_label: "sensor_i2c.c", source_fingerprint: fingerprint("j"), fingerprint: fingerprint("j") }, { reference_id: "firmware-main", category: "Firmware", display_label: "main.c", source_fingerprint: fingerprint("k"), fingerprint: fingerprint("k") }] },
    { category: "Hardware", references: [{ reference_id: "hardware-board", category: "Hardware", display_label: "board.pcb", source_fingerprint: fingerprint("l"), fingerprint: fingerprint("l") }] },
    { category: "Documents", references: [{ reference_id: "document-architecture", category: "Documents", display_label: "architecture.md", source_fingerprint: fingerprint("m"), fingerprint: fingerprint("m") }] },
  ],
  evidence_sources: [{ source: "HIL", count: 1 }, { source: "EDA", count: 1 }, { source: "Firmware", count: 1 }, { source: "Memory", count: 0 }],
  capabilities: [{ capability: "Project Files", status: "AVAILABLE" }, { capability: "HIL", status: "AVAILABLE" }, { capability: "EDA", status: "AVAILABLE" }, { capability: "Knowledge", status: "AVAILABLE" }],
  generated_at: "2026-08-10T14:14:00Z",
  fingerprint: fingerprint("n"),
};

export const populatedWorkspaceFixture: AcceptanceWorkspaceFixture = {
  projects,
  activeProjectId: activeProject.project_id,
  project: activeProject,
  context,
  conversations,
  conversation,
  chatProject: { tenant_id: activeProject.tenant_id, project_id: activeProject.project_id, project_name_reference: activeProject.name, workspace_reference: "acceptance-project-files", fingerprint: activeProject.fingerprint },
  references,
  runtimePanel: { hil_status: "OBSERVED", eda_status: "OBSERVED", memory_statuses: ["OBSERVED"], runtime_health: [] },
  sessionId: "acceptance-session-i2c",
  chatAvailable: true,
  catalogError: null,
};

export const unavailableWorkspaceFixture: AcceptanceWorkspaceFixture = {
  projects: [],
  activeProjectId: "",
  project: null,
  context: null,
  conversations: [],
  conversation: null,
  chatProject: null,
  references: [],
  runtimePanel: null,
  sessionId: null,
  chatAvailable: false,
  catalogError: "Project Catalog 不可用",
};
