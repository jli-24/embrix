import type { ChatAnswer } from "../api/chatClient";
import { demoContextReferences, demoProposal } from "./demoEvidence";

export type DemoRecommendation = {
  label: string;
  risk: "LOW";
  impact: string;
  status: "REVIEW_REQUIRED";
};

export type DemoChat = {
  question: string;
  answer: string;
  createdAt: string;
  references: typeof demoContextReferences;
  recommendations: DemoRecommendation[];
};

export const demoChat: DemoChat = {
  question: "为什么功耗这么高？ 平均功耗 120mA",
  answer: [
    "根据工程分析和最近的 HIL Observation，当前功耗偏高的可能原因如下：",
    "",
    "1. Wi-Fi 未进入低功耗模式",
    "2. I2C 传感器采样频率过高",
    "3. OLED 刷新率过高",
    "",
    "Evidence",
    "[HIL Observation]  [Power Analysis]  [Firmware Analysis]",
    "",
    "Recommendation",
    "✓ 启用 Wi-Fi modem-sleep",
    "✓ 将传感器采样频率调整为 10Hz",
    "✓ 降低 OLED 刷新率至 10Hz",
  ].join("\n"),
  createdAt: "2026-08-10T14:33:00+08:00",
  references: demoContextReferences,
  recommendations: [{ label: demoProposal.display_label, risk: "LOW", impact: "Power reduction", status: "REVIEW_REQUIRED" }],
};

export const demoChatAnswer: ChatAnswer = {
  answer_id: "ui-preview-answer-1",
  status: "COMPLETED",
  answer_text: demoChat.answer,
  response_type: "CHAT",
  artifact_refs: [],
  evidence_references: demoChat.references,
  evidence_details: [],
  recommendations: [],
  created_at: demoChat.createdAt,
  fingerprint: `sha256:${"f".repeat(64)}`,
};
