import type { ConversationDetail } from "./api/projectClient";

export function buildConversationReport(conversation: ConversationDetail): string {
  const lines = ["# Embedded Copilot Conversation Report", ""];
  for (const turn of conversation.turns.filter((item) => item.status === "COMPLETED")) {
    lines.push("### 问题", "", turn.question, "", `创建时间：${turn.created_at}`, "", "### 回答", "", turn.answer_text ?? "回答不可用", "", `完成时间：${turn.completed_at ?? "不可用"}`, "");
    if (turn.evidence.length) {
      lines.push("### 证据", "", ...turn.evidence.map((item) => `- ${item.label}\n  - Reference: ${item.reference_id}\n  - Status: ${item.status}\n  - Fingerprint: ${item.source_fingerprint}\n  - Observed: ${item.observed_at}`), "");
    }
  }
  return lines.join("\n");
}
