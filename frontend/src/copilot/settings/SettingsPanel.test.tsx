import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { CapabilityStatusView } from "../api/capabilityClient";
import { ProviderRequestError, type ProviderProjection } from "../api/providerClient";
import { markDesktopSessionActive } from "../api/sessionState";
import type { FrontendProviderDiagnosticEvent } from "../api/webviewSessionDiagnostic";
import { isFrontendCredentialInputInvalid, SettingsPanel } from "./SettingsPanel";

const preferences = {
  theme: "system" as const,
  density: "comfortable" as const,
  answerDetail: "standard" as const,
  evidenceDisplay: "expanded" as const,
};

const configuredProjection: ProviderProjection = {
  provider: "deepseek",
  model: "deepseek-chat",
  endpoint_id: "endpoint-deepseek",
  credential_ref: "credential:deepseek",
  protocol_family: "OPENAI_CHAT",
  model_profile_id: "model-deepseek-deepseek-chat",
  model_pool: ["deepseek-chat", "deepseek-reasoner"].map((id) => ({ provider_id: "deepseek", endpoint_id: "endpoint-deepseek", model_profile_id: `model-deepseek-${id}`, model_id: id, display_name: id, availability: "AVAILABLE", availability_source: "PROVIDER_API", capabilities: ["TEXT"], verified_at: null })),
  base_url: "https://api.deepseek.com/v1",
  credential_status: "CONFIGURED",
  connection_status: "PROVIDER_CONNECTION_UNVERIFIED",
  last_test_status: "PROVIDER_CONNECTION_UNVERIFIED",
  tested_at: null,
  configuration_fingerprint: "sha256:configuration",
  tested_configuration_fingerprint: null,
  transmission_status: "NOT_APPROVED",
  restart_required: false,
  fingerprint: "sha256:provider",
};

const readyCapabilities: CapabilityStatusView[] = [
  { capability: "CHAT_ANALYSIS", status: "AVAILABLE", reason_code: "READY", detail: "runtime", runtime_status: "available", fingerprint: "sha256:runtime" },
  { capability: "PROVIDER_CONFIGURATION", status: "AVAILABLE", reason_code: "CONFIGURED", detail: "provider", fingerprint: "sha256:provider" },
  { capability: "CHAT_MODEL", status: "AVAILABLE", reason_code: "CONFIGURED", detail: "model", fingerprint: "sha256:model" },
];

const locations = [
  { location_id: "location-1", display_label: "31313", availability: "AVAILABLE", path: "D:\\xxx\\31313", path_leaf: "31313", project_type: "STM32", fingerprint: "sha256:location" },
];

const providerCatalogFetcher: typeof fetch = async (input) => {
  const url = String(input);
  if (url.endsWith("/model-gateway/providers")) return jsonResponse([
    { provider_id: "openai", display_name: "OpenAI", provider_kind: "openai", supported_protocols: ["OPENAI_RESPONSES", "OPENAI_CHAT"], preset_status: "READY", enabled: true },
    { provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true },
    { provider_id: "zhipu", display_name: "智谱 AI", provider_kind: "zhipu", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true },
    { provider_id: "custom", display_name: "自定义 Endpoint", provider_kind: "custom", supported_protocols: ["CUSTOM"], preset_status: "READY", enabled: true },
  ]);
  if (url.endsWith("/model-gateway/provider-templates")) return jsonResponse([
    { provider_id: "zhipu", display_name: "智谱 AI", provider_kind: "zhipu", supported_protocols: ["OPENAI_CHAT"], preset_status: "PRESET_PARTIAL", default_endpoint_template: "https://open.bigmodel.cn/api/paas/v4", model_discovery_mode: "AUTO_DISCOVERY", enabled: true },
    { provider_id: "openai-compatible", display_name: "OpenAI Compatible", provider_kind: "custom", supported_protocols: ["OPENAI_CHAT"], preset_status: "PRESET_PARTIAL", default_endpoint_template: null, model_discovery_mode: "MANUAL_MODEL_ID", enabled: true },
  ]);
  if (url.endsWith("/usage/accounts")) return jsonResponse([]);
  return jsonResponse([]);
};

type Deferred<T> = {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function statusValue(container: HTMLElement, label: string): string {
  const localized: Record<string, string> = { Runtime: "运行时", Provider: "提供商", Model: "测试模型", Credential: "凭据", Connection: "连接" };
  const renderedLabel = localized[label] ?? label;
  const strong = Array.from(container.querySelectorAll("strong")).find((item) => item.textContent === renderedLabel);
  return strong?.parentElement?.textContent?.slice(renderedLabel.length).trim() ?? "";
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const renderedLabel = label === "保存配置" ? "更新配置" : label;
  const value = Array.from(container.querySelectorAll("button")).find((item) => item.textContent === renderedLabel);
  if (!(value instanceof HTMLButtonElement)) throw new Error(`Missing button: ${label}`);
  return value;
}

function accessibleButtonName(value: HTMLButtonElement): string {
  return value.getAttribute("aria-label")?.trim() || value.textContent?.trim() || "";
}

function navigationButton(container: HTMLElement, label: string): HTMLButtonElement {
  const renderedLabel = label === "模型与提供商" ? "提供商管理" : label;
  const value = Array.from(container.querySelectorAll('[data-settings-navigation] button')).find((item) => item.getAttribute("aria-label") === renderedLabel);
  if (!(value instanceof HTMLButtonElement)) throw new Error(`Missing navigation button: ${label}`);
  return value;
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

const mountedRoots: Array<{ root: Root; container: HTMLDivElement }> = [];

async function renderSettings({
  providerFetcher,
  locationFetcher = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse(locations)),
  capabilities = readyCapabilities,
  diagnosticSink = () => undefined,
  initialProviderView = "detail",
  modelGatewayFetcher = providerCatalogFetcher,
  canonicalProviderConnections = false,
}: {
  providerFetcher: typeof fetch;
  locationFetcher?: typeof fetch;
  capabilities?: CapabilityStatusView[];
  diagnosticSink?: (event: FrontendProviderDiagnosticEvent) => void;
  initialProviderView?: "list" | "detail";
  modelGatewayFetcher?: typeof fetch;
  canonicalProviderConnections?: boolean;
}): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedRoots.push({ root, container });
  await act(async () => {
    root.render(<SettingsPanel preferences={preferences} onChange={() => undefined} onClose={() => undefined} capabilities={capabilities} providerFetcher={providerFetcher} locationFetcher={locationFetcher} diagnosticSink={diagnosticSink} modelGatewayFetcher={modelGatewayFetcher} canonicalProviderConnections={canonicalProviderConnections} />);
  });
  await settle();
  if (initialProviderView === "detail") {
    const currentProviderId = container.querySelector('[data-provider-current-id]')?.getAttribute("data-provider-current-id") || "deepseek";
    const manage = container.querySelector(`button[data-provider-manage-id="${currentProviderId}"]`);
    if (!(manage instanceof HTMLButtonElement)) throw new Error("Missing DeepSeek management action");
    await act(async () => { manage.click(); await Promise.resolve(); });
  }
  return { root, container };
}

beforeAll(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
});

afterEach(async () => {
  for (const mounted of mountedRoots.splice(0)) {
    await act(async () => mounted.root.unmount());
    mounted.container.remove();
  }
  markDesktopSessionActive();
});

describe("P0-1G.15J Provider readiness semantics", () => {
  it("exposes Version & Update as a real Settings section", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)) });
    const navigation = navigationButton(container, "版本与更新");
    expect(navigation.getAttribute("aria-controls")).toBe("settings-section-version-update");
    await act(async () => { navigation.click(); await Promise.resolve(); });
    expect((container.querySelector('[data-settings-section="version-update"]') as HTMLElement | null)?.hidden).toBe(false);
    expect(container.querySelector('[data-version-update-panel]')).not.toBeNull();
  });

  it.each(["READY", "STALE_WITH_CACHE", "FAILED"])("handles discovery %s and preserves staged persistence", async (discoveryState) => {
    const empty = { ...configuredProjection, provider: "openai", model: "", model_pool: [], base_url: "https://api.openai.com/v1", configuration_state: "FULLY_CONFIGURED", active_provider_id: "zhipu", endpoint_id: "endpoint-openai", credential_ref: "credential:openai", model_profile_id: null };
    const pool = ["gpt-4o", "gpt-4o-mini"].map((id) => ({ ...configuredProjection.model_pool![0], provider_id: "openai", endpoint_id: "endpoint-openai", model_profile_id: `model-openai-${id}`, model_id: id, display_name: id }));
    const discovered = { ...empty, model_pool: discoveryState === "FAILED" ? [] : pool, discovery_state: discoveryState };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
      if (String(url).includes("models/refresh")) return jsonResponse(discovered);
      if (String(url).endsWith("/configuration")) return jsonResponse({ ...discovered, model: JSON.parse(String(init?.body)).model, configuration_state: "FULLY_CONFIGURED", active_provider_id: "openai" });
      return jsonResponse(empty);
    });
    const { container } = await renderSettings({ providerFetcher: fetcher });
    await settle();
    const key = container.querySelector('[aria-label="API Key"]') as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(key, "onboarding-new-key");
      key.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => button(container, "保存配置").click());
    await settle();
    const select = container.querySelector('select[aria-label="测试模型"]') as HTMLSelectElement;
    expect(statusValue(container, "Credential")).toBe("已保存");
    expect(statusValue(container, "Provider")).toBe("已配置，未验证");
    expect(button(container, "保存配置").disabled).toBe(true);
    expect(button(container, "测试连接").disabled).toBe(true);
    expect(select.disabled).toBe(discoveryState === "FAILED");
    if (discoveryState === "FAILED") {
      expect(container.textContent).toContain("暂时无法获取可用模型");
      return;
    }
    expect(select.options.length).toBe(3);
    expect(select.value).toBe("");
    if (discoveryState === "STALE_WITH_CACHE") expect(container.textContent).toContain("目录刷新失败 / 使用缓存");
    await act(async () => { select.value = "gpt-4o"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(button(container, "保存配置").disabled).toBe(true);
    expect(button(container, "测试连接").disabled).toBe(false);
    expect(button(container, "保存配置").className).toContain("ide-secondary-button");
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/configuration"))).toHaveLength(0);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes("test-connection"))).toBe(false);
  });
  it("saves an official credential before a model exists and discovers once", async () => {
    const empty = { ...configuredProjection, provider: "openai", model: "", model_id: null, model_pool: [], base_url: "https://api.openai.com/v1", credential_status: "NOT_CONFIGURED" };
    const discovery = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
      if (String(url).includes("models/refresh")) return discovery.promise;
      if (String(url).endsWith("/credential") && init?.method === "PUT") return jsonResponse({ ...empty, credential_status: "CONFIGURED" });
      return jsonResponse(empty);
    });
    const { container } = await renderSettings({ providerFetcher: fetcher });
    await settle();
    const input = container.querySelector('[aria-label="API Key"]') as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "onboarding-new-key");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => button(container, "保存配置").click());
    await settle();
    expect(container.textContent).not.toContain("Model 不能为空");
    expect(fetcher.mock.calls.filter(([url, init]) => String(url).endsWith("/credential") && init?.method === "PUT")).toHaveLength(1);
    expect(statusValue(container, "Credential")).toBe("已保存");
    expect(container.textContent).toContain("正在获取可用模型");
    expect(button(container, "测试连接").disabled).toBe(true);
    discovery.resolve(jsonResponse({ ...empty, credential_status: "CONFIGURED" }));
    await settle();
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes("models/refresh"))).toHaveLength(1);
    expect(button(container, "保存配置").disabled).toBe(true);
    expect(container.textContent).toContain("暂无可用测试模型");
  });
  it("renders a centered modal with navigable settings sections", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)) });
    await settle();

    expect(container.querySelector('[role="dialog"][data-settings-layout="modal"]')).not.toBeNull();
    expect(container.querySelector('[data-settings-navigation]')).not.toBeNull();
    for (const label of ["常规", "外观", "模型与提供商", "高级"]) {
      expect(navigationButton(container, label)).toBeInstanceOf(HTMLButtonElement);
    }
    expect(container.querySelector('[data-settings-navigation] button[aria-label="研究模式"]')).toBeNull();
    expect(container.querySelector('[data-settings-section="provider"]')?.hasAttribute("hidden")).toBe(false);

    await act(async () => {
      navigationButton(container, "高级").click();
      await Promise.resolve();
    });

    expect(container.querySelector('[data-settings-section="advanced"]')?.hasAttribute("hidden")).toBe(false);
    expect(container.querySelector('[data-settings-section="provider"]')?.hasAttribute("hidden")).toBe(true);
    expect(container.querySelector('[data-settings-section="advanced"] details.ide-settings__danger-zone')?.hasAttribute("open")).toBe(false);
  });

  it("keeps save and Test Connection next to the credential field", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)) });
    await settle();

    const credentialLabel = container.querySelector('label:has(input[aria-label="API Key"])');
    const actions = container.querySelector('[data-provider-actions="near-credential"]');
    expect(credentialLabel).not.toBeNull();
    expect(actions).not.toBeNull();
    expect(credentialLabel?.compareDocumentPosition(actions ?? credentialLabel)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(button(container, "测试连接")).not.toBeNull();
  });

  it("navigates LIST, ADD, result, and DETAIL inside the single Settings surface", async () => {
    let connectionSaved = false;
    const zhipuProjection: ProviderProjection = {
      ...configuredProjection,
      provider: "zhipu",
      model: "",
      base_url: "https://open.bigmodel.cn/api/paas/v4",
      endpoint_id: "endpoint-zhipu",
      credential_ref: "credential:zhipu",
      protocol_family: "OPENAI_CHAT",
      model_profile_id: null,
      model_pool: ["glm-5.3", "glm-5.3-flash"].map((modelId) => ({ ...configuredProjection.model_pool![0], provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: `model-zhipu-${modelId}`, model_id: modelId, display_name: modelId })),
      available_models: ["legacy-glm-4.5"],
    };
    const providerFetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("provider_id=zhipu") || init?.method === "PUT" || url.includes("models/refresh")) {
        if (url.includes("/credential") && init?.method === "PUT") connectionSaved = true;
        return jsonResponse(zhipuProjection);
      }
      return jsonResponse(configuredProjection);
    });
    const modelGatewayFetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/model-gateway/providers")) return jsonResponse(connectionSaved ? [{ provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true }, { provider_id: "zhipu", display_name: "智谱 AI", provider_kind: "zhipu", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true }] : [{ provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true }]);
      if (url.endsWith("/model-gateway/provider-templates")) return jsonResponse([{ provider_id: "zhipu", display_name: "智谱 AI", provider_kind: "zhipu", supported_protocols: ["OPENAI_CHAT"], preset_status: "PRESET_PARTIAL", default_endpoint_template: "https://open.bigmodel.cn/api/paas/v4", model_discovery_mode: "AUTO_DISCOVERY", enabled: true }]);
      return jsonResponse([]);
    };
    const { container } = await renderSettings({ providerFetcher, initialProviderView: "list", modelGatewayFetcher });
    await settle();

    expect(container.querySelector('[data-provider-internal-view]')).toBeNull();
    await act(async () => { (container.querySelector('button[aria-label="添加模型提供商"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    expect(container.querySelector('[data-provider-internal-view="add"][data-provider-add-step="template"]')).not.toBeNull();
    expect(container.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    await act(async () => { (container.querySelector('[data-provider-template="zhipu"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    expect(container.querySelector('[data-provider-add-step="connection"]')).not.toBeNull();
    expect(container.querySelector('select[aria-label="测试模型"]')).toBeNull();
    const key = container.querySelector('[data-provider-add-step="connection"] input[aria-label="API Key"]') as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(key, "zhipu-secret");
    key.dispatchEvent(new Event("input", { bubbles: true }));
    await act(async () => { (container.querySelector('[aria-label="保存连接"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await settle();
    expect(container.querySelector('[data-provider-add-step="result"]')).not.toBeNull();
    expect(container.querySelector('[data-provider-add-step="result"]')?.textContent).toContain("共 2 个模型");
    expect(container.querySelector('[data-provider-add-step="result"]')?.textContent).not.toContain("legacy-glm-4.5");
    await act(async () => { (container.querySelector('[aria-label="完成"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await settle();
    expect(container.querySelector('[data-provider-internal-view]')).toBeNull();

    const manage = container.querySelector('button[data-provider-manage-id="zhipu"]');
    expect(manage).not.toBeNull();
    expect((manage as HTMLButtonElement).closest('[data-provider-card]')?.textContent).toContain("已配置");
    await act(async () => { (manage as HTMLButtonElement).click(); await Promise.resolve(); });
    await settle();
    expect(container.querySelector('[data-provider-internal-view="detail"][data-provider-connection-id="zhipu"]')).not.toBeNull();
    expect(container.querySelector('select[aria-label="提供商"]')).toBeNull();
    await act(async () => { navigationButton(container, "高级").click(); await Promise.resolve(); });
    expect(container.querySelector('[data-provider-internal-view]')).toBeNull();
    await act(async () => { navigationButton(container, "提供商管理").click(); await Promise.resolve(); });
    expect(container.querySelector('[data-provider-internal-view]')).toBeNull();
  });

  it("retains the saved connection when ADD model discovery fails", async () => {
    const zhipuProjection: ProviderProjection = {
      ...configuredProjection,
      provider: "zhipu",
      model: "",
      base_url: "https://open.bigmodel.cn/api/paas/v4",
      endpoint_id: "endpoint-zhipu",
      credential_ref: "credential:zhipu",
      protocol_family: "OPENAI_CHAT",
      model_profile_id: null,
      model_pool: [],
    };
    const providerFetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/models/refresh")) return jsonResponse({ code: "MODEL_DISCOVERY_UNAVAILABLE" }, 503);
      if (url.includes("/provider/configuration") || url.includes("/provider/credential")) return jsonResponse(zhipuProjection);
      return jsonResponse(configuredProjection);
    });
    const modelGatewayFetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/model-gateway/providers")) return jsonResponse([{ provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true }]);
      if (url.endsWith("/model-gateway/provider-templates")) return jsonResponse([{ provider_id: "zhipu", display_name: "智谱 AI", provider_kind: "zhipu", supported_protocols: ["OPENAI_CHAT"], preset_status: "PRESET_PARTIAL", default_endpoint_template: "https://open.bigmodel.cn/api/paas/v4", model_discovery_mode: "AUTO_DISCOVERY", enabled: true }]);
      return jsonResponse([]);
    };
    const { container } = await renderSettings({ providerFetcher, modelGatewayFetcher, initialProviderView: "list" });
    await act(async () => { (container.querySelector('button[aria-label="添加模型提供商"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await act(async () => { (container.querySelector('[data-provider-template="zhipu"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    const key = container.querySelector('[data-provider-add-step="connection"] input[aria-label="API Key"]') as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(key, "zhipu-secret");
      key.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { (container.querySelector('[aria-label="保存连接"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await settle();

    expect(container.querySelector('[data-provider-save-result="discovery-failed"]')).not.toBeNull();
    expect(container.textContent).toContain("连接已保存");
    expect(container.textContent).toContain("模型发现失败");
    expect(container.textContent).toContain("你可以稍后在提供商详情中重新刷新模型");
    expect(container.textContent).not.toContain("zhipu-secret");
    expect(providerFetcher.mock.calls.some(([input]) => String(input).includes("/models/refresh"))).toBe(true);
  });

  it("renders the single supported relay protocol as read-only user copy", async () => {
    const modelGatewayFetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/model-gateway/providers")) return jsonResponse([]);
      if (url.endsWith("/model-gateway/provider-templates")) return jsonResponse([{
        provider_id: "custom-relay",
        display_name: "自定义 API / 中转站",
        provider_kind: "custom",
        supported_protocols: ["OPENAI_CHAT"],
        preset_status: "PRESET_PARTIAL",
        default_endpoint_template: null,
        model_discovery_mode: "AUTO_DISCOVERY",
        enabled: true,
      }]);
      return jsonResponse([]);
    };
    const { container } = await renderSettings({
      providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)),
      initialProviderView: "list",
      modelGatewayFetcher,
    });
    await act(async () => { (container.querySelector('button[aria-label="添加模型提供商"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await act(async () => { (container.querySelector('[data-provider-template="custom-relay"]') as HTMLButtonElement).click(); await Promise.resolve(); });

    expect(container.querySelector('select[aria-label="接口格式"]')).toBeNull();
    expect(container.textContent).toContain("OpenAI 兼容（Chat Completions）");
    expect(container.textContent).toContain("保存后将创建连接并发现可用模型，不会自动启用任何模型。");
    expect(container.textContent).not.toContain("此步骤只保存连接名称、Provider、Endpoint、Credential 和 Protocol");
  });

  it("blocks only obviously invalid credential text in the UI", () => {
    expect(isFrontendCredentialInputInvalid("x")).toBe(true);
    expect(isFrontendCredentialInputInvalid("   ")).toBe(false);
    expect(isFrontendCredentialInputInvalid("abcdefgh")).toBe(false);
    expect(isFrontendCredentialInputInvalid("abcd efgh")).toBe(true);
    expect(isFrontendCredentialInputInvalid("abcd\n efgh")).toBe(true);
  });
  it("does not project unconfigured or unavailable while Provider is loading", async () => {
    const pendingProvider = deferred<Response>();
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockReturnValue(pendingProvider.promise) });

    expect(statusValue(container, "Runtime")).toBe("加载中");
    expect(statusValue(container, "Provider")).toBe("加载中");
    expect(statusValue(container, "Model")).toBe("加载中");
    expect(statusValue(container, "Credential")).toBe("加载中");
    expect(container.textContent).not.toContain("Credential未配置");
    expect(container.textContent).toContain("Model Request Readiness");
  });

  it("uses explicit backend model availability and does not infer it from connection alone", async () => {
    const verified = {
      ...configuredProjection,
      connection_status: "AVAILABLE" as const,
      last_test_status: "AVAILABLE" as const,
      tested_configuration_fingerprint: configuredProjection.configuration_fingerprint,
      model_available: true,
      verified_model: "deepseek-chat",
      available_models: ["deepseek-chat"],
    };
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(verified)) });
    await settle();
    expect(statusValue(container, "Model")).toBe("可用");
  });

  it("renders Model unavailable only when the backend explicitly reports a model failure", async () => {
    const failed = {
      ...configuredProjection,
      connection_status: "AVAILABLE" as const,
      last_test_status: "AVAILABLE" as const,
      tested_configuration_fingerprint: configuredProjection.configuration_fingerprint,
      model_available: false,
      verified_model: null,
      available_models: [],
    };
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(failed)) });
    await settle();
    expect(statusValue(container, "Model")).toBe("不可用");
  });

  it("renders authoritative available/configured state and keeps connection unverified independent", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)) });
    await settle();

    expect(statusValue(container, "Runtime")).toBe("可用");
    expect(statusValue(container, "Provider")).toBe("已配置，未验证");
    expect(statusValue(container, "Model")).toBe("已配置");
    expect(statusValue(container, "Credential")).toBe("已保存");
    expect(statusValue(container, "Connection")).toBe("未验证");
  });

  it("renders an enabled Test Connection button when Credential is configured", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)) });
    await settle();

    expect(button(container, "测试连接").disabled).toBe(false);
  });

  it("keeps the saved connection status and Test Connection independent while the model is edited", async () => {
    const availableProjection = {
      ...configuredProjection,
      connection_status: "AVAILABLE" as const,
      last_test_status: "AVAILABLE" as const,
      tested_configuration_fingerprint: configuredProjection.configuration_fingerprint,
    };
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(availableProjection)) });
    await settle();
    expect(statusValue(container, "Connection")).toBe("已验证");

    const modelInput = container.querySelector('select[aria-label="测试模型"]');
    if (!(modelInput instanceof HTMLSelectElement)) throw new Error("Missing test model selector");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
      setter?.call(modelInput, "deepseek-reasoner");
      modelInput.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    expect(statusValue(container, "Connection")).toBe("已验证");
    expect(button(container, "测试连接").disabled).toBe(false);
  });

  it("disables save for an existing Credential until a new key is entered", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)) });
    await settle();

    expect(button(container, "保存配置").disabled).toBe(true);
    expect(button(container, "保存配置").className).toContain("ide-secondary-button");
    const input = container.querySelector('input[aria-label="API Key"]');
    if (!(input instanceof HTMLInputElement)) throw new Error("Missing API Key input");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "replacement-secret");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await Promise.resolve();
    });
    expect(button(container, "保存配置").disabled).toBe(false);
    expect(button(container, "保存配置").className).toContain("ide-primary-button");
  });

  it("does not save a changed test model", async () => {
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    const modelInput = container.querySelector('select[aria-label="测试模型"]');
    if (!(modelInput instanceof HTMLSelectElement)) throw new Error("Missing test model selector");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
      setter?.call(modelInput, "deepseek-reasoner");
      modelInput.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    expect(button(container, "保存配置").disabled).toBe(true);
    expect(providerFetcher).toHaveBeenCalledTimes(1);
  });

  it("keeps Save visible and accessible through idle, disabled, saving, and error states", async () => {
    const pendingSave = deferred<Response>();
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockReturnValueOnce(pendingSave.promise);
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    const idleButton = button(container, "保存配置");
    expect(idleButton.disabled).toBe(true);
    expect(accessibleButtonName(idleButton)).toBe("保存配置");

    const input = container.querySelector('input[aria-label="API Key"]');
    if (!(input instanceof HTMLInputElement)) throw new Error("Missing API Key input");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "replacement-secret");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      button(container, "保存配置").click();
      await Promise.resolve();
    });

    const saveButton = button(container, "保存中…");
    expect(saveButton.disabled).toBe(true);
    expect(saveButton.className).toContain("ide-primary-button");
    expect(saveButton.getAttribute("aria-busy")).toBe("true");
    expect(accessibleButtonName(saveButton)).toBe("保存中…");

    pendingSave.reject(new Error("save failed"));
    await settle();

    const failedButton = button(container, "保存配置");
    expect(failedButton.disabled).toBe(false);
    expect(failedButton.getAttribute("aria-busy")).toBe("false");
    expect(accessibleButtonName(failedButton)).toBe("保存配置");
    expect(container.textContent).toContain("Provider 配置保存失败。");
  });

  it("uses the provider projection for status instead of the capability readiness flag", async () => {
    const failed = { ...configuredProjection, connection_status: "PROVIDER_AUTH_FAILED" as const, last_test_status: "PROVIDER_AUTH_FAILED" as const };
    const { container } = await renderSettings({
      providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(failed)),
    });
    await settle();

    expect(statusValue(container, "Provider")).toBe("认证失败");
    expect(container.textContent).not.toContain("Ready");
  });

  it("keeps clean save and Test Connection disabled without a Credential", async () => {
    const projection = { ...configuredProjection, credential_status: "NOT_CONFIGURED" as const, connection_status: "PROVIDER_NOT_CONFIGURED" as const, last_test_status: "PROVIDER_NOT_CONFIGURED" as const };
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(projection)) });
    await settle();

    expect(button(container, "保存配置").disabled).toBe(true);
    expect(button(container, "保存配置").className).toContain("ide-secondary-button");
    expect(button(container, "测试连接").disabled).toBe(true);
  });

  it("saves a new Credential through the injected Provider request path", async () => {
    const updatedProjection = { ...configuredProjection, connection_status: "PROVIDER_CONNECTION_UNVERIFIED" as const, last_test_status: "PROVIDER_CONNECTION_UNVERIFIED" as const };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(updatedProjection))
      .mockResolvedValueOnce(jsonResponse(updatedProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    const input = container.querySelector('input[aria-label="API Key"]');
    if (!(input instanceof HTMLInputElement)) throw new Error("Missing API Key input");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "replacement-secret");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await Promise.resolve();
    });
    await act(async () => {
      button(container, "保存配置").click();
      await Promise.resolve();
    });
    await settle();

    expect(providerFetcher).toHaveBeenCalledTimes(3);
    expect(providerFetcher.mock.calls[1]?.[0]).toBe("/api/v1/copilot/desktop/system/provider/credential");
    expect(providerFetcher.mock.calls[1]?.[1]).toEqual(expect.objectContaining({ method: "PUT" }));
    expect(container.textContent).toContain("✓ 已保存");
    expect(button(container, "保存配置").className).toContain("ide-secondary-button");
  });

  it.each([
    ["rate limit", jsonResponse({ ...configuredProjection, connection_status: "PROVIDER_RATE_LIMITED", last_test_status: "PROVIDER_RATE_LIMITED" })],
    ["network", jsonResponse({ ...configuredProjection, connection_status: "PROVIDER_NETWORK_UNAVAILABLE", last_test_status: "PROVIDER_NETWORK_UNAVAILABLE" })],
  ])("does not change the clean Save state after a %s connection failure", async (_label, failureResponse) => {
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(failureResponse);
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    const saveButton = button(container, "保存配置");
    expect(saveButton.disabled).toBe(true);
    expect(saveButton.className).toContain("ide-secondary-button");

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    expect(button(container, "保存配置").disabled).toBe(true);
    expect(button(container, "保存配置").className).toContain("ide-secondary-button");
  });

  it("renders Credential Management safe metadata without the secret", async () => {
    const projection: ProviderProjection = {
      ...configuredProjection,
      updated_at: "2026-09-01T10:00:00Z",
      validation_status: "PROVIDER_CONNECTION_UNVERIFIED",
    };
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(projection)) });
    await settle();

    const management = container.querySelector("details[data-credential-management]");
    if (!(management instanceof HTMLDetailsElement)) throw new Error("Missing Credential Management");
    await act(async () => { management.querySelector("summary")?.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(management.open).toBe(true);
    expect(management.textContent).toContain("Credential Exists");
    expect(management.textContent).toContain("Provider");
    expect(management.textContent).toContain("DeepSeek");
    expect(management.textContent).toContain("sha256:provider");
    expect(management.textContent).toContain("2026-09-01T10:00:00Z");
    expect(management.textContent).toContain("PROVIDER_CONNECTION_UNVERIFIED");
    expect(management.textContent).toContain("Connection Validated");
    expect(management.textContent).toContain("Connection Failed");
    expect(management.textContent).not.toContain("replacement-secret");
  });

  it("triggers the existing backend Test Connection API", async () => {
    const testedProjection: ProviderProjection = {
      ...configuredProjection,
      connection_status: "AVAILABLE",
      last_test_status: "AVAILABLE",
      tested_at: "2026-09-01T04:00:00Z",
      tested_configuration_fingerprint: configuredProjection.configuration_fingerprint,
    };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(testedProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    expect(providerFetcher).toHaveBeenCalledTimes(2);
    expect(providerFetcher.mock.calls[1]?.[0]).toBe("/api/v1/copilot/desktop/system/provider/test-connection");
    expect(providerFetcher.mock.calls[1]?.[1]).toEqual(expect.objectContaining({ method: "POST" }));
  });

  it("shows a loading state while Test Connection is pending", async () => {
    const pendingTest = deferred<Response>();
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockReturnValueOnce(pendingTest.promise);
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });

    const connectionButton = button(container, "测试中…");
    expect(connectionButton.disabled).toBe(true);
    expect(container.textContent).toContain("Connection 测试中");

    pendingTest.resolve(jsonResponse(configuredProjection));
    await settle();
  });

  it("renders safe success details from the Test Connection projection", async () => {
    const testedProjection: ProviderProjection = {
      ...configuredProjection,
      connection_status: "AVAILABLE",
      last_test_status: "AVAILABLE",
      tested_at: "2026-09-01T04:00:00Z",
      tested_configuration_fingerprint: configuredProjection.configuration_fingerprint,
    };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(testedProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    expect(container.textContent).toContain("Connection 已验证");
    expect(container.textContent).toContain("Provider DeepSeek");
    expect(container.textContent).toContain("Model deepseek-chat");
    expect(container.textContent).toMatch(/Latency \d+ ms/u);
    expect(container.textContent).toContain("Verified At 2026-09-01T04:00:00Z");
  });

  it("renders a categorized failure without exposing raw provider errors", async () => {
    const failedProjection: ProviderProjection = {
      ...configuredProjection,
      connection_status: "PROVIDER_AUTH_FAILED",
      last_test_status: "PROVIDER_AUTH_FAILED",
    };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(failedProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    const failure = container.querySelector('[aria-label="Connection 验证失败"]');
    expect(failure?.textContent).toContain("Connection 验证失败");
    expect(failure?.textContent).toContain("Reason 认证失败");
    expect(failure?.textContent).toContain("PROVIDER_AUTH_FAILED");
    expect(failure?.textContent).not.toContain("stack");
    expect(failure?.textContent).not.toContain("API Key");
  });

  it("does not classify a provider configuration failure as a network error", async () => {
    const failedProjection: ProviderProjection = {
      ...configuredProjection,
      connection_status: "PROVIDER_CONFIG_INVALID",
      last_test_status: "PROVIDER_CONFIG_INVALID",
    };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(failedProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    const failure = container.querySelector('[aria-label="Connection 验证失败"]');
    expect(failure?.textContent).toContain("Reason 配置错误");
    expect(failure?.textContent).toContain("PROVIDER_CONFIG_INVALID");
    expect(failure?.textContent).not.toContain("Reason 网络错误");
  });

  it("renders safe provider response diagnostics for a rate limit", async () => {
    const failedProjection: ProviderProjection = {
      ...configuredProjection,
      connection_status: "PROVIDER_RATE_LIMITED",
      last_test_status: "PROVIDER_RATE_LIMITED",
      connection_diagnostic: {
        stage: "PROVIDER_RESPONSE",
        http_method: "POST",
        request_url: "https://api.deepseek.com/v1/chat/completions",
        http_status: 429,
        provider_error_code: "RATE_LIMITED",
        normalized_error: "PROVIDER_RATE_LIMITED",
        transport_error_code: null,
      },
    };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(failedProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    const failure = container.querySelector('[aria-label="Connection 验证失败"]');
    expect(statusValue(container, "Provider")).toBe("已配置，未验证");
    expect(statusValue(container, "Model")).toBe("可用");
    expect(statusValue(container, "Connection")).toBe("未验证");
    expect(failure?.textContent).toContain("Provider DeepSeek");
    expect(failure?.textContent).toContain("Reason 请求受限");
    expect(failure?.textContent).toContain("Stage Provider 响应");
    expect(failure?.textContent).toContain("HTTP 429");
    expect(failure?.textContent).toContain("Endpoint endpoint-deepseek");
    expect(failure?.textContent).toContain("建议 稍后重试。");
  });

  it("projects Project Locations as readable metadata instead of internal states", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)) });
    await settle();

    await act(async () => { navigationButton(container, "高级").click(); });
    const locationsSection = container.querySelector('[data-settings-section="advanced"] .ide-settings__locations');
    expect(locationsSection?.textContent).toContain("项目");
    expect(locationsSection?.textContent).toContain("31313");
    expect(locationsSection?.textContent).toContain("路径");
    expect(locationsSection?.textContent).toContain("D:\\xxx\\31313");
    expect(locationsSection?.textContent).toContain("状态");
    expect(locationsSection?.textContent).toContain("已加载");
    expect(locationsSection?.textContent).toContain("类型");
    expect(locationsSection?.textContent).toContain("STM32");
    expect(locationsSection?.textContent).not.toContain("AVAILABLE");
    expect(locationsSection?.textContent).not.toContain("UNKNOWN");
  });

  it("loads Project Locations independently of legacy Provider diagnostics in canonical mode", async () => {
    const locationFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(locations));
    const { container } = await renderSettings({
      providerFetcher: vi.fn<typeof fetch>().mockRejectedValue(new TypeError("legacy provider failed")),
      locationFetcher,
      canonicalProviderConnections: true,
      initialProviderView: "list",
    });
    await act(async () => { navigationButton(container, "高级").click(); });

    expect(locationFetcher).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".ide-settings__locations")?.textContent).toContain("31313");
    expect(container.querySelector(".ide-settings__locations")?.textContent).not.toContain("工程位置加载中");
    expect(container.querySelector(".ide-settings__diagnostics")?.textContent).toContain("旧版 Provider 诊断");
  });

  it("shows canonical Project Locations empty and error states with a read-only retry", async () => {
    const locationFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ detail: "DESKTOP_IDENTITY_REQUIRED" }, 401))
      .mockResolvedValueOnce(jsonResponse(locations));
    const { container } = await renderSettings({
      providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)),
      locationFetcher,
      canonicalProviderConnections: true,
      initialProviderView: "list",
    });
    await act(async () => { navigationButton(container, "高级").click(); });
    expect(container.querySelector(".ide-settings__locations")?.textContent).toContain("暂无工程位置");

    await act(async () => { button(container, "重试读取工程位置").click(); });
    await settle();
    expect(container.querySelector(".ide-settings__locations")?.textContent).toContain("Desktop 身份未建立");
    await act(async () => { button(container, "重试读取工程位置").click(); });
    await settle();
    expect(container.querySelector(".ide-settings__locations")?.textContent).toContain("31313");
    expect(locationFetcher).toHaveBeenCalledTimes(3);
  });

  it("shows unconfigured only for an authoritative NOT_CONFIGURED projection", async () => {
    const projection = { ...configuredProjection, credential_status: "NOT_CONFIGURED" as const };
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(projection)) });
    await settle();

    expect(statusValue(container, "Credential")).toBe("未配置");
    expect(container.textContent).toContain("未配置");
    expect(button(container, "测试连接").disabled).toBe(true);
  });

  it("maps an ordinary projection failure to ERROR without claiming unconfigured", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockRejectedValue(new TypeError("fetch failed")) });
    await settle();

    expect(statusValue(container, "Runtime")).toBe("状态不可用");
    expect(statusValue(container, "Provider")).toBe("状态不可用");
    expect(statusValue(container, "Model")).toBe("状态不可用");
    expect(statusValue(container, "Credential")).toBe("配置状态暂不可用");
    expect(container.textContent).toContain("Provider 配置暂不可用。");
    expect(container.textContent).not.toContain("Credential未配置");
  });

  it("keeps DESKTOP_IDENTITY_REQUIRED distinct from Provider unavailable", async () => {
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockRejectedValue(new ProviderRequestError("DESKTOP_IDENTITY_REQUIRED")) });
    await settle();

    expect(container.textContent).toContain("Desktop 身份未建立");
    expect(statusValue(container, "Provider")).toBe("状态不可用");
    expect(statusValue(container, "Credential")).toBe("身份状态不可用");
  });

  it("clears a stale Provider error after a later successful projection fetch", async () => {
    const failingFetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("fetch failed"));
    const locationFetcher = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse(locations));
    const diagnosticSink = () => undefined;
    const { root, container } = await renderSettings({ providerFetcher: failingFetcher, locationFetcher, diagnosticSink });
    await settle();
    expect(container.textContent).toContain("Provider 配置暂不可用。");

    const succeedingFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection));
    await act(async () => {
      root.render(<SettingsPanel preferences={preferences} onChange={() => undefined} onClose={() => undefined} capabilities={readyCapabilities} providerFetcher={succeedingFetcher} locationFetcher={locationFetcher} diagnosticSink={diagnosticSink} />);
    });
    await settle();

    expect(container.textContent).not.toContain("Provider 配置暂不可用。");
    expect(statusValue(container, "Credential")).toBe("已保存");
  });

  it("does not treat connection-unverified capability debt as model unavailability", async () => {
    const capabilities = readyCapabilities.map((item) => item.capability === "CHAT_MODEL"
      ? { ...item, status: "UNAVAILABLE" as const, reason_code: "PROVIDER_CONNECTION_UNVERIFIED" }
      : item);
    const { container } = await renderSettings({ providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)), capabilities });
    await settle();

    expect(statusValue(container, "Model")).toBe("已配置");
    expect(statusValue(container, "Connection")).toBe("未验证");
  });

  it("uses the authoritative connection projection when the capability reason is stale", async () => {
    const capabilities = readyCapabilities.map((item) => item.capability === "CHAT_MODEL"
      ? { ...item, status: "UNAVAILABLE" as const, reason_code: "STALE_CAPABILITY_PROJECTION" }
      : item);
    const { container } = await renderSettings({
      providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)),
      capabilities,
    });
    await settle();

    expect(statusValue(container, "Model")).toBe("已配置");
    expect(statusValue(container, "Connection")).toBe("未验证");
  });

  it("refreshes a stale Model state after a model-confirming Test Connection", async () => {
    const staleCapabilities = readyCapabilities.map((item) => item.capability === "CHAT_MODEL"
      ? { ...item, status: "UNAVAILABLE" as const, reason_code: "STALE_CAPABILITY_PROJECTION" }
      : item);
    const testedProjection: ProviderProjection = {
      ...configuredProjection,
      connection_status: "AVAILABLE",
      last_test_status: "AVAILABLE",
      tested_at: "2026-09-11T02:00:00Z",
      tested_configuration_fingerprint: configuredProjection.configuration_fingerprint,
    };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(testedProjection));
    const { container } = await renderSettings({ providerFetcher, capabilities: staleCapabilities });
    await settle();

    expect(statusValue(container, "Model")).toBe("已配置");
    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    expect(statusValue(container, "Model")).toBe("可用");
    expect(statusValue(container, "Connection")).toBe("未验证");
  });

  it("keeps Model configured when a connection result has no model verification evidence", async () => {
    const endpointOnlyProjection: ProviderProjection = {
      ...configuredProjection,
      connection_status: "AVAILABLE",
      last_test_status: "AVAILABLE",
      tested_at: null,
      tested_configuration_fingerprint: null,
    };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(endpointOnlyProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    expect(statusValue(container, "Model")).toBe("已配置");
  });

  it.each([
    ["429", "PROVIDER_RATE_LIMITED" as const],
    ["401", "PROVIDER_AUTH_FAILED" as const],
  ])("keeps Model available after a rate-limited provider response and unavailable after auth failure", async (_httpStatus, connectionStatus) => {
    const failedProjection: ProviderProjection = {
      ...configuredProjection,
      connection_status: connectionStatus,
      last_test_status: connectionStatus,
      connection_diagnostic: {
        stage: "PROVIDER_RESPONSE",
        http_method: "POST",
        request_url: "https://api.deepseek.com/v1/chat/completions",
        http_status: Number(_httpStatus),
        provider_error_code: connectionStatus,
        normalized_error: connectionStatus,
        transport_error_code: null,
      },
    };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(failedProjection));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    expect(statusValue(container, "Model")).toBe(connectionStatus === "PROVIDER_RATE_LIMITED" ? "可用" : "不可用");
  });

  it("keeps Model status unknown after a network error", async () => {
    const networkFailure = { ...configuredProjection, connection_status: "PROVIDER_NETWORK_UNAVAILABLE" as const, last_test_status: "PROVIDER_NETWORK_UNAVAILABLE" as const };
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockResolvedValueOnce(jsonResponse(networkFailure));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    expect(statusValue(container, "Model")).toBe("未知");
  });

  it("keeps Model status unknown when Test Connection rejects at the network boundary", async () => {
    const providerFetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(configuredProjection))
      .mockRejectedValueOnce(new TypeError("network error"));
    const { container } = await renderSettings({ providerFetcher });
    await settle();

    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    expect(statusValue(container, "Model")).toBe("未知");
  });

  it("ignores an obsolete Provider response that resolves after a newer projection", async () => {
    const firstResponse = deferred<Response>();
    const firstFetcher = vi.fn<typeof fetch>().mockReturnValue(firstResponse.promise);
    const locationFetcher = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse(locations));
    const diagnosticSink = () => undefined;
    const { root, container } = await renderSettings({ providerFetcher: firstFetcher, locationFetcher, diagnosticSink });
    const latestProjection = { ...configuredProjection, model: "deepseek-reasoner" };
    const latestFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(latestProjection));

    await act(async () => {
      root.render(<SettingsPanel preferences={preferences} onChange={() => undefined} onClose={() => undefined} capabilities={readyCapabilities} providerFetcher={latestFetcher} locationFetcher={locationFetcher} diagnosticSink={diagnosticSink} />);
    });
    await settle();
    expect((container.querySelector('select[aria-label="测试模型"]') as HTMLSelectElement).value).toBe("deepseek-reasoner");

    firstResponse.resolve(jsonResponse({ ...configuredProjection, model: "obsolete-model" }));
    await settle();
    expect((container.querySelector('select[aria-label="测试模型"]') as HTMLSelectElement).value).toBe("deepseek-reasoner");
  });

  it("ignores an obsolete Project Locations response after a newer location fetch", async () => {
    const providerFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection));
    const firstResponse = deferred<Response>();
    const firstLocationFetcher = vi.fn<typeof fetch>().mockReturnValue(firstResponse.promise);
    const diagnosticSink = () => undefined;
    const { root, container } = await renderSettings({ providerFetcher, locationFetcher: firstLocationFetcher, diagnosticSink });
    const latestLocationFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([
      { ...locations[0], display_label: "Latest Workspace" },
    ]));

    await act(async () => {
      root.render(<SettingsPanel preferences={preferences} onChange={() => undefined} onClose={() => undefined} capabilities={readyCapabilities} providerFetcher={providerFetcher} locationFetcher={latestLocationFetcher} diagnosticSink={diagnosticSink} />);
    });
    await settle();
    expect(container.textContent).toContain("Latest Workspace");

    firstResponse.resolve(jsonResponse([{ ...locations[0], display_label: "Obsolete Workspace" }]));
    await settle();
    expect(container.textContent).toContain("Latest Workspace");
    expect(container.textContent).not.toContain("Obsolete Workspace");
  });

  it("emits H10 readiness only after Provider and Project Locations both succeed", async () => {
    const providerResponse = deferred<Response>();
    const locationResponse = deferred<Response>();
    const events: FrontendProviderDiagnosticEvent[] = [];
    await renderSettings({
      providerFetcher: vi.fn<typeof fetch>().mockReturnValue(providerResponse.promise),
      locationFetcher: vi.fn<typeof fetch>().mockReturnValue(locationResponse.promise),
      diagnosticSink: (event) => { events.push(event); },
    });

    expect(events.some((event) => event.stage === "WEBVIEW_FRONTEND_READINESS_PASS")).toBe(false);
    providerResponse.resolve(jsonResponse(configuredProjection));
    await settle();
    expect(events.some((event) => event.stage === "WEBVIEW_FRONTEND_READINESS_PASS")).toBe(false);
    locationResponse.resolve(jsonResponse(locations));
    await settle();

    expect(events.filter((event) => event.stage === "WEBVIEW_FRONTEND_READINESS_PASS")).toEqual([]);
  });

  it("never emits H10 readiness when a required dependency fails", async () => {
    const events: FrontendProviderDiagnosticEvent[] = [];
    await renderSettings({
      providerFetcher: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(configuredProjection)),
      locationFetcher: vi.fn<typeof fetch>().mockRejectedValue(new TypeError("locations failed")),
      diagnosticSink: (event) => { events.push(event); },
    });
    await settle();

    expect(events.some((event) => event.stage === "WEBVIEW_FRONTEND_READINESS_PASS")).toBe(false);
  });

  it("keeps Provider Detail fixed to the managed connection", async () => {
    const deepseekProjection: ProviderProjection = {
      ...configuredProjection,
      endpoint_id: "endpoint-deepseek",
      credential_ref: "credential:deepseek",
      protocol_family: "OPENAI_CHAT",
      model_profile_id: "model-deepseek-deepseek-chat",
      model_pool: [{ provider_id: "deepseek", endpoint_id: "endpoint-deepseek", model_profile_id: "model-deepseek-deepseek-chat", model_id: "deepseek-chat", display_name: "DeepSeek Chat", availability: "AVAILABLE", availability_source: "PROVIDER_API", capabilities: ["TEXT"], verified_at: "2026-09-12T00:00:00Z" }],
    };
    const zhipuProjection: ProviderProjection = {
      ...configuredProjection,
      provider: "zhipu",
      model: "glm-4.7-flash",
      base_url: "https://open.bigmodel.cn/api/paas/v4",
      endpoint_id: "endpoint-zhipu",
      credential_ref: "credential:zhipu",
      protocol_family: "OPENAI_CHAT",
      model_profile_id: "model-zhipu-glm-4.7-flash",
      model_pool: [{ provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: "model-zhipu-glm-4.7-flash", model_id: "glm-4.7-flash", display_name: "GLM 4.7 Flash", availability: "AVAILABLE", availability_source: "PROVIDER_API", billing_class: "FREE", billing_label: "免费", capabilities: ["TEXT"], verified_at: "2026-09-12T00:00:00Z" }],
    };
    const providerFetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input).includes("provider_id=zhipu") || init?.method === "POST") return jsonResponse(zhipuProjection);
      return jsonResponse(deepseekProjection);
    });
    const modelGatewayFetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/model-gateway/providers")) return jsonResponse([
        { provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true },
        { provider_id: "zhipu", display_name: "智谱 AI", provider_kind: "zhipu", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true },
      ]);
      if (url.endsWith("/model-gateway/provider-templates")) return jsonResponse([]);
      return jsonResponse([]);
    };
    const { container } = await renderSettings({ providerFetcher, modelGatewayFetcher, initialProviderView: "list" });
    await settle();
    (container.querySelector('button[data-provider-manage-id="zhipu"]') as HTMLButtonElement).click();
    await settle();
    const zhipuModelSelect = container.querySelector('select[aria-label="测试模型"]');
    if (!(zhipuModelSelect instanceof HTMLSelectElement)) throw new Error("Missing Zhipu model selector");
    expect(Array.from(zhipuModelSelect.options).map((item) => item.value)).toEqual(["", "glm-4.7-flash"]);
    expect(zhipuModelSelect.value).toBe("glm-4.7-flash");
    expect(container.querySelector('select[aria-label="提供商"]')).toBeNull();
  });

  it("tests the current Draft identity without saving it first", async () => {
    const zhipuProjection: ProviderProjection = {
      ...configuredProjection,
      provider: "zhipu",
      model: "glm-4.7-flash",
      base_url: "https://open.bigmodel.cn/api/paas/v4",
      credential_status: "CONFIGURED",
      endpoint_id: "endpoint-zhipu",
      credential_ref: "credential:zhipu",
      protocol_family: "OPENAI_CHAT",
      model_profile_id: "model-zhipu-glm-4.7-flash",
      model_pool: [{ provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: "model-zhipu-glm-4.7-flash", model_id: "glm-4.7-flash", display_name: "GLM 4.7 Flash", availability: "RATE_LIMITED", availability_source: "PROVIDER_API", billing_class: "FREE", billing_label: "免费", capabilities: ["TEXT"], verified_at: "2026-09-12T00:00:00Z" }],
    };
    const providerFetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (init?.method === "POST") return jsonResponse({ ...zhipuProjection, connection_status: "PROVIDER_RATE_LIMITED", last_test_status: "PROVIDER_RATE_LIMITED" });
      if (String(input).includes("provider_id=zhipu")) return jsonResponse(zhipuProjection);
      return jsonResponse(configuredProjection);
    });
    const modelGatewayFetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/model-gateway/providers")) return jsonResponse([
        { provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true },
        { provider_id: "zhipu", display_name: "智谱 AI", provider_kind: "zhipu", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true },
      ]);
      if (url.endsWith("/model-gateway/provider-templates")) return jsonResponse([]);
      return jsonResponse([]);
    };
    const { container } = await renderSettings({ providerFetcher, modelGatewayFetcher, initialProviderView: "list" });
    await settle();
    (container.querySelector('button[data-provider-manage-id="zhipu"]') as HTMLButtonElement).click();
    await settle();
    await act(async () => {
      button(container, "测试连接").click();
      await Promise.resolve();
    });
    await settle();

    const testCall = providerFetcher.mock.calls.find(([, init]) => init?.method === "POST");
    expect(testCall?.[1]?.body).toBe(JSON.stringify({
      provider_id: "zhipu",
      endpoint_id: "endpoint-zhipu",
      credential_ref: "credential:zhipu",
      model_profile_id: "model-zhipu-glm-4.7-flash",
      model_id: "glm-4.7-flash",
      protocol_family: "OPENAI_CHAT",
    }));
    expect(providerFetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
    expect(container.textContent).toContain("免费");
    expect(container.textContent).toContain("当前繁忙");
  });

  it("keeps custom Base URL editable and restricts probes to discovered models", async () => {
    const unavailable: ProviderProjection = {
      ...configuredProjection,
      model_pool: [{ provider_id: "deepseek", endpoint_id: "endpoint-deepseek", model_profile_id: "model-deepseek-deepseek-chat", model_id: "deepseek-chat", display_name: "DeepSeek Chat", availability: "MODEL_UNAVAILABLE", availability_source: "PROVIDER_API", capabilities: ["TEXT"], verified_at: null }],
    };
    const custom: ProviderProjection = {
      ...configuredProjection,
      provider: "custom",
      model: "relay-model",
      base_url: "https://relay.example/v1",
      credential_status: "NOT_CONFIGURED",
      connection_status: "PROVIDER_NOT_CONFIGURED",
      last_test_status: "PROVIDER_NOT_CONFIGURED",
      model_pool: [],
    };
    const providerFetcher = vi.fn<typeof fetch>().mockImplementation(async (input) =>
      String(input).includes("provider_id=custom") ? jsonResponse(custom) : jsonResponse(unavailable));
    const modelGatewayFetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/model-gateway/providers")) return jsonResponse([
        { provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", enabled: true },
        { provider_id: "custom", display_name: "自定义 Endpoint", provider_kind: "custom", supported_protocols: ["CUSTOM"], preset_status: "READY", enabled: true },
      ]);
      if (url.endsWith("/model-gateway/provider-templates")) return jsonResponse([]);
      return jsonResponse([]);
    };
    const { container } = await renderSettings({ providerFetcher, modelGatewayFetcher, initialProviderView: "list" });
    await settle();
    await act(async () => { (container.querySelector('button[data-provider-manage-id="deepseek"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await settle();
    expect((container.querySelector('select[aria-label="测试模型"]') as HTMLSelectElement).options).toHaveLength(1);
    expect(button(container, "测试连接").disabled).toBe(true);
    await act(async () => { (container.querySelector('[data-provider-internal-view="detail"] .ide-settings__back-button') as HTMLButtonElement).click(); await Promise.resolve(); });
    await act(async () => { (container.querySelector('button[data-provider-manage-id="custom"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await settle();
    const modelSelect = container.querySelector('select[aria-label="测试模型"]');
    const baseInput = container.querySelector('input[aria-label="Base URL"]');
    expect(modelSelect).toBeInstanceOf(HTMLSelectElement);
    expect(button(container, "测试连接").disabled).toBe(true);
    expect(baseInput).toBeInstanceOf(HTMLInputElement);
    expect((baseInput as HTMLInputElement).readOnly).toBe(false);
  });
});

describe("Model billing metadata projection", () => {
  function zhipuProjection(modelPool: NonNullable<ProviderProjection["model_pool"]>, model = "glm-4.7"): ProviderProjection {
    return {
      ...configuredProjection,
      provider: "zhipu",
      model,
      base_url: "https://open.bigmodel.cn/api/paas/v4",
      credential_status: "CONFIGURED",
      endpoint_id: "endpoint-zhipu",
      credential_ref: "credential:zhipu",
      protocol_family: "OPENAI_CHAT",
      model_profile_id: `model-zhipu-${model}`,
      model_pool: modelPool,
    };
  }

  it("renders FREE, PAID and UNKNOWN labels from backend billing metadata", async () => {
    const pool: NonNullable<ProviderProjection["model_pool"]> = [
      { provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: "model-zhipu-free", model_id: "glm-4.7-flash", display_name: "GLM 4.7 Flash", availability: "AVAILABLE", availability_source: "PROVIDER_API", billing_class: "FREE", billing_label: "免费", capabilities: ["TEXT"], verified_at: null },
      { provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: "model-zhipu-paid", model_id: "glm-4.7", display_name: "GLM 4.7", availability: "AVAILABLE", availability_source: "PROVIDER_API", billing_class: "PAID", billing_label: "付费", capabilities: ["TEXT"], verified_at: null },
      { provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: "model-zhipu-unknown", model_id: "totally-free-looking-flash", display_name: "Unknown Flash", availability: "AVAILABLE", availability_source: "PROVIDER_API", billing_class: "UNKNOWN", billing_label: "费用未知", capabilities: ["TEXT"], verified_at: null },
      { provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: "model-zhipu-denied", model_id: "glm-5.3", display_name: "GLM 5.3", availability: "PERMISSION_DENIED", availability_source: "PROVIDER_API", billing_class: "PAID", billing_label: "付费", capabilities: ["TEXT"], verified_at: null },
    ];
    const projection = zhipuProjection(pool);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(projection));
    const { container } = await renderSettings({ providerFetcher: fetcher });
    await settle();
    const select = container.querySelector('select[aria-label="测试模型"]');
    if (!(select instanceof HTMLSelectElement)) throw new Error("Missing Model selector");
    expect(Array.from(select.options).map((option) => option.value)).toEqual(["", "glm-4.7-flash", "glm-4.7", "totally-free-looking-flash"]);
    expect(select.textContent).toContain("GLM 4.7 Flash · 免费");
    expect(select.textContent).toContain("GLM 4.7 · 付费");
    expect(select.textContent).toContain("Unknown Flash · 费用未知");
    expect(select.textContent).not.toContain("GLM 5.3");
  });

  it("keeps a rate-limited FREE model selectable and never infers billing from its name", async () => {
    const pool: NonNullable<ProviderProjection["model_pool"]> = [
      { provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: "model-zhipu-flash", model_id: "totally-free-looking-flash", display_name: "Unknown Flash", availability: "RATE_LIMITED", availability_source: "PROVIDER_API", billing_class: "UNKNOWN", billing_label: "费用未知", capabilities: ["TEXT"], verified_at: null },
      { provider_id: "zhipu", endpoint_id: "endpoint-zhipu", model_profile_id: "model-zhipu-free", model_id: "glm-4.7-flash", display_name: "GLM 4.7 Flash", availability: "RATE_LIMITED", availability_source: "PROVIDER_API", billing_class: "FREE", billing_label: "免费", capabilities: ["TEXT"], verified_at: null },
    ];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(zhipuProjection(pool, "glm-4.7-flash")));
    const { container } = await renderSettings({ providerFetcher: fetcher });
    await settle();
    const select = container.querySelector('select[aria-label="测试模型"]');
    if (!(select instanceof HTMLSelectElement)) throw new Error("Missing Model selector");
    expect(select.textContent).toContain("GLM 4.7 Flash · 免费 · 当前繁忙");
    expect(select.textContent).toContain("Unknown Flash · 费用未知 · 当前繁忙");
    expect(select.value).toBe("glm-4.7-flash");
  });
});
