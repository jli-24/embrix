import type { EngineeringContextProjection, ProjectWorkspaceIdentity, ProjectWorkspaceNavigationItem } from "../projectWorkspaceIdentity";

const activeProjectIdentity: ProjectWorkspaceIdentity = {
  id: "car-001",
  name: "循迹智能小车",
  type: "STM32",
  platform: "STM32",
  version: null,
  branch: null,
  lifecycle: "ready",
  conversationState: "none",
};

const navigationItem: ProjectWorkspaceNavigationItem = {
  identity: activeProjectIdentity,
  lastActivityAt: "2026-08-12T00:00:00Z",
};

const contextProjection: EngineeringContextProjection = {
  projectId: activeProjectIdentity.id,
  references: [],
  evidenceSources: [],
  resources: [
    { capability: "Project Files", status: "AVAILABLE" },
    { capability: "Knowledge", status: "AVAILABLE" },
    { capability: "HIL", status: "UNAVAILABLE" },
    { capability: "EDA", status: "UNAVAILABLE" },
  ],
};

export const projectReadyWorkspaceFixture = {
  activeProjectIdentity,
  navigationItem,
  conversation: null,
  contextProjection,
};
