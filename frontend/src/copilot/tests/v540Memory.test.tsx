import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChatInput } from "../chat/ChatInput";
import { ChatPanel } from "../chat/ChatPanel";
import { projectReadyWorkspaceFixture } from "../acceptanceFixtures/projectReadyFixture";
import { memoryWorkspaceFixture } from "../acceptanceFixtures/memoryFixture";
import { normalizeEngineeringMemoryProjection, type EngineeringMemoryProjection } from "../memory/memoryViewModel";
import { RuntimePanel } from "../runtime/RuntimePanel";

const callbacks = {
  onToggleReference: () => undefined,
  onAttach: () => undefined,
  onSubmit: () => undefined,
  onRetry: () => undefined,
};

const emptyProjection: EngineeringMemoryProjection = {
  projectId: projectReadyWorkspaceFixture.activeProjectIdentity.id,
  state: "empty",
  items: [],
};

describe("v5.4.0 Engineering Memory projection", () => {
  it("renders an empty Project Context memory state without AI learning language", () => {
    const markup = renderToStaticMarkup(<RuntimePanel activeProjectIdentity={projectReadyWorkspaceFixture.activeProjectIdentity} context={null} projection={projectReadyWorkspaceFixture.contextProjection} memoryProjection={emptyProjection} panel={null} serviceError={null} />);
    expect(markup).toContain("工程记忆");
    expect(markup).toContain("暂无工程记忆");
    expect(markup).toContain("完成当前工程任务后，Copilot 将保存安全工程摘要。");
    expect(markup.toLowerCase()).not.toContain("正在学习");
    expect(markup.toLowerCase()).not.toContain("智能等级");
  });

  it("renders safe memory summaries in Project Context and keeps Evidence separate", () => {
    const markup = renderToStaticMarkup(<RuntimePanel activeProjectIdentity={memoryWorkspaceFixture.activeProjectIdentity} context={null} projection={{ projectId: "car-001", references: [], evidenceSources: [{ source: "HIL", count: 1 }], resources: [] }} memoryProjection={memoryWorkspaceFixture.memoryProjection} panel={null} serviceError={null} />);
    for (const label of ["I2C 总线稳定性问题", "ESP32-S3 当前 I2C 通信需要降低频率。", "PWM 电机控制方案", "电机控制采用定时器 PWM 输出。", "Conversation", "Evidence", "2026/08/12"]) expect(markup).toContain(label);
    expect(markup.indexOf("当前引用")).toBeLessThan(markup.indexOf("工程记忆"));
    expect(markup.indexOf("工程记忆")).toBeLessThan(markup.indexOf("证据来源"));
    expect(markup).not.toContain("MemoryStatus");
  });

  it("fails closed when a memory projection belongs to another Project", () => {
    const projection = { ...memoryWorkspaceFixture.memoryProjection, projectId: "another-project" };
    const normalized = normalizeEngineeringMemoryProjection(memoryWorkspaceFixture.activeProjectIdentity.id, projection);
    expect(normalized.state).toBe("empty");
    expect(normalized.items).toHaveLength(0);
  });

  it("keeps memory lifecycle metadata separate from Project lifecycle", () => {
    expect(memoryWorkspaceFixture.memoryProjection.state).toBe("available");
    expect(projectReadyWorkspaceFixture.activeProjectIdentity.lifecycle).toBe("ready");
    expect(projectReadyWorkspaceFixture.activeProjectIdentity.conversationState).toBe("none");
    expect(memoryWorkspaceFixture.memoryProjection.items[0]?.confidence).toBe("confirmed");
    expect(memoryWorkspaceFixture.memoryProjection.items[0]?.relatedEvidenceIds).toEqual(["hil_obs_20250810", "fw_ref_0012"]);
  });

  it("keeps memory fixture free of sensitive or filesystem fields", () => {
    const serialized = JSON.stringify(memoryWorkspaceFixture).toLowerCase();
    for (const forbidden of ["path", "fingerprint", "secret", "token", "raw answer", "raw log", "command"]) expect(serialized).not.toContain(forbidden);
  });

  it("keeps memory projection out of the completed conversation", () => {
    const markup = renderToStaticMarkup(<ChatPanel title="I2C 分析" activeProjectIdentity={memoryWorkspaceFixture.activeProjectIdentity} memoryProjection={memoryWorkspaceFixture.memoryProjection} sessionReady turns={[{ turn_id: "turn-1", request_id: "request-1", status: "COMPLETED", question: "I2C?", answer_text: "分析", evidence: [], recommendations: [], context_references: [], error_code: null, created_at: "2026-08-12T10:00:00Z", completed_at: "2026-08-12T10:01:00Z" }]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="expanded" {...callbacks} />);
    expect(markup).not.toContain("工程记忆");
    expect(markup).not.toContain("已形成 2 条工程摘要");
    expect(markup).not.toContain("I2C 总线稳定性问题");
  });

  it("uses approved MEMORY ContextReference only and labels it Engineering Memory", () => {
    const memoryReference = { tenant_id: "tenant", project_id: "car-001", reference_id: "approved-memory-1", reference_kind: "MEMORY" as const, source_fingerprint: "sha256:approved", display_label: "Approved memory reference", fingerprint: "sha256:approved" };
    const markup = renderToStaticMarkup(<ChatInput connected hasActiveProject pending={false} references={[memoryReference]} selected={[memoryReference]} placeholder="问题" {...callbacks} />);
    expect(markup).not.toContain("Engineering Memory");
    expect(markup).not.toContain("Approved memory reference");
    expect(markup).not.toContain("sha256:approved");
  });
});
