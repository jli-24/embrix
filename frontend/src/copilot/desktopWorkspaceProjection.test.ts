import { describe, expect, it } from "vitest";

import { projectMemoryProjection, projectTaskProjection, projectWorkflow } from "./desktopWorkspaceProjection";
import type { DesktopWorkspaceProjection } from "./api/desktopWorkspaceClient";

const projection = { schema_version: "v6.7", identity: { tenant_id: "tenant", requester_id: "desktop", project_id: "project-a", project_fingerprint: "sha256:a" }, project: { project_id: "project-a", tenant_id: "tenant", name: "A", relative_path: "a", status: "READY", fingerprint: "sha256:a", initialization_status: "READY" }, context: null, conversations: { items: [{ reference_id: "conversation-1", kind: "CONVERSATION", status: "COMPLETED", summary: "I2C analysis", fingerprint: "sha256:c", source_references: [], limitations: [], confidence: null }], next_cursor: null, limit: 20, total_available: null }, evidence: { items: [], next_cursor: null, limit: 20, total_available: null }, memory: { items: [{ reference_id: "memory-1", kind: "MEMORY", status: "ACTIVE", summary: "Approved memory", fingerprint: "sha256:m", source_references: ["evidence-1"], limitations: [], confidence: 0.92 }], next_cursor: null, limit: 20, total_available: null }, knowledge: { items: [], next_cursor: null, limit: 20, total_available: null }, reviews: { items: [], next_cursor: null, limit: 20, total_available: null }, validations: { items: [], next_cursor: null, limit: 20, total_available: null }, findings: { items: [], next_cursor: null, limit: 20, total_available: null }, proposals: { items: [], next_cursor: null, limit: 20, total_available: null }, changes: { items: [], next_cursor: null, limit: 20, total_available: null }, operations: { items: [], next_cursor: null, limit: 20, total_available: null }, audit: { items: [], next_cursor: null, limit: 20, total_available: null }, capabilities: [], workflow_summary: { status: "APPROVED", current_reference: "conversation-1", stages: ["APPROVED"], source_references: [] }, generated_at: "2026-08-15T00:00:00Z", projection_fingerprint: "sha256:p" } as DesktopWorkspaceProjection;

describe("backend-authoritative Desktop projection adapter", () => {
  it("preserves backend memory confidence and workflow status", () => {
    expect(projectMemoryProjection(projection).items[0]?.confidence).toBe("confirmed");
    expect(projectTaskProjection(projection, "conversation-1")?.conversationStatus).toBe("completed");
    expect(projectWorkflow(projection).status).toBe("idle");
  });

  it("only marks a scoped canonical application as a finished workflow", () => {
    expect(projectWorkflow({ ...projection, workflow_summary: { ...projection.workflow_summary, status: "APPLIED", current_reference: "operation-1" } }).status).toBe("finished");
    expect(projectWorkflow({ ...projection, workflow_summary: { ...projection.workflow_summary, status: "UNAVAILABLE" } }).status).toBe("idle");
  });
});
