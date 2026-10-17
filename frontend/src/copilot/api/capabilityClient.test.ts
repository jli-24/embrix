import { describe, expect, it, vi } from "vitest";
import { fetchCapabilityStatus, CapabilityRequestError } from "./capabilityClient";

describe("capability client", () => {
  it("binds the Desktop identity and reads the explicit read-only boundary", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([{ capability: "BUILD", status: "UNAVAILABLE", reason_code: "READ_ONLY_DESKTOP_BOUNDARY", detail: "read only", fingerprint: `sha256:${"a".repeat(64)}` }]), { status: 200 }));
    const values = await fetchCapabilityStatus(fetcher);
    expect(values[0].status).toBe("UNAVAILABLE");
    expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({ "X-Copilot-Requester": "desktop-user", "X-Environment-Tenant": "desktop-tenant" });
  });

  it("maps unavailable capability projection to a stable error", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ error: "DESKTOP_IDENTITY_REQUIRED" }), { status: 401 }));
    await expect(fetchCapabilityStatus(fetcher)).rejects.toEqual(new CapabilityRequestError("DESKTOP_IDENTITY_REQUIRED"));
  });
});
