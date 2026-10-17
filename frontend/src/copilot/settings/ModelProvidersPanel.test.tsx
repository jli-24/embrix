import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { ModelProvidersPanel } from "./ModelProvidersPanel";
import type { ProviderConnection } from "../api/providerConnectionClient";

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function connection(overrides: Partial<ProviderConnection> = {}): ProviderConnection {
  return {
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
    current_model_count: 2,
    verification: { total: 1, current: 1, historical: 0, ambiguous_legacy: 0, latest_status: "PASS", latest_at: "2026-09-14T00:00:00Z" },
    usage: { status: "UNKNOWN", current_balance: null, last_known_balance: null, queried_at: null },
    ...overrides,
  };
}

async function mounted(fetcher: typeof fetch, onManageProvider = vi.fn()): Promise<{ container: HTMLDivElement; root: Root; onManageProvider: typeof onManageProvider }> {
  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => { root.render(<ModelProvidersPanel fetcher={fetcher} onManageProvider={onManageProvider} />); await Promise.resolve(); await Promise.resolve(); });
  return { container, root, onManageProvider };
}

describe("Provider Connection management list", () => {
  it("renders two real connections from the connection API, including same-provider connections", async () => {
    const first = connection({ legacy_source: "provider_configuration_migration", credential_configured: false });
    const second = connection({ connection_id: "conn-b", connection_name: "公司 API", endpoint_url: "https://relay.example.test/v1" });
    const fetcher: typeof fetch = async (input) => String(input).endsWith("/provider-connections") ? response([first, second]) : response([]);
    const { container, root } = await mounted(fetcher);
    const cards = container.querySelectorAll("[data-provider-card]");
    expect(cards).toHaveLength(2);
    expect(cards[0]?.getAttribute("data-provider-connection-id")).toBe("conn-a");
    expect(cards[1]?.getAttribute("data-provider-connection-id")).toBe("conn-b");
    expect(container.textContent).toContain("个人 API");
    expect(container.textContent).toContain("公司 API");
    expect(cards[0]?.textContent).toContain("api.deepseek.com");
    expect(cards[0]?.textContent).toContain("迁移保留连接");
    expect(cards[0]?.textContent).toContain("凭据未配置");
    expect(cards[1]?.textContent).toContain("relay.example.test");
    expect(container.textContent).not.toContain("连接 1");
    root.unmount();
  });

  it("uses connection_id for the manage action and backend display data for names", async () => {
    const onManageProvider = vi.fn();
    const fetcher: typeof fetch = async (input) => String(input).endsWith("/provider-connections") ? response([connection()]) : response([]);
    const { container, root } = await mounted(fetcher, onManageProvider);
    (container.querySelector('[data-provider-manage-id="conn-a"]') as HTMLButtonElement).click();
    expect(onManageProvider).toHaveBeenCalledWith("conn-a", "个人 API");
    root.unmount();
  });

  it("does not render provider templates as configured connections", async () => {
    const fetcher: typeof fetch = async (input) => String(input).endsWith("/provider-connections") ? response([]) : response([{ provider_id: "deepseek", display_name: "DeepSeek" }]);
    const { container, root } = await mounted(fetcher);
    expect(container.textContent).toContain("尚未保存模型提供商连接");
    expect(container.querySelector("[data-provider-template]")).toBeNull();
    root.unmount();
  });

  it("keeps the latest connection refresh and shows a read failure instead of an empty list", async () => {
    let resolveFirst!: (value: Response) => void;
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(response([connection({ connection_name: "最新连接" })]));
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ModelProvidersPanel fetcher={fetcher} />); });
    await act(async () => { root.render(<ModelProvidersPanel fetcher={fetcher} refreshSignal={1} />); });
    expect(container.textContent).toContain("最新连接");
    await act(async () => { resolveFirst(response([connection({ connection_name: "旧连接" })])); });
    expect(container.textContent).not.toContain("旧连接");
    root.unmount();

    const failed = await mounted(async () => response({ detail: "offline" }, 503));
    expect(failed.container.textContent).toContain("连接读取失败");
    expect(failed.container.textContent).not.toContain("尚未保存模型提供商连接");
    failed.root.unmount();
  });
});
