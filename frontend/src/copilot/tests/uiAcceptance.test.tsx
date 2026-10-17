import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { ChatInput, insertContextReference } from "../chat/ChatInput";
import { ChatPanel } from "../chat/ChatPanel";
import CopilotApp, { circuitReviewAvailability, mapCapabilityStatus } from "../CopilotApp";
import type { ConversationDetail } from "../api/projectClient";
import { DesktopShell } from "../layout/DesktopShell";
import { EngineeringCopilotPanel } from "../layout/EngineeringWorkbench";
import { TopNavigation } from "../layout/TopNavigation";
import { readUiPreferences, resolveUiTheme, UI_PREFERENCES_KEY } from "../preferences";
import { SettingsPanel } from "../settings/SettingsPanel";
import { populatedWorkspaceFixture } from "../acceptanceFixtures/workspaceFixtures";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("desktop UI acceptance surface", () => {
  it("shows schematic BLOCKED only after an explicit bound Task click", async () => {
    const project = populatedWorkspaceFixture.projects[0];
    const detail = {
      ...populatedWorkspaceFixture.conversation!,
      engineering_plan: { plan_id: "plan-1", project_id: project.project_id, requirement_id: "requirement-1",
        title: "STM32 plan", objective: "Schematic review", status: "APPROVED", phases: [], verification_strategy: [], fingerprint: `sha256:${"a".repeat(64)}`,
        model_operation_bindings: [
        { phase_id: "phase-1", plan_task_id: "task-1", operation_id: "SCHEMATIC_DESIGN_PROPOSAL", stage_id: "SCHEMATIC_DESIGN" },
      ] } as ConversationDetail["engineering_plan"],
      engineering_plan_review: { status: "APPROVED" } as ConversationDetail["engineering_plan_review"],
      hardware_selection: { status: "APPROVED" } as ConversationDetail["hardware_selection"],
    };
    const candidatePath = `/api/v1/copilot/projects/${project.project_id}/conversations/${detail.conversation_id}/schematic-plan/model-candidate-proposal`;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === candidatePath) return new Response(JSON.stringify({
        status: "BLOCKED", operation_id: "SCHEMATIC_DESIGN_PROPOSAL",
        blocking_reasons: ["POWER_SOURCE_UNRESOLVED"], reason_code: "POWER_SOURCE_UNRESOLVED",
      }), { status: 409, headers: { "Content-Type": "application/json" } });
      const payload = url === "/api/v1/copilot/projects" ? [project]
        : url === `/api/v1/copilot/projects/${project.project_id}` ? project
        : url === `/api/v1/copilot/projects/${project.project_id}/context` ? populatedWorkspaceFixture.context
        : url.includes(`/api/v1/copilot/projects/${project.project_id}/conversations?`) ? {
          items: populatedWorkspaceFixture.conversations.slice(0, 2), next_cursor: null,
        }
        : url === `/api/v1/copilot/projects/${project.project_id}/conversations/${detail.conversation_id}` ? detail
        : url === `/api/v1/copilot/projects/${project.project_id}/conversations/${populatedWorkspaceFixture.conversations[1].conversation_id}` ? {
          ...detail, conversation_id: populatedWorkspaceFixture.conversations[1].conversation_id,
          engineering_plan: null, engineering_plan_review: null, hardware_selection: null,
        }
        : null;
      return new Response(JSON.stringify(payload), { status: payload === null ? 503 : 200 });
    });
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<CopilotApp />); await Promise.resolve(); });
      expect(fetcher.mock.calls.some(([input]) => String(input) === candidatePath)).toBe(false);
      const projectButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(project.name));
      await act(async () => { projectButton?.click(); await Promise.resolve(); });
      const conversationButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(detail.title));
      await act(async () => { conversationButton?.click(); await Promise.resolve(); });
      const button = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent === "请求原理图候选");
      expect(button, container.textContent ?? "empty UI").toBeDefined();
      await act(async () => { button?.click(); await Promise.resolve(); });
      expect(fetcher.mock.calls.filter(([input]) => String(input) === candidatePath)).toHaveLength(1);
      expect(container.textContent).toContain("项目供电来源与连接关系未批准，原理图候选已阻断。");
      const nextConversation = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
        (item) => item.textContent?.includes(populatedWorkspaceFixture.conversations[1].title),
      );
      await act(async () => { nextConversation?.click(); await Promise.resolve(); });
      expect(container.textContent).not.toContain("项目供电来源与连接关系未批准，原理图候选已阻断。");
      expect(container.textContent).not.toContain("请求原理图候选");
    } finally {
      await act(async () => { root.unmount(); });
      vi.unstubAllGlobals();
    }
  });
  it("mounts the bound hardware consumer without automatic gateway calls and clears failure on scope change", async () => {
    const project = populatedWorkspaceFixture.projects[0];
    const detail = {
      ...populatedWorkspaceFixture.conversation!,
      engineering_plan: { plan_id: "plan-1", project_id: project.project_id, requirement_id: "requirement-1",
        title: "STM32 plan", objective: "Schematic review", status: "APPROVED", phases: [], verification_strategy: [], fingerprint: `sha256:${"a".repeat(64)}`,
        model_operation_bindings: [
        { phase_id: "phase-1", plan_task_id: "task-1", operation_id: "HARDWARE_SELECTION_PROPOSAL", stage_id: "HARDWARE_SELECTION" },
      ] } as ConversationDetail["engineering_plan"],
      engineering_plan_review: { status: "APPROVED" } as ConversationDetail["engineering_plan_review"],
      hardware_selection: null,
    };
    const candidatePath = `/api/v1/copilot/projects/${project.project_id}/conversations/${detail.conversation_id}/hardware-selection/model-candidate-proposal`;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const consumptionPath=`/api/v1/copilot/projects/${project.project_id}/conversations/${detail.conversation_id}/model-consumption`;
      const fp=`sha256:${"1".repeat(64)}`;
      if(url===`${consumptionPath}/preview`)return new Response(JSON.stringify({schema_version:1,status:"READY",blockers:[],preview:{schema_version:1,preview_id:"preview",fingerprint:fp,not_after:"2099-01-01T00:00:00Z",scope:{project_id:project.project_id,conversation_id:detail.conversation_id,request_id:JSON.parse(String(init?.body)).request_id,selected_model:{model_id:"gpt-5.6-luna"}}}}));
      if(url===`${consumptionPath}/grants`)return new Response(JSON.stringify({schema_version:1,grant_id:"grant",preview_id:"preview",preview_fingerprint:fp,scope_fingerprint:fp,status:"ACTIVE",revision:1,not_after:"2099-01-01T00:00:00Z",counter_status:"UNKNOWN",generation_status:"UNKNOWN",total_external_request_count:null}));
      if (url === candidatePath) return new Response(JSON.stringify({
        status: "BLOCKED", operation_id: "HARDWARE_SELECTION_PROPOSAL",
        blocking_reasons: ["POWER_SOURCE_UNRESOLVED"], reason_code: "POWER_SOURCE_UNRESOLVED",
      }), { status: 409, headers: { "Content-Type": "application/json" } });
      const payload = url === "/api/v1/copilot/projects" ? [project]
        : url === `/api/v1/copilot/projects/${project.project_id}` ? project
        : url === `/api/v1/copilot/projects/${project.project_id}/context` ? populatedWorkspaceFixture.context
        : url.includes(`/api/v1/copilot/projects/${project.project_id}/conversations?`) ? {
          items: populatedWorkspaceFixture.conversations.slice(0, 2), next_cursor: null,
        }
        : url === `/api/v1/copilot/projects/${project.project_id}/conversations/${detail.conversation_id}` ? detail
        : url === `/api/v1/copilot/projects/${project.project_id}/conversations/${populatedWorkspaceFixture.conversations[1].conversation_id}` ? {
          ...detail, conversation_id: populatedWorkspaceFixture.conversations[1].conversation_id,
          engineering_plan: null, engineering_plan_review: null, hardware_selection: null,
        }
        : null;
      return new Response(JSON.stringify(payload), { status: payload === null ? 503 : 200 });
    });
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<CopilotApp />); await Promise.resolve(); });
      expect(fetcher.mock.calls.some(([input]) => String(input) === candidatePath)).toBe(false);
      const projectButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(project.name));
      await act(async () => { projectButton?.click(); await Promise.resolve(); });
      const conversationButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(detail.title));
      await act(async () => { conversationButton?.click(); await Promise.resolve(); });
      const button = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent === "请求硬件候选");
      expect(button, container.textContent ?? "empty UI").toBeDefined();
      expect(button?.disabled).toBe(true);
      const consumptionButton=(label:string)=>[...container.querySelectorAll<HTMLButtonElement>("button")].find(item=>item.textContent===label)!;
      await act(async()=>consumptionButton("准备本次消费预览").click());
      await act(async()=>container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
      await act(async()=>consumptionButton("确认本次消费授权").click());
      expect(fetcher.mock.calls.filter(([input])=>String(input)===candidatePath)).toHaveLength(0);
      await act(async () => { button?.click(); await Promise.resolve(); });
      expect(fetcher.mock.calls.filter(([input]) => String(input) === candidatePath)).toHaveLength(1);
      expect(container.textContent).toContain("HARDWARE_CANDIDATE_UNAVAILABLE");
      const nextConversation = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
        (item) => item.textContent?.includes(populatedWorkspaceFixture.conversations[1].title),
      );
      await act(async () => { nextConversation?.click(); await Promise.resolve(); });
      expect(container.textContent).not.toContain("HARDWARE_CANDIDATE_UNAVAILABLE");
      expect(container.textContent).not.toContain("请求硬件候选");
    } finally {
      await act(async () => { root.unmount(); });
      vi.unstubAllGlobals();
    }
  });
  it("keeps ERC and predesign entries independent for legacy-ready sources", () => {
    const source = {
      requirement_context: { status: "DRAFT", lifecycle: "REQUIREMENTS_READY", fingerprint: "sha256:requirement", open_questions: [], conflicts: [] },
      engineering_plan: { status: "APPROVED" }, engineering_plan_review: { status: "APPROVED" },
      architecture_design: { status: "APPROVED", architecture_id: "architecture-1", fingerprint: "sha256:architecture" },
      architecture_review: { status: "APPROVED" },
      hardware_selection: { status: "APPROVED", approval: { approval_id: "approval-1" }, bom: { fingerprint: "sha256:bom" }, hardware_truth: { fingerprint: "sha256:truth" } },
      schematic_production: { erc_receipt: { operation_id: "erc-1" } },
      turns: [{ turn_id: "turn-design", route: "GREENFIELD_DESIGN", status: "COMPLETED" }],
    } as unknown as ConversationDetail;
    const available = circuitReviewAvailability(source);
    expect(available.ercOperationId).toBe("erc-1");
    expect(available.predesignSource?.hardware_selection_approval_id).toBe("approval-1");
    const unresolved = circuitReviewAvailability({
      ...source, requirement_context: { ...source.requirement_context!, open_questions: [{ category: "power", question: "VIN?", provenance: "user" }] },
    });
    expect(unresolved.ercOperationId).toBe("erc-1");
    expect(unresolved.predesignSource).toBeNull();
  });
  it("waits for an explicit project selection before loading a real project", async () => {
    const project = populatedWorkspaceFixture.projects[0];
    const remainingProject = { ...project, project_id: "remaining-project", name: "Remaining Project" };
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const payload = url === "/api/v1/copilot/projects" ? [project, remainingProject]
        : url === `/api/v1/copilot/projects/${project.project_id}` && init?.method === "DELETE" ? { project_id: project.project_id }
          : url === `/api/v1/copilot/projects/${project.project_id}` ? project
            : url === `/api/v1/copilot/projects/${remainingProject.project_id}` ? remainingProject
          : url === `/api/v1/copilot/projects/${project.project_id}/context` ? populatedWorkspaceFixture.context
            : url === `/api/v1/copilot/projects/${remainingProject.project_id}/context` ? { ...populatedWorkspaceFixture.context, project_id: remainingProject.project_id }
              : url.includes(`/api/v1/copilot/projects/${project.project_id}/conversations?`) || url.includes(`/api/v1/copilot/projects/${remainingProject.project_id}/conversations?`) ? { items: [], next_cursor: null }
              : null;
      return new Response(JSON.stringify(payload), { status: payload === null ? 503 : 200 });
    });
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<CopilotApp />); await Promise.resolve(); });
      expect(container.textContent).toContain(project.name);
      expect(container.textContent).toContain("请选择项目");
      expect(fetcher).toHaveBeenCalledTimes(1);

      const projectButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent?.includes(project.name));
      expect(projectButton).toBeDefined();
      await act(async () => { projectButton?.click(); await Promise.resolve(); });
      expect(fetcher.mock.calls.some(([input]) => String(input) === `/api/v1/copilot/projects/${project.project_id}`)).toBe(true);
      expect(container.querySelector(".embrix-project-row.is-active")?.textContent).toContain(project.name);

      const menuButton = container.querySelector<HTMLButtonElement>(`[aria-label="${project.name} 项目菜单"]`);
      await act(async () => { menuButton?.click(); });
      const removeButton = container.querySelector<HTMLButtonElement>(`[aria-label="Remove from Embrix"]`);
      await act(async () => { removeButton?.click(); });
      const confirmButton = container.querySelector<HTMLButtonElement>(`[aria-label="Confirm Remove from Embrix"]`);
      await act(async () => { confirmButton?.click(); await Promise.resolve(); });
      expect(container.textContent).toContain(remainingProject.name);
      expect(container.textContent).toContain("请选择项目");
      expect(container.querySelector(".embrix-project-row.is-active")).toBeNull();
      expect(fetcher.mock.calls.some(([input]) => String(input) === `/api/v1/copilot/projects/${remainingProject.project_id}`)).toBe(false);
      const remainingButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent?.includes(remainingProject.name));
      await act(async () => { remainingButton?.click(); await Promise.resolve(); });
      expect(container.querySelector(".embrix-project-row.is-active")?.textContent).toContain(remainingProject.name);
    } finally {
      await act(async () => { root.unmount(); });
      vi.unstubAllGlobals();
    }
  });
  it("renders the project-first IDE shell without production demo data", () => {
    const markup = renderToStaticMarkup(<CopilotApp />);
    for (const label of ["Embrix", "AI EMBEDDED ENGINEERING", "项目", "Conversation", "Engineering Context", "Settings", "告诉 Embrix 你想做什么。"]) expect(markup).toContain(label);
    expect(markup).toContain("AI");
    for (const duplicate of ["PROJECT EXPLORER", "ENGINEERING COPILOT", "Provider Configured", "Credential Saved", "Model Readiness", "Project Location"]) expect(markup).not.toContain(duplicate);
    expect(markup).not.toContain("ESP32-S3 Sensor Gateway");
    expect(markup).toContain("Settings");
    for (const topbarForbidden of ["GLM-4V", "导出报告", "帮助"]) expect(markup).not.toContain(topbarForbidden);
    for (const forbidden of ["120mA", "98%", "100%", "robot", "notification", "raw log", "prompt", "token", "secret", "Operator"]) expect(markup.toLowerCase()).not.toContain(forbidden.toLowerCase());
  });
  it("keeps the fixed IDE regions as separate layout surfaces", () => {
    const markup = renderToStaticMarkup(<DesktopShell density="compact" theme="light" top={<header>Top</header>} left={<aside>Projects</aside>} center={<main>Copilot</main>} right={<aside>Context</aside>} leftOpen={false} rightOpen={false} onCloseLeft={() => undefined} onCloseRight={() => undefined} />);
    for (const region of ["ide-shell__top", "ide-shell__left", "ide-shell__center", "ide-shell__right"]) expect(markup).toContain(region);
  });
  it("keeps the minimal composer visible while offline", () => {
    const markup = renderToStaticMarkup(<ChatInput connected={false} pending={false} references={[]} selected={[]} onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} />);
    expect(markup).toContain('placeholder="请先选择 Project 或完成环境配置"');
    expect(markup).toContain('data-composer-state="unavailable"');
    expect(markup).toContain("textarea");
    expect(markup).toContain("ide-send-button");
    expect(markup).toContain("ide-composer__footer");
    expect(markup).not.toContain("ide-context-menu");
    expect(markup).not.toContain("Research");
    expect(markup).not.toContain("Enter 发送");
    expect(markup).not.toContain("添加工程上下文");
  });

  it("uses conversation-first UX and moves Research mode to Settings", () => {
    const chat = renderToStaticMarkup(<ChatInput connected pending={false} references={[]} selected={[]} onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} />);
    expect(chat).toContain("ide-composer__editor");
    expect(chat).toContain("ide-composer__footer");
    expect(chat.indexOf("ide-composer__editor")).toBeLessThan(chat.indexOf("ide-composer__footer"));
    expect(chat).not.toContain("Enter 发送");
    expect(chat).not.toContain("Shift+Enter");
    expect(chat).not.toContain("position:absolute");
    expect(chat).not.toContain("position:fixed");
    expect(chat).toContain('placeholder=""');
    expect(chat).toContain('data-composer-layout="conversation-rail"');
    expect(chat).toContain('data-composer-footer="in-flow"');
    expect(chat).toContain('data-composer-controls="attach-send-only"');
    expect(chat).toContain('data-composer-radius="24px"');
    expect(chat).toContain('data-composer-shadow="subtle"');
    expect(chat).toContain('data-composer-plus="icon-only"');
    expect(chat).toContain('data-send-state="empty"');
    expect(chat).toContain('data-send-shape="circle"');
    expect(chat).toContain('data-composer-reference-entry="removed"');
    expect(chat).toContain('data-composer-research-control="removed"');
    expect(chat).toContain('data-enter-send-hint="hidden"');
    expect(chat).not.toContain("询问关于");
    expect(chat).not.toContain("Research:");
    expect(chat).not.toContain("Research mode");
    expect(chat).not.toContain("Shift+Enter");
    expect(chat).not.toContain("添加附件引用");
    const settings = renderToStaticMarkup(<SettingsPanel preferences={{ theme: "light", density: "compact", answerDetail: "standard", evidenceDisplay: "expanded" }} onChange={() => undefined} onClose={() => undefined} researchMode="AUTO" onResearchModeChange={() => undefined} capabilities={[]} />);
    const navigation = settings.match(/<nav[^>]*data-settings-navigation[^>]*>[\s\S]*?<\/nav>/u)?.[0] ?? "";
    expect(navigation).not.toContain("研究模式");
    expect(settings).toContain('data-auto-research-backend-dispatch="NOT_IMPLEMENTED"');
    for (const option of ["Auto", "Local Only", "Web Required"]) expect(settings).toContain(option);
    expect(settings).toContain("WEB_REQUIRED 会在发送问题时请求外部搜索");
    expect(settings).toContain("自动研究调度尚未接入");
    const copilot = renderToStaticMarkup(<EngineeringCopilotPanel projectName="31313" workflowStatus="IDLE" evidenceCount={0} findings={[]} proposals={[]} chat={<div>Conversation stream</div>} onFinding={() => undefined} />);
    expect(copilot).toContain("Conversation stream");
    expect(copilot).not.toContain("工程生命周期");
    expect(copilot).not.toContain("data-lifecycle");
    expect(copilot).not.toContain("需求");
    expect(copilot).not.toContain("验证");
    expect(copilot).not.toContain("当前阶段：等待任务");
    expect(copilot).not.toContain("ENGINEERING COPILOT");
    expect(copilot).toContain('data-conversation-primary="true"');
    expect(copilot).toContain('data-fixed-analysis-dashboard-removed="true"');
    expect(copilot).toContain('data-internal-pipeline="hidden"');
    expect(copilot).toContain('data-copilot-collapse="removed"');
    expect(copilot).not.toContain("<details");
    expect(copilot).not.toContain("AI 理解摘要");
  });

  it("keeps Provider Management as the default internal list view", () => {
    const settings = renderToStaticMarkup(<SettingsPanel preferences={{ theme: "light", density: "compact", answerDetail: "standard", evidenceDisplay: "expanded" }} onChange={() => undefined} onClose={() => undefined} capabilities={[]} />);
    expect(settings).toContain('class="ide-secondary-button"');
    expect(settings).toContain("提供商管理");
    expect(settings).toContain("添加模型提供商");
    expect(settings).not.toContain('data-provider-internal-view="detail"');
    expect(settings).not.toContain('select aria-label="提供商"');
  });

  it("keeps the native empty state free of duplicate project copy and internal headings", () => {
    const chat = renderToStaticMarkup(<ChatPanel title="Copilot 工程分析" projectName="31313" sessionReady={false} turns={[]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="compact" onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} onRetry={() => undefined} />);
    expect(chat).toContain("告诉 Embrix 你想做什么。");
    expect(chat).toContain("从需求开始，我会和你一起完成工程。");
    expect(chat).not.toContain(">31313<");
    expect(chat).not.toContain("从一个工程目标开始");
    expect(chat).not.toContain("Copilot 分析");
    expect(chat).not.toContain(">分析<");
  });

  it("uses one canonical conversation rail for messages, approval content, and the composer", () => {
    const chat = renderToStaticMarkup(<ChatPanel title="Conversation" sessionReady turns={[{ turn_id: "turn-rail", request_id: "request-rail", status: "COMPLETED", question: "需求", answer_text: "回答", evidence: [], recommendations: [], context_references: [], error_code: null, created_at: "2026-08-31T00:00:00Z", completed_at: "2026-08-31T00:01:00Z" }]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="compact" pendingApproval={<div data-approval-surface="true">Approval</div>} onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} onRetry={() => undefined} />);
    expect(chat).toContain('data-conversation-rail="messages"');
    expect(chat).toContain('data-conversation-rail="approval"');
    expect(chat).toContain('data-conversation-rail="composer"');
    expect(chat).toContain('data-composer-layout="conversation-rail"');
    expect(chat).toContain("ds-message--assistant");
    expect(chat).toContain("ds-message--user");
  });
  it("does not add a report action to ordinary conversation turns", () => {
    const markup = renderToStaticMarkup(<ChatPanel title="Copilot 工程分析" canExport sessionReady={false} turns={[{ turn_id: "turn-1", request_id: "request-1", status: "COMPLETED", question: "需求", answer_text: "回答", evidence: [], recommendations: [], context_references: [], error_code: null, created_at: "2026-08-31T00:00:00Z", completed_at: "2026-08-31T00:01:00Z" }]} pendingQuestion={null} availableReferences={[]} pending={false} error={null} selected={[]} evidenceDisplay="compact" onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} onRetry={() => undefined} onExport={() => undefined} />);
    expect(markup).not.toContain('data-report-delivery="contextual"');
    expect(markup).not.toContain("生成报告");
  });

  it("keeps workbench topbar outside the resizable main grid and removes default copilot collapse", () => {
    const markup = renderToStaticMarkup(<DesktopShell density="compact" theme="light" top={<header>Top</header>} left={<aside>Projects</aside>} center={<main>Fallback</main>} right={<aside>Context</aside>} activity={<div>Activity</div>} copilot={<div>Copilot</div>} workspace={<main>Workspace</main>} inspector={<aside>Inspector</aside>} bottom={<div>Bottom</div>} leftOpen={false} rightOpen={false} onCloseLeft={() => undefined} onCloseRight={() => undefined} />);
    expect(markup).toContain("ide-shell__main-grid");
    expect(markup).toContain('data-topbar-resize-isolated="true"');
    expect(markup).toContain('data-layout-priority="AUTO_LAYOUT"');
    expect(markup).not.toContain("收起 Engineering Copilot");
  });
  it("normalizes backend capability casing in the status strip", () => {
    const markup = renderToStaticMarkup(<TopNavigation onToggleLeft={() => undefined} onToggleRight={() => undefined} statuses={[{ label: "Connection", value: "available", tone: "success" }]} />);
    expect(markup).toContain("Connection 可用");
  });

  it("does not promote provider reason strings to an available status", () => {
    expect(mapCapabilityStatus("CONFIGURED")).toBe("AVAILABLE");
    expect(mapCapabilityStatus("NOT_CONFIGURED")).toBe("UNAVAILABLE");
    expect(mapCapabilityStatus("PROVIDER_AUTH_FAILED")).toBe("UNAVAILABLE");
    expect(mapCapabilityStatus("PROVIDER_CONNECTION_UNVERIFIED")).toBe("UNVERIFIED");
  });
  it("keeps Credential Saved distinct from Provider Configured", async () => {
    const { mapCredentialSavedStatus } = await import("../CopilotApp");
    expect(mapCredentialSavedStatus({ provider_configured: "CONFIGURED" })).toBe("UNAVAILABLE");
    expect(mapCredentialSavedStatus({ credential_saved: "TRUE" })).toBe("AVAILABLE");
  });
  it("inserts only the selected read-only context reference", () => {
    expect(insertContextReference("检查 I2C @", "HIL")).toBe("检查 I2C @HIL ");
    expect(insertContextReference("检查 I2C", "Memory")).toBe("检查 I2C @Memory ");
  });

  it("renders attachment metadata as a reference tray without a local path", () => {
    const attachment = { tenant_id: "tenant", project_id: "project", reference_id: "attachment-1", reference_kind: "WORKSPACE" as const, source_fingerprint: `sha256:${"a".repeat(64)}`, display_label: "sensor_i2c.c", fingerprint: `sha256:${"b".repeat(64)}` };
    const markup = renderToStaticMarkup(<ChatInput connected pending={false} references={[attachment]} selected={[attachment]} onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} />);
    for (const label of ["sensor_i2c.c", "附件引用", "SHA-256"]) expect(markup).toContain(label);
    for (const forbidden of ["上传文件", "SOURCE_FILE", "C:\\", "/Users/"]) expect(markup).not.toContain(forbidden);
  });

  it("preserves the v1 preference key and resolves system appearance", () => {
    const storage = { getItem: (key: string) => key === UI_PREFERENCES_KEY ? JSON.stringify({ theme: "system", density: "comfortable", answerDetail: "detailed", evidenceDisplay: "compact" }) : null };
    expect(readUiPreferences(storage).theme).toBe("system");
    expect(resolveUiTheme("system", true)).toBe("dark");
    expect(resolveUiTheme("system", false)).toBe("light");
  });
});
