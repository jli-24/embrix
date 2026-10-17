import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { ProviderConnectionSettingsPanel } from "./ProviderConnectionSettingsPanel";
import type { ProviderConnection } from "../api/providerConnectionClient";

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

const connection: ProviderConnection = {
  connection_id: "conn-a",
  connection_name: "个人 API",
  provider_id: "deepseek",
  provider_display_name: "DeepSeek",
  status: "AVAILABLE",
  endpoint_id: "endpoint-a",
  endpoint_url: "https://api.deepseek.com/v1",
  protocol_family: "OPENAI_CHAT",
  credential_ref: "credential:a",
  credential_configured: true,
  credential_revision: "rev-a",
  created_at: "2026-09-14T00:00:00Z",
  updated_at: "2026-09-14T00:00:00Z",
  deleted_at: null,
  discovery_state: "READY",
  discovery_required: false,
  current_model_count: 1,
  verification: { total: 1, current: 1, historical: 0, ambiguous_legacy: 0, latest_status: "PASS", latest_at: "2026-09-14T00:00:00Z" },
  usage: { status: "UNKNOWN", current_balance: null, last_known_balance: null, queried_at: null },
};

const model = {
  model_profile_id: "profile-a",
  provider_id: "deepseek",
  provider_display_name: "DeepSeek",
  connection_id: "conn-a",
  connection_name: "个人 API",
  endpoint_id: "endpoint-a",
  protocol_family: "OPENAI_CHAT",
  credential_ref: "credential:a",
  model_id: "deepseek-chat",
  display_name: "DeepSeek Chat",
  billing_class: "PAID",
  billing_provenance: "PROVIDER_METADATA",
  billing_source: null,
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
  capability_provenance: [],
  can_enable: true,
  enable_block_reason: null,
  capabilities: ["TEXT"],
  allowed: false,
};

async function mounted(fetcher: typeof fetch): Promise<{ container: HTMLDivElement; root: Root }> {
  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => { root.render(<ProviderConnectionSettingsPanel fetcher={fetcher} />); await Promise.resolve(); await Promise.resolve(); });
  return { container, root };
}

describe("Task D Provider Connection detail", () => {
  it("ignores a late detail response after switching connections", async () => {
    let resolveFirst!: (value: Response) => void;
    const second = { ...connection, connection_id: "conn-b", connection_name: "公司 API" };
    const fetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/provider-connections")) return response([connection, second]);
      if (url.endsWith("/provider-connections/conn-a")) return new Promise<Response>((resolve) => { resolveFirst = resolve; });
      if (url.endsWith("/provider-connections/conn-b")) return response(second);
      if (url.includes("/governance/available-models") || url.includes("/test-history") || url.includes("/runtime-health")) return response([]);
      if (url.endsWith("/usage")) return response({ status: "UNSUPPORTED", current_balance: null });
      return response([]);
    };
    const { container, root } = await mounted(fetcher);
    await act(async () => { (container.querySelector('[data-provider-manage-id="conn-a"]') as HTMLButtonElement).click(); });
    await act(async () => { (container.querySelector(".ide-settings__back-button") as HTMLButtonElement).click(); });
    await act(async () => { (container.querySelector('[data-provider-manage-id="conn-b"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await act(async () => { resolveFirst(response(connection)); });
    expect(container.querySelector('[data-provider-connection-id="conn-b"]')?.textContent).toContain("公司 API");
    expect(container.querySelector('[data-provider-connection-id="conn-b"]')?.textContent).not.toContain("个人 API");
    root.unmount();
  });

  it("separates model, history and health read failures from empty results", async () => {
    const fetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("/provider-connections")) return response([connection]);
      if (url.includes("/governance/available-models")) return response({ detail: "models down" }, 503);
      if (url.includes("/test-history")) return response({ detail: "history down" }, 503);
      if (url.includes("/runtime-health")) return response({ detail: "health down" }, 503);
      if (url.endsWith("/usage")) return response({ status: "UNSUPPORTED", current_balance: null });
      if (url.endsWith("/provider-connections/conn-a")) return response(connection);
      return response([]);
    };
    const { container, root } = await mounted(fetcher);
    await act(async () => { (container.querySelector('[data-provider-manage-id="conn-a"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    expect(container.textContent).toContain("模型列表读取失败");
    expect(container.textContent).toContain("连接测试历史读取失败");
    expect(container.textContent).toContain("运行健康读取失败");
    expect(container.textContent).not.toContain("暂无连接测试记录");
    root.unmount();
  });

  it("keeps detail fixed to connection_id and routes all mutations through the connection API", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push([url, init]);
      if (url.endsWith("/provider-templates")) return response([{ provider_id: "deepseek", display_name: "DeepSeek", provider_kind: "deepseek", supported_protocols: ["OPENAI_CHAT"], preset_status: "READY", default_endpoint_template: "https://api.deepseek.com/v1", model_discovery_mode: "AUTO_DISCOVERY", enabled: true }]);
      if (url.endsWith("/provider-connections") && !init?.method) return response([connection]);
      if (url.includes("/governance/available-models")) return response([model]);
      if (url.includes("/test-history")) return response([
        { test_id: "verification-a", provider_id: "deepseek", connection_id: "conn-a", endpoint_id: "endpoint-a", model_id: "deepseek-chat", credential_ref: "credential:secret", credential_revision: "rev-a", protocol: "OPENAI_CHAT", tested_at: "2026-09-14T00:00:01Z", latency_ms: 42.5, status: "AVAILABLE", error_code: null },
        { test_id: "verification-b", provider_id: "deepseek", connection_id: "conn-a", endpoint_id: "endpoint-a", model_id: "deepseek-chat", credential_ref: "credential:secret", credential_revision: "rev-a", protocol: "OPENAI_CHAT", tested_at: "2026-09-13T00:00:01Z", latency_ms: null, status: "PROVIDER_AUTH_FAILED", error_code: "PROVIDER_AUTH_FAILED" },
      ]);
      if (url.includes("/runtime-health")) return response([{ identity: { provider_id: "deepseek", endpoint_id: "endpoint-a", credential_ref: "credential:secret", model_profile_id: "profile-a", model_id: "deepseek-chat" }, endpoint_status: "AVAILABLE", last_success_at: "2026-09-14T00:00:02Z", last_failure_at: null, error_rate: null, latency_ms: 38.25, quota_status: "AVAILABLE", observed_at: "2026-09-14T00:00:02Z", sample_count: 2 }]);
      if (url.endsWith("/usage") && !init?.method) return response({ connection_id: "conn-a", provider_id: "deepseek", endpoint_id: "endpoint-a", credential_ref: "credential:a", billing_class: "UNKNOWN", current_balance: null, balance_status: "UNSUPPORTED", balance_source: null, queried_at: null, last_known_balance: null, last_success_at: null, balance: null, currency: null, usage_today: null, usage_month: null, budget_remaining: null, quota_remaining: null, reset_at: null, status: "UNSUPPORTED", source: null });
      if (url.includes("/models/refresh")) return response({ connection_id: "conn-a", provider_id: "deepseek", endpoint_id: "endpoint-a", status: "READY", raw_model_count: 1, normalized_model_count: 1, models: [{ model_profile_id: "profile-a", provider_id: "deepseek", endpoint_id: "endpoint-a", remote_model_id: "deepseek-chat", display_name: "DeepSeek Chat", advertised_family: "chat", capabilities: ["TEXT"], quality_tier: "STANDARD", billing_class: "PAID", billing_provenance: "PROVIDER_METADATA", billing_source: null, catalog_source: "PROVIDER_API", lifecycle_status: "ACTIVE", deprecated: false, metadata_updated_at: null, metadata_source: "provider", enabled: true, verification_status: "VERIFIED" }], diagnostic_summary: null, error_code: null });
      if (url.includes("/test-connection")) return response({ ...connection, test_result: { status: "AVAILABLE", reason_code: null, tested_at: "2026-09-14T00:00:03Z" } });
      if (url.includes("/provider-connections/conn-a") && init?.method === "PATCH") return response({ ...connection, connection_name: "个人 API v2", endpoint_url: "https://api.deepseek.com/v2" });
      if (url.includes("/provider-connections/conn-a/credential")) return response({ connection_id: "conn-a", credential_ref: "credential:a", configured: true, credential_revision: "rev-b" });
      if (url.includes("/provider-connections/conn-a") && init?.method === "DELETE") return response({ connection_id: "conn-a", status: "DISABLED" });
      return response(connection);
    });
    const { container, root } = await mounted(fetcher);
    await act(async () => { (container.querySelector('[data-provider-manage-id="conn-a"]') as HTMLButtonElement).click(); await Promise.resolve(); await Promise.resolve(); });
    expect(container.querySelector('[data-provider-internal-view="detail"][data-provider-connection-id="conn-a"]')).not.toBeNull();
    expect(container.textContent).toContain("DeepSeek · 个人 API");
    expect(container.querySelector('select[aria-label="提供商"]')).toBeNull();
    expect(container.textContent).toContain("测试模型");
    expect(container.textContent).toContain("连接测试历史");
    expect(container.textContent).toContain("2026-09-14");
    expect(container.textContent).toContain("AVAILABLE");
    expect(container.textContent).toContain("42.5 ms");
    expect(container.textContent).toContain("运行健康");
    expect(container.textContent).toContain("配额：AVAILABLE");
    expect(container.textContent).toContain("样本：2");
    expect(container.textContent).not.toContain("credential:secret");
    expect(container.textContent).not.toContain("secret.example");
    expect(container.textContent).not.toContain("RAW_PROVIDER_ERROR");

    const name = container.querySelector('input[aria-label="连接名称"]') as HTMLInputElement;
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setValue?.call(name, "个人 API v2");
    name.dispatchEvent(new Event("input", { bubbles: true }));
    await act(async () => { (Array.from(container.querySelectorAll("button")).find((item) => item.textContent === "保存配置") as HTMLButtonElement).click(); await Promise.resolve(); });
    expect(calls.some(([url, init]) => url.endsWith("/provider-connections/conn-a") && init?.method === "PATCH")).toBe(true);
    await act(async () => { (Array.from(container.querySelectorAll("button")).find((item) => item.textContent === "刷新模型") as HTMLButtonElement).click(); await Promise.resolve(); });
    expect(calls.some(([url, init]) => url.endsWith("/provider-connections/conn-a/models/refresh") && init?.method === "POST")).toBe(true);
    await act(async () => { (container.querySelector('[aria-label="测试模型"]') as HTMLSelectElement).value = "deepseek-chat"; container.querySelector('[aria-label="测试模型"]')?.dispatchEvent(new Event("change", { bubbles: true })); await Promise.resolve(); });
    await act(async () => { (Array.from(container.querySelectorAll("button")).find((item) => item.textContent === "测试") as HTMLButtonElement).click(); await Promise.resolve(); });
    expect(calls.some(([url, init]) => url.endsWith("/provider-connections/conn-a/test-connection") && init?.method === "POST")).toBe(true);
    expect(container.textContent).not.toContain("余额：不可获取");
    await act(async () => { (container.querySelector(".ide-danger-button") as HTMLButtonElement).click(); await Promise.resolve(); });
    expect(calls.some(([url, init]) => url.endsWith("/provider-connections/conn-a") && init?.method === "DELETE")).toBe(true);
    root.unmount();
  });

  it("does not report PASS for a business failure returned with HTTP 200", async () => {
    const calls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/provider-connections")) return response([connection]);
      if (url.includes("/governance/available-models")) return response([model]);
      if (url.includes("/test-connection")) return response({
        ...connection,
        test_result: { status: "PROVIDER_AUTH_FAILED", reason_code: "PROVIDER_AUTH_FAILED", tested_at: "2026-09-14T00:00:03Z" },
      });
      if (url.includes("/test-history") || url.includes("/runtime-health")) return response([]);
      if (url.endsWith("/usage")) return response({ status: "UNSUPPORTED", current_balance: null });
      if (url.endsWith("/provider-connections/conn-a")) return response(connection);
      return response([]);
    };
    const { container, root } = await mounted(fetcher);
    await act(async () => { (container.querySelector('[data-provider-manage-id="conn-a"]') as HTMLButtonElement).click(); await Promise.resolve(); });
    await act(async () => {
      const select = container.querySelector('select[aria-label="测试模型"]') as HTMLSelectElement;
      select.value = "deepseek-chat";
      select.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    await act(async () => { (Array.from(container.querySelectorAll("button")).find((item) => item.textContent === "测试") as HTMLButtonElement).click(); await Promise.resolve(); });

    expect(container.textContent).toContain("认证失败");
    expect(container.textContent).not.toContain("Verification：PASS");
    expect(calls.filter((url) => url.includes("/test-history"))).toHaveLength(2);
    expect(calls.filter((url) => url.includes("/runtime-health"))).toHaveLength(2);
    root.unmount();
  });
});
