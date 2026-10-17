import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReviewQueuePanel } from "./ReviewQueuePanel";

describe("v6.3 Review Queue panel", () => {
  it("renders safe review identity and status without mutation controls", () => {
    const markup = renderToStaticMarkup(<ReviewQueuePanel reviews={[{
      review_id: "review-1", project_id: "project-1", target_type: "MEMORY_CANDIDATE",
      target_id: "memory-candidate-1", target_fingerprint: `sha256:${"a".repeat(64)}`,
      requester: "engineer-1", status: "PENDING", created_at: "2026-08-14T00:00:00Z",
      decision: null, reviewer: null, decided_at: null, fingerprint: `sha256:${"b".repeat(64)}`,
    }]} />);
    expect(markup).toContain("MEMORY_CANDIDATE");
    expect(markup).toContain("PENDING");
    expect(markup).not.toContain("Approve");
    expect(markup).not.toContain("Apply");
  });
});
