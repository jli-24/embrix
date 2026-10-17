import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChatInput } from "../chat/ChatInput";
import { ChatPanel } from "../chat/ChatPanel";
import { ProjectExplorer } from "../project/ProjectExplorer";
import { RuntimePanel } from "../runtime/RuntimePanel";
import { projectReadyWorkspaceFixture } from "../acceptanceFixtures/projectReadyFixture";

const callbacks = {
  onToggleReference: () => undefined,
  onAttach: () => undefined,
  onSubmit: () => undefined,
  onRetry: () => undefined,
};

describe("v5.3.0 project workspace identity", () => {
  it("keeps one active project identity across navigation, header, empty workspace, context and composer", () => {
    const fixture = projectReadyWorkspaceFixture;
    const sidebar = renderToStaticMarkup(<ProjectExplorer projects={[fixture.navigationItem]} activeProjectId={fixture.activeProjectIdentity.id} conversations={[]} catalogError={null} catalogState="ready" settingsOpen={false} onProjectChange={() => undefined} onNewProject={() => undefined} onNewConversation={() => undefined} onConversationChange={() => undefined} onToggleSettings={() => undefined} />);
    const chat = renderToStaticMarkup(<ChatPanel title="Copilot 工程分析" activeProjectIdentity={fixture.activeProjectIdentity} sessionReady turns={[]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="expanded" {...callbacks} />);
    const context = renderToStaticMarkup(<RuntimePanel activeProjectIdentity={fixture.activeProjectIdentity} context={null} projection={fixture.contextProjection} panel={null} serviceError={null} />);

    expect(sidebar).toContain(fixture.activeProjectIdentity.name);
    expect(context).toContain(fixture.activeProjectIdentity.name);
    expect(chat).not.toContain(fixture.activeProjectIdentity.name);
    expect(sidebar).toContain("STM32");
    for (const label of ["告诉 Embrix 你想做什么。", "工程上下文", "已创建"]) expect(chat + context).toContain(label);
    expect(chat).not.toContain("工程 Copilot");
    expect(chat).toContain('placeholder=""');
    expect(chat).not.toContain("询问关于「循迹智能小车」的工程问题...");
    expect(context).toContain("暂无引用");
    expect(context).toContain("尚未产生工程证据");
  });

  it("keeps project-ready fixture free of filesystem and provider secrets", () => {
    const serialized = JSON.stringify(projectReadyWorkspaceFixture).toLowerCase();
    for (const forbidden of ["path", "fingerprint", "metadata", "secret", "token"]) expect(serialized).not.toContain(forbidden);
    expect(projectReadyWorkspaceFixture.activeProjectIdentity.lifecycle).toBe("ready");
    expect(projectReadyWorkspaceFixture.activeProjectIdentity.conversationState).toBe("none");
    expect(projectReadyWorkspaceFixture.conversation).toBeNull();
  });

  it("removes composer context entry points", () => {
    const markup = renderToStaticMarkup(<ChatInput connected={false} hasActiveProject={false} pending={false} references={[]} selected={[]} {...callbacks} />);
    expect(markup).not.toContain("ide-context-menu");
    expect(markup).not.toContain('aria-label="添加工程上下文"');
    expect(markup).not.toContain('aria-expanded="false"');
    expect(markup).not.toContain("Research:");
  });
});
