import type { ConversationResponseType, ConversationTurn } from "../api/projectClient";

export type ConversationMode = "EXPLORATION" | "DESIGN" | "DOCUMENT" | "EXECUTION";
export type ResponseType = ConversationResponseType;

export type RendererDecision = {
  responseType: ResponseType;
  mode: ConversationMode;
  renderer: "chat-markdown" | "document" | "artifact";
  source: "response-metadata" | "default-chat";
};

const EXECUTION_PHRASES = [
  "修改代码",
  "改代码",
  "编译",
  "构建",
  "运行测试",
  "执行测试",
  "调试错误",
  "调试",
  "执行工程任务",
  "执行任务",
  "modify code",
  "change code",
  "compile",
  "build",
  "run tests",
  "debug",
  "execute the task",
];
const DESIGN_PHRASES = [
  "设计方案",
  "架构设计",
  "技术路线",
  "硬件方案",
  "软件方案",
  "系统架构",
  "控制方案",
  "怎么设计",
  "如何设计",
  "工程设计",
  "新建工程设计",
  "design solution",
  "system architecture",
  "technical approach",
  "hardware design",
  "software design",
];
const PRIVATE_LINE_PATTERN = /^\s*(?:#{1,6}\s*)?(?:CONFIRMED|UNKNOWN|PROPOSED|OPEN_QUESTION|Requirement\s+ID|Evidence|Recommendation|DISCOVERY_STARTED|REQUIREMENTS_PARTIAL|REQUIREMENTS_REFINING|REQUIREMENTS_READY|DESIGN_READY|已确认需求|待确认|未知需求|需求状态|需求编号|需求分类)\s*(?::|：|$)/iu;
const PRIVATE_TOKEN_PATTERN = /(?<![A-Za-z0-9_])(?:CONFIRMED|UNKNOWN|PROPOSED|OPEN_QUESTION|Requirement\s+ID|DISCOVERY_STARTED|REQUIREMENTS_PARTIAL|REQUIREMENTS_REFINING|REQUIREMENTS_READY|DESIGN_READY)(?![A-Za-z0-9_])/giu;
const RAW_CONTEXT_LINE_PATTERN = /^\s*(?:Project(?:\s+(?:mode|type))?|Current\s+project\s+context|工程上下文|当前工程上下文|RequirementContext(?:\s+part)?|Memory\s+Evidence|Graph\s+Evidence|Knowledge\s+Evidence|Multimodal\s+Evidence|Conflicts|Provenance)\s*:/iu;
const RAW_CONTEXT_KEY_PATTERN = /(?:"(?:project_id|conversation_id|standard_fields|requirements|constraints|proposed_items|open_questions|lifecycle|project_creation_mode|target_platform|product_goal)"\s*:|RequirementContext\s+part\s+\d+\/\d+)/iu;
const REPORT_HEADING_PATTERN = /^\s*#{0,6}\s*(?:项目需求分析|项目需求分析报告|需求分析报告|已确认需求|未确认需求|需求推断)\s*:?[ \t]*$/u;

function normalize(value: string): string {
  return value.trim().replace(/\s+/gu, " ").toLocaleLowerCase();
}

function hasPhrase(value: string, phrases: string[]): boolean {
  return phrases.some((phrase) => value.includes(phrase.toLocaleLowerCase()));
}

export function resolveConversationMode(
  question: string,
  route?: ConversationTurn["route"],
  previousAnswer?: string | null,
  responseType?: ResponseType | null,
): ConversationMode {
  if (responseType === "DOCUMENT") return "DOCUMENT";
  if (responseType === "ARTIFACT") return "EXPLORATION";
  const normalized = normalize(question);
  if (hasPhrase(normalized, EXECUTION_PHRASES)) return "EXECUTION";
  if (hasPhrase(normalized, DESIGN_PHRASES)) return "DESIGN";

  switch (route) {
    case "GREENFIELD_DESIGN":
      return "DESIGN";
    case "BROWNFIELD_ANALYSIS":
    case "DEBUG_ANALYSIS":
    case "FIRMWARE":
      return "EXECUTION";
    default:
      return "EXPLORATION";
  }
}

export function resolveResponseType(input: { responseType?: ResponseType | null; question: string; previousAnswer?: string | null }): ResponseType {
  if (input.responseType === "CHAT" || input.responseType === "DOCUMENT" || input.responseType === "ARTIFACT") return input.responseType;
  return "CHAT";
}

/**
 * Renderer selection is a presentation of the durable response contract.
 * Answer length, headings and Markdown AST are never selection signals.
 */
export function resolveRendererDecision(input: { responseType?: ResponseType | null; question: string; route?: ConversationTurn["route"]; previousAnswer?: string | null; failed?: boolean; clarification?: boolean }): RendererDecision {
  if (input.failed || input.clarification) return { responseType: "CHAT", mode: "EXPLORATION", renderer: "chat-markdown", source: "default-chat" };
  const hasMetadata = input.responseType === "CHAT" || input.responseType === "DOCUMENT" || input.responseType === "ARTIFACT";
  const responseType = resolveResponseType(input);
  return {
    responseType,
    mode: resolveConversationMode(input.question, input.route, input.previousAnswer, responseType),
    renderer: responseType === "DOCUMENT" ? "document" : responseType === "ARTIFACT" ? "artifact" : "chat-markdown",
    source: hasMetadata ? "response-metadata" : "default-chat",
  };
}

function sanitizeLine(line: string, mode: ConversationMode): string | null {
  const normalized = line.trim();
  if (!normalized) return "";
  if (PRIVATE_LINE_PATTERN.test(normalized) || RAW_CONTEXT_LINE_PATTERN.test(normalized) || RAW_CONTEXT_KEY_PATTERN.test(normalized)) return null;
  if (mode !== "DOCUMENT" && REPORT_HEADING_PATTERN.test(normalized)) return null;

  let cleaned = line
    .replace(/^\s*(?:根据当前工程信息|当前工程信息|已经确定)\s*[:：]?\s*/u, "")
    .replace(/^\s*目前还需要确认\s*[:：]?\s*/u, "接下来需要确认：")
    .replace(/^\s*当前需求理解\s*[:：]?\s*/u, "")
    .replace(PRIVATE_TOKEN_PATTERN, "")
    .replace(/[ \t]{2,}/gu, " ")
    .trim();
  return cleaned || null;
}

export function sanitizeAssistantMarkdown(content: string, mode: ConversationMode): string {
  let inFence = false;
  const lines: string[] = [];
  for (const line of content.split(/\r?\n/u)) {
    if (/^\s*```/u.test(line)) {
      inFence = !inFence;
      lines.push(line);
      continue;
    }
    if (inFence) {
      lines.push(line);
      continue;
    }
    const cleaned = sanitizeLine(line, mode);
    if (cleaned !== null) lines.push(cleaned);
  }
  return lines.join("\n").replace(/\n{3,}/gu, "\n\n").trim();
}
