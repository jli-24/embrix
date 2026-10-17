import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChatPanel } from "../chat/ChatPanel";
import { workflowWorkspaceFixture } from "../acceptanceFixtures/workflowFixture";
import { ProjectExplorer } from "../project/ProjectExplorer";
import { RuntimePanel } from "../runtime/RuntimePanel";
import { EngineeringWorkflow } from "../workflow/EngineeringWorkflow";
import { createWorkflowStages, WORKFLOW_STAGES, workflowIdentity } from "../workflow/engineeringWorkflowModel";

const callbacks = {
  onToggleReference: () => undefined,
  onAttach: () => undefined,
  onSubmit: () => undefined,
  onRetry: () => undefined,
};

describe("v5.5.0 Engineering Workflow projection", () => {
  it("keeps the five engineering lifecycle stages fixed and ordered", () => {
    expect(WORKFLOW_STAGES).toEqual(["requirement", "architecture", "hardware", "firmware", "verification"]);
    expect(workflowWorkspaceFixture.workflowProjection.stages.map((stage) => stage.stage)).toEqual([...WORKFLOW_STAGES]);
    expect(workflowWorkspaceFixture.workflowProjection.stages.every((stage) => stage.status === "finished")).toBe(true);

    const untrustedStageWorkflow = {
      ...workflowWorkspaceFixture.workflowProjection,
      stages: [...workflowWorkspaceFixture.workflowProjection.stages, { stage: "planning", title: "planning", status: "finished" }],
    } as unknown as typeof workflowWorkspaceFixture.workflowProjection;
    const markup = renderToStaticMarkup(<EngineeringWorkflow workflow={untrustedStageWorkflow} />);
    expect(markup).not.toContain("planning");
    expect((markup.match(/data-stage=/g) ?? []).length).toBe(WORKFLOW_STAGES.length);
  });

  it("represents running and finished as projection states, not task completion claims", () => {
    const running = { ...workflowWorkspaceFixture.workflowProjection, status: "running" as const, stages: createWorkflowStages("pending") };
    const runningMarkup = renderToStaticMarkup(<EngineeringWorkflow workflow={running} />);
    const finishedMarkup = renderToStaticMarkup(<EngineeringWorkflow workflow={workflowWorkspaceFixture.workflowProjection} />);

    expect(runningMarkup).toContain('data-workflow-status="running"');
    expect(runningMarkup).toContain('data-status="pending"');
    expect(finishedMarkup).toContain('data-workflow-status="finished"');
    expect(finishedMarkup).toContain("验证");
    expect(finishedMarkup).not.toContain("任务完成");
    expect(finishedMarkup).not.toContain("硬件验证通过");
  });

  it("fails closed when a conversation has no workflow projection", () => {
    const markup = renderToStaticMarkup(<EngineeringWorkflow workflow={null} />);
    expect(markup).toContain("暂无工程流程");
    expect(markup).not.toContain("问题理解");
  });

  it("binds Workflow, Conversation, Memory and Context to one project and conversation", () => {
    const fixture = workflowWorkspaceFixture;
    const sidebar = renderToStaticMarkup(<ProjectExplorer projects={[fixture.navigationItem]} activeProjectId={fixture.projectId} conversations={fixture.conversations} catalogError={null} catalogState="ready" settingsOpen={false} onProjectChange={() => undefined} onNewProject={() => undefined} onNewConversation={() => undefined} onConversationChange={() => undefined} onToggleSettings={() => undefined} />);
    const conversation = renderToStaticMarkup(<ChatPanel title={fixture.conversation.title} activeProjectIdentity={fixture.activeProjectIdentity} memoryProjection={fixture.memoryProjection} workflowProjection={fixture.workflowProjection} sessionReady turns={fixture.conversation.turns} pendingQuestion={null} availableReferences={fixture.references} pending={false} error={null} selected={[]} evidenceDisplay="expanded" {...callbacks} />);
    const context = renderToStaticMarkup(<RuntimePanel activeProjectIdentity={fixture.activeProjectIdentity} context={null} projection={fixture.contextProjection} memoryProjection={fixture.memoryProjection} panel={fixture.runtimePanel} serviceError={null} />);

    expect(fixture.conversation.project_id).toBe(fixture.projectId);
    expect(fixture.contextProjection.projectId).toBe(fixture.projectId);
    expect(fixture.memoryProjection.projectId).toBe(fixture.projectId);
    expect(fixture.workflowProjection.id).toBe(workflowIdentity(fixture.projectId, fixture.conversationId));
    expect(sidebar + conversation + context).toContain(fixture.activeProjectIdentity.name);
    expect(sidebar).toContain("STM32");
  });

  it("keeps the document flow in the central conversation without internal projections", () => {
    const fixture = workflowWorkspaceFixture;
    const markup = renderToStaticMarkup(<ChatPanel title={fixture.conversation.title} activeProjectIdentity={fixture.activeProjectIdentity} memoryProjection={fixture.memoryProjection} workflowProjection={fixture.workflowProjection} sessionReady turns={fixture.conversation.turns} pendingQuestion={null} availableReferences={fixture.references} pending={false} error={null} selected={[]} evidenceDisplay="expanded" {...callbacks} />);

    expect(markup.indexOf("为什么 I2C 读取失败？")).toBeGreaterThanOrEqual(0);
    expect(markup).not.toContain("问题理解");
    expect(markup).not.toContain("分析流程");
    expect(markup).not.toContain("<details");
    expect(markup).not.toContain("工程记忆");
    expect(markup).not.toContain("相关工程对象");
  });

  it("keeps fixture and rendered workflow free of runtime and sensitive fields", () => {
    const serialized = JSON.stringify(workflowWorkspaceFixture).toLowerCase();
    const rendered = renderToStaticMarkup(<EngineeringWorkflow workflow={workflowWorkspaceFixture.workflowProjection} />).toLowerCase();
    for (const forbidden of ["agent", "executor", "reasoning", "thought", "chain of thought", "prompt", "token", "raw log", "secret", "path"]) {
      expect(serialized).not.toContain(forbidden);
      expect(rendered).not.toContain(forbidden);
    }
  });
});
