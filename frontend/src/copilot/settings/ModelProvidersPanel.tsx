import { useEffect, useRef, useState } from "react";

import type { AccountUsageProjection, ProviderProjection } from "../api/providerClient";
import {
  fetchModelGatewayProviderTemplates,
  fetchModelGatewayProviders,
  ModelGatewayRequestError,
  type ModelGatewayProvider,
  type ModelGatewayProviderTemplate,
} from "../api/modelGatewayClient";
import { fetchAccountUsage, refreshAccountUsage, refreshProviderModelPool } from "../api/providerClient";
import {
  fetchProviderConnections,
  ProviderConnectionRequestError,
  type ProviderConnection,
} from "../api/providerConnectionClient";

type ModelProvidersPanelProps = {
  fetcher?: typeof fetch;
  refreshSignal?: number;
  onAddProvider?(): void;
  onManageProvider?(connectionId: string, connectionName: string): void;
  onConnectionsChange?(value: ProviderConnection[]): void;
  actionsDisabled?: boolean;
  // Kept as a source-compatible seam for callers that still pass the old
  // projection. Provider management never reads it as connection identity.
  configuration?: ProviderProjection | null;
  refreshPoolFetcher?: typeof fetch;
  onConfigurationRefresh?(value: ProviderProjection): void;
  onUsageChange?(value: AccountUsageProjection[]): void;
  onTemplatesChange?(value: ModelGatewayProviderTemplate[]): void;
};

const protocolLabels: Record<string, string> = {
  OPENAI_RESPONSES: "OpenAI 兼容（Responses）",
  OPENAI_CHAT: "OpenAI 兼容（Chat Completions）",
  OPENAI_CHAT_COMPLETIONS: "OpenAI 兼容（Chat Completions）",
  ANTHROPIC_MESSAGES: "Anthropic Messages",
  GOOGLE_GENAI: "Google Gemini",
  GOOGLE_GENERATIVE_LANGUAGE: "Google Gemini",
  LOCAL_OPENAI_COMPATIBLE: "本地 OpenAI 兼容",
  DEEPSEEK_CHAT: "DeepSeek Chat",
  CUSTOM: "自定义协议",
};

const connectionStatusLabels: Record<string, string> = {
  HEALTHY: "正常",
  PARTIAL: "部分可用",
  UNAVAILABLE: "不可用",
  UNCONFIGURED: "未配置",
  UNKNOWN: "未知",
  AVAILABLE: "正常",
  PROVIDER_NOT_CONFIGURED: "未配置",
  PROVIDER_AUTH_FAILED: "认证失败",
  PROVIDER_NETWORK_UNAVAILABLE: "网络不可用",
  PROVIDER_MODEL_UNAVAILABLE: "模型不可用",
  PROVIDER_RATE_LIMITED: "受限",
  PROVIDER_CONFIG_INVALID: "配置无效",
  PROVIDER_CONNECTION_UNVERIFIED: "未验证",
  DISABLED: "已停用",
  QUARANTINED: "已隔离",
};

export function protocolDisplayName(value: string): string {
  return protocolLabels[value] ?? value;
}

export function connectionDisplayStatus(connection: Pick<ProviderConnection, "status" | "credential_configured"> & { health_status?: string }): string {
  const health = typeof connection.health_status === "string"
    ? connection.health_status
    : "UNKNOWN";
  return connectionStatusLabels[health] ?? "未知";
}

function errorLabel(error: unknown): string {
  if (error instanceof ProviderConnectionRequestError && error.code === "PROVIDER_CONNECTION_NETWORK_UNAVAILABLE") return "连接服务不可达";
  if (error instanceof ProviderConnectionRequestError) return "连接列表暂不可用";
  return "连接列表暂不可用";
}

function endpointHost(endpointUrl: string): string {
  try {
    return new URL(endpointUrl).host || "Endpoint 未配置";
  } catch {
    return "Endpoint 无效";
  }
}

function CanonicalModelProvidersPanel({
  fetcher,
  refreshSignal = 0,
  onAddProvider,
  onManageProvider,
  onConnectionsChange,
  actionsDisabled = false,
}: ModelProvidersPanelProps) {
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  async function refresh(): Promise<void> {
    if (fetcher === undefined || actionsDisabled) return;
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const value = await fetchProviderConnections(fetcher);
      if (currentRequest !== requestId.current) return;
      setConnections(value);
      onConnectionsChange?.(value);
    } catch (reason: unknown) {
      if (currentRequest !== requestId.current) return;
      setConnections([]);
      setError(errorLabel(reason));
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    if (fetcher === undefined) return;
    void refresh();
    return () => { requestId.current += 1; };
  }, [fetcher]);

  useEffect(() => {
    if (refreshSignal === 0) return;
    void refresh();
  }, [refreshSignal]);

  return <section className="ide-settings__model-gateway" aria-labelledby="model-gateway-heading" data-model-gateway-panel>
    <div className="ide-settings__section-heading">
      <div><span className="eyebrow">模型网关</span><h4 id="model-gateway-heading">提供商管理</h4></div>
      <button className="ide-secondary-button" type="button" onClick={() => void refresh()} disabled={loading || actionsDisabled} aria-busy={loading}>{loading ? "刷新中…" : "刷新连接"}</button>
    </div>
    {actionsDisabled && <p className="ide-form-error" role="alert">Desktop 会话已过期，Provider 操作已暂停。</p>}
    <p className="ide-settings__hint">这里显示已经保存的真实 Provider Connection；提供商模板只用于添加连接，不会混入当前连接列表。</p>
    <div className="ide-settings__provider-add" aria-label="添加模型提供商">
      <div><strong>添加模型提供商</strong><small>从后端模板创建新的真实连接。</small></div>
      <button type="button" className="ide-primary-button" aria-label="添加模型提供商" onClick={() => onAddProvider?.()} disabled={actionsDisabled}>＋ 添加模型提供商</button>
    </div>
    <div className="ide-settings__provider-catalog" aria-label="已配置连接">
      {connections.length === 0 && !loading && !error && <p role="status">尚未保存模型提供商连接。</p>}
      {connections.map((connection) => {
        const status = connectionDisplayStatus(connection);
        return <article key={connection.connection_id} className="ide-settings__provider-card" data-provider-card data-provider-connection-id={connection.connection_id}>
          <div><strong>{connection.connection_name}</strong><small>{connection.provider_display_name} · {endpointHost(connection.endpoint_url)}</small></div>
          <span className={status === "正常" ? "is-verified" : undefined}>{status}</span>
          <small>{connection.legacy_source ? "迁移保留连接" : "当前连接"} · {connection.credential_configured ? "凭据已配置" : "凭据未配置"}</small>
          <small>{connection.current_model_count} 个模型 · {protocolDisplayName(connection.protocol_family)}</small>
          <small>最近验证：{connection.verification.latest_at ?? "未知"}{connection.block_reason ? ` · 阻断：${connection.block_reason}` : ""}</small>
          <div className="ide-settings__provider-card-actions">
            <button type="button" className="ide-secondary-button" data-provider-manage-id={connection.connection_id} aria-label={`管理 ${connection.connection_name}`} onClick={() => onManageProvider?.(connection.connection_id, connection.connection_name)} disabled={actionsDisabled}>管理</button>
          </div>
        </article>;
      })}
    </div>
    {error && <p className="ide-form-error" role="alert">连接读取失败：{error}。可重试刷新连接。</p>}
  </section>;
}

function legacyErrorLabel(error: unknown): string {
  return error instanceof ModelGatewayRequestError && error.code === "MODEL_GATEWAY_NETWORK_UNAVAILABLE"
    ? "目录服务不可达"
    : "目录状态暂不可用";
}

function legacyStatusLabel(provider: ModelGatewayProvider, configuration: ProviderProjection | null | undefined): string {
  if (!provider.enabled) return "已禁用";
  const profile = configuration?.provider_profiles?.find((item) => item.provider_id === provider.provider_id);
  if (profile !== undefined) {
    if (profile.credential_status === "UNAVAILABLE") return "未知";
    if (!profile.configured) return "未配置";
    if (profile.connection_status === "AVAILABLE") return "可用";
    if (profile.connection_status === "PROVIDER_CONNECTION_UNVERIFIED") return "已配置";
    return connectionStatusLabels[profile.connection_status] ?? "已配置";
  }
  if (configuration?.provider === provider.provider_id && configuration.connection_status === "PROVIDER_CONNECTION_UNVERIFIED") return "已配置";
  return configuration?.provider === provider.provider_id ? connectionStatusLabels[configuration.connection_status] ?? "未知" : "已配置";
}

function LegacyModelProvidersPanel(props: ModelProvidersPanelProps) {
  const { fetcher, refreshPoolFetcher, configuration = null, onConfigurationRefresh, onAddProvider, onManageProvider, onUsageChange, onTemplatesChange, refreshSignal = 0, actionsDisabled = false } = props;
  const [providers, setProviders] = useState<ModelGatewayProvider[]>([]);
  const [usage, setUsage] = useState<AccountUsageProjection[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    if (fetcher === undefined || actionsDisabled) return;
    setLoading(true); setError(null);
    try {
      setProviders(await fetchModelGatewayProviders(fetcher));
      const templates = await fetchModelGatewayProviderTemplates(fetcher);
      onTemplatesChange?.(templates);
      if (refreshPoolFetcher !== undefined && configuration?.provider) onConfigurationRefresh?.(await refreshProviderModelPool(refreshPoolFetcher, configuration.provider));
    } catch (reason: unknown) { setError(legacyErrorLabel(reason)); } finally { setLoading(false); }
  }

  useEffect(() => {
    if (fetcher === undefined) return;
    let alive = true;
    void fetchModelGatewayProviders(fetcher).then((value) => { if (alive) setProviders(value); }).catch((reason: unknown) => { if (alive) setError(legacyErrorLabel(reason)); });
    void fetchModelGatewayProviderTemplates(fetcher).then((value) => onTemplatesChange?.(value)).catch(() => undefined);
    void fetchAccountUsage(fetcher).then((value) => { if (alive) { setUsage(value); onUsageChange?.(value); } }).catch(() => undefined);
    return () => { alive = false; };
  }, [fetcher, onTemplatesChange, onUsageChange]);

  useEffect(() => { if (refreshSignal > 0) void refresh(); }, [refreshSignal]);

  return <section className="ide-settings__model-gateway" aria-labelledby="model-gateway-heading" data-model-gateway-panel>
    <div className="ide-settings__section-heading"><div><span className="eyebrow">模型网关</span><h4 id="model-gateway-heading">提供商管理</h4></div><button className="ide-secondary-button" type="button" onClick={() => void refresh()} disabled={loading || actionsDisabled}>{loading ? "刷新中…" : "刷新目录"}</button></div>
    {actionsDisabled && <p className="ide-form-error" role="alert">Desktop 会话已过期，Provider 操作已暂停。</p>}
    <p className="ide-settings__hint">连接列表兼容投影仅供旧嵌入式调用方使用。</p>
    <div className="ide-settings__provider-add"><div><strong>添加模型提供商</strong><small>从后端模板创建新的真实连接。</small></div><button type="button" className="ide-primary-button" aria-label="添加模型提供商" onClick={() => onAddProvider?.()} disabled={actionsDisabled}>＋ 添加模型提供商</button></div>
    <div className="ide-settings__provider-catalog">{providers.length === 0 && <p role="status">尚未保存模型提供商连接。</p>}{providers.map((provider) => <article key={provider.provider_id} className="ide-settings__provider-card" data-provider-card><div><strong>{provider.display_name}</strong><small>{provider.provider_kind}</small></div><span>{legacyStatusLabel(provider, configuration)}</span><small>{provider.supported_protocols.map(protocolDisplayName).join(" · ")}</small><div className="ide-settings__provider-card-actions"><button type="button" className="ide-secondary-button" data-provider-manage-id={provider.provider_id} aria-label={`管理 ${provider.display_name}`} onClick={() => onManageProvider?.(provider.provider_id, provider.display_name)} disabled={actionsDisabled}>管理</button></div></article>)}</div>
    {error && <p className="ide-form-error" role="status">{error}</p>}
    {usage.length > 0 && <span hidden>{usage.length}</span>}
  </section>;
}

export function ModelProvidersPanel(props: ModelProvidersPanelProps) {
  // The canonical product path omits the old projection prop entirely. The
  // compatibility branch is retained only for older embedded Settings tests
  // and callers that explicitly provide that legacy projection.
  return props.configuration !== undefined ? <LegacyModelProvidersPanel {...props} /> : <CanonicalModelProvidersPanel {...props} />;
}
