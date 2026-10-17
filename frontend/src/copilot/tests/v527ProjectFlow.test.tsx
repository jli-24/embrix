import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { ChatInput } from "../chat/ChatInput";
import { ChatPanel } from "../chat/ChatPanel";
import { createdParentProjectWorkspaceFixture, createdProjectWorkspaceFixture, projectCreationWorkspaceFixture, projectParentCreationWorkspaceFixture } from "../acceptanceFixtures/projectCreationFixtures";
import { ProjectCreatePanel } from "../project/ProjectCreatePanel";
import type { ProjectPreview, ProjectView } from "../api/projectClient";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import { RuntimePanel } from "../runtime/RuntimePanel";

const callbacks = {
  onToggleReference: () => undefined,
  onAttach: () => undefined,
  onSubmit: () => undefined,
  onRetry: () => undefined,
};

function buttonByText(container: HTMLElement, text: string): HTMLButtonElement {
  const button = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(text));
  if (!button) throw new Error(`button not found: ${text}`);
  return button;
}

function previewFor(name: string): ProjectPreview {
  return {
    preview_id: "preview-test",
    name,
    location: projectCreationWorkspaceFixture.dataSource.locations[0],
    existing_directory: null,
    directory_name: name,
    creation_mode: "CREATE_NEW",
    relative_path: name,
    project_type: "STM32",
    mode: "CREATE",
    expires_at: "2099-01-01T00:00:00Z",
    write_summary: ["Project Identity"],
    fingerprint: "sha256:preview",
  };
}

const createdProject: ProjectView = {
  project_id: "created-test",
  tenant_id: "desktop-tenant",
  name: "测试项目",
  location_id: "acceptance-default-project-space",
  directory_name: "测试项目",
  creation_mode: "CREATE_NEW",
  relative_path: "测试项目",
  project_type: "STM32",
  initialization_status: "READY",
  platform: "STM32",
  version: null,
  branch: null,
  failure_code: null,
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
  last_activity_at: "2026-08-16T00:00:00Z",
  fingerprint: "sha256:created",
};

describe("v5.2.7 project creation and context foundation", () => {
  it("renders controlled parent-folder language without exposing internal references", () => {
    const markup = renderToStaticMarkup(<ProjectCreatePanel dataSource={projectCreationWorkspaceFixture.dataSource} onClose={() => undefined} onCreated={() => undefined} />);
    expect(markup).toContain("ide-dialog-scrim--panel");
    expect(markup).toContain("新建嵌入式项目");
    expect(markup).toContain("项目位置");
    expect(markup).toContain("默认工程空间");
    expect(markup).toContain("选择文件夹");
    expect(markup).toContain("创建项目");
    expect(markup).not.toContain("预览");
    expect(markup).not.toContain("工程位置路径");
    expect(markup).not.toContain("工程位置显示名称");
    expect(markup).not.toContain("项目空间");
    expect(markup).not.toContain("Create Workspace");
    for (const forbidden of ["location_id", "fingerprint", "relative_path", "C:\\Users\\", "/Users/"]) expect(markup).not.toContain(forbidden);
  });

  it("uses the attach CTA without exposing the preview concept", () => {
    const fixture = projectCreationWorkspaceFixture.dataSource;
    const location = fixture.locations[0];
    const markup = renderToStaticMarkup(<ProjectCreatePanel dataSource={{
      ...fixture,
      initial: { name: "已有工程", projectType: "STM32", creationMode: "ATTACH_EXISTING" },
      directories: { [location.location_id]: [{ directory_id: "directory-1", display_name: "已有工程", fingerprint: "sha256:directory" }] },
    }} onClose={() => undefined} onCreated={() => undefined} />);
    expect(markup).toContain("接入项目");
    expect(markup).not.toContain("预览");
  });

  it("runs preview once, then blocks duplicate confirm requests", async () => {
    let releasePreview!: (value: ProjectPreview) => void;
    let releaseConfirm!: (value: ProjectView) => void;
    const preview = vi.fn(() => new Promise<ProjectPreview>((resolve) => { releasePreview = resolve; }));
    const confirm = vi.fn(() => new Promise<ProjectView>((resolve) => { releaseConfirm = resolve; }));
    const onCreated = vi.fn(async () => undefined);
    const dataSource = { ...projectCreationWorkspaceFixture.dataSource, preview, confirm };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<ProjectCreatePanel dataSource={dataSource} onClose={() => undefined} onCreated={onCreated} />); await Promise.resolve(); });
      await act(async () => { buttonByText(container, "创建项目").click(); });
      expect(preview).toHaveBeenCalledTimes(1);
      expect(buttonByText(container, "正在检查…").disabled).toBe(true);
      await act(async () => { buttonByText(container, "正在检查…").click(); });
      expect(preview).toHaveBeenCalledTimes(1);
      await act(async () => { releasePreview(previewFor("测试项目")); await Promise.resolve(); });
      expect(container.textContent).toContain("创建确认");
      await act(async () => { buttonByText(container, "确认创建").click(); });
      await act(async () => { buttonByText(container, "正在创建…").click(); });
      expect(confirm).toHaveBeenCalledTimes(1);
      releaseConfirm(createdProject);
      await act(async () => { await Promise.resolve(); });
      expect(onCreated).toHaveBeenCalledTimes(1);
    } finally {
      root.unmount();
      container.remove();
    }
  });

  it("keeps the confirmation step after preview expiration", async () => {
    const confirm = vi.fn(async () => { throw new Error("preview expired"); });
    const dataSource = { ...projectCreationWorkspaceFixture.dataSource, preview: async (input: Parameters<typeof projectCreationWorkspaceFixture.dataSource.preview>[0]) => previewFor(input.name), confirm };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<ProjectCreatePanel dataSource={dataSource} onClose={() => undefined} onCreated={() => undefined} />); await Promise.resolve(); });
      await act(async () => { buttonByText(container, "创建项目").click(); await Promise.resolve(); });
      expect(container.textContent).toContain("创建确认");
      await act(async () => { buttonByText(container, "确认创建").click(); await Promise.resolve(); });
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(container.textContent).toContain("创建确认");
      expect(container.textContent).toContain("项目创建失败，请检查工程位置或项目名称。");
      await act(async () => { buttonByText(container, "返回修改").click(); });
      expect((container.querySelector("input") as HTMLInputElement).value).toBe("循迹智能小车");
    } finally {
      root.unmount();
      container.remove();
    }
  });

  it("uses the attach confirmation CTA after validation", async () => {
    const location = projectCreationWorkspaceFixture.dataSource.locations[0];
    const directory = { directory_id: "directory-attach", display_name: "已有工程", fingerprint: "sha256:directory" };
    const dataSource = {
      ...projectCreationWorkspaceFixture.dataSource,
      initial: { name: "已有工程", projectType: "STM32" as const, creationMode: "ATTACH_EXISTING" as const },
      directories: { [location.location_id]: [directory] },
      preview: async (input: Parameters<typeof projectCreationWorkspaceFixture.dataSource.preview>[0]) => ({ ...previewFor(input.name), creation_mode: "ATTACH_EXISTING" as const, mode: "ATTACH" as const, existing_directory: directory }),
    };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<ProjectCreatePanel dataSource={dataSource} onClose={() => undefined} onCreated={() => undefined} />); await Promise.resolve(); await Promise.resolve(); });
      await act(async () => { buttonByText(container, "接入项目").click(); await Promise.resolve(); });
      expect(container.textContent).toContain("确认接入");
      expect(container.textContent).not.toContain("确认创建");
    } finally {
      root.unmount();
      container.remove();
    }
  });

  it("keeps the created Project identity consistent and does not create a conversation", () => {
    const fixture = createdProjectWorkspaceFixture;
    expect(fixture.project?.project_id).toBe(fixture.activeProjectId);
    expect(fixture.conversation).toBeNull();
    expect(fixture.context?.project_id).toBe(fixture.activeProjectId);
    const markup = renderToStaticMarkup(<ChatPanel title="New conversation" projectName={fixture.project?.name} updatedAt={fixture.project?.updated_at} sessionReady={false} turns={[]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="expanded" {...callbacks} />);
    expect(markup).toContain("告诉 Embrix 你想做什么。");
    expect(markup).not.toContain("工程 Copilot");
    expect(markup).not.toContain("循迹智能小车");
    expect(markup).not.toContain("尚未开始工程分析");
    expect(markup).not.toContain("New conversation");
  });

  it("provides an isolated parent-location acceptance fixture", () => {
    const fixture = projectParentCreationWorkspaceFixture;
    const location = fixture.dataSource.locations[0];
    expect(location.display_label).toBe("Embedded Projects");
    expect(fixture.dataSource.initial).toMatchObject({ name: "循迹智能小车", projectType: "STM32", creationMode: "CREATE_NEW" });
    expect(createdParentProjectWorkspaceFixture.conversation).toBeNull();
    expect(createdParentProjectWorkspaceFixture.project?.project_id).toBe(createdParentProjectWorkspaceFixture.activeProjectId);
  });

  it("fails closed with IDE language for an empty Engineering Context", () => {
    const markup = renderToStaticMarkup(<RuntimePanel project={createdProjectWorkspaceFixture.project} context={createdProjectWorkspaceFixture.context} panel={null} serviceError={null} />);
    expect(markup).toContain("工程上下文");
    expect(markup).toContain("暂无引用");
    expect(markup).toContain("尚未产生工程证据");
    expect(markup).toContain("等待连接");
    expect(markup).not.toContain("Runtime Dashboard");
    for (const forbidden of ["CPU", "Temperature", "98%", "120mA", "Build", "Flash", "Execute"]) expect(markup).not.toContain(forbidden);
  });

  it("keeps composer context entry points removed", () => {
    const markup = renderToStaticMarkup(<ChatInput connected pending={false} references={[]} selected={[]} {...callbacks} />);
    expect(markup).not.toContain('aria-label="添加工程上下文"');
    expect(markup).not.toContain("Research:");
    expect(markup).not.toContain("Enter 发送");
    expect(markup).not.toContain("选择工程上下文");
  });
});
