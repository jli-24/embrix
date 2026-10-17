import { describe, expect, it, vi } from "vitest";

import { fetchDesktopWorkspace } from "./desktopWorkspaceClient";

describe("desktop workspace projection client", () => {
  it("uses the canonical bounded projection route and desktop identity headers", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ schema_version: "v6.7" }), { status: 200 }));
    await fetchDesktopWorkspace("project A", { limit: 7, cursor: "cursor-1", fetcher });
    expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/api/v1/copilot/desktop/projects/project%20A/workspace?limit=7&cursor=cursor-1"), expect.objectContaining({ headers: { "X-Copilot-Requester": "desktop-user", "X-Environment-Tenant": "desktop-tenant" } }));
  });

  it("rejects a non-v6.7 response instead of fabricating projection state", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ schema_version: "legacy" }), { status: 200 }));
    await expect(fetchDesktopWorkspace("project-1", { fetcher })).rejects.toMatchObject({ code: "DESKTOP_WORKSPACE_RESPONSE_INVALID" });
  });

  it("passes the selected conversation explicitly to the Owner projection", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ schema_version: "v6.7" }), { status: 200 }));
    await fetchDesktopWorkspace("project-1", { conversationId: "conversation-b", fetcher });
    expect(fetcher.mock.calls[0][0]).toContain("conversation_id=conversation-b");
  });

  it("passes an explicit task scope for canonical workflow status", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ schema_version: "v6.7" }), { status: 200 }));
    await fetchDesktopWorkspace("project-1", { taskId: "task-current", fetcher });
    expect(fetcher.mock.calls[0][0]).toContain("task_id=task-current");
  });
});
