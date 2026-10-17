import { describe, expect, it } from "vitest";

import type { ContextReference } from "../api/chatClient";
import type { AuditView, MemoryView, RuntimeHealth } from "../api/environmentClient";
import type { WorkspaceSnapshot } from "../api/workspaceClient";
import { buildConversationReport } from "../exportReport";
import {
  buildEngineeringTree,
  buildSafeMarkdown,
  countMemoryStatuses,
  findReferenceAudit,
  proposalReferences,
  runtimeHealthTone,
} from "../viewModels";

const fingerprint = `sha256:${"a".repeat(64)}`;

const workspace: WorkspaceSnapshot = {
  project_id: "gateway",
  status: "PROJECTED",
  fingerprint,
  artifacts: [
    { artifact_id: "fw-1", artifact_type: "FIRMWARE", status: "PROPOSED", filenames: ["main.c"] },
    { artifact_id: "pcb-1", artifact_type: "PCB_BOARD", status: "WAITING_APPROVAL", filenames: ["gateway.kicad_pcb"] },
    { artifact_id: "doc-1", artifact_type: "REQUIREMENT", status: "PROPOSED", filenames: ["requirements.md"] },
    { artifact_id: "test-1", artifact_type: "HIL_TEST", status: "PROPOSED", filenames: ["hil_plan.md"] },
    { artifact_id: "misc-1", artifact_type: "BINARY", status: "PROPOSED", filenames: ["firmware.bin"] },
  ],
};

const reference = (kind: ContextReference["reference_kind"]): ContextReference => ({
  tenant_id: "tenant-1",
  project_id: "gateway",
  reference_id: `${kind.toLowerCase()}-1`,
  reference_kind: kind,
  source_fingerprint: fingerprint,
  display_label: `${kind} reference`,
  fingerprint,
});

describe("copilot desktop view models", () => {
  it("exports only completed answer and evidence allowlist fields", () => {
    const report = buildConversationReport({ conversation_id: "c-1", project_id: "p-1", title: "Sensitive title", created_at: "2026-08-11T08:00:00Z", updated_at: "2026-08-11T08:01:00Z", fingerprint, turns: [{ turn_id: "t-1", request_id: "r-1", status: "COMPLETED", question: "I2C?", answer_text: "Answer", evidence: [{ source_type: "HIL", label: "Observation", reference_id: "obs-1", status: "Observed", summary: "summary", source_fingerprint: fingerprint, observed_at: "2026-08-11T08:00:30Z" }], recommendations: [{ review: "Do not export", reason: "private", status: "Proposal", reference_id: "rec-1", source_fingerprint: fingerprint }], context_references: [{ prompt: "hidden", raw_log: "secret" }], error_code: null, created_at: "2026-08-11T08:00:00Z", completed_at: "2026-08-11T08:01:00Z" }] });
    for (const allowed of ["I2C?", "Answer", "Observation", "obs-1", "Observed", "sha256:", "2026-08-11T08:00:30Z"]) expect(report).toContain(allowed);
    for (const forbidden of ["Sensitive title", "Do not export", "private", "prompt", "raw_log", "secret", "summary"]) expect(report).not.toContain(forbidden);
  });
  it("groups only projected artifacts and filenames", () => {
    const tree = buildEngineeringTree(workspace);
    expect(tree.map((group) => group.label)).toEqual(["Firmware", "Hardware", "Documents", "Tests", "Other"]);
    expect(tree[0].artifacts[0].filenames).toEqual(["main.c"]);
    expect(JSON.stringify(tree)).not.toContain("components/");
    expect(buildEngineeringTree(null).every((group) => group.artifacts.length === 0)).toBe(true);
  });

  it("matches evidence audit by exact reference identity", () => {
    const evidence = reference("EVIDENCE");
    const audit: AuditView[] = [
      { event_type: "HIL_OBSERVED", object_reference: "evidence-10", status: "OBSERVED", timestamp: "2026-08-10T14:30:00Z" },
      { event_type: "HIL_OBSERVED", object_reference: "evidence-1", status: "OBSERVED", timestamp: "2026-08-10T14:31:00Z" },
    ];
    expect(findReferenceAudit(evidence, audit)?.timestamp).toBe("2026-08-10T14:31:00Z");
  });

  it("uses only proposal references for recommendations", () => {
    expect(proposalReferences([reference("EVIDENCE"), reference("PROPOSAL")])).toHaveLength(1);
  });

  it("counts exact memory statuses and keeps health qualitative", () => {
    const memory: MemoryView[] = [
      { memory_reference: "m-1", status: "REVIEW_REQUIRED" },
      { memory_reference: "m-2", status: "APPROVED" },
      { memory_reference: "m-3", status: "APPROVED" },
      { memory_reference: "m-4", status: "REJECTED" },
    ];
    expect(countMemoryStatuses(memory)).toEqual({ REVIEW_REQUIRED: 1, APPROVED: 2, REJECTED: 1 });
    expect(runtimeHealthTone({ component: "MODEL", status: "HEALTHY", observed_at: "2026-08-10T14:30:00Z" } satisfies RuntimeHealth)).toBe("healthy");
  });

  it("exports only the current safe exchange fields", () => {
    const exported = buildSafeMarkdown({
      projectName: "Gateway",
      question: "Why is power high?",
      answer: "The projected evidence indicates sustained activity.",
      createdAt: "2026-08-10T14:30:00Z",
      references: [reference("EVIDENCE")],
    });
    expect(exported).toContain("Why is power high?");
    expect(exported).toContain("evidence-1");
    for (const forbidden of ["Prompt", "Token", "Raw evidence", "Secret", "Provider payload"]) expect(exported).not.toContain(forbidden);
  });
});
