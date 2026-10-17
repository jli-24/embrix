import { describe, expect, it, vi } from "vitest";

import { checkDesktopUpdate, fetchDesktopUpdateStatus, installDesktopUpdate, restartDesktopApp } from "./desktopUpdateClient";
import { getDesktopSessionState, markDesktopSessionActive } from "./sessionState";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("desktop update API client", () => {
  it("uses the status and check endpoints", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse({ state: "UNCHECKED" }));
    await fetchDesktopUpdateStatus(fetcher);
    await checkDesktopUpdate(fetcher);
    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/copilot/desktop/update/status", "GET"],
      ["/api/v1/copilot/desktop/update/check", "POST"],
    ]);
  });

  it("binds installation to the candidate build and digest and supports restart", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse({ state: "WAIT_RESTART" }));
    await installDesktopUpdate({ build_id: "build-42", sha256: "abc123" }, fetcher);
    await restartDesktopApp(fetcher);
    expect(fetcher.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ build_id: "build-42", sha256: "abc123" }));
    expect(fetcher.mock.calls[1]?.[0]).toBe("/api/v1/copilot/desktop/update/restart");
  });

  it("returns backend rejection codes", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "UPDATE_IDENTITY_MISMATCH" }, 409));
    await expect(installDesktopUpdate({ build_id: "build-42", sha256: "abc123" }, fetcher)).rejects.toMatchObject({ code: "UPDATE_IDENTITY_MISMATCH" });
  });

  it("expires the shared desktop session when the backend reports its session expired", async () => {
    markDesktopSessionActive();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "DESKTOP_SESSION_EXPIRED" }, 401));
    await expect(fetchDesktopUpdateStatus(fetcher)).rejects.toMatchObject({ code: "DESKTOP_SESSION_EXPIRED" });
    expect(getDesktopSessionState()).toBe("EXPIRED");
    markDesktopSessionActive();
  });
});
