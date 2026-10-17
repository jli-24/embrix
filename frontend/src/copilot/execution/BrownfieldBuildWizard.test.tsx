import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BrownfieldBuildWizard } from "./BrownfieldBuildWizard";

const preview = {
  project_id: "project-1", project_fingerprint: "sha256:project", workspace_fingerprint: "sha256:workspace",
  workspace_target_id: "target-1", workspace_target_fingerprint: "sha256:target",
  source_files: ["CMakeLists.txt", "src/main.c", "cmake/toolchain.cmake", "startup.s", "system.c", "include/core.h", "include/device.h", "linker.ld"],
};

const context = {
  project_id: "project-1", project_fingerprint: "sha256:project", workspace_fingerprint: "sha256:workspace",
  workspace_target_id: "target-1", workspace_target_fingerprint: "sha256:target",
  plan: { plan_id: "plan-1", title: "Build existing source", status: "PROPOSED" }, review: null,
  firmware_spec: { mcu_exact_part: "actual-target", source_layout: ["src/main.c"] },
  configuration: { toolchain_id: "observed-toolchain", toolchain_version: "observed-version", profile: "release", build_system: "CMAKE" },
  proposal_fingerprint: "sha256:proposal", fingerprint: "sha256:context",
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const result = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label));
  if (!result) throw new Error(`button missing: ${label}`);
  return result;
}

async function mount(fetcher: typeof fetch, projectId: string | null = "project-1", onApproved?: () => void) {
  vi.stubGlobal("fetch", fetcher);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<BrownfieldBuildWizard projectId={projectId} onApproved={onApproved} />); await Promise.resolve(); });
  return { container, root };
}

afterEach(() => { vi.unstubAllGlobals(); document.body.replaceChildren(); });

describe("Brownfield Build wizard", () => {
  it("loads authoritative snapshot read only and never creates a proposal on mount", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.endsWith("/preview")) return response(preview);
      if (url.endsWith("/brownfield-build")) return response({ detail: "NOT_FOUND" }, 404);
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher);
    expect(container.textContent).toContain("sha256:workspace");
    expect(container.textContent).toContain("target-1");
    expect(requests.every((item) => item.method === "GET")).toBe(true);
    expect(button(container, "创建 Build Plan Proposal").disabled).toBe(true);
    expect(button(container, "批准 Build Plan").disabled).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("refuses proposal when no Workspace Owner preview exists", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/preview")
      ? response({ detail: "WORKSPACE_UNAVAILABLE" }, 409)
      : response({ detail: "NOT_FOUND" }, 404)) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher);
    expect(container.textContent).toContain("BLOCKED");
    expect(button(container, "创建 Build Plan Proposal").disabled).toBe(true);
    expect(button(container, "批准 Build Plan").disabled).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("does not show an old approved plan as current after source identity changes", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/preview")
      ? response(preview)
      : response({ ...context, workspace_fingerprint: "sha256:old", plan: { ...context.plan, status: "APPROVED" }, review: { status: "APPROVED" } })) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher);
    expect(container.textContent).toContain("APPROVAL_STALE");
    expect(container.textContent).not.toContain("Build existing source");
    expect(button(container, "批准 Build Plan").disabled).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("reviews server proposal identity and never invokes build or operation approval itself", async () => {
    const requests: Array<{ url: string; method: string; body?: Record<string, unknown> }> = [];
    let saved = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      requests.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined });
      if (url.endsWith("/preview")) return response(preview);
      if (url.endsWith("/brownfield-build") && method === "GET") return saved ? response(context) : response({ detail: "NOT_FOUND" }, 404);
      if (url.endsWith("/proposal")) { saved = true; return response(context); }
      if (url.endsWith("/decision")) return response({ ...context, plan: { ...context.plan, status: "APPROVED" }, review: { status: "APPROVED" } });
      throw new Error(url);
    }) as unknown as typeof fetch;
    const onApproved = vi.fn();
    const { container, root } = await mount(fetcher, "project-1", onApproved);
    const fields: Record<string, string> = {
      "Target platform": "STM32", "MCU family": "STM32F4", "MCU exact part": "actual-target",
      "Source paths": "src/main.c", "Toolchain ID": "observed-toolchain", "Toolchain version": "observed-version",
      "Build profile": "release", "Build directory": "build", "Output stem": "firmware",
      "CMake project": "CMakeLists.txt", "CMake toolchain file": "cmake/toolchain.cmake",
      "Startup source": "startup.s", "System source": "system.c", "CMSIS include": "include/core.h",
      "Device include": "include/device.h", "Linker script": "linker.ld",
    };
    await act(async () => {
      for (const [label, value] of Object.entries(fields)) {
        const field = container.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLTextAreaElement;
        const setter = Object.getOwnPropertyDescriptor(field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")?.set;
        setter?.call(field, value);
        field.dispatchEvent(new Event("input", { bubbles: true }));
      }
      const buildSystem = container.querySelector('[aria-label="Build system"]') as HTMLSelectElement;
      buildSystem.value = "CMAKE";
      buildSystem.dispatchEvent(new Event("change", { bubbles: true }));
      const artifact = container.querySelector('[aria-label="ELF artifact"]') as HTMLInputElement;
      artifact.click();
    });
    expect(button(container, "创建 Build Plan Proposal").disabled).toBe(false);
    await act(async () => { button(container, "创建 Build Plan Proposal").click(); await Promise.resolve(); });
    const proposal = requests.find((item) => item.url.endsWith("/proposal"));
    expect(proposal?.body?.workspace_fingerprint).toBe(preview.workspace_fingerprint);
    expect(proposal?.body?.source_layout).toEqual(["src/main.c"]);
    expect(container.textContent).toContain("sha256:proposal");
    await act(async () => { button(container, "批准 Build Plan").click(); await Promise.resolve(); });
    expect(requests.find((item) => item.url.endsWith("/decision"))?.body).toEqual({ proposal_fingerprint: "sha256:proposal", decision: "APPROVED" });
    expect(onApproved).toHaveBeenCalledOnce();
    expect(requests.filter((item) => item.method === "POST")).toHaveLength(2);
    expect(requests.some((item) => item.url.includes("/operations/") || item.url.includes("/firmware-build/confirm"))).toBe(false);
    await act(async () => { root.unmount(); });
  });
});
