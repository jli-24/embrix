import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatPanel } from "../chat/ChatPanel";
import { ProjectExplorer } from "../project/ProjectExplorer";

const callbacks = {
  onProjectChange: () => undefined,
  onNewProject: () => undefined,
  onNewConversation: () => undefined,
  onConversationChange: () => undefined,
  onToggleSettings: () => undefined,
};

const project = {
  project_id: "project-1",
  tenant_id: "tenant",
  name: "ESP32-S3 Sensor Gateway",
  location_id: "location",
  directory_name: "gateway",
  creation_mode: "CREATE_NEW" as const,
  relative_path: "gateway",
  project_type: "ESP_IDF" as const,
  initialization_status: "READY" as const,
  platform: "ESP32-S3",
  version: null,
  branch: "main",
  failure_code: null,
  created_at: "2026-08-10T14:30:00Z",
  updated_at: "2026-08-10T14:30:00Z",
  last_activity_at: "2026-08-10T14:30:00Z",
  fingerprint: `sha256:${"a".repeat(64)}`,
};

describe("v5.2.5 empty state and hierarchy", () => {
  it("separates catalog unavailable from no projects created", () => {
    const unavailable = renderToStaticMarkup(<ProjectExplorer projects={[]} activeProjectId="" conversations={[]} catalogError="Project Catalog 不可用" catalogState="unavailable" settingsOpen={false} {...callbacks} />);
    expect(unavailable).toContain("Project Catalog 不可用");
    expect(unavailable).toContain("请检查项目服务连接后重试");
    expect(unavailable).not.toContain("尚未创建项目");

    const readyEmpty = renderToStaticMarkup(<ProjectExplorer projects={[]} activeProjectId="" conversations={[]} catalogError={null} catalogState="ready" settingsOpen={false} {...callbacks} />);
    expect(readyEmpty).toContain("尚未创建项目");
    expect(readyEmpty).not.toContain("Project Catalog 不可用");
  });

  it("keeps active Project identity visually explicit", () => {
    const markup = renderToStaticMarkup(<ProjectExplorer projects={[project]} activeProjectId="project-1" conversations={[]} catalogError={null} catalogState="ready" settingsOpen={false} {...callbacks} />);
    expect(markup).toContain("ide-project-row is-active");
    expect(markup).toContain("ESP32-S3 Sensor Gateway");
  });

  it("uses an IDE workspace onboarding state without a hero CTA", () => {
    const markup = renderToStaticMarkup(<ChatPanel title="Copilot 工程分析" projectName={null} sessionReady={false} turns={[]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="expanded" onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} onRetry={() => undefined} />);
    expect(markup).toContain("告诉 Embrix 你想做什么。");
    expect(markup).toContain("从需求开始，我会和你一起完成工程。");
    expect(markup).not.toContain("创建项目");
    expect(markup).not.toContain("hero");
  });
});
