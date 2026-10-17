import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";
import { RightRuntimePanel } from "./RightRuntimePanel";

it("shows Owner currentness and a stable blocked reason for the selected conversation", () => {
  const projection = {
    identity: { project_id: "project-a" },
    workflow_summary: { status: "UNAVAILABLE" },
    evidence: { items: [] },
    memory: { items: [] },
    reviews: { items: [] },
    validations: { items: [] },
    findings: { items: [] },
    proposals: { items: [] },
    changes: { items: [] },
    operations: { items: [] },
    audit: { items: [] },
    authoritative_facts: [{
      domain: "SCHEMATIC",
      owner: "ProjectCatalog",
      project_id: "project-a",
      conversation_id: "conversation-b",
      object_id: "plan-a",
      revision: "r1",
      fingerprint: "sha256:plan",
      source_reference: "conversation:conversation-b",
      source_fingerprint: "sha256:plan",
      currentness: "STALE",
      reason_code: "CIRCUIT_REVISION_CHANGED",
    }],
  } as unknown as DesktopWorkspaceProjection;
  const markup = renderToStaticMarkup(<RightRuntimePanel
    activeProjectIdentity={null}
    context={null}
    panel={null}
    serviceError={null}
    workspaceProjection={projection}
  />);
  expect(markup).toContain('data-component="s01-owner-fact"');
  expect(markup).toContain("SCHEMATIC · conversation-b");
  expect(markup).toContain("STALE · r1");
  expect(markup).toContain("CIRCUIT_REVISION_CHANGED");
  expect(markup).toContain("ProjectCatalog · conversation:conversation-b");
});
