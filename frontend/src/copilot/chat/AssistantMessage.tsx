import { Message } from "../../design-system/components";
import type { ChatEvidence, ChatRecommendation, ConversationTurn } from "../api/projectClient";
import { DocumentRenderer } from "./DocumentRenderer";
import { ArtifactRenderer } from "./ArtifactRenderer";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { resolveRendererDecision, sanitizeAssistantMarkdown, type ResponseType } from "./conversationRenderer";

function failureMessage(errorCode?: string | null, analysisState?: string) {
  if (analysisState === "WAITING_FOR_CLARIFICATION") return "我还不能确定你希望分析哪一部分。\n\n你可以告诉我：\n• 设计一个新的功能或模块\n• 分析已有源码\n• 排查编译/运行问题\n• 查找某个函数、外设或调用关系";
  if (errorCode === "DESKTOP_SESSION_EXPIRED" || errorCode?.includes("会话已过期")) return "当前桌面会话已过期。\n\n建议：重新启动 Embrix 后继续当前工程。";
  if (analysisState === "PROVIDER_UNAVAILABLE") return "当前无法连接模型服务。\n\n原因：模型服务暂不可用。\n\n建议：稍后重试，或检查 Provider 配置。";
  if (analysisState === "INSUFFICIENT_EVIDENCE") return "当前工程信息还不够。\n\n建议：补充相关源码、日志或硬件信息后再继续。";
  if (errorCode === "COPILOT_CHAT_PROVIDER_NOT_CONFIGURED") return "当前无法连接模型服务。\n\n原因：模型服务尚未配置。\n\n建议：先在设置中完成 Provider 配置。";
  if (errorCode === "COPILOT_CHAT_PROVIDER_UNAVAILABLE") return "当前无法连接模型服务。\n\n原因：模型服务暂不可用。\n\n建议：稍后重试。";
  if (errorCode === "PROVIDER_AUTH_FAILED") return "当前无法连接模型服务。\n\n原因：认证失败。\n\n建议：检查 Provider 配置后重新测试连接。";
  if (errorCode === "PROVIDER_NETWORK_UNAVAILABLE" || errorCode === "PROVIDER_TRANSPORT_FAILED") return "当前无法连接模型服务。\n\n原因：网络连接失败。\n\n建议：检查网络后重试。";
  if (errorCode === "PROVIDER_MODEL_UNAVAILABLE") return "当前无法完成请求。\n\n原因：当前模型暂不可用。\n\n建议：稍后重试或检查模型配置。";
  if (errorCode === "PROVIDER_RATE_LIMITED") return "当前无法连接模型服务。\n\n原因：请求频率或配额受限。\n\n建议：稍后重试或检查 Provider 配额。";
  if (errorCode === "PROVIDER_CONFIG_INVALID") return "当前无法连接模型服务。\n\n原因：Provider 配置无效。\n\n建议：检查配置后重新测试连接。";
  if (errorCode === "PROVIDER_PARSE_FAILED") return "模型服务已经响应，但内容无法读取。\n\n建议：重新发送当前问题。";
  if (errorCode === "PROVIDER_CONNECTION_UNVERIFIED") return "当前无法发送工程问题。\n\n原因：模型服务尚未通过连接测试。\n\n建议：先测试 Provider 连接。";
  if (errorCode === "EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED") return "发送工程信息前需要你的确认。\n\n请确认后再继续。";
  if (errorCode === "EXTERNAL_TRANSMISSION_DENIED") return "本次请求没有发送到外部模型。\n\n你可以调整外发设置后重试。";
  if (errorCode === "EXTERNAL_TRANSMISSION_APPROVAL_EXPIRED" || errorCode === "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH") return "本次请求的确认已失效。\n\n建议：重新提交当前问题。";
  if (errorCode === "ANSWER_EMPTY") return "模型没有返回内容。\n\n建议：重新发送当前问题。";
  if (errorCode === "ANSWER_SCHEMA_INVALID" || errorCode === "ANSWER_CONTRACT_MISMATCH") return "这次回答没有通过格式校验。\n\n建议：重新发送当前问题。";
  if (errorCode === "ANSWER_REJECTED_UNSAFE") return "这次回答没有通过安全校验。\n\n建议：调整问题范围后重试。";
  if (errorCode === "ANSWER_REJECTED_EVIDENCE_MISMATCH") return "这次回答与当前工程信息不匹配。\n\n建议：补充相关工程证据后重试。";
  if (errorCode === "COPILOT_CHAT_EMPTY_PROJECT" || errorCode === "COPILOT_CHAT_NO_ANALYZABLE_SOURCE") return "当前工程还没有可分析的内容。\n\n建议：先选择工程目录或加入可分析的源码。";
  if (errorCode === "COPILOT_CHAT_INSUFFICIENT_EVIDENCE") return "我还缺少足够的工程信息，暂时不能可靠回答。\n\n建议：补充源码、日志或硬件信息。";
  if (analysisState === "FAILED" || errorCode === "ANALYSIS_RUNTIME_FAILED") return "这次处理没有完成。\n\n建议：稍后重试。";
  return "这次处理没有完成。\n\n建议：重新发送当前问题。";
}

export type AssistantMessageProps = {
  answer: string | null;
  evidence: ChatEvidence[];
  recommendations: ChatRecommendation[];
  pending: boolean;
  failed?: boolean;
  errorCode?: string | null;
  analysisState?: string;
  evidenceDisplay: "expanded" | "compact";
  onEvidenceSelect?: (evidence: ChatEvidence) => void;
  question?: string;
  route?: ConversationTurn["route"];
  previousAnswer?: string | null;
  responseType?: ResponseType | null;
  artifactRefs?: string[];
};

export function AssistantMessage({ answer, pending, failed, errorCode, analysisState, question = "", route, previousAnswer, responseType, artifactRefs = [] }: AssistantMessageProps) {
  const clarification = analysisState === "WAITING_FOR_CLARIFICATION";
  const source = failed || clarification ? failureMessage(errorCode, analysisState) : answer?.trim() ? answer : pending ? "我正在处理这个工程问题，请稍候。" : "暂无回复";
  const decision = resolveRendererDecision({ responseType, question, route, previousAnswer, failed, clarification });
  const content = sanitizeAssistantMarkdown(source, decision.mode);
  return <Message variant="assistant" label="Embrix"><div className="ide-answer-body" aria-label="Embrix 回复" data-response-type={decision.responseType} data-response-type-source={decision.source}>
    {decision.renderer === "document" ? <DocumentRenderer content={content} /> : decision.renderer === "artifact" ? <ArtifactRenderer content={content} artifactRefs={artifactRefs} /> : <MarkdownRenderer content={content} mode={decision.mode} />}
  </div></Message>;
}
