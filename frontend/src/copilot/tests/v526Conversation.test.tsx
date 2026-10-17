import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatPanel } from "../chat/ChatPanel";
import { UserMessage } from "../chat/UserMessage";
import type { ConversationTurn } from "../api/projectClient";

const fingerprint = `sha256:${"a".repeat(64)}`;
const turn: ConversationTurn = {
  turn_id: "turn-conversation-fidelity",
  request_id: "request-conversation-fidelity",
  status: "COMPLETED",
  question: "Why does I2C read fail?",
  answer_text: "The current project evidence points to a timing and signal-quality issue.",
  evidence: [{
    source_type: "HIL",
    label: "I2C timeout observation",
    reference_id: "hil_obs_20250810",
    status: "Observed",
    summary: "I2C timeout observed on the target",
    source_fingerprint: fingerprint,
    observed_at: "2026-08-10T14:10:00",
  }],
  recommendations: [{
    review: "Review I2C bus timing",
    reason: "Proposal based on the observed timeout",
    status: "Proposal",
    reference_id: "proposal-1",
    source_fingerprint: fingerprint,
  }],
  context_references: [],
  error_code: null,
  created_at: "2026-08-10T14:14:00",
  completed_at: "2026-08-10T14:15:00",
};

const chatProps = {
  sessionReady: true,
  turns: [turn],
  pendingQuestion: null,
  availableReferences: [],
  pending: false,
  error: null,
  selected: [],
  evidenceDisplay: "expanded" as const,
  onToggleReference: () => undefined,
  onAttach: () => undefined,
  onSubmit: () => undefined,
  onRetry: () => undefined,
};

describe("v5.2.6 conversation fidelity", () => {
  it("renders a compact right-aligned question with the existing turn time", () => {
    const markup = renderToStaticMarkup(<UserMessage question="Why does I2C read fail?" createdAt="2026-08-10T14:14:00" />);
    expect(markup).toContain("ds-message--user");
    expect(markup).toContain("Why does I2C read fail?");
    expect(markup).toContain('class="ide-user-time"');
    expect(markup).toContain(">14:14</time>");
    expect(markup).not.toContain(">你<");
  });

  it("does not invent a time when the message timestamp is invalid", () => {
    const markup = renderToStaticMarkup(<UserMessage question="No timestamp" createdAt="not-a-date" />);
    expect(markup).not.toContain("ide-user-time");
  });

  it("keeps the conversation flow without legacy report projections", () => {
    const markup = renderToStaticMarkup(<ChatPanel title="I2C failure analysis" projectName="ESP32-S3 Sensor Gateway" updatedAt="2026-08-10T14:14:00" {...chatProps} />);
    for (const label of ["Why does I2C read fail?", "Embrix", "The current project evidence points to a timing and signal-quality issue."]) expect(markup).toContain(label);
    expect(markup).not.toContain("复制全部");
    expect(markup).not.toContain("复制代码");
    for (const label of ["I2C timeout observation", "Observed", "reference: hil_obs_20250810", "2026-08-10 14:10", "fingerprint", "Review I2C bus timing", "ide-recommendation-list"]) expect(markup).not.toContain(label);
    expect(markup).not.toContain("ESP32-S3 Sensor Gateway");
    expect(markup).not.toContain("I2C failure analysis");
    expect(markup).toContain('data-renderer="chat-markdown"');
    // The DOM marker documents that the legacy fixed dashboard is removed;
    // it is not a visible action label and should not trip this copy check.
    const visibleMarkup = markup.replace(/data-fixed-analysis-dashboard-removed="true"/g, "");
    for (const forbidden of ["Apply", "Execute", "Fix", "Build", "Flash", "avatar", "robot"]) expect(visibleMarkup.toLowerCase()).not.toContain(forbidden.toLowerCase());
  });
});
