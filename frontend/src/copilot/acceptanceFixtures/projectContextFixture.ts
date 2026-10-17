import type { ContextReference } from "../api/chatClient";
import type { ProjectContext, ProjectView } from "../api/projectClient";
import type { AcceptanceWorkspaceFixture } from "./workspaceFixtures";

const fingerprint = (value: string) => `sha256:${value.repeat(64).slice(0, 64)}`;
const tenantId = "acceptance-tenant";
const projectId = "acceptance-project-line-car-context";

const project: ProjectView = {
  project_id: projectId,
  tenant_id: tenantId,
  name: "循迹智能小车",
  location_id: "acceptance-location-context",
  directory_name: "循迹智能小车",
  creation_mode: "CREATE_NEW",
  relative_path: "循迹智能小车",
  project_type: "STM32",
  initialization_status: "READY",
  platform: "STM32",
  version: null,
  branch: null,
  failure_code: null,
  created_at: "2026-08-12T00:00:00Z",
  updated_at: "2026-08-12T00:00:00Z",
  last_activity_at: "2026-08-12T00:00:00Z",
  fingerprint: fingerprint("project-context"),
};

function projectReferences(category: string, count: number): ProjectContext["groups"][number]["references"] {
  return Array.from({ length: count }, (_, index) => ({
    reference_id: `${category.toLowerCase()}-reference-${index + 1}`,
    category,
    display_label: `${category} reference ${index + 1}`,
    source_fingerprint: fingerprint(`${category}-source-${index}`),
    fingerprint: fingerprint(`${category}-reference-${index}`),
  }));
}

const context: ProjectContext = {
  project_id: projectId,
  status: "READY",
  groups: [
    { category: "Firmware", references: projectReferences("Firmware", 3) },
    { category: "Hardware", references: projectReferences("Hardware", 2) },
    { category: "Documents", references: projectReferences("Documents", 5) },
  ],
  evidence_sources: [
    { source: "HIL", count: 2 },
    { source: "Firmware", count: 3 },
    { source: "Memory", count: 1 },
  ],
  capabilities: [
    { capability: "Project Files", status: "AVAILABLE" },
    { capability: "Knowledge", status: "AVAILABLE" },
    { capability: "HIL", status: "UNAVAILABLE" },
    { capability: "EDA", status: "UNAVAILABLE" },
  ],
  generated_at: project.updated_at,
  fingerprint: fingerprint("context-project-context"),
};

const references: ContextReference[] = [
  ...Array.from({ length: 3 }, (_, index) => `Firmware reference ${index + 1}`),
  ...Array.from({ length: 2 }, (_, index) => `Hardware reference ${index + 1}`),
  ...Array.from({ length: 5 }, (_, index) => `Documents reference ${index + 1}`),
  "Engineering Memory reference 1",
].map((displayLabel, index) => ({
  tenant_id: tenantId,
  project_id: projectId,
  reference_id: `context-reference-${index + 1}`,
  reference_kind: displayLabel.startsWith("Engineering Memory") ? "MEMORY" : "WORKSPACE",
  source_fingerprint: fingerprint(`context-source-${index}`),
  display_label: displayLabel,
  fingerprint: fingerprint(`context-reference-${index}`),
}));

export const projectContextWorkspaceFixture: AcceptanceWorkspaceFixture = {
  projects: [project],
  activeProjectId: projectId,
  project,
  context,
  conversations: [],
  conversation: null,
  chatProject: null,
  references,
  runtimePanel: null,
  sessionId: null,
  chatAvailable: false,
  catalogError: null,
};
