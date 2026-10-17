import type { ContextReference, RuntimePanel as ChatRuntimePanel } from "./api/chatClient";
import type { ConversationDetail, ProjectContext, ProjectType, ProjectView } from "./api/projectClient";

export type ProjectWorkspaceIdentity = {
  id: string;
  name: string;
  type?: ProjectType;
  platform?: string | null;
  version?: string | null;
  branch?: string | null;
  lifecycle: "created" | "ready";
  conversationState: "none" | "active" | "completed";
};

export type ProjectWorkspaceNavigationItem = {
  identity: ProjectWorkspaceIdentity;
  lastActivityAt: string;
};

export type EngineeringContextProjection = {
  projectId: string;
  references: Array<{ category: string; count: number }>;
  evidenceSources: Array<{ source: string; count: number }>;
  resources: Array<{ capability: string; status: string }>;
};

export function projectTypeLabel(type: ProjectType | undefined): string | null {
  if (!type) return null;
  return ({ ESP_IDF: "ESP-IDF", STM32: "STM32", ARDUINO: "Arduino", GENERIC_FIRMWARE: "Generic Firmware", EXISTING_FIRMWARE: "Existing Firmware" } as Record<ProjectType, string>)[type] ?? null;
}

export function identityPlatform(identity: Pick<ProjectWorkspaceIdentity, "platform" | "type">): string {
  return identity.platform ?? projectTypeLabel(identity.type) ?? "不可用";
}

export function toProjectWorkspaceIdentity(project: ProjectView | null, conversation: ConversationDetail | null, analyzing = false): ProjectWorkspaceIdentity | null {
  if (!project) return null;
  const hasCompletedTurn = Boolean(conversation?.turns.some((turn) => turn.status === "COMPLETED"));
  const hasActiveTurn = analyzing || Boolean(conversation?.turns.some((turn) => turn.status === "PENDING"));
  return {
    id: project.project_id,
    name: project.name,
    type: project.project_type,
    platform: project.platform,
    version: project.version,
    branch: project.branch,
    lifecycle: project.initialization_status === "READY" ? "ready" : "created",
    conversationState: hasCompletedTurn ? "completed" : hasActiveTurn ? "active" : "none",
  };
}

export function toProjectWorkspaceNavigationItem(project: ProjectView, conversation: ConversationDetail | null = null): ProjectWorkspaceNavigationItem {
  return {
    identity: toProjectWorkspaceIdentity(project, conversation) ?? {
      id: project.project_id,
      name: project.name,
      type: project.project_type,
      platform: project.platform,
      version: project.version,
      branch: project.branch,
      lifecycle: "created",
      conversationState: "none",
    },
    lastActivityAt: project.last_activity_at,
  };
}

export function toEngineeringContextProjection(identity: ProjectWorkspaceIdentity | null, context: ProjectContext | null, panel: ChatRuntimePanel | null): EngineeringContextProjection | null {
  if (!identity || !context || context.project_id !== identity.id) return null;
  const resources = new Map((context.capabilities ?? []).map((item) => [item.capability, item.status]));
  if (panel?.hil_status && panel.hil_status !== "UNAVAILABLE") resources.set("HIL", panel.hil_status);
  if (panel?.eda_status && panel.eda_status !== "UNAVAILABLE") resources.set("EDA", panel.eda_status);
  return {
    projectId: identity.id,
    references: context.groups.filter((group) => group.references.length > 0).map((group) => ({ category: group.category, count: group.references.length })),
    evidenceSources: context.evidence_sources.filter((item) => item.count > 0).map((item) => ({ source: item.source, count: item.count })),
    resources: ["Project Files", "Knowledge", "HIL", "EDA"].map((capability) => ({ capability, status: resources.get(capability) ?? "UNAVAILABLE" })),
  };
}
