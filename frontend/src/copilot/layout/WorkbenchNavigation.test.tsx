import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import CopilotApp from "../CopilotApp";
import { populatedWorkspaceFixture } from "../acceptanceFixtures/workspaceFixtures";
import { EngineeringWorkspace } from "./EngineeringWorkbench";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => vi.unstubAllGlobals());

describe("workbench navigation", () => {
  it("keeps Verification visible for a loaded empty resource projection", () => {
    const markup = renderToStaticMarkup(<EngineeringWorkspace projectName="Empty Project" resources={[]} activeResourceId={null} onResource={() => undefined} activeActivity="test" capabilities={[]} resourceState="LOADED_ZERO" />);
    expect(markup).toContain("Verification");
    expect(markup).toContain("切换页面不会执行测试");
  });
  it("shows each selected workspace without a selected project or resource, and opens Verification without executing", async () => {
    const fetcher = vi.fn(async () => new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<CopilotApp />); await Promise.resolve(); });
      expect(container.querySelector(".ide-shell__center")?.textContent).toContain("告诉 Embrix 你想做什么");
      await act(async () => { container.querySelector<HTMLButtonElement>('.embrix-activity-bar [aria-label="Explorer"]')?.click(); });
      expect(container.querySelector(".ide-shell__center")?.textContent).toContain("Explorer");
      for (const [activity, heading] of [["Code", "Code"], ["Hardware", "Hardware"], ["Simulation", "Virtual Lab"], ["Control", "Control Lab"], ["Reports", "Reports"], ["Test", "Verification"]]) {
        await act(async () => { container.querySelector<HTMLButtonElement>(`.embrix-activity-bar [aria-label="${activity}"]`)?.click(); });
        expect(container.querySelector(".ide-shell__center")?.textContent).toContain(heading);
      }
      expect(container.querySelector<HTMLElement>(".ide-shell__main-grid")?.style.getPropertyValue("--bottom-height")).not.toBe("0px");
      expect(container.querySelector(".ide-shell--engineering .ide-shell__bottom")).not.toBeNull();
      await act(async () => { container.querySelector<HTMLButtonElement>('.ide-topbar__actions button:last-child')?.click(); });
      expect(container.querySelector(".ide-shell__center")?.textContent).toContain("Firmware Build");
      expect(fetcher).toHaveBeenCalledTimes(1);
      await act(async () => { container.querySelector<HTMLButtonElement>('.embrix-activity-bar [aria-label="Copilot"]')?.click(); });
      expect(container.querySelector(".ide-shell__center")?.textContent).toContain("告诉 Embrix 你想做什么");
    } finally {
      await act(async () => { root.unmount(); });
    }
  });

  it.each(["success", "failure"])("ignores a late %s response from the previously selected project", async (outcome) => {
    const first = populatedWorkspaceFixture.projects[0];
    const second = { ...first, project_id: "second-project", name: "Second Project" };
    let resolveFirstContext: ((value: Response) => void) | undefined;
    let rejectFirstContext: ((reason?: unknown) => void) | undefined;
    const firstContext = new Promise<Response>((resolve, reject) => { resolveFirstContext = resolve; rejectFirstContext = reject; });
    const fetcher = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/copilot/projects") return Promise.resolve(new Response(JSON.stringify([first, second])));
      if (url === `/api/v1/copilot/projects/${first.project_id}/context`) return firstContext;
      const payload = url === `/api/v1/copilot/projects/${first.project_id}` ? first
        : url === `/api/v1/copilot/projects/${second.project_id}` ? second
          : url === `/api/v1/copilot/projects/${second.project_id}/context` ? { ...populatedWorkspaceFixture.context, project_id: second.project_id }
            : url.includes("/conversations?") ? { items: [], next_cursor: null } : null;
      return Promise.resolve(new Response(JSON.stringify(payload), { status: payload ? 200 : 503 }));
    });
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<CopilotApp />); await Promise.resolve(); });
      const projectButton = (name: string) => [...container.querySelectorAll<HTMLButtonElement>(".embrix-project-row > button:first-child")].find((button) => button.textContent?.includes(name));
      await act(async () => { projectButton(first.name)?.click(); await Promise.resolve(); });
      await act(async () => { projectButton(second.name)?.click(); await Promise.resolve(); });
      expect(container.querySelector(".ide-topbar__project")?.textContent).toContain(second.name);
      await act(async () => { container.querySelector<HTMLButtonElement>('.embrix-activity-bar [aria-label="Test"]')?.click(); });
      expect(container.querySelector(".ide-shell__center")?.textContent).toContain("工程资源读取失败");
      expect(container.querySelector<HTMLElement>(".ide-shell__main-grid")?.style.getPropertyValue("--bottom-height")).not.toBe("0px");
      expect(container.querySelector(".ide-shell--engineering .ide-shell__bottom")).not.toBeNull();
      await act(async () => {
        if (outcome === "success") resolveFirstContext?.(new Response(JSON.stringify(populatedWorkspaceFixture.context)));
        else rejectFirstContext?.(new Error("late first project failure"));
        await Promise.resolve();
      });
      expect(container.querySelector(".ide-topbar__project")?.textContent).toContain(second.name);
      expect(container.textContent).not.toContain("无法加载 Project 和工程上下文");
    } finally {
      await act(async () => { root.unmount(); });
    }
  });

  it("keeps Verification open when changing projects on the Test page", async () => {
    const project = populatedWorkspaceFixture.projects[0];
    const fetcher = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url === "/api/v1/copilot/projects" ? [project]
        : url === `/api/v1/copilot/projects/${project.project_id}` ? project
          : url === `/api/v1/copilot/projects/${project.project_id}/context` ? populatedWorkspaceFixture.context
            : url.includes("/conversations?") ? { items: [], next_cursor: null } : null;
      return Promise.resolve(new Response(JSON.stringify(payload), { status: payload ? 200 : 503 }));
    });
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<CopilotApp />); await Promise.resolve(); });
      await act(async () => { container.querySelector<HTMLButtonElement>('.embrix-activity-bar [aria-label="Test"]')?.click(); });
      const projectButton = [...container.querySelectorAll<HTMLButtonElement>(".embrix-project-row > button:first-child")]
        .find((button) => button.textContent?.includes(project.name));
      await act(async () => { projectButton?.click(); await Promise.resolve(); });
      expect(container.querySelector(".ide-shell--engineering .ide-shell__bottom")).not.toBeNull();
      expect(container.querySelector<HTMLElement>(".ide-shell__main-grid")?.style.getPropertyValue("--bottom-height")).not.toBe("0px");
      expect(container.querySelector(".ide-shell__bottom")?.textContent).toContain("Verification");
    } finally {
      await act(async () => { root.unmount(); });
    }
  });
});
