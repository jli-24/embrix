import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { GovernanceModel } from "../api/modelGovernanceClient";
import { deriveModelReadiness, groupGovernanceModels, ModelGovernancePanel } from "./ModelGovernancePanel";

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

const evidence = (capability: string, model: GovernanceModel) => ({ capability, provenance: "PROBED", status: "PASS", source: "capability_probe", observed_at: "2026-09-14T00:00:00Z", provider_id: model.provider_id, endpoint_id: model.endpoint_id, credential_ref: model.credential_ref, model_profile_id: model.model_profile_id, model_id: model.model_id, credential_revision: null, endpoint_revision: null });

function model(overrides: Partial<GovernanceModel> = {}): GovernanceModel {
  const value = {
    model_profile_id: "profile-a",
    provider_id: "deepseek",
    provider_display_name: "DeepSeek",
    connection_id: "conn-personal",
    connection_name: "个人 API",
    endpoint_id: "endpoint-a",
    protocol_family: "OPENAI_CHAT",
    credential_ref: "credential:a",
    model_id: "model-a",
    display_name: "Model A",
    billing_class: "PAID",
    billing_provenance: "PROVIDER_METADATA",
    billing_source: "provider",
    availability: "AVAILABLE",
    availability_status: "AVAILABLE",
    availability_provenance: "PROBED",
    identity_status: "VALID",
    identity_status_reason: null,
    record_provenance: "ENDPOINT_DISCOVERY",
    verification_status: "VERIFIED",
    discovery_status: "DISCOVERED",
    discovered_at: "2026-09-14T00:00:00Z",
    metadata_source: "provider",
    metadata_updated_at: null,
    verified_at: "2026-09-14T00:00:00Z",
    credential_revision: null,
    endpoint_revision: null,
    capability_provenance: [] as GovernanceModel["capability_provenance"],
    can_enable: true,
    enable_block_reason: null,
    capabilities: ["TEXT", "REASONING"],
    allowed: false,
    policy_id: null,
    policy_scope: null,
    policy_project_id: null,
    policy_stage_id: null,
    capability_warning: null,
    ...overrides,
  } as GovernanceModel;
  if (!("capability_provenance" in overrides)) value.capability_provenance = [evidence("TEXT", value), evidence("REASONING", value)];
  return value;
}

function governanceFetcher(models: GovernanceModel[]): typeof fetch {
  return async (input) => {
    const url = String(input);
    if (url.endsWith("/stages")) return response([{ stage_id: "PCB_REVIEW", display_name: "PCB 审查", required_capabilities: ["TEXT"] }]);
    if (url.includes("/available-models")) return response(models);
    if (url.includes("/project-defaults")) return response({}, 404);
    if (url.includes("/stage-policies")) return response([]);
    if (url.includes("/allowed-models")) return response({});
    return response([]);
  };
}

async function mounted(fetcher: typeof fetch, projectId?: string): Promise<{ container: HTMLDivElement; root: Root }> {
  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => { root.render(<ModelGovernancePanel fetcher={fetcher} projectId={projectId} />); await Promise.resolve(); await Promise.resolve(); });
  return { container, root };
}

describe("Task D model governance projection", () => {
  it("exposes exactly current, project and stage strategy tabs", () => {
    const markup = renderToStaticMarkup(<ModelGovernancePanel fetcher={governanceFetcher([])} projectId="project-1" />);
    expect(markup).toContain("当前模型");
    expect(markup).toContain("项目策略");
    expect(markup).toContain("阶段策略");
    expect(markup).not.toContain("内置模型参考目录");
    expect(markup).not.toContain("历史绑定");
  });

  it("groups by backend connection_id and keeps real connection names", () => {
    const same = model({ model_id: "model-b", display_name: "Model B" });
    const other = model({ connection_id: "conn-company", connection_name: "公司 API", endpoint_id: "endpoint-b", credential_ref: "credential:b", model_id: "model-c" });
    const groups = groupGovernanceModels([model(), same, other]);
    expect(groups).toHaveLength(2);
    expect(groups.find((item) => item.connection_id === "conn-personal")?.models).toHaveLength(2);
    expect(groups.find((item) => item.connection_id === "conn-company")?.connection_name).toBe("公司 API");
    expect(groups.map((item) => item.key)).not.toContain("deepseek\u001fendpoint-a\u001fcredential:a");
  });

  it("renders a model group and does not surface catalog or history", async () => {
    const { container, root } = await mounted(governanceFetcher([model()]));
    expect(container.querySelectorAll("[data-provider-connection-group]")).toHaveLength(1);
    expect(container.querySelector(".ide-model-governance__group-header")?.textContent).toContain("DeepSeek · 个人 API · 1 个模型");
    expect(container.textContent).toContain("Model A");
    expect(container.textContent).not.toContain("连接 1");
    expect(container.textContent).not.toContain("内置模型参考目录");
    expect(container.textContent).not.toContain("历史绑定");
    root.unmount();
  });

  it("keeps Allowed independent for two models with the same connection", async () => {
    const allowed = model({ allowed: true, policy_id: "policy-a", policy_scope: "GLOBAL" });
    const notAllowed = model({ model_id: "model-b", display_name: "Model B" });
    const { container, root } = await mounted(governanceFetcher([allowed, notAllowed]));
    const buttons = Array.from(container.querySelectorAll("[data-model-allow-button]"));
    expect(buttons.map((item) => item.getAttribute("aria-pressed"))).toEqual(["true", "false"]);
    root.unmount();
  });

  it("derives readiness only from verification, availability and PASS evidence", () => {
    const unverified = model({ verification_status: "UNVERIFIED", capabilities: ["TEXT", "VISION"], capability_provenance: [] });
    expect(deriveModelReadiness(unverified)).toEqual({ operational: "UNVERIFIED", capabilities: [] });
    expect(deriveModelReadiness(model({ capabilities: ["TEXT"], availability_status: "AVAILABLE" })).capabilities).toEqual(["TEXT"]);
    expect(deriveModelReadiness(model({ availability_status: "RATE_LIMITED" }).valueOf() as GovernanceModel).operational).toBe("RATE_LIMITED");
    expect(deriveModelReadiness(model({ model_id: "glm-vision", capabilities: ["VISION"], capability_provenance: [] })).capabilities).toEqual([]);
  });

  it("keeps complete identity in project and stage policy controls", async () => {
    const { container, root } = await mounted(governanceFetcher([model({ allowed: true })]), "project-1");
    const projectTab = container.querySelector('[data-model-governance-tab="project"]') as HTMLButtonElement;
    await act(async () => { projectTab.click(); await Promise.resolve(); });
    expect(container.querySelector('[aria-label="项目默认模型策略"]')).not.toBeNull();
    const stageTab = container.querySelector('[data-model-governance-tab="stage"]') as HTMLButtonElement;
    await act(async () => { stageTab.click(); await Promise.resolve(); });
    expect(container.querySelector('[aria-label="编辑阶段策略"]')).not.toBeNull();
    root.unmount();
  });

  it("runs only an explicit TEXT capability probe and refreshes canonical readiness", async () => {
    const calls: string[] = [];
    let probed = false;
    const candidate = model({ capabilities: [], capability_provenance: [], can_enable: false, verification_status: "UNVERIFIED" });
    const fetcher: typeof fetch = async (input, init) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/stages")) return response([]);
      if (url.includes("/available-models")) return response([probed ? model({ capabilities: ["TEXT"], capability_provenance: [evidence("TEXT", candidate)] }) : candidate]);
      if (url.includes("/project-defaults")) return response({}, 404);
      if (url.includes("/stage-policies")) return response([]);
      if (url.includes("/capability-probes/TEXT")) {
        probed = true;
        return response({ endpoint_id: "endpoint-a", model_profile_id: "profile-a", probe_type: "TEXT", probe_version: "v1", timestamp: "2026-09-14T00:00:00Z", status: "PASS" });
      }
      return response([]);
    };
    const { container, root } = await mounted(fetcher, "project-1");
    expect(calls.some((item) => item.includes("capability-probes"))).toBe(false);
    const details = container.querySelector("[data-model-details-button]") as HTMLButtonElement;
    await act(async () => { details.click(); });
    const probe = container.querySelector('[data-capability-probe="TEXT"]') as HTMLButtonElement;
    expect(probe?.textContent).toContain("真实请求");
    await act(async () => { probe.click(); await Promise.resolve(); });
    expect(calls.filter((item) => item.includes("capability-probes/TEXT"))).toHaveLength(1);
    expect(calls.filter((item) => item.includes("scope=PROJECT"))).toHaveLength(2);
    expect(calls.filter((item) => item.includes("scope=STAGE"))).toHaveLength(2);
    expect(container.textContent).toContain("文本");
    root.unmount();
  });

  it("shows which stage read failed without carrying its error into another tab", async () => {
    const fetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/stages")) return response([{ stage_id: "SYSTEM_ARCHITECTURE", display_name: "系统架构", required_capabilities: ["TEXT"] }]);
      if (url.includes("/available-models")) return response([model({ allowed: true })]);
      if (url.includes("/project-defaults")) return response({}, 404);
      if (url.includes("/stage-policies")) return response({ detail: "STAGE_POLICY_READ_FAILED" }, 503);
      return response([]);
    };
    const { container, root } = await mounted(fetcher, "project-1");
    await act(async () => { (container.querySelector('[data-model-governance-tab="stage"]') as HTMLButtonElement).click(); });
    expect(container.textContent).toContain("阶段策略读取失败");
    expect(container.textContent).toContain("STAGE_POLICY_READ_FAILED");
    await act(async () => { (container.querySelector('[data-model-governance-tab="current"]') as HTMLButtonElement).click(); });
    expect(container.textContent).not.toContain("STAGE_POLICY_READ_FAILED");
    root.unmount();
  });

  it("does not describe a failed candidate read as no eligible models", async () => {
    const fetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/stages")) return response([{ stage_id: "SYSTEM_ARCHITECTURE", display_name: "系统架构", required_capabilities: ["TEXT"] }]);
      if (url.includes("scope=GLOBAL")) return response([model()]);
      if (url.includes("scope=PROJECT") || url.includes("scope=STAGE")) return response({ detail: "candidate down" }, 503);
      if (url.includes("/project-defaults")) return response({}, 404);
      if (url.includes("/stage-policies")) return response([]);
      return response([]);
    };
    const { container, root } = await mounted(fetcher, "project-1");
    await act(async () => { (container.querySelector('[data-model-governance-tab="project"]') as HTMLButtonElement).click(); });
    expect(container.textContent).toContain("项目候选读取失败");
    expect(container.textContent).not.toContain("当前没有同时满足已允许");
    await act(async () => { (container.querySelector('[data-model-governance-tab="stage"]') as HTMLButtonElement).click(); });
    expect(container.textContent).toContain("阶段候选读取失败");
    expect(container.textContent).not.toContain("当前没有同时满足以下条件的模型");
    root.unmount();
  });

  it("refreshes project and stage candidates after Allowed changes", async () => {
    let allowed = false;
    const fetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/stages")) return response([{ stage_id: "REQUIREMENT_ANALYSIS", display_name: "需求分析", required_capabilities: ["TEXT"] }]);
      if (url.includes("/available-models")) return response([model({ allowed, can_enable: true })]);
      if (url.includes("/project-defaults")) return response({}, 404);
      if (url.includes("/stage-policies")) return response([]);
      if (url.includes("/allowed-models")) { allowed = true; return response({}); }
      return response([]);
    };
    const { container, root } = await mounted(fetcher, "project-1");
    await act(async () => { (container.querySelector("[data-model-allow-button]") as HTMLButtonElement).click(); await Promise.resolve(); });
    await act(async () => { (container.querySelector('[data-model-governance-tab="project"]') as HTMLButtonElement).click(); });
    expect(container.textContent).not.toContain("当前没有同时满足已允许");
    await act(async () => { (container.querySelector('[data-model-governance-tab="stage"]') as HTMLButtonElement).click(); });
    expect(container.textContent).not.toContain("当前没有同时满足以下条件的模型");
    root.unmount();
  });
});
