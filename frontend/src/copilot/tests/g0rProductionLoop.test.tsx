import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ResearchReviewPanel } from "../chat/ResearchReviewPanel";
import type { EngineeringResearchSnapshot, RequirementChangeProposal } from "../api/projectClient";

const snapshot: EngineeringResearchSnapshot = {
  classification: "DIRECT_MATCH",
  evidence_pack: {
    status: "COMPLETE",
    projects: [],
    source_reports: [],
    fingerprint: "sha256:" + "1".repeat(64),
  },
  reference_reviews: [{
    project_identity: "alice/watch",
    title: "Watch reference",
    description: "公开参考项目",
    decision: "DEFER",
    selected_features: [],
    feature_deltas: [{
      delta_id: "delta-1",
      feature: "GPS",
      delta: "REFERENCE_EXTRA",
      evidence_state: "PRESENT",
      evidence_refs: ["github:alice/watch:fact:gps"],
      description: "参考项目包含 GPS",
      fingerprint: "sha256:" + "2".repeat(64),
    }],
    fingerprint: "sha256:" + "3".repeat(64),
  }],
  feature_decisions: [],
  fingerprint: "sha256:" + "4".repeat(64),
};

const proposal: RequirementChangeProposal = {
  proposal_id: "proposal-1",
  base_requirement_fingerprint: "sha256:" + "5".repeat(64),
  research_fingerprint: null,
  decisions: [],
  summary: "ADD VIBRATION",
  status: "PENDING",
  fingerprint: "sha256:" + "6".repeat(64),
};

describe("G0R production review surface", () => {
  it("exposes typed reference and final confirmation controls", () => {
    const markup = renderToStaticMarkup(<ResearchReviewPanel snapshot={snapshot} proposal={proposal} onReferenceDecision={() => undefined} onFeatureDecision={() => undefined} onConfirmProposal={() => undefined} />);
    expect(markup).toContain("参考项目审阅");
    expect(markup).toContain("排除");
    expect(markup).toContain("采纳 GPS");
    expect(markup).toContain("确认提交");
    expect(markup).toContain('data-g0r-production-review="true"');
  });

  it.each([
    ["UNAVAILABLE", "参考资料检索暂不可用"],
    ["NO_RELEVANT_REFERENCE", "未找到相关参考项目"],
    ["PARTIAL", "部分参考资料可用"],
  ] as const)("explains empty research status %s without implying model failure", (status, label) => {
    const markup = renderToStaticMarkup(<ResearchReviewPanel snapshot={{ ...snapshot, classification: "NO_RELEVANT_REFERENCE", evidence_pack: { ...snapshot.evidence_pack, status }, reference_reviews: [] }} onReferenceDecision={() => undefined} onFeatureDecision={() => undefined} onConfirmProposal={() => undefined} />);
    expect(markup).toContain(label);
    expect(markup).toContain('data-research-empty="true"');
    expect(markup).not.toContain("UNAVAILABLE · NO_RELEVANT_REFERENCE");
    expect(markup).not.toContain("采纳 GPS");
  });

  it("keeps pending confirmation visible when no reference project is available", () => {
    const markup = renderToStaticMarkup(<ResearchReviewPanel snapshot={{ ...snapshot, evidence_pack: { ...snapshot.evidence_pack, status: "PARTIAL" }, reference_reviews: [] }} proposal={proposal} onReferenceDecision={() => undefined} onFeatureDecision={() => undefined} onConfirmProposal={() => undefined} />);
    expect(markup).toContain("部分参考资料可用");
    expect(markup).toContain("确认提交");
    expect(markup).not.toContain('data-research-empty="true"');
  });

});
