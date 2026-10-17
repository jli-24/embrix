import { describe, expect, it, vi } from "vitest";
import { analyzeDebug, analyzeFirmware, createReview, fetchGovernedKnowledgeProjection, fetchMemoryProjection, fetchReviewQueue, searchKnowledge } from "./engineeringClient";

function response(payload: unknown, ok = true): Response {
  return { ok, json: async () => payload } as Response;
}

describe("engineeringClient", () => {
  it("keeps project-bound headers and read-only knowledge payload", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response([]));
    await searchKnowledge("project-1", "I2C", fetcher);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/copilot/knowledge/search", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "X-Environment-Tenant": "desktop-tenant" }),
      body: JSON.stringify({ project_id: "project-1", query: "I2C", sources: ["LOCAL"], top_k: 4 }),
    }));
  });

  it("maps firmware, debug and review endpoints without execution controls", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ project_id: "project-1" }))
      .mockResolvedValueOnce(response({ project_id: "project-1" }))
      .mockResolvedValueOnce(response({ review_id: "review-1" }));
    await analyzeFirmware("project-1", "I2C init", fetcher);
    await analyzeDebug("project-1", "failure-1", "I2C_TIMEOUT", "timeout", fetcher);
    await createReview({ project_id: "project-1", target_type: "KNOWLEDGE", target_id: "knowledge-1", target_fingerprint: `sha256:${"a".repeat(64)}` }, fetcher);
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/copilot/firmware/analyze",
      "/api/v1/copilot/debug/analyze",
      "/api/v1/copilot/reviews",
    ]);
    expect(fetcher.mock.calls.every(([path]) => !String(path).includes("build") && !String(path).includes("flash"))).toBe(true);
  });

  it("exposes stable API errors", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ error: "DEBUG_ANALYSIS_UNAVAILABLE" }, false));
    await expect(analyzeDebug("project-1", "failure-1", "HARDFAULT", "fault", fetcher)).rejects.toMatchObject({ code: "DEBUG_ANALYSIS_UNAVAILABLE" });
  });

  it("accepts additive v6.3 review targets and safe projections", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response([]));
    await fetchReviewQueue("project-1", { target_type: "MEMORY_CANDIDATE" }, fetcher);
    await fetchMemoryProjection("project-1", fetcher);
    await fetchGovernedKnowledgeProjection("project-1", fetcher);
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/copilot/reviews?project_id=project-1&target_type=MEMORY_CANDIDATE",
      "/api/v1/copilot/memory?project_id=project-1",
      "/api/v1/copilot/knowledge/governed?project_id=project-1",
    ]);
  });

  it("keeps legacy TypeScript JSON handling forward-compatible for unknown enum values", async () => {
    const futureReview = { review_id: "review-future", project_id: "project-1", target_type: "FUTURE_TARGET", target_id: "target-1" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response([futureReview]));
    const queue = await fetchReviewQueue("project-1", {}, fetcher);
    expect(queue[0].target_type).toBe("FUTURE_TARGET");
  });
});
