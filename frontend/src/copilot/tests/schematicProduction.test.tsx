import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SchematicProductionProposalCard } from "../layout/AgentSurfacePanels";
import type { SchematicProductionState } from "../api/projectClient";

const state: SchematicProductionState = {
  request: { request_id: "request-1", project_id: "project-1", conversation_id: "conversation-1", requester: "engineer-1", target_relative_path: "hardware/main.kicad_sch", fingerprint: "sha256:" + "1".repeat(64) },
  proposal: { proposal_id: "proposal-1", components: [{ reference: "U1", value: "Controller", symbol_binding: "MCU:STM32F0" }], nets: [{ net_name: "VCC", nodes: ["U1.1", "J1.1"] }], status: "AWAITING_APPROVAL", fingerprint: "sha256:" + "2".repeat(64) },
  decision: { decision: "PENDING", reviewer: null, decided_at: null },
  operation_id: null,
  status: "AWAITING_APPROVAL",
  fingerprint: "sha256:" + "3".repeat(64),
};

describe("schematic production proposal projection", () => {
  it("shows a safe approval card without internal identities", () => {
    const markup = renderToStaticMarkup(<SchematicProductionProposalCard production={state} onDecision={() => undefined} />);
    expect(markup).toContain("Schematic Production Proposal");
    expect(markup).toContain("hardware/main.kicad_sch");
    expect(markup).toContain("Approve Generation");
    expect(markup).toContain("Current Design Truth");
    expect(markup).not.toContain("sha256:");
    expect(markup).not.toContain("proposal-1");
  });
});
