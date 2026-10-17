import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import CopilotApp from "../CopilotApp";
import { populatedWorkspaceFixture } from "../acceptanceFixtures/workspaceFixtures";

vi.mock("../settings/SettingsPanel", () => ({ SettingsPanel: ({ onClose }: { onClose(): void }) => <button type="button" onClick={onClose}>关闭设置</button> }));

afterEach(() => vi.unstubAllGlobals());

describe("chat model capability refresh", () => {
  it("refreshes the current project projection when Settings closes without a provider request", async () => {
    const project = populatedWorkspaceFixture.projects[0];
    let workspaceReads = 0;
    const emptyPage = { items: [], next_cursor: null, limit: 20, total_available: null };
    const workspace = () => ({
      schema_version: "v6.7", identity: { tenant_id: "desktop-tenant", requester_id: "desktop-user", project_id: project.project_id, project_fingerprint: "sha256:project" },
      project: { project_id: project.project_id, tenant_id: "desktop-tenant", name: project.name, relative_path: "", status: "READY", fingerprint: "sha256:project", initialization_status: "READY" },
      context: null, conversations: emptyPage, evidence: emptyPage, memory: emptyPage, knowledge: emptyPage,
      reviews: emptyPage, validations: emptyPage, findings: emptyPage, proposals: emptyPage, changes: emptyPage,
      operations: emptyPage, audit: emptyPage,
      capabilities: [{ capability: "CHAT_MODEL", status: workspaceReads === 1 ? "UNAVAILABLE" : "AVAILABLE", reason_code: workspaceReads === 1 ? "MODEL_CREDENTIAL_GATE_FAILED" : "GOVERNED_SELECTION_READY", detail: "offline projection" }],
      workflow_summary: { status: "IDLE", current_reference: null, stages: [], source_references: [] },
      generated_at: "2026-09-29T00:00:00Z", projection_fingerprint: "sha256:projection",
    });
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url === "/api/v1/copilot/projects" ? [project]
        : url === `/api/v1/copilot/projects/${project.project_id}` ? project
          : url === `/api/v1/copilot/projects/${project.project_id}/context` ? populatedWorkspaceFixture.context
            : url.includes(`/api/v1/copilot/projects/${project.project_id}/conversations?`) ? { items: [], next_cursor: null }
              : url.includes(`/api/v1/copilot/desktop/projects/${project.project_id}/workspace?`) ? (workspaceReads++, workspace())
                : null;
      return new Response(JSON.stringify(payload), { status: payload === null ? 503 : 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<CopilotApp />); await Promise.resolve(); });
      const projectButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent?.includes(project.name));
      await act(async () => { projectButton?.click(); await Promise.resolve(); });
      expect(workspaceReads).toBe(1);
      expect(container.querySelector(".ide-topbar")?.textContent).toContain("AI 不可用");
      const settings = container.querySelector<HTMLButtonElement>('[aria-label="Settings"]');
      await act(async () => settings?.click());
      const close = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "关闭设置");
      await act(async () => { close?.click(); await Promise.resolve(); });
      expect(workspaceReads).toBe(2);
      expect(container.querySelector(".ide-topbar")?.textContent).toContain("AI 可用");
      expect(fetcher.mock.calls.some(([input]) => String(input).includes("/model-gateway/invoke"))).toBe(false);
    } finally {
      await act(async () => root.unmount());
    }
  });
});
