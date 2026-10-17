import { describe, expect, it, vi } from "vitest";
import { classifyWorkbenchResourceState, fetchWorkbench, type WorkbenchProjection } from "./workbenchClient";

const projection: WorkbenchProjection = {
  schema_version: "workbench-v1",
  project_id: "project-1",
  project_fingerprint: `sha256:${"a".repeat(64)}`,
  resources: [],
  findings: [],
  repair_alternatives: [],
  truncated: false,
  generated_at: "2026-08-31T00:00:00Z",
  projection_fingerprint: `sha256:${"b".repeat(64)}`,
};

describe("workbench projection client", () => {
  it("keeps physical-empty and projected-zero states distinct", () => {
    expect(classifyWorkbenchResourceState(projection)).toBe("LOADED_ZERO");
    expect(classifyWorkbenchResourceState({ ...projection, directory_state: "EMPTY" })).toBe("EMPTY_DIRECTORY");
    expect(classifyWorkbenchResourceState({ ...projection, resources: [{ resource_id: "resource-1", parent_id: null, name: "main.c", relative_path: "main.c", node_type: "FILE", category: "FIRMWARE", language: "C", source_fingerprint: null }] })).toBe("LOADED");
  });

  it("requests the additive read-only projection and validates its version", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(projection), { status: 200 }));
    await expect(fetchWorkbench("project/1", { fetcher })).resolves.toEqual(projection);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/copilot/desktop/projects/project%2F1/workbench?limit=500", expect.objectContaining({ credentials: "same-origin" }));
  });

  it("rejects an invalid response schema", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ ...projection, schema_version: "v6.7" }), { status: 200 }));
    await expect(fetchWorkbench("project-1", { fetcher })).rejects.toMatchObject({ code: "DESKTOP_WORKBENCH_RESPONSE_INVALID" });
  });
});
