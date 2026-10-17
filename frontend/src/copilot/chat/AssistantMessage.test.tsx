import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AssistantMessage } from "./AssistantMessage";
import { DocumentRenderer } from "./DocumentRenderer";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { resolveConversationMode, resolveResponseType } from "./conversationRenderer";
import { createConversationState } from "./conversationState";

const baseProps = {
  evidence: [],
  recommendations: [],
  pending: false,
  failed: false,
  evidenceDisplay: "compact" as const,
};

const internalLabels = ["CONFIRMED", "UNKNOWN", "PROPOSED", "OPEN_QUESTION", "Requirement ID", "Evidence", "Recommendation"];

function renderAssistant(answer: string | null, props: Partial<Parameters<typeof AssistantMessage>[0]> = {}) {
  return renderToStaticMarkup(<AssistantMessage {...baseProps} answer={answer} {...props} />);
}

describe("AssistantMessage renderer routing", () => {
  it("uses conversational Markdown for exploration and hides report metadata", () => {
    const markup = renderAssistant(
      "# 项目需求分析报告\n\n明白，你想开发一个 STM32 智能小车。\n\nEvidence: internal\nCONFIRMED\nRequirement ID: REQ-001\nRecommendation: hidden",
      { question: "我要做一个 STM32 智能小车" },
    );

    expect(markup).toContain('data-renderer="chat-markdown"');
    expect(markup).toContain("明白，你想开发一个 STM32 智能小车");
    expect(markup).not.toContain("项目需求分析报告");
    for (const label of internalLabels) expect(markup).not.toContain(label);
  });

  it("uses Markdown for design and execution while preserving Markdown structure", () => {
    const design = renderAssistant(
      "# 系统架构\n\n- STM32 控制器\n- 电机驱动\n\n| 模块 | 作用 |\n| --- | --- |\n| MCU | 主控 |\n\n> 先定义接口边界。",
      { question: "设计系统架构" },
    );
    expect(design).toContain('data-renderer="chat-markdown"');
    for (const tag of ["<h1>", "<ul>", "<table>", "<blockquote>"]) expect(design).toContain(tag);

    const execution = renderAssistant("任务理解\n\n- 先检查 PID 参数\n- 尚未执行测试", { question: "修改代码并运行测试" });
    expect(execution).toContain('data-renderer="chat-markdown"');
    expect(execution).toContain("尚未执行测试");
    expect(execution).not.toContain("执行结果：已通过");
  });

  it("uses Document Renderer only for an explicit document request", () => {
    const markup = renderAssistant(
      "# 项目需求分析报告\n\n## 项目目标\n完成智能小车控制。\n\nEvidence: internal\nUNKNOWN",
      { question: "生成需求分析报告", responseType: "DOCUMENT" },
    );

    expect(markup).toContain('data-renderer="document"');
    expect(markup).toContain("项目需求分析报告");
    expect(markup).toContain("项目目标");
    for (const label of internalLabels) expect(markup).not.toContain(label);
  });

  it("uses explicit document metadata for a report confirmation", () => {
    const confirmed = renderAssistant("# 项目需求分析报告\n\n正式内容", {
      question: "确认生成",
      previousAnswer: "当前项目需求已经整理完成，是否生成需求分析报告？",
      responseType: "DOCUMENT",
    });
    expect(confirmed).toContain('data-renderer="document"');

    for (const question of ["好", "继续", "下一步", "可以"]) {
      const ordinary = renderAssistant("继续进行工程协作。", { question, previousAnswer: "可以继续。" });
      expect(ordinary).toContain('data-renderer="chat-markdown"');
      expect(ordinary).not.toContain('data-renderer="document"');
    }
  });

  it("uses existing route values only as non-document renderer hints", () => {
    expect(resolveConversationMode("继续分析", "GREENFIELD_DESIGN")).toBe("DESIGN");
    expect(resolveConversationMode("继续分析", "DEBUG_ANALYSIS")).toBe("EXECUTION");
    expect(resolveConversationMode("继续分析", "UNKNOWN")).toBe("EXPLORATION");
    expect(resolveConversationMode("设计系统架构并生成测试报告", "GREENFIELD_DESIGN", null, "DOCUMENT")).toBe("DOCUMENT");
  });

  it("keeps pending and failed responses in safe conversational Markdown", () => {
    const pending = renderAssistant(null, { pending: true, question: "下一步怎么设计" });
    expect(pending).toContain('data-renderer="chat-markdown"');
    expect(pending).toContain("我正在处理这个工程问题");
    expect(pending).not.toContain("工程上下文");

    const failed = renderAssistant(null, { failed: true, errorCode: "ANSWER_SCHEMA_INVALID", analysisState: "FAILED" });
    expect(failed).toContain("这次回答没有通过格式校验");
    expect(failed).toContain('data-renderer="chat-markdown"');
  });
});

describe("MarkdownRenderer copy and selection behavior", () => {
  it("renders selectable text without copy actions and restricts code blocks to the language allowlist", () => {
    const markup = renderToStaticMarkup(<MarkdownRenderer content={'普通工程说明\n\n```c\nint main(void) {}\n```'} mode="EXPLORATION" />);
    expect(markup).toContain('style="user-select:text"');
    expect(markup).toContain("<pre");
    expect(markup).toContain("int main(void) {}");
    expect(markup).not.toContain("复制代码");
    expect(markup).not.toContain("data-copy-scope");
    expect(markup).not.toContain("user-select:none");
  });

  it("keeps product positioning and unknown fenced text out of code blocks", () => {
    const markup = renderToStaticMarkup(<MarkdownRenderer content={'AI Engineering Agent\n\nUnderstand\n↓\nDesign\n\n```unknown\nAI Document Generator\n```\n\n```\n普通流程说明\n```'} mode="EXPLORATION" />);
    expect(markup).toContain("AI Engineering Agent");
    expect(markup).toContain("AI Document Generator");
    expect(markup).toContain("普通流程说明");
    expect((markup.match(/<pre/gu) ?? []).length).toBe(0);
    expect(markup).not.toContain('data-language="unknown"');
  });

  it("does not create unsafe or relative links", () => {
    const markup = renderToStaticMarkup(<MarkdownRenderer content="[安全链接](https://example.com) [相对链接](/local) [脚本链接](javascript:alert(1))" mode="EXPLORATION" />);
    expect(markup).toContain('href="https://example.com"');
    expect(markup).not.toContain('href="/local"');
    expect(markup).not.toContain("javascript:");
  });

  it("keeps assistant messages continuous without a full-copy action", () => {
    const markup = renderAssistant("# 说明\n\n- 检查 `TIM3`\n\n```c\nint main(void) {}\n```", { question: "解释一下 STM32 定时器" });
    expect(markup).not.toContain("复制全部");
    expect(markup).not.toContain("data-copy-scope");
    expect(markup).toContain("检查");
  });

  it("uses the explicit artifact renderer only for an artifact response contract", () => {
    const markup = renderAssistant("生成的工程文件已关联。", { question: "生成 main.c", responseType: "ARTIFACT", artifactRefs: ["artifact-main-c"] });
    expect(markup).toContain('data-renderer="artifact"');
    expect(markup).toContain("artifact-main-c");
  });

  it("keeps document content continuous without chapter actions", () => {
    const markup = renderToStaticMarkup(<DocumentRenderer content={"# 项目架构\n\n## 硬件\nSTM32F103\n\n## 软件\nPID 控制"} />);
    expect(markup).toContain('data-renderer="document"');
    expect(markup).not.toContain("复制章节");
    expect(markup).not.toContain("data-copy-scope");
  });
});

describe("conversation state and response decision contract", () => {
  it("uses response metadata and never classifies from Markdown or question text", () => {
    expect(resolveResponseType({ responseType: "ARTIFACT", question: "普通说明" })).toBe("ARTIFACT");
    expect(resolveResponseType({ question: "生成需求分析报告" })).toBe("CHAT");
    expect(resolveResponseType({ question: "这是一段很长的回答", previousAnswer: "# 很多标题\n\n正文" })).toBe("CHAT");
    expect(resolveResponseType({ question: "继续", previousAnswer: "当前内容是否需要生成报告？" })).toBe("CHAT");
    expect(resolveResponseType({ question: "确认生成", previousAnswer: "当前内容是否需要生成报告？" })).toBe("CHAT");
  });

  it("keeps messages and projections as one derived conversation state", () => {
    const state = createConversationState({ messages: [], contextProjection: null, activeArtifact: null, executionReference: "operation-1", question: "设计系统架构" });
    expect(state.mode).toBe("DESIGN");
    expect(state.execution_reference).toBe("operation-1");
    expect(state.messages).toHaveLength(0);
  });
});
