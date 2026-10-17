import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkbenchExplorer } from "./WorkbenchExplorer";

const project = { identity: { id: "p-1", name: "31313", platform: "ESP-IDF", lifecycle: "ready", conversationState: "active" }, lastActivityAt: "2026-08-31T00:00:00Z" } as const;

describe("workbench explorer management", () => {
  it("keeps real project removal separate from local file deletion and exposes history management", () => {
    const markup = renderToStaticMarkup(<WorkbenchExplorer projects={[project]} activeProjectId="p-1" resources={[]} activeResourceId={null} resourceState="LOADED_ZERO" conversations={[{ conversation_id: "c-1", title: "需求澄清" }]} activeConversationId="c-1" onProjectChange={() => undefined} onResource={() => undefined} onNewProject={() => undefined} onNewConversation={() => undefined} onConversationChange={() => undefined} onRemoveProject={() => undefined} catalogError={null} />);
    expect(markup).toContain("项目菜单");
    expect(markup).toContain("从 Embrix 移除");
    expect(markup).toContain("不删除本地工程目录");
    expect(markup).toContain("对话菜单");
    expect(markup).not.toContain("Delete 不可用");
    expect(markup).not.toContain("Conversation persistence 未提供安全管理合同");
    expect(markup).toContain("当前项目尚未发现可加载的工程资源");
    expect(markup).not.toContain("Workbench 已加载");
    expect(markup).toContain("Project Settings 不可用");
    expect(markup).toContain('class="embrix-compact-add"');
    expect(markup).toContain('aria-label="新建对话"');
    expect(markup).not.toContain("工程资源 0");
  });

  it("exposes a real conversation delete action without internal contract text", () => {
    const markup = renderToStaticMarkup(<WorkbenchExplorer projects={[]} activeProjectId="" resources={[]} activeResourceId={null} resourceState="NO_PROJECT" conversations={[{ conversation_id: "c-1", title: "需求澄清" }]} onProjectChange={() => undefined} onResource={() => undefined} onNewProject={() => undefined} onDeleteConversation={() => undefined} catalogError={null} />);
    expect(markup).toContain("删除对话");
    expect(markup).toContain(">删除</button>");
    expect(markup).not.toContain("暂无可用操作");
  });

  it.each([
    ["NO_PROJECT", "请选择项目"],
    ["NO_LOCATION", "当前项目尚未关联工程目录"],
    ["LOADING", "正在加载工程资源"],
    ["API_ERROR", "工程资源加载失败"],
    ["EMPTY_DIRECTORY", "当前项目尚未发现可加载的工程资源"],
    ["LOADED_ZERO", "当前项目尚未发现可加载的工程资源"],
  ] as const)("keeps %s resource state distinct", (resourceState, message) => {
    const markup = renderToStaticMarkup(<WorkbenchExplorer projects={[]} activeProjectId="" resources={[]} activeResourceId={null} resourceState={resourceState} resourceError={resourceState === "API_ERROR" ? "NETWORK" : null} onProjectChange={() => undefined} onResource={() => undefined} onNewProject={() => undefined} catalogError={null} />);
    expect(markup).toContain(message);
    expect(markup).toContain(`data-resource-state="${resourceState}"`);
  });

  it("offers retry only for a failed read-only workbench request", () => {
    const markup = renderToStaticMarkup(<WorkbenchExplorer projects={[]} activeProjectId="" resources={[]} activeResourceId={null} resourceState="API_ERROR" resourceError="NETWORK" onProjectChange={() => undefined} onResource={() => undefined} onNewProject={() => undefined} onRetry={() => undefined} catalogError={null} />);
    expect(markup).toContain(">重试</button>");
  });

  it("distinguishes duplicate conversation titles without mutating persistence", () => {
    const markup = renderToStaticMarkup(<WorkbenchExplorer projects={[]} activeProjectId="" resources={[]} activeResourceId={null} resourceState="NO_PROJECT" conversations={[{ conversation_id: "c-1", title: "新对话" }, { conversation_id: "c-2", title: "新对话" }, { conversation_id: "c-3", title: "新对话" }]} catalogError={null} onProjectChange={() => undefined} onResource={() => undefined} onNewProject={() => undefined} />);
    expect(markup).toContain(">新对话<");
    expect(markup).toContain(">新对话 2<");
    expect(markup).toContain(">新对话 3<");
  });

  it("keeps resource reference contextual and disabled without a safe send contract", () => {
    const resource = { resource_id: "r-1", parent_id: null, name: "main.c", relative_path: "main.c", node_type: "FILE" as const, category: "FIRMWARE", language: "C", source_fingerprint: "sha256:r" };
    const markup = renderToStaticMarkup(<WorkbenchExplorer projects={[project]} activeProjectId="p-1" resources={[resource]} activeResourceId={null} resourceState="LOADED" onProjectChange={() => undefined} onResource={() => undefined} onNewProject={() => undefined} catalogError={null} />);
    expect(markup).toContain("工程资源");
    expect(markup).toContain("引用到当前对话");
    expect(markup).toContain("当前引用发送链不可用");
    const enabled = renderToStaticMarkup(<WorkbenchExplorer projects={[project]} activeProjectId="p-1" resources={[resource]} activeResourceId={null} resourceState="LOADED" onProjectChange={() => undefined} onResource={() => undefined} onNewProject={() => undefined} onReferenceResource={() => undefined} catalogError={null} />);
    expect(enabled).toContain("引用到当前对话");
    expect(enabled).not.toContain("当前引用发送链不可用");
  });
});
