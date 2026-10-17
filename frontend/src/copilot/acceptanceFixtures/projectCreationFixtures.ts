import type { ContextReference } from "../api/chatClient";
import type { ProjectContext, ProjectLocation, ProjectPreview, ProjectView } from "../api/projectClient";
import type { ProjectCreateDataSource } from "../project/ProjectCreatePanel";

const fingerprint = (value: string) => `sha256:${value.repeat(64).slice(0, 64)}`;

const location: ProjectLocation = {
  location_id: "acceptance-default-project-space",
  display_label: "默认工程空间",
  availability: "AVAILABLE",
  fingerprint: fingerprint("location"),
};

const parentLocation: ProjectLocation = {
  location_id: "acceptance-parent-location",
  display_label: "Embedded Projects",
  availability: "AVAILABLE",
  fingerprint: fingerprint("parent-location"),
};

const createdProject: ProjectView = {
  project_id: "acceptance-project-line-car",
  tenant_id: "acceptance-tenant",
  name: "循迹智能小车",
  location_id: location.location_id,
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
  fingerprint: fingerprint("project"),
};

const context: ProjectContext = {
  project_id: createdProject.project_id,
  status: "READY",
  groups: [],
  evidence_sources: [],
  capabilities: [
    { capability: "Project Files", status: "AVAILABLE" },
    { capability: "Knowledge", status: "AVAILABLE" },
  ],
  generated_at: createdProject.updated_at,
  fingerprint: fingerprint("context"),
};

const preview = (name: string): ProjectPreview => ({
  preview_id: "acceptance-project-preview",
  name,
  location,
  existing_directory: null,
  directory_name: name,
  creation_mode: "CREATE_NEW",
  relative_path: name,
  project_type: "STM32",
  mode: "CREATE",
  expires_at: "2099-01-01T00:00:00Z",
  write_summary: ["Project Identity", "Engineering Context", "Conversation Context"],
  fingerprint: fingerprint("preview"),
});

export const projectCreationWorkspaceFixture: {
  dataSource: ProjectCreateDataSource;
  initialProject: ProjectView | null;
  initialContext: ProjectContext | null;
  initialReferences: ContextReference[];
} = {
  dataSource: {
    locations: [location],
    directories: {},
    initial: { name: "循迹智能小车", projectType: "STM32", creationMode: "CREATE_NEW" },
    preview: async (input) => preview(input.name),
    confirm: async () => createdProject,
  },
  initialProject: null,
  initialContext: null,
  initialReferences: [],
};

export const createdProjectWorkspaceFixture = {
  projects: [createdProject],
  activeProjectId: createdProject.project_id,
  project: createdProject,
  context,
  conversations: [],
  conversation: null,
  chatProject: null,
  references: [] as ContextReference[],
  runtimePanel: null,
  sessionId: null,
  chatAvailable: false,
  catalogError: null,
};

const parentCreatedProject: ProjectView = {
  ...createdProject,
  project_id: "acceptance-project-line-car-parent",
  location_id: parentLocation.location_id,
};

export const projectParentCreationWorkspaceFixture: {
  dataSource: ProjectCreateDataSource;
  initialProject: ProjectView | null;
  initialContext: ProjectContext | null;
  initialReferences: ContextReference[];
} = {
  dataSource: {
    locations: [parentLocation],
    directories: {},
    initial: { name: "循迹智能小车", projectType: "STM32", creationMode: "CREATE_NEW" },
    preview: async (input) => ({ ...preview(input.name), location: parentLocation }),
    confirm: async () => parentCreatedProject,
  },
  initialProject: null,
  initialContext: null,
  initialReferences: [],
};

export const createdParentProjectWorkspaceFixture = {
  ...createdProjectWorkspaceFixture,
  projects: [parentCreatedProject],
  activeProjectId: parentCreatedProject.project_id,
  project: parentCreatedProject,
  context: { ...context, project_id: parentCreatedProject.project_id },
};

export type ProjectCreationFixture = typeof projectCreationWorkspaceFixture;
