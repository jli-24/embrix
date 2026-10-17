import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ConversationActivityTrace } from "./ConversationActivityTrace";
import type { ConversationActivityItem } from "./conversationActivity";

const item = (overrides: Partial<ConversationActivityItem> = {}): ConversationActivityItem => ({
  id: "activity-1",
  kind: "VERIFICATION",
  title: "KiCad ERC",
  status: "PASS",
  result_state: "VERIFIED",
  not_applicable: false,
  started_at: "2026-09-20T10:00:00Z",
  completed_at: "2026-09-20T10:00:02Z",
  duration_ms: null,
  capability: "Schematic Verification",
  tool: "KiCad",
  operation: "sch erc",
  summary: "0 Errors / 0 Warnings",
  evidence_refs: ["SchematicErcReceipt"],
  artifact_refs: [],
  approval: null,
  children: [],
  developer_detail: "KicadErcAdapter",
  ...overrides,
});

describe("ConversationActivityTrace", () => {
  it("shows completed context, inapplicable retrieval, and completed answer", () => {
    const markup = renderToStaticMarkup(<ConversationActivityTrace activities={[
      item({ id: "context", title: "准备工程上下文" }),
      item({ id: "evidence", title: "检索工程证据", status: "PASS", result_state: "NOT_APPLICABLE", not_applicable: true }),
      item({ id: "answer", title: "生成回答" }),
    ]} initiallyExpanded />);
    expect(markup).toContain("已完成 2 个步骤，1 项不适用");
    expect(markup).toContain("准备工程上下文</span><span class=\"conversation-activity__status\">已完成");
    expect(markup).toContain("检索工程证据</span><span class=\"conversation-activity__status\">不适用");
    expect(markup).toContain("生成回答</span><span class=\"conversation-activity__status\">已完成");
  });

  it("does not show a green completion icon for entirely inapplicable or unresolved activities", () => {
    const inapplicable = renderToStaticMarkup(<ConversationActivityTrace activities={[item({ result_state: "NOT_APPLICABLE", not_applicable: true })]} />);
    const pending = renderToStaticMarkup(<ConversationActivityTrace activities={[item({ status: "PENDING", result_state: null })]} />);
    const unavailable = renderToStaticMarkup(<ConversationActivityTrace activities={[item({ status: "WARNING", result_state: "UNAVAILABLE" })]} initiallyExpanded />);
    expect(inapplicable).toContain("本次步骤均不适用");
    expect(inapplicable).not.toContain("已完成 1 个步骤");
    expect(pending).toContain("○");
    expect(pending).not.toContain("✓");
    expect(unavailable).toContain("暂不可用");
    expect(unavailable).not.toContain("✓");
  });
  it("keeps natural language primary and collapses trace details by default", () => {
    const markup = renderToStaticMarkup(<ConversationActivityTrace activities={[item()]} />);
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain("已完成 1 个步骤");
    expect(markup).toContain("展开");
    expect(markup).toContain('data-trace-collapsed="true"');
  });

  it("exposes capability, tool, operation and evidence only in expandable detail", () => {
    const markup = renderToStaticMarkup(<ConversationActivityTrace activities={[item()]} initiallyExpanded />);
    expect(markup).toContain("Schematic Verification");
    expect(markup).toContain("KiCad");
    expect(markup).toContain("sch erc");
    expect(markup).toContain("SchematicErcReceipt");
    expect(markup).toContain("开发者详情");
    expect(markup).toContain("KicadErcAdapter");
    expect(markup).not.toContain("chain-of-thought");
    expect(markup).not.toContain("private reasoning");
  });

  it("uses safe visual states for running, unknown, not applicable and stale", () => {
    const markup = renderToStaticMarkup(<ConversationActivityTrace activities={[
      item({ id: "running", title: "生成 PCB", status: "RUNNING", result_state: null, summary: "正在执行" }),
      item({ id: "unknown", title: "Return Path", status: "WARNING", result_state: "UNKNOWN", summary: "缺少 reference-plane assignment" }),
      item({ id: "na", title: "Differential Pair", status: "PASS", result_state: "NOT_APPLICABLE", not_applicable: true }),
      item({ id: "stale", title: "旧验证", status: "STALE", result_state: "STALE", summary: "需要重新验证" }),
    ]} initiallyExpanded />);
    expect(markup).toContain("○");
    expect(markup).toContain("?");
    expect(markup).toContain("—");
    expect(markup).toContain("↻");
    expect(markup).toContain("尚无法验证");
    expect(markup).toContain("需要重新验证");
  });

  it("does not render a fake trace when no activities exist", () => {
    expect(renderToStaticMarkup(<ConversationActivityTrace activities={[]} />)).toBe("");
  });
});
