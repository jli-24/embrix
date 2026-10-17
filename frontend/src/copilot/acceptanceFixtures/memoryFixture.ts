import type { EngineeringMemoryProjection, EngineeringMemoryView } from "../memory/memoryViewModel";
import type { ProjectWorkspaceIdentity, ProjectWorkspaceNavigationItem } from "../projectWorkspaceIdentity";

const activeProjectIdentity: ProjectWorkspaceIdentity = {
  id: "car-001",
  name: "循迹智能小车",
  type: "STM32",
  platform: "STM32",
  version: null,
  branch: null,
  lifecycle: "ready",
  conversationState: "completed",
};

const memories: EngineeringMemoryView[] = [
  {
    id: "memory-001",
    title: "I2C 总线稳定性问题",
    summary: "ESP32-S3 当前 I2C 通信需要降低频率。",
    source: "conversation",
    createdAt: "2026-08-12",
    confidence: "confirmed",
    relatedEvidenceIds: ["hil_obs_20250810", "fw_ref_0012"],
  },
  {
    id: "memory-002",
    title: "PWM 电机控制方案",
    summary: "电机控制采用定时器 PWM 输出。",
    source: "evidence",
    createdAt: "2026-08-11",
    confidence: "candidate",
    relatedEvidenceIds: ["pwm_evidence_001"],
  },
];

export const memoryWorkspaceFixture = {
  activeProjectIdentity,
  navigationItem: {
    identity: activeProjectIdentity,
    lastActivityAt: "2026-08-12T14:20:00Z",
  } satisfies ProjectWorkspaceNavigationItem,
  conversation: { completed: true },
  memoryProjection: {
    projectId: activeProjectIdentity.id,
    state: "available",
    items: memories,
  } satisfies EngineeringMemoryProjection,
};
