import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";
import { FirmwareBuildPanel } from "./FirmwareBuildPanel";

const readyContext = {
  status: "READY", reason_code: "READY", context_version: "context-v1", project: "Demo",
  target: "stm32f4", source_revision: "sha256:source-current", configuration: "release",
  workspace_fingerprint: "sha256:workspace-current",
  toolchain: "gcc 13", plan: "approved stage", proposal: "proposal-1",
};

function projection(operationIds: string[] = [], projectId = "project-1"): DesktopWorkspaceProjection {
  return {
    identity: { tenant_id: "tenant-1", requester_id: "trusted-desktop-reviewer", project_id: projectId, project_fingerprint: "sha256:project" },
    operations: { items: operationIds.map((id) => ({ reference_id: id, kind: "OPERATION", status: "SUCCEEDED", summary: `Build ${id}`, fingerprint: null, source_references: [], limitations: [], confidence: null })), next_cursor: null, limit: 20, total_available: operationIds.length },
  } as unknown as DesktopWorkspaceProjection;
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function deferredResponse() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

function successfulReceipt(operationId: string, projectId = "project-1", fingerprint = "sha256:receipt") {
  return {
    operation_id: operationId, project_id: projectId, operation_type: "BUILD", fingerprint,
    status: "SUCCEEDED", tool_execution_status: "SUCCEEDED", flash_verification_status: "NOT_RUN", target_runtime_verification_status: "NOT_RUN",
    source_revision: "sha256:source-old",
    artifact: { artifact_id: `artifact-${operationId}`, target_id: "stm32f4", workspace_fingerprint: "sha256:workspace-old", build_configuration: "release", build_operation_id: operationId },
    build_evidence: { result_status: "BUILD_PASS", tool_observed: true, compiler_exit_code: 0, linker_exit_code: 0, elf_verified: true, bin_verified: true, output_artifact_ids: [`artifact-${operationId}`] },
    evidence_references: [`build-evidence-${operationId}`], recovery_state: "NONE",
  };
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const result = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label));
  if (!result) throw new Error(`button missing: ${label}`);
  return result;
}

async function mount(fetcher: typeof fetch, props: { projectId: string | null; conversationId: string | null; desktopProjection: DesktopWorkspaceProjection | null; creationMode?: "CREATE_NEW" | "ATTACH_EXISTING"; projectName?: string | null }) {
  vi.stubGlobal("fetch", fetcher);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<FirmwareBuildPanel {...props} creationMode={props.creationMode ?? "ATTACH_EXISTING"} projectName={props.projectName ?? null} />); await Promise.resolve(); });
  return { container, root };
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe("desktop Firmware Build workflow", () => {
  it("does not offer Brownfield preparation for a newly created project", async () => {
    const fetcher = vi.fn(async () => response({ ...readyContext, status: "BLOCKED", reason_code: "CONVERSATION_REQUIRED", project: null, plan: null, proposal: null })) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: null, desktopProjection: projection(), creationMode: "CREATE_NEW", projectName: "Minimal MCU Control Core" });
    expect(button(container, "已有工程首次构建").disabled).toBe(true);
    expect(container.textContent).toContain("仅附加已有工程的项目");
    expect(container.textContent).toContain("Minimal MCU Control Core");
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { root.unmount(); });
  });

  it("restores Brownfield history without a conversation and never replays the build", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.includes("/firmware-build/brownfield/context")) return response({ status: "BLOCKED", reason_code: "CONTEXT_UNAVAILABLE", context_version: null, project: null, target: null, source_revision: null, workspace_fingerprint: null, configuration: null, toolchain: null, plan: null, proposal: null });
      if (url.includes("/status")) return response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "FAILED", recovery_state: "NONE" });
      if (url.includes("/receipt")) return response({ detail: "OPERATION_RECEIPT_NOT_FOUND" }, 404);
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: null, desktopProjection: projection(["old-build"]) });
    expect(requests).toHaveLength(1);
    await act(async () => { button(container, "Build old-build").click(); await Promise.resolve(); });
    expect(container.textContent).toContain("构建失败");
    expect(container.textContent).toContain("证据缺失或不可用");
    expect(requests.every((item) => item.method === "GET")).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("refreshes the parent Build Context when the wizard approves a Brownfield plan", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    const catalogContext = {
      project_id: "project-1", project_fingerprint: "sha256:project", workspace_fingerprint: "sha256:workspace",
      workspace_target_id: "target-1", workspace_target_fingerprint: "sha256:target",
      plan: { plan_id: "plan-1", title: "Existing source build", status: "PROPOSED" }, review: null,
      firmware_spec: { mcu_exact_part: "user-confirmed-part", source_layout: ["src/main.c"] },
      configuration: { toolchain_id: "observed-toolchain", toolchain_version: "observed-version", profile: "release", build_system: "CMAKE" },
      proposal_fingerprint: "sha256:proposal", fingerprint: "sha256:catalog-context",
    };
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.endsWith("/brownfield-build/preview")) return response({ project_id: "project-1", project_fingerprint: "sha256:project", workspace_fingerprint: "sha256:workspace", workspace_target_id: "target-1", workspace_target_fingerprint: "sha256:target", source_files: ["src/main.c"] });
      if (url.endsWith("/brownfield-build") && (init?.method ?? "GET") === "GET") return response(catalogContext);
      if (url.endsWith("/brownfield-build/decision")) return response({ ...catalogContext, plan: { ...catalogContext.plan, status: "APPROVED" }, review: { status: "APPROVED" } });
      if (url.includes("/firmware-build/brownfield/context")) return response({ ...readyContext, status: "BLOCKED", reason_code: "PROPOSAL_REQUIRED", proposal: null });
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: null, desktopProjection: projection() });
    await act(async () => { button(container, "已有工程首次构建").click(); await Promise.resolve(); });
    expect(button(container, "批准 Build Plan").disabled).toBe(false);
    await act(async () => { button(container, "批准 Build Plan").click(); await Promise.resolve(); });
    expect(requests.filter((item) => item.url.includes("/firmware-build/brownfield/context"))).toHaveLength(2);
    expect(button(container, "审查并准备 Proposal").disabled).toBe(false);
    expect(requests.filter((item) => item.method === "POST")).toHaveLength(1);
    await act(async () => { root.unmount(); });
  });

  it("continues Brownfield proposal, task and independent approval without a conversation", async () => {
    const requests: Array<{ url: string; method: string; body?: Record<string, unknown> }> = [];
    let reviewed = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      requests.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined });
      if (url.includes("/firmware-build/brownfield/context")) return response(reviewed ? readyContext : { ...readyContext, status: "BLOCKED", reason_code: "PROPOSAL_REQUIRED", proposal: null });
      if (url.endsWith("/firmware-build/brownfield/proposals")) { reviewed = true; return response(readyContext); }
      if (url.endsWith("/firmware-build/brownfield/confirm")) return response({ status: "CONFIRMED", task_id: "task-1", operation_id: "operation-1" });
      if (url.includes("/status")) return response({ operation_id: "operation-1", project_id: "project-1", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE" });
      if (url.includes("/approval")) return response({ grant_id: "grant-1", operation_id: "operation-1", status: "ISSUED" });
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: null, desktopProjection: projection() });
    expect(requests).toHaveLength(1);
    expect(container.querySelector('[role="status"]')?.textContent).toContain("请审查并准备 Proposal");
    await act(async () => { button(container, "审查并准备 Proposal").click(); await Promise.resolve(); });
    expect(requests.find((item) => item.url.endsWith("/brownfield/proposals"))?.body).toEqual({ project_id: "project-1", context_version: "context-v1", reviewed: true });
    await act(async () => { button(container, "确认 Firmware Build 任务").click(); await Promise.resolve(); });
    expect(requests.find((item) => item.url.endsWith("/brownfield/confirm"))?.body).toEqual({ project_id: "project-1", context_version: "context-v1", confirmed: true });
    expect(container.textContent).toContain("WAIT_APPROVAL");
    expect(requests.filter((item) => item.url.includes("/approval"))).toHaveLength(0);
    await act(async () => { button(container, "批准当前 Build Operation").click(); await Promise.resolve(); });
    expect(requests.filter((item) => item.url.includes("/approval"))).toHaveLength(1);
    expect(requests.some((item) => item.url.includes("/firmware-build/context?") || item.url.includes("/firmware-build/confirm"))).toBe(false);
    await act(async () => { root.unmount(); });
  });

  it("stays BLOCKED without project or conversation and creates nothing", async () => {
    const fetcher = vi.fn() as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: null, conversationId: null, desktopProjection: null });
    expect(container.textContent).toContain("请先选择项目");
    expect(container.querySelector("details")?.textContent).toContain("PROJECT_REQUIRED");
    expect(button(container, "确认 Firmware Build 任务").disabled).toBe(true);
    expect(button(container, "审查并准备 Proposal").disabled).toBe(true);
    expect(fetcher).not.toHaveBeenCalled();
    await act(async () => { root.unmount(); });
  });

  it.each([
    ["CONVERSATION_REQUIRED", "已批准的构建上下文"],
    ["APPROVED_PLAN_MISSING", "尚无已批准的 Build Plan"],
    ["PROJECT_CATALOG_FORBIDDEN", "无权访问该项目"],
  ])("explains %s with a next step and preserves the technical code", async (reasonCode, guidance) => {
    const fetcher = vi.fn(async () => response({ ...readyContext, status: "BLOCKED", reason_code: reasonCode, project: null, plan: null, proposal: null })) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: null, desktopProjection: projection() });
    expect(container.querySelector('[role="status"]')?.textContent).toContain(guidance);
    expect(container.querySelector('[role="status"]')?.textContent).not.toContain(reasonCode);
    expect(container.querySelector("details")?.textContent).toContain(reasonCode);
    expect(container.textContent).not.toContain("项目缺失");
    expect(button(container, "确认 Firmware Build 任务").disabled).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("explains a context API failure and keeps its code in technical details", async () => {
    const fetcher = vi.fn(async () => response({ detail: "FIRMWARE_BUILD_UNAVAILABLE" }, 503)) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: null, desktopProjection: projection() });
    expect(container.querySelector('[role="status"]')?.textContent).toContain("请检查服务连接后重试");
    expect(container.querySelector("details")?.textContent).toContain("FIRMWARE_BUILD_UNAVAILABLE");
    expect(container.textContent).not.toContain("项目缺失");
    await act(async () => { root.unmount(); });
  });

  it("separates preview, proposal review, task confirmation and trusted operation approval", async () => {
    const requests: Array<{ url: string; method: string; body?: Record<string, unknown> }> = [];
    let reviewed = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined;
      requests.push({ url, method, body });
      if (url.includes("/firmware-build/context")) return response(reviewed ? readyContext : { ...readyContext, status: "BLOCKED", reason_code: "PROPOSAL_REQUIRED", proposal: null });
      if (url.endsWith("/firmware-build/proposals")) { reviewed = true; return response(readyContext); }
      if (url.endsWith("/firmware-build/confirm")) return response({ status: "CONFIRMED", message: "confirmed", task_id: "task-1", operation_id: "operation-1" });
      if (url.includes("/status")) return response({ operation_id: "operation-1", project_id: "project-1", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE", safe_detail: "approval required" });
      if (url.includes("/approval")) return response({ grant_id: "grant-1", operation_id: "operation-1", status: "ISSUED", fingerprint: "sha256:grant" });
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection() });
    expect(requests).toHaveLength(1);
    expect(requests[0].method).toBe("GET");
    expect(container.querySelector('[role="status"]')?.textContent).toContain("请审查并准备 Proposal");
    expect(button(container, "确认 Firmware Build 任务").disabled).toBe(true);
    await act(async () => { button(container, "审查并准备 Proposal").click(); await Promise.resolve(); });
    expect(requests.filter((item) => item.method === "POST")).toHaveLength(1);
    expect(requests[1].body).toEqual({ project_id: "project-1", conversation_id: "conversation-1", context_version: "context-v1", reviewed: true });
    expect(button(container, "确认 Firmware Build 任务").disabled).toBe(false);
    await act(async () => { button(container, "确认 Firmware Build 任务").click(); await Promise.resolve(); });
    expect(requests.filter((item) => item.method === "POST")).toHaveLength(2);
    expect(requests.find((item) => item.url.endsWith("/confirm"))?.body).toEqual({ project_id: "project-1", conversation_id: "conversation-1", context_version: "context-v1", confirmed: true });
    expect(container.textContent).toContain("operation-1");
    expect(container.textContent).toContain("stm32f4");
    expect(container.textContent).toContain("sha256:source-current");
    expect(container.textContent).toContain("release");
    expect(button(container, "批准当前 Build Operation").disabled).toBe(false);
    await act(async () => { button(container, "批准当前 Build Operation").click(); await Promise.resolve(); });
    const approval = requests.find((item) => item.url.includes("/approval"));
    expect(approval?.body?.reviewer_id).toBe("trusted-desktop-reviewer");
    expect(requests.filter((item) => item.method === "POST")).toHaveLength(3);
    await act(async () => { root.unmount(); });
  });

  it("restores a historic receipt read only and identifies an older source", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("/status")) return response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: "sha256:receipt" });
      if (url.includes("/receipt")) return response(successfulReceipt("old-build"));
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["old-build"]) });
    expect(requests).toHaveLength(1);
    await act(async () => { button(container, "Build old-build").click(); await Promise.resolve(); });
    expect(container.textContent).toContain("artifact-old-build");
    expect(container.textContent).toContain("构建成功");
    expect(container.textContent).toContain("旧源码版本");
    expect(container.textContent).toContain("Flash：NOT_RUN · Device：NOT_RUN");
    expect(button(container, "批准当前 Build Operation").disabled).toBe(true);
    expect(requests.every((item) => item.method === "GET")).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it.each([
    [{ project_id: "project-1", operation_id: "old-build", task_id: "task-old" }, "task-old"],
    [{ project_id: "project-1", operation_id: null, task_id: "task-old" }, "task-old"],
    [null, "UNKNOWN"],
    [{ project_id: "another-project", operation_id: "old-build", task_id: "foreign-task" }, "UNKNOWN"],
    [{ project_id: "project-1", operation_id: "another-build", task_id: "foreign-task" }, "UNKNOWN"],
  ])("restores only a bound historical Task reference from status correlation", async (correlation, expectedTask) => {
    const requests: Array<{ url: string; method: string }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("/status")) return response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE", correlation });
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["old-build"]) });
    await act(async () => { button(container, "Build old-build").click(); await Promise.resolve(); });
    expect(container.textContent).toContain(`Task：${expectedTask}`);
    expect(container.textContent).toContain("历史待审批操作缺少可核对的上下文绑定，保持只读");
    expect(button(container, "批准当前 Build Operation").disabled).toBe(true);
    expect(requests.every((item) => item.method === "GET")).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("limits receipt version wording to the source and Workspace dimensions checked", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("/status")) return response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "SUCCEEDED", recovery_state: "NONE" });
      if (url.includes("/receipt")) return response({
        operation_id: "old-build", project_id: "project-1", operation_type: "BUILD",
        status: "SUCCEEDED", tool_execution_status: "SUCCEEDED", source_revision: readyContext.source_revision,
        artifact: { artifact_id: "artifact-old", workspace_fingerprint: readyContext.workspace_fingerprint, build_operation_id: "old-build" },
        build_evidence: { result_status: "BUILD_PASS", tool_observed: true, output_artifact_ids: ["artifact-old"] },
        evidence_references: [],
      });
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["old-build"]) });
    await act(async () => { button(container, "Build old-build").click(); await Promise.resolve(); });
    expect(container.textContent).toContain("源码文件版本与 Workspace 指纹相同（仅核对这两项）");
    expect(container.textContent).not.toContain("当前源码及 Workspace 版本");
    await act(async () => { root.unmount(); });
  });

  it("does not call an artifact current when workspace inputs changed", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("/status")) return response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "SUCCEEDED", recovery_state: "NONE" });
      if (url.includes("/receipt")) return response({
        operation_id: "old-build", project_id: "project-1", operation_type: "BUILD",
        status: "SUCCEEDED", tool_execution_status: "SUCCEEDED",
        source_revision: readyContext.source_revision,
        artifact: { artifact_id: "artifact-old", workspace_fingerprint: "sha256:workspace-old", build_operation_id: "old-build" },
        build_evidence: { result_status: "BUILD_PASS", tool_observed: true, output_artifact_ids: ["artifact-old"] },
        evidence_references: [],
      });
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["old-build"]) });
    await act(async () => { button(container, "Build old-build").click(); await Promise.resolve(); });
    expect(container.textContent).toContain("源码文件版本相同，Workspace 已变化");
    expect(container.textContent).not.toContain("当前源码及 Workspace 版本");
    expect(container.textContent).toContain("操作完成；构建证据缺失或不可用");
    await act(async () => { root.unmount(); });
  });

  it("does not approve after the context version changes", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    let contextReads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.includes("/firmware-build/context")) return response({ ...readyContext, context_version: ++contextReads === 1 ? "context-v1" : "context-v2" });
      if (url.endsWith("/firmware-build/confirm")) return response({ status: "CONFIRMED", message: "confirmed", task_id: "task-1", operation_id: "operation-1" });
      if (url.includes("/status")) return response({ operation_id: "operation-1", project_id: "project-1", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE" });
      throw new Error(`unexpected request: ${url}`);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection() });
    await act(async () => { button(container, "确认 Firmware Build 任务").click(); await Promise.resolve(); });
    await act(async () => { button(container, "批准当前 Build Operation").click(); await Promise.resolve(); });
    expect(container.textContent).toContain("FIRMWARE_BUILD_CONTEXT_CHANGED");
    expect(requests.filter((item) => item.url.includes("/approval"))).toHaveLength(0);
    expect(button(container, "批准当前 Build Operation").disabled).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("reports unavailable evidence without fabricating a receipt", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("/status")) return response({ operation_id: "failed-build", project_id: "project-1", operation_type: "BUILD", status: "FAILED", recovery_state: "NONE" });
      if (url.includes("/receipt")) return response({ detail: "OPERATION_RECEIPT_NOT_FOUND" }, 404);
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["failed-build"]) });
    await act(async () => { button(container, "Build failed-build").click(); await Promise.resolve(); });
    expect(container.textContent).toContain("构建失败");
    expect(container.textContent).toContain("证据缺失或不可用");
    expect(container.textContent).not.toContain("Artifact：");
    await act(async () => { root.unmount(); });
  });

  it.each([
    ["sha256:receipt", "sha256:receipt", true],
    [null, "sha256:receipt", false],
    ["sha256:receipt", "", false],
    ["sha256:another", "sha256:receipt", false],
  ])("requires matching nonempty status and receipt fingerprints for historical success", async (statusFingerprint, receiptFingerprint, success) => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("/status")) return response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: statusFingerprint });
      if (url.includes("/receipt")) return response(successfulReceipt("old-build", "project-1", receiptFingerprint));
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["old-build"]) });
    await act(async () => { button(container, "Build old-build").click(); await Promise.resolve(); });
    expect(container.textContent?.includes("历史构建成功")).toBe(success);
    if (!success) expect(container.textContent).toContain("操作完成；构建证据缺失或不可用");
    await act(async () => { root.unmount(); });
  });

  it("ignores an old project operation response after switching projects", async () => {
    const oldStatus = deferredResponse();
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("old-build/status")) return oldStatus.promise;
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["old-build"]) });
    act(() => { button(container, "Build old-build").click(); });
    await act(async () => { root.render(<FirmwareBuildPanel projectId="project-2" conversationId="conversation-1" desktopProjection={null} creationMode="ATTACH_EXISTING" projectName={null} />); await Promise.resolve(); });
    await act(async () => { oldStatus.resolve(response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE" })); await Promise.resolve(); });
    expect(container.textContent).not.toContain("Operation：old-build");
    expect(container.textContent).not.toContain("等待 Operation Approval");
    await act(async () => { root.unmount(); });
  });

  it("does not restore a confirmed Task after its project changes during operation loading", async () => {
    const oldStatus = deferredResponse();
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.endsWith("/firmware-build/confirm")) return response({ status: "CONFIRMED", message: "confirmed", task_id: "stale-task", operation_id: "old-build" });
      if (url.includes("old-build/status")) return oldStatus.promise;
      if (url.includes("new-build/status")) return response({ operation_id: "new-build", project_id: "project-2", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE", correlation: { project_id: "project-2", operation_id: "new-build", task_id: "current-task" } });
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection() });
    await act(async () => { button(container, "确认 Firmware Build 任务").click(); await Promise.resolve(); });
    await act(async () => { root.render(<FirmwareBuildPanel projectId="project-2" conversationId="conversation-1" desktopProjection={projection(["new-build"], "project-2")} creationMode="ATTACH_EXISTING" projectName={null} />); await Promise.resolve(); });
    await act(async () => { button(container, "Build new-build").click(); await Promise.resolve(); });
    await act(async () => { oldStatus.resolve(response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE" })); await Promise.resolve(); });
    expect(container.textContent).toContain("Task：current-task");
    expect(container.textContent).not.toContain("Task：stale-task");
    expect(container.textContent).toContain("Operation：new-build");
    await act(async () => { root.unmount(); });
  });

  it("keeps the newer project's operation busy when an old historical read settles", async () => {
    const oldStatus = deferredResponse();
    const newStatus = deferredResponse();
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("old-build/status")) return oldStatus.promise;
      if (url.includes("new-build/status")) return newStatus.promise;
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["old-build"]) });
    act(() => { button(container, "Build old-build").click(); });
    await act(async () => { root.render(<FirmwareBuildPanel projectId="project-2" conversationId="conversation-1" desktopProjection={projection(["new-build"], "project-2")} creationMode="ATTACH_EXISTING" projectName={null} />); await Promise.resolve(); });
    act(() => { button(container, "Build new-build").click(); });
    await act(async () => { oldStatus.resolve(response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE" })); await Promise.resolve(); });
    expect(button(container, "刷新 Operation / Receipt").disabled).toBe(true);
    await act(async () => { newStatus.resolve(response({ operation_id: "new-build", project_id: "project-2", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE" })); await Promise.resolve(); });
    expect(button(container, "刷新 Operation / Receipt").disabled).toBe(false);
    await act(async () => { root.unmount(); });
  });

  it("keeps the latest selection when an earlier refresh receipt resolves last", async () => {
    const staleReceipt = deferredResponse();
    let oldReceiptReads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/firmware-build/context")) return response(readyContext);
      if (url.includes("/status")) {
        const id = url.includes("new-build") ? "new-build" : "old-build";
        return response({ operation_id: id, project_id: "project-1", operation_type: "BUILD", status: "SUCCEEDED", recovery_state: "NONE", receipt_fingerprint: "sha256:receipt" });
      }
      if (url.includes("old-build/receipt")) return ++oldReceiptReads === 1 ? response(successfulReceipt("old-build")) : staleReceipt.promise;
      if (url.includes("new-build/receipt")) return response(successfulReceipt("new-build"));
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection(["old-build", "new-build"]) });
    await act(async () => { button(container, "Build old-build").click(); await Promise.resolve(); });
    act(() => { button(container, "刷新 Operation / Receipt").click(); });
    await act(async () => { button(container, "Build new-build").click(); await Promise.resolve(); });
    await act(async () => { staleReceipt.resolve(response(successfulReceipt("old-build"))); await Promise.resolve(); });
    expect(container.textContent).toContain("Operation：new-build");
    expect(container.textContent).toContain("Artifact：artifact-new-build");
    expect(container.textContent).not.toContain("Artifact：artifact-old-build");
    await act(async () => { root.unmount(); });
  });

  it.each(["刷新 Context Preview", "审查并准备 Proposal"])("ignores an old project's %s response", async (action) => {
    const oldResponse = deferredResponse();
    let oldContextReads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("project-2")) return response({ ...readyContext, project: "Current Project" });
      if (url.endsWith("/firmware-build/proposals")) return oldResponse.promise;
      if (url.includes("/firmware-build/context")) return ++oldContextReads === 1 ? response({ ...readyContext, project: "Old Project" }) : oldResponse.promise;
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection() });
    act(() => { button(container, action).click(); });
    await act(async () => { root.render(<FirmwareBuildPanel projectId="project-2" conversationId="conversation-1" desktopProjection={null} creationMode="ATTACH_EXISTING" projectName={null} />); await Promise.resolve(); });
    await act(async () => { oldResponse.resolve(response({ ...readyContext, project: "Stale Project" })); await Promise.resolve(); });
    expect(container.textContent).toContain("Current Project");
    expect(container.textContent).not.toContain("Stale Project");
    await act(async () => { root.unmount(); });
  });

  it("ignores an old approval response after switching projects", async () => {
    const oldApproval = deferredResponse();
    const requests: string[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("project-2")) return response({ ...readyContext, project: "Current Project" });
      if (url.includes("/firmware-build/context")) return response({ ...readyContext, project: "Old Project" });
      if (url.endsWith("/firmware-build/confirm")) return response({ status: "CONFIRMED", message: "confirmed", task_id: "old-task", operation_id: "old-build" });
      if (url.includes("old-build/status")) return response({ operation_id: "old-build", project_id: "project-1", operation_type: "BUILD", status: "WAIT_APPROVAL", recovery_state: "NONE" });
      if (url.includes("old-build/approval")) return oldApproval.promise;
      throw new Error(url);
    }) as unknown as typeof fetch;
    const { container, root } = await mount(fetcher, { projectId: "project-1", conversationId: "conversation-1", desktopProjection: projection() });
    await act(async () => { button(container, "确认 Firmware Build 任务").click(); await Promise.resolve(); });
    await act(async () => { button(container, "批准当前 Build Operation").click(); await Promise.resolve(); await Promise.resolve(); });
    expect(requests.filter((url) => url.includes("old-build/approval"))).toHaveLength(1);
    await act(async () => { root.render(<FirmwareBuildPanel projectId="project-2" conversationId="conversation-1" desktopProjection={null} creationMode="ATTACH_EXISTING" projectName={null} />); await Promise.resolve(); });
    await act(async () => { oldApproval.resolve(response({ grant_id: "old-grant", operation_id: "old-build", status: "ISSUED" })); await Promise.resolve(); });
    expect(container.textContent).toContain("Current Project");
    expect(container.textContent).not.toContain("Old Project");
    expect(container.textContent).not.toContain("Operation：old-build");
    expect(requests.filter((url) => url.includes("old-build/status"))).toHaveLength(2);
    await act(async () => { root.unmount(); });
  });
});
