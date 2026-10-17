import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { markDesktopSessionActive, markDesktopSessionExpired } from "../api/sessionState";
import { VersionUpdatePanel } from "./VersionUpdatePanel";

const candidateProjection = {
  state: "CANDIDATE",
  installed_identity: "1.2.0+build-41",
  channel: "stable",
  candidate: { product_version: "1.3.0", build_id: "build-42", sha256: "trusted-hash", size: 512, notes: ["修复问题"] },
  notes: [],
  error_code: null,
};

const runningIdentity = {
  schema_version: 1,
  product_name: "Embrix",
  product_version: "7.1.0",
  build_id: "running-build-123",
  commit: "abc123",
  dirty: false,
  source_fingerprint: "running-fingerprint",
  build_time: "2026-09-28T10:00:00+00:00",
  mode: "production",
  channel: "stable",
  platform: "windows",
  architecture: "amd64",
};

const mounted: Array<{ root: Root; container: HTMLDivElement }> = [];

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

async function render(fetcher: typeof fetch): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push({ root, container });
  await act(async () => root.render(<VersionUpdatePanel fetcher={fetcher} />));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  return container;
}

beforeAll(() => Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }));

afterEach(async () => {
  for (const item of mounted.splice(0)) {
    await act(async () => item.root.unmount());
    item.container.remove();
  }
  markDesktopSessionActive();
  vi.restoreAllMocks();
});

describe("VersionUpdatePanel", () => {
  it("confirms the exact build and digest inside Embrix before installing", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => String(url) === "/desktop/build-identity.json"
      ? jsonResponse(runningIdentity)
      : jsonResponse(candidateProjection));
    const browserConfirm = vi.spyOn(window, "confirm");
    const container = await render(fetcher);
    expect(container.textContent).toContain("发现候选");
    expect(container.textContent).toContain("trusted-hash");
    expect(container.textContent).not.toContain("最新");
    const install = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "安装更新");
    if (!(install instanceof HTMLButtonElement)) throw new Error("Missing install action");
    await act(async () => { install.click(); await Promise.resolve(); });
    expect(browserConfirm).not.toHaveBeenCalled();
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/install"))).toBe(false);
    const confirmation = container.querySelector('[data-update-confirmation]');
    expect(confirmation?.textContent).toContain("build-42");
    expect(confirmation?.textContent).toContain("trusted-hash");
    const confirmInstall = Array.from(confirmation?.querySelectorAll("button") ?? []).find((button) => button.textContent === "确认安装");
    if (!(confirmInstall instanceof HTMLButtonElement)) throw new Error("Missing confirmation action");
    await act(async () => { confirmInstall.click(); await Promise.resolve(); });
    expect(fetcher.mock.calls.find(([url]) => String(url).endsWith("/install"))?.[1]?.body).toBe(JSON.stringify({ build_id: "build-42", sha256: "trusted-hash" }));
    expect(container.textContent).toContain("发现候选");
  });

  it("cancels the in-app install confirmation without sending a request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => String(url) === "/desktop/build-identity.json"
      ? jsonResponse(runningIdentity)
      : jsonResponse(candidateProjection));
    const container = await render(fetcher);
    const install = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "安装更新");
    if (!(install instanceof HTMLButtonElement)) throw new Error("Missing install action");
    await act(async () => { install.click(); });
    const cancel = Array.from(container.querySelectorAll('[data-update-confirmation] button')).find((button) => button.textContent === "取消");
    if (!(cancel instanceof HTMLButtonElement)) throw new Error("Missing cancel action");
    await act(async () => { cancel.click(); });
    expect(container.querySelector('[data-update-confirmation]')).toBeNull();
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/install"))).toBe(false);
  });

  it("does not show candidate data from a non-candidate state", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => String(url) === "/desktop/build-identity.json"
      ? jsonResponse(runningIdentity)
      : jsonResponse({ ...candidateProjection, state: "VALIDATION_FAILED" }));
    const container = await render(fetcher);
    expect(container.textContent).toContain("校验失败");
    expect(container.textContent).not.toContain("trusted-hash");
    expect(Array.from(container.querySelectorAll("button")).some((button) => button.textContent === "安装更新")).toBe(false);
  });

  it("disables update actions for an expired desktop session", async () => {
    markDesktopSessionExpired();
    const fetcher = vi.fn<typeof fetch>();
    const container = await render(fetcher);
    const check = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "检查更新");
    expect(check).toBeInstanceOf(HTMLButtonElement);
    expect((check as HTMLButtonElement).disabled).toBe(true);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("shows the running package identity when the fixed-slot update service is unavailable", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => String(url) === "/desktop/build-identity.json"
      ? jsonResponse(runningIdentity)
      : jsonResponse({ detail: "DESKTOP_UPDATE_UNAVAILABLE" }, 503));
    const container = await render(fetcher);
    expect(container.textContent).toContain("运行包版本");
    expect(container.textContent).toContain("7.1.0");
    expect(container.textContent).toContain("running-build-123");
    expect(container.textContent).toContain("abc123");
    expect(container.textContent).toContain("running-fingerprint");
    expect(container.textContent).toContain("2026-09-28T10:00:00+00:00");
    expect(container.textContent).toContain("此包更新不可用");
    expect((Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "检查更新") as HTMLButtonElement).disabled).toBe(true);
    expect(fetcher.mock.calls.map(([url]) => url)).toContain("/desktop/build-identity.json");
  });

  it("keeps running package identity separate from fixed-slot identity", async () => {
    const slotProjection = { ...candidateProjection, state: "NO_UPDATE", installed_identity: { product_version: "7.0.0", build_id: "slot-build-456", source_fingerprint: "slot-fingerprint", build_time: "2026-09-01T10:00:00+00:00", mode: "production" } };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => String(url) === "/desktop/build-identity.json"
      ? jsonResponse(runningIdentity)
      : jsonResponse(slotProjection));
    const container = await render(fetcher);
    expect(container.textContent).toContain("运行包版本7.1.0");
    expect(container.textContent).toContain("运行包 Buildrunning-build-123");
    expect(container.textContent).toContain("固定安装槽版本7.0.0");
    expect(container.textContent).toContain("固定安装槽 Buildslot-build-456");
  });

  it("does not display malformed running package metadata as identity", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => String(url) === "/desktop/build-identity.json"
      ? jsonResponse({ ...runningIdentity, schema_version: 2 })
      : jsonResponse({ ...candidateProjection, state: "UNCHECKED" }));
    const container = await render(fetcher);
    expect(container.textContent).toContain("运行包身份不可用");
    expect(container.textContent).not.toContain("running-build-123");
    expect(container.textContent).toContain("未检查");
  });
});
