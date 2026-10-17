import type { EnvironmentProject } from "../api/environmentClient";
import type { WorkspaceSnapshot } from "../api/workspaceClient";

export type DemoArtifactGroup = "Firmware" | "Hardware" | "Documents" | "Tests";

export type DemoArtifact = {
  type: DemoArtifactGroup;
  name: string;
  kind: "folder" | "file";
};

export type DemoProject = {
  projectId: string;
  projectName: string;
  status: string;
  lastUpdate: string;
  artifacts: DemoArtifact[];
};

export const demoProjects: DemoProject[] = [
  {
    projectId: "esp32-s3-sensor-gateway",
    projectName: "ESP32-S3 Sensor Gateway",
    status: "ACTIVE",
    lastUpdate: "2 minutes ago",
    artifacts: [
      { type: "Firmware", name: "main/", kind: "folder" },
      { type: "Firmware", name: "components/", kind: "folder" },
      { type: "Firmware", name: "CMakeLists.txt", kind: "file" },
      { type: "Hardware", name: "pcb/", kind: "folder" },
      { type: "Hardware", name: "schematic/", kind: "folder" },
      { type: "Hardware", name: "bom/", kind: "folder" },
      { type: "Documents", name: "requirements.md", kind: "file" },
      { type: "Documents", name: "architecture.md", kind: "file" },
      { type: "Tests", name: "hil/", kind: "folder" },
      { type: "Tests", name: "test_plan.md", kind: "file" },
    ],
  },
  { projectId: "power-monitor", projectName: "Power Monitor", status: "ACTIVE", lastUpdate: "3 days ago", artifacts: [] },
  { projectId: "ble-tracker", projectName: "BLE Tracker", status: "ACTIVE", lastUpdate: "1 week ago", artifacts: [] },
];

export const demoEnvironmentProjects: EnvironmentProject[] = demoProjects.map((project) => ({
  environment: { tenant_id: "desktop-tenant", status: "UI_PREVIEW" },
  project: {
    project_id: project.projectId,
    project_name_reference: project.projectName,
    workspace_reference: `ui-preview:${project.projectId}`,
    status: project.status,
  },
}));

const demoTypeByGroup: Record<DemoArtifactGroup, string> = {
  Firmware: "FIRMWARE_SOURCE",
  Hardware: "PCB_BOARD",
  Documents: "ENGINEERING_DOCUMENT",
  Tests: "HIL_TEST",
};

export const demoWorkspaceProjection: WorkspaceSnapshot = {
  project_id: "esp32-s3-sensor-gateway",
  status: "PROJECTED",
  fingerprint: `sha256:${"d".repeat(64)}`,
  artifacts: demoProjects[0].artifacts.map((artifact, index) => ({
    artifact_id: `ui-preview-artifact-${index + 1}`,
    artifact_type: demoTypeByGroup[artifact.type],
    status: "PROPOSED" as const,
    filenames: [artifact.name],
  })),
};
