import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CopilotChatRequestError, submitQuestion } from "../api/chatClient";
import { fetchProviderConfiguration, ProviderRequestError } from "../api/providerClient";
import { getDesktopSessionState, markDesktopSessionActive, markDesktopSessionExpired, readDesktopSessionErrorCode } from "../api/sessionState";
import { SettingsPanel } from "../settings/SettingsPanel";

const preferences = {
  theme: "system" as const,
  density: "comfortable" as const,
  answerDetail: "standard" as const,
  evidenceDisplay: "expanded" as const,
};

afterEach(() => markDesktopSessionActive());

describe("desktop session lifecycle", () => {
  it("maps the safe expired response and blocks later provider requests", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ error: "DESKTOP_SESSION_EXPIRED" }), { status: 401 }));

    await expect(fetchProviderConfiguration(fetcher)).rejects.toEqual(new ProviderRequestError("DESKTOP_SESSION_EXPIRED"));
    expect(getDesktopSessionState()).toBe("EXPIRED");
    await expect(fetchProviderConfiguration(fetcher)).rejects.toEqual(new ProviderRequestError("DESKTOP_SESSION_EXPIRED"));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("reads FastAPI detail responses through the shared session boundary", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ detail: "DESKTOP_SESSION_EXPIRED" }), { status: 401 }));

    await expect(fetchProviderConfiguration(fetcher)).rejects.toEqual(new ProviderRequestError("DESKTOP_SESSION_EXPIRED"));
    expect(readDesktopSessionErrorCode({ detail: "DESKTOP_SESSION_EXPIRED" })).toBe("DESKTOP_SESSION_EXPIRED");
    expect(getDesktopSessionState()).toBe("EXPIRED");
  });

  it("blocks protected chat actions before sending a request after expiry", async () => {
    const fetcher = vi.fn<typeof fetch>();
    markDesktopSessionExpired();
    const project = { tenant_id: "tenant", project_id: "project", project_name_reference: "Project", workspace_reference: "workspace", fingerprint: "sha256:project" };

    await expect(submitQuestion("session", project, "question", [], "standard", false, fetcher)).rejects.toEqual(new CopilotChatRequestError("DESKTOP_SESSION_EXPIRED"));
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("surfaces expiry and disables provider actions instead of showing cached availability", () => {
    markDesktopSessionExpired();
    const markup = renderToStaticMarkup(<SettingsPanel preferences={preferences} onChange={() => undefined} onClose={() => undefined} capabilities={[{ capability: "PROVIDER_CONFIGURATION", status: "AVAILABLE", reason_code: "READY", detail: "ready", fingerprint: "sha256:capability" }]} />);

    expect(markup).toContain("Desktop 会话已过期");
    expect(markup).not.toContain("Provider 配置保存失败");
    expect(markup).toContain("会话已过期");
    expect(markup).toContain("disabled=\"\"");
  });
});
