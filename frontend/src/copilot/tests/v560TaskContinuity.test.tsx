import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ProjectExplorer } from "../project/ProjectExplorer";
import { RuntimePanel } from "../runtime/RuntimePanel";
import { ChatPanel } from "../chat/ChatPanel";
import { EngineeringTask } from "../task/EngineeringTask";
import { EngineeringWorkflow } from "../workflow/EngineeringWorkflow";
import { taskContinuityWorkspaceFixture } from "../acceptanceFixtures/taskContinuityFixture";
import { conversationTaskStatus, engineeringTaskStatus, taskIdentity } from "../task/engineeringTaskModel";
import { reduceChatStreamEvent } from "../api/chatClient";
import { AssistantMessage } from "../chat/AssistantMessage";
import type { ConversationDetail, ConversationTurn } from "../api/projectClient";

const callbacks = {
  onToggleReference: () => undefined,
  onAttach: () => undefined,
  onSubmit: () => undefined,
  onRetry: () => undefined,
};

function conversationWith(status: ConversationTurn["status"]): ConversationDetail {
  return {
    conversation_id: "conversation-test-001",
    project_id: "car-001",
    title: "为什么 I2C 读取失败？",
    turns: [{ turn_id: "turn-1", request_id: "request-1", status, question: "为什么 I2C 读取失败？", answer_text: null, evidence: [], recommendations: [], context_references: [], error_code: null, created_at: "2026-08-12T09:00:00Z", completed_at: null }],
    created_at: "2026-08-12T09:00:00Z",
    updated_at: "2026-08-12T09:00:00Z",
    fingerprint: "test-only",
  };
}

function conversationWithTurns(statuses: ConversationTurn["status"][]): ConversationDetail {
  const base = conversationWith("FAILED");
  return {
    ...base,
    turns: statuses.map((status, index) => ({
      ...base.turns[0],
      turn_id: `turn-${index + 1}`,
      request_id: `request-${index + 1}`,
      status,
      analysis_state: status === "COMPLETED" ? "COMPLETED" : "FAILED",
    })),
  };
}

describe("v5.6.0 Engineering Task Continuity", () => {
  it("keeps Task identity separate from Workflow identity", () => {
    const fixture = taskContinuityWorkspaceFixture;
    expect(fixture.taskIdentity.projectId).toBe(fixture.projectIdentity.id);
    expect(fixture.taskIdentity.conversationId).toBe(fixture.conversationId);
    expect(fixture.workflowProjection.id).toBe(`workflow:${fixture.projectId}:${fixture.conversationId}`);
    expect("workflow" in fixture.taskIdentity).toBe(false);
    expect("task" in fixture.workflowProjection).toBe(false);
  });

  it("maps lifecycle states without exposing request errors as task failures", () => {
    expect(conversationTaskStatus(null)).toBe("not_started");
    expect(engineeringTaskStatus(null)).toBe("created");
    expect(conversationTaskStatus(conversationWith("PENDING"), true)).toBe("active");
    expect(engineeringTaskStatus(conversationWith("PENDING"), true)).toBe("running");
    expect(conversationTaskStatus(conversationWith("COMPLETED"))).toBe("completed");
    expect(engineeringTaskStatus(conversationWith("COMPLETED"))).toBe("completed");
    expect(engineeringTaskStatus(conversationWith("FAILED"))).toBe("failed");
    expect(taskIdentity("car-001", "conversation-test-001", "I2C", "running").status).toBe("running");
  });

  it("uses the latest persisted turn instead of historical failures", () => {
    const latestCompleted = conversationWithTurns(["FAILED", "COMPLETED"]);
    const latestFailed = conversationWithTurns(["COMPLETED", "FAILED"]);

    expect(conversationTaskStatus(latestCompleted)).toBe("completed");
    expect(engineeringTaskStatus(latestCompleted)).toBe("completed");
    expect(conversationTaskStatus(latestFailed)).toBe("failed");
    expect(engineeringTaskStatus(latestFailed)).toBe("failed");
  });

  it("prioritizes clarification state over the compatible ANSWER_FAILED terminal event", () => {
    const conversation = conversationWithTurns(["FAILED", "WAITING_FOR_CLARIFICATION"]);
    conversation.turns[1].analysis_state = "WAITING_FOR_CLARIFICATION";
    expect(conversationTaskStatus(conversation)).toBe("clarification");
    expect(engineeringTaskStatus(conversation)).toBe("created");
    const stream = reduceChatStreamEvent(
      { status: "started", answer: "", errorCode: null, analysisState: "REQUESTED", traceId: null },
      { event_id: "terminal-1", event_type: "ANSWER_FAILED", answer_chunk: null, answer_fingerprint: null, analysis_state: "WAITING_FOR_CLARIFICATION", reason_code: "CLARIFICATION_REQUIRED" },
    );
    expect(stream.status).toBe("clarification");
    expect(stream.errorCode).toBe("CLARIFICATION_REQUIRED");
  });

  it("keeps ANSWER_COMPLETED separate from Greenfield requirement lifecycle", () => {
    const conversation = conversationWith("COMPLETED");
    conversation.turns[0].analysis_state = "REQUIREMENTS_PARTIAL";
    conversation.turns[0].route = "GREENFIELD_DISCOVERY";
    expect(conversationTaskStatus(conversation)).toBe("discovery");
    expect(engineeringTaskStatus(conversation)).toBe("discovery");

    const stream = reduceChatStreamEvent(
      { status: "streaming", answer: "已收到", errorCode: null, analysisState: "REQUIREMENTS_PARTIAL", traceId: null },
      { event_id: "terminal-2", event_type: "ANSWER_COMPLETED", answer_chunk: null, answer_fingerprint: "sha256:test", analysis_state: "REQUIREMENTS_PARTIAL", reason_code: "ANSWER_COMPLETED" },
    );
    expect(stream.status).toBe("completed");
    expect(stream.analysisState).toBe("REQUIREMENTS_PARTIAL");
  });

  it("renders clarification UX without failure or retry language", () => {
    const markup = renderToStaticMarkup(<AssistantMessage answer={null} evidence={[]} recommendations={[]} pending={false} failed={true} errorCode="CLARIFICATION_REQUIRED" analysisState="WAITING_FOR_CLARIFICATION" evidenceDisplay="expanded" />);
    expect(markup).not.toContain(">需要补充信息<");
    expect(markup).toContain("设计一个新的功能或模块");
    expect(markup).not.toContain("分析失败");
    expect(markup).not.toContain("Provider");
    expect(markup).not.toContain("重试");
  });

  it("renders History as engineering task summaries with explicit positive counts", () => {
    const fixture = taskContinuityWorkspaceFixture;
    const markup = renderToStaticMarkup(<ProjectExplorer projects={[fixture.navigationItem]} activeProjectId={fixture.projectId} activeConversationId={fixture.conversationId} conversations={[]} taskSummaries={fixture.taskSummaries} catalogError={null} catalogState="ready" settingsOpen={false} onProjectChange={() => undefined} onNewProject={() => undefined} onNewConversation={() => undefined} onConversationChange={() => undefined} onToggleSettings={() => undefined} />);
    expect(markup).toContain(fixture.taskIdentity.title);
    expect(markup).toContain("✓ 已完成");
    expect(markup).toContain("Evidence 3");
    expect(markup).toContain("Memory 1");
    expect(markup).toContain("● 分析中");
    expect(markup).not.toContain("Evidence 0");
    expect(markup).not.toContain("Memory 0");
  });

  it("keeps task projections out of the primary conversation surface", () => {
    const fixture = taskContinuityWorkspaceFixture;
    const markup = renderToStaticMarkup(<ChatPanel title={fixture.taskIdentity.title} activeProjectIdentity={fixture.projectIdentity} taskProjection={fixture.taskProjection} workflowProjection={fixture.workflowProjection} sessionReady turns={[]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="compact" {...callbacks} />);
    expect(markup).toContain("告诉 Embrix 你想做什么。");
    expect(markup).not.toContain(fixture.taskIdentity.title);
    expect(markup).not.toContain("分析流程");
    expect(markup).not.toContain("<details");
    expect(markup).not.toContain("当前话题");
    expect(markup).not.toContain("工程任务");
    expect(markup).not.toContain("工程记忆");
    expect(markup).not.toContain("生成工程分析结果");
    expect(markup).not.toContain("生成只读工程建议");
  });

  it("projects Current Task and Context with one project identity", () => {
    const fixture = taskContinuityWorkspaceFixture;
    const markup = renderToStaticMarkup(<RuntimePanel activeProjectIdentity={fixture.projectIdentity} taskProjection={fixture.taskProjection} context={null} projection={fixture.contextProjection} memoryProjection={null} panel={null} serviceError={null} />);
    expect(fixture.taskProjection.identity.projectId).toBe(fixture.projectIdentity.id);
    expect(fixture.contextProjection.projectId).toBe(fixture.projectIdentity.id);
    expect(markup).toContain("当前任务");
    expect(markup).toContain(fixture.taskIdentity.title);
    expect(markup).toContain("已完成");
    expect(markup).toContain("HIL");
    expect(markup).toContain("2");
  });

  it("fails closed on Project switch and does not leak Task, Workflow or Memory", () => {
    const fixture = taskContinuityWorkspaceFixture;
    const projectB = { ...fixture.projectIdentity, id: "car-002", name: "另一工程", conversationState: "none" as const };
    const markup = renderToStaticMarkup(<ChatPanel title="Copilot 工程分析" activeProjectIdentity={projectB} taskProjection={null} workflowProjection={null} memoryProjection={null} sessionReady turns={[]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="expanded" {...callbacks} />);
    const context = renderToStaticMarkup(<RuntimePanel activeProjectIdentity={projectB} taskProjection={null} context={null} projection={{ projectId: projectB.id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} panel={null} serviceError={null} />);
    expect(markup).not.toContain(fixture.taskIdentity.title);
    expect(markup).not.toContain("data-task-status");
    expect(context).not.toContain(fixture.taskIdentity.title);
    expect(context).not.toContain("I2C");
  });

  it("keeps task fixture and rendered projections free of forbidden runtime fields", () => {
    const fixture = taskContinuityWorkspaceFixture;
    const rendered = renderToStaticMarkup(<><EngineeringTask task={fixture.taskProjection} /><EngineeringWorkflow workflow={fixture.workflowProjection} /></>).toLowerCase();
    const serialized = JSON.stringify(fixture).toLowerCase();
    for (const forbidden of ["agent", "runtime", "tool execution", "prompt", "token", "reasoning", "raw answer", "raw log", "path", "fingerprint"]) {
      expect(serialized).not.toContain(forbidden);
      expect(rendered).not.toContain(forbidden);
    }
  });
});
