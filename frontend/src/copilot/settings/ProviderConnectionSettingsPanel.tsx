import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchModelGatewayProviderTemplates,
  ModelGatewayRequestError,
  type ModelGatewayProviderTemplate,
} from "../api/modelGatewayClient";
import {
  fetchGovernanceModels,
  type GovernanceModel,
} from "../api/modelGovernanceClient";
import {
  createProviderConnection,
  deleteProviderConnection,
  fetchProviderConnection,
  fetchProviderConnectionRuntimeHealth,
  fetchProviderConnectionTestHistory,
  fetchProviderConnectionUsage,
  patchProviderConnection,
  refreshProviderConnectionModels,
  refreshProviderConnectionUsage,
  saveProviderConnectionCredential,
  testProviderConnectionModel,
  ProviderConnectionRequestError,
  type ProviderConnection,
  type ProviderConnectionModel,
  type ProviderConnectionUsage,
  type ProviderConnectionRuntimeHealth,
  type ProviderConnectionTestRecord,
} from "../api/providerConnectionClient";
import { ModelProvidersPanel, protocolDisplayName } from "./ModelProvidersPanel";

type ProviderConnectionSettingsPanelProps = {
  fetcher?: typeof fetch;
  actionsDisabled?: boolean;
};

type View =
  | { type: "list" }
  | { type: "add-template" }
  | { type: "add-form"; templateId: string }
  | { type: "add-result"; connectionId: string; modelCount: number; error: string | null }
  | { type: "detail"; connectionId: string };

type ReadState = "LOADING" | "EMPTY" | "ERROR" | "READY";

type SelectableModel = Pick<ProviderConnectionModel, "model_profile_id" | "remote_model_id" | "display_name" | "capabilities"> & {
  capability_provenance?: GovernanceModel["capability_provenance"];
  allowed?: boolean;
  policy_status?: string;
  policy_scope?: string | null;
  policy_block_reason?: string | null;
  verified_at?: string | null;
};

const capabilityLabels: Record<string, string> = {
  TEXT: "文本",
  VISION: "视觉",
  REASONING: "推理",
  CODING: "代码",
  TOOL_CALLING: "工具调用",
  STRUCTURED_OUTPUT: "结构化输出",
};

function capabilityText(values: string[]): string {
  return values.map((value) => capabilityLabels[value] ?? value).join(" · ") || "能力证据未提供";
}

function errorLabel(error: unknown): string {
  if (error instanceof ProviderConnectionRequestError) return error.code;
  if (error instanceof ModelGatewayRequestError) return error.code;
  return "PROVIDER_CONNECTION_UNAVAILABLE";
}

function isCredentialless(template: ModelGatewayProviderTemplate): boolean {
  return template.provider_kind === "local";
}

function isUsageSupported(value: ProviderConnectionUsage): boolean {
  return ["AVAILABLE", "SUPPORTED", "STALE", "AUTH_FAILED"].includes(value.balance_status);
}

function safeStatus(value: string | null | undefined): string {
  return value && value.trim() ? value : "UNKNOWN";
}

const testFailureLabels: Record<string, string> = {
  PROVIDER_AUTH_FAILED: "认证失败，请检查此连接的凭据。",
  PROVIDER_MODEL_UNAVAILABLE: "模型不可用，请确认模型仍在此连接中。",
  PROVIDER_NETWORK_UNAVAILABLE: "网络连接失败，请检查 Endpoint 与网络。",
  PROVIDER_RATE_LIMITED: "请求受限，请稍后重试。",
  PROVIDER_CONFIG_INVALID: "连接配置无效，请检查 Endpoint 与协议。",
  PROVIDER_NOT_CONFIGURED: "连接尚未配置，请先保存凭据。",
  PROVIDER_CONNECTION_UNVERIFIED: "连接尚未验证，请重试并查看诊断详情。",
};

function testFailureLabel(status: string, reason: string | null): string {
  return testFailureLabels[status] ?? testFailureLabels[reason ?? ""] ?? "连接验证未通过，请查看诊断详情。";
}

function safeLatency(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} ms` : "未知";
}

function safeTimestamp(value: string | null | undefined): string {
  if (!value) return "未知";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? "未知" : `${timestamp.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

function safePercent(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? `${(value * 100).toFixed(1)}%`
    : "未知";
}

const healthLabels: Record<string, string> = {
  HEALTHY: "正常",
  PARTIAL: "部分可用",
  UNAVAILABLE: "不可用",
  UNCONFIGURED: "未配置",
  UNKNOWN: "未知",
};

function healthLabel(value: string | null | undefined): string {
  return value ? healthLabels[value] ?? value : "未知";
}

function modelFromGovernance(value: GovernanceModel): SelectableModel {
  return {
    model_profile_id: value.model_profile_id,
    remote_model_id: value.model_id,
    display_name: value.display_name,
    capabilities: value.capabilities,
    capability_provenance: value.capability_provenance,
    allowed: value.allowed,
    policy_status: value.policy_status,
    policy_scope: value.policy_scope,
    policy_block_reason: value.policy_block_reason,
    verified_at: value.verified_at,
  };
}

function modelFromRefresh(value: ProviderConnectionModel): SelectableModel {
  return {
    model_profile_id: value.model_profile_id,
    remote_model_id: value.remote_model_id,
    display_name: value.display_name,
    capabilities: value.capabilities,
  };
}

export function ProviderConnectionSettingsPanel({ fetcher, actionsDisabled = false }: ProviderConnectionSettingsPanelProps) {
  const [view, setView] = useState<View>({ type: "list" });
  const [templates, setTemplates] = useState<ModelGatewayProviderTemplate[]>([]);
  const [connection, setConnection] = useState<ProviderConnection | null>(null);
  const [models, setModels] = useState<SelectableModel[]>([]);
  const [verifications, setVerifications] = useState<ProviderConnectionTestRecord[]>([]);
  const [runtimeHealth, setRuntimeHealth] = useState<ProviderConnectionRuntimeHealth[]>([]);
  const [modelsState, setModelsState] = useState<ReadState>("LOADING");
  const [historyState, setHistoryState] = useState<ReadState>("LOADING");
  const [healthState, setHealthState] = useState<ReadState>("LOADING");
  const detailSession = useRef(0);
  const resourceRequest = useRef({ models: 0, history: 0, health: 0 });
  const [usage, setUsage] = useState<{ value: ProviderConnectionUsage | null; error: boolean; supported: boolean }>({ value: null, error: false, supported: false });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [connectionName, setConnectionName] = useState("");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [addName, setAddName] = useState("");
  const [addEndpoint, setAddEndpoint] = useState("");
  const [addProtocol, setAddProtocol] = useState("");
  const [addSecret, setAddSecret] = useState("");
  const [testModelId, setTestModelId] = useState("");
  const [testState, setTestState] = useState<"IDLE" | "TESTING" | "SUCCESS" | "FAILURE">("IDLE");
  const [testFailure, setTestFailure] = useState<string | null>(null);

  const selectedTemplate = useMemo(() => templates.find((item) => item.provider_id === templateId) ?? null, [templateId, templates]);
  const selectedModel = models.find((item) => item.remote_model_id === testModelId) ?? null;

  useEffect(() => {
    if (fetcher === undefined) return;
    let alive = true;
    void fetchModelGatewayProviderTemplates(fetcher).then((value) => {
      if (alive) setTemplates(value);
    }).catch(() => {
      if (alive) setTemplates([]);
    });
    return () => { alive = false; };
  }, [fetcher]);

  useEffect(() => () => { detailSession.current += 1; }, []);

  function resetDetail(): void {
    detailSession.current += 1;
    setConnection(null);
    setModels([]);
    setVerifications([]);
    setRuntimeHealth([]);
    setModelsState("LOADING");
    setHistoryState("LOADING");
    setHealthState("LOADING");
    setUsage({ value: null, error: false, supported: false });
    setConnectionName("");
    setEndpointUrl("");
    setSecret("");
    setTestModelId("");
    setTestState("IDLE");
    setTestFailure(null);
    setError(null);
  }

  async function readModels(connectionId: string, session: number): Promise<void> {
    if (fetcher === undefined) return;
    const request = ++resourceRequest.current.models;
    setModelsState("LOADING");
    try {
      const value = (await fetchGovernanceModels(fetcher, "GLOBAL"))
        .filter((item) => item.connection_id === connectionId).map(modelFromGovernance);
      if (session !== detailSession.current || request !== resourceRequest.current.models) return;
      setModels(value);
      setModelsState(value.length ? "READY" : "EMPTY");
    } catch {
      if (session !== detailSession.current || request !== resourceRequest.current.models) return;
      setModels([]);
      setModelsState("ERROR");
    }
  }

  async function readHistory(connectionId: string, session: number): Promise<void> {
    if (fetcher === undefined) return;
    const request = ++resourceRequest.current.history;
    setHistoryState("LOADING");
    try {
      const value = (await fetchProviderConnectionTestHistory(connectionId, fetcher)).slice(0, 7);
      if (session !== detailSession.current || request !== resourceRequest.current.history) return;
      setVerifications(value);
      setHistoryState(value.length ? "READY" : "EMPTY");
    } catch {
      if (session !== detailSession.current || request !== resourceRequest.current.history) return;
      setVerifications([]);
      setHistoryState("ERROR");
    }
  }

  async function readHealth(connectionId: string, session: number): Promise<void> {
    if (fetcher === undefined) return;
    const request = ++resourceRequest.current.health;
    setHealthState("LOADING");
    try {
      const value = await fetchProviderConnectionRuntimeHealth(connectionId, fetcher);
      if (session !== detailSession.current || request !== resourceRequest.current.health) return;
      setRuntimeHealth(value);
      setHealthState(value.length ? "READY" : "EMPTY");
    } catch {
      if (session !== detailSession.current || request !== resourceRequest.current.health) return;
      setRuntimeHealth([]);
      setHealthState("ERROR");
    }
  }

  async function openDetail(connectionId: string): Promise<void> {
    if (fetcher === undefined) return;
    resetDetail();
    const session = detailSession.current;
    setView({ type: "detail", connectionId });
    setLoading(true);
    try {
      const value = await fetchProviderConnection(connectionId, fetcher);
      if (session !== detailSession.current) return;
      setConnection(value);
      setConnectionName(value.connection_name);
      setEndpointUrl(value.endpoint_url);
      await Promise.all([readModels(connectionId, session), readHistory(connectionId, session), readHealth(connectionId, session),
        fetchProviderConnectionUsage(connectionId, fetcher).then((nextUsage) => {
          if (session === detailSession.current) setUsage({ value: nextUsage, error: false, supported: isUsageSupported(nextUsage) });
        }).catch(() => { if (session === detailSession.current) setUsage({ value: null, error: true, supported: false }); }),
      ]);
    } catch (reason: unknown) {
      if (session === detailSession.current) setError(errorLabel(reason));
    } finally {
      if (session === detailSession.current) setLoading(false);
    }
  }

  function openAdd(): void {
    setError(null);
    setTemplateId("");
    setAddName("");
    setAddEndpoint("");
    setAddProtocol("");
    setAddSecret("");
    setView({ type: "add-template" });
  }

  function chooseTemplate(value: ModelGatewayProviderTemplate): void {
    setTemplateId(value.provider_id);
    setAddName(value.provider_kind === "custom" ? "" : value.display_name);
    setAddEndpoint(value.default_endpoint_template ?? "");
    setAddProtocol(value.supported_protocols.length === 1 ? value.supported_protocols[0] : "");
    setAddSecret("");
    setError(null);
    setView({ type: "add-form", templateId: value.provider_id });
  }

  async function saveNewConnection(): Promise<void> {
    if (fetcher === undefined || selectedTemplate === null) return;
    const name = addName.trim();
    const endpoint = addEndpoint.trim();
    if (!name || !endpoint) {
      setError("请输入连接名称和接口地址。");
      return;
    }
    if (selectedTemplate.supported_protocols.length > 1 && !addProtocol) {
      setError("请选择协议。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      let created = await createProviderConnection({
        provider_id: selectedTemplate.provider_id,
        connection_name: name,
        endpoint_url: endpoint,
        protocol_family: addProtocol || undefined,
      }, fetcher);
      const credential = addSecret.trim();
      if (credential && !isCredentialless(selectedTemplate)) {
        await saveProviderConnectionCredential(created.connection_id, credential, fetcher);
      }
      let modelCount = created.current_model_count;
      let discoveryError: string | null = null;
      try {
        const refreshed = await refreshProviderConnectionModels(created.connection_id, fetcher);
        modelCount = refreshed.normalized_model_count;
        created = await fetchProviderConnection(created.connection_id, fetcher);
      } catch (reason: unknown) {
        discoveryError = errorLabel(reason);
      }
      setView({ type: "add-result", connectionId: created.connection_id, modelCount, error: discoveryError });
    } catch (reason: unknown) {
      setError(errorLabel(reason));
    } finally {
      setLoading(false);
    }
  }

  async function saveDetail(): Promise<void> {
    if (fetcher === undefined || connection === null) return;
    const name = connectionName.trim();
    const endpoint = endpointUrl.trim();
    if (!name || !endpoint) {
      setError("连接名称和接口地址不能为空。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      let next = await patchProviderConnection(connection.connection_id, {
        connection_name: name,
        endpoint_url: endpoint,
      }, fetcher);
      if (secret.trim()) {
        await saveProviderConnectionCredential(connection.connection_id, secret.trim(), fetcher);
        setSecret("");
        next = await fetchProviderConnection(connection.connection_id, fetcher);
      }
      setConnection(next);
      setConnectionName(next.connection_name);
      setEndpointUrl(next.endpoint_url);
    } catch (reason: unknown) {
      setError(errorLabel(reason));
    } finally {
      setLoading(false);
    }
  }

  async function refreshModels(): Promise<void> {
    if (fetcher === undefined || connection === null) return;
    const session = detailSession.current;
    const connectionId = connection.connection_id;
    const request = ++resourceRequest.current.models;
    setModelsState("LOADING");
    setLoading(true);
    setError(null);
    try {
      const result = await refreshProviderConnectionModels(connectionId, fetcher);
      if (session !== detailSession.current || request !== resourceRequest.current.models) return;
      setModels(result.models.map(modelFromRefresh));
      setModelsState(result.models.length ? "READY" : "EMPTY");
      const next = await fetchProviderConnection(connectionId, fetcher);
      if (session === detailSession.current && request === resourceRequest.current.models) setConnection(next);
    } catch (reason: unknown) {
      if (session === detailSession.current && request === resourceRequest.current.models) {
        setModels([]);
        setModelsState("ERROR");
        setError(`模型发现失败 · ${errorLabel(reason)}`);
      }
    } finally {
      if (session === detailSession.current && request === resourceRequest.current.models) setLoading(false);
    }
  }

  async function testConnection(): Promise<void> {
    if (fetcher === undefined || connection === null || selectedModel === null) return;
    const session = detailSession.current;
    const connectionId = connection.connection_id;
    setTestState("TESTING");
    setTestFailure(null);
    setError(null);
    try {
      const result = await testProviderConnectionModel(connectionId, {
        model_profile_id: selectedModel.model_profile_id,
        model_id: selectedModel.remote_model_id,
      }, fetcher);
      if (session !== detailSession.current) return;
      setConnection(result);
      if (result.test_result?.status === "AVAILABLE") {
        setTestState("SUCCESS");
      } else {
        setTestState("FAILURE");
        setTestFailure(testFailureLabel(result.test_result?.status ?? "UNKNOWN", result.test_result?.reason_code ?? null));
      }
    } catch (reason: unknown) {
      if (session !== detailSession.current) return;
      setTestState("FAILURE");
      setTestFailure("连接测试请求失败，请检查网络或稍后重试。");
      setError(`连接验证请求失败 · ${errorLabel(reason)}`);
    } finally {
      if (session === detailSession.current) await Promise.all([
        readModels(connectionId, session), readHistory(connectionId, session), readHealth(connectionId, session),
      ]);
    }
  }

  async function refreshUsage(): Promise<void> {
    if (fetcher === undefined || connection === null) return;
    try {
      const value = await refreshProviderConnectionUsage(connection.connection_id, fetcher);
      setUsage({ value, error: false, supported: isUsageSupported(value) });
    } catch {
      setUsage((current) => ({ ...current, error: true }));
    }
  }

  async function deleteConnection(): Promise<void> {
    if (fetcher === undefined || connection === null) return;
    setLoading(true);
    setError(null);
    try {
      await deleteProviderConnection(connection.connection_id, fetcher);
      resetDetail();
      setView({ type: "list" });
      setRefreshSignal((value) => value + 1);
    } catch (reason: unknown) {
      setError(`连接删除失败 · ${errorLabel(reason)}`);
    } finally {
      setLoading(false);
    }
  }

  if (view.type === "list") {
    return <ModelProvidersPanel fetcher={fetcher} refreshSignal={refreshSignal} onAddProvider={openAdd} onManageProvider={(id) => void openDetail(id)} actionsDisabled={actionsDisabled} />;
  }

  if (view.type === "add-template") {
    return <section className="ide-provider-internal-view" data-provider-internal-view="add" data-provider-add-step="template">
      <button type="button" className="ide-settings__back-button" onClick={() => setView({ type: "list" })}>← 提供商管理</button>
      <div className="ide-provider-detail-heading"><div><span className="eyebrow">添加连接</span><h3>选择提供商模板</h3></div></div>
      <p className="ide-settings__hint">模板只提供默认配置，保存后才会创建真实 Provider Connection。</p>
      <div className="ide-settings__provider-catalog" aria-label="提供商模板">
        {templates.map((template) => <button type="button" className="ide-settings__provider-card" data-provider-template={template.provider_id} key={template.provider_id} onClick={() => chooseTemplate(template)}>
          <strong>{template.display_name}</strong><small>{template.provider_kind === "local" ? "本地 Endpoint" : template.provider_kind === "custom" ? "自定义 API / 中转站" : "官方提供商"}</small><small>{template.default_endpoint_template ?? "需要填写 Endpoint"}</small><small>{template.supported_protocols.map(protocolDisplayName).join(" · ")}</small>
        </button>)}
        {templates.length === 0 && <p role="status">提供商模板暂不可用。</p>}
      </div>
    </section>;
  }

  if (view.type === "add-form" && selectedTemplate !== null) {
    return <section className="ide-provider-internal-view" data-provider-internal-view="add" data-provider-add-step="connection">
      <button type="button" className="ide-settings__back-button" onClick={() => setView({ type: "add-template" })}>← 提供商模板</button>
      <h3>{selectedTemplate.display_name}</h3>
      <label>连接名称<input value={addName} onChange={(event) => setAddName(event.target.value)} aria-label="连接名称" /></label>
      <label>Endpoint<input value={addEndpoint} onChange={(event) => setAddEndpoint(event.target.value)} aria-label="Endpoint" /></label>
      {selectedTemplate.supported_protocols.length === 1
        ? <p><strong>协议</strong> {protocolDisplayName(selectedTemplate.supported_protocols[0])}</p>
        : <label>协议<select value={addProtocol} onChange={(event) => setAddProtocol(event.target.value)} aria-label="协议" disabled={loading}><option value="">请选择协议</option>{selectedTemplate.supported_protocols.map((protocol) => <option key={protocol} value={protocol}>{protocolDisplayName(protocol)}</option>)}</select></label>}
      <label>API Key<input type="password" value={addSecret} onChange={(event) => setAddSecret(event.target.value)} aria-label="API Key" autoComplete="new-password" placeholder={isCredentialless(selectedTemplate) ? "本地 Endpoint 无需凭据" : "输入 API Key"} /></label>
      {error && <p className="ide-form-error" role="alert">{error}</p>}
      <p className="ide-settings__hint">保存后将创建连接并发现可用模型，不会自动启用任何模型。</p>
      <div className="ide-settings__actions ide-settings__provider-actions"><button type="button" className="ide-secondary-button" onClick={() => setView({ type: "add-template" })} disabled={loading}>取消</button><button type="button" className="ide-primary-button" aria-label="保存连接" onClick={() => void saveNewConnection()} disabled={loading}>{loading ? "保存中…" : "保存连接"}</button></div>
    </section>;
  }

  if (view.type === "add-result") {
    return <section className="ide-provider-internal-view" data-provider-internal-view="add" data-provider-add-step="result"><h3>连接已保存</h3><p role="status">发现模型：{view.modelCount} 个</p>{view.error && <p className="ide-form-error" role="alert">模型发现失败：{view.error}</p>}<p className="ide-settings__hint">Connection ID：{view.connectionId}</p><button type="button" className="ide-primary-button" onClick={() => { setView({ type: "list" }); setRefreshSignal((value) => value + 1); }}>完成</button></section>;
  }

  return <section className="ide-provider-internal-view" data-provider-internal-view="detail" data-provider-connection-id={view.type === "detail" ? view.connectionId : ""}>
    <button type="button" className="ide-settings__back-button" onClick={() => { resetDetail(); setView({ type: "list" }); }}>← 提供商管理</button>
    {loading && connection === null && <p role="status">连接详情加载中…</p>}
    {connection !== null && <>
      <div className="ide-provider-detail-heading"><div><span className="eyebrow">连接详情</span><h3>{connection.provider_display_name} · {connection.connection_name}</h3></div><span>{healthLabel(connection.health_status)}</span></div>
      <section className="ide-provider-detail-section" aria-labelledby="provider-connection-config-heading"><h4 id="provider-connection-config-heading">连接配置</h4><label>连接名称<input value={connectionName} onChange={(event) => setConnectionName(event.target.value)} aria-label="连接名称" /></label><p><strong>提供商</strong> {connection.provider_display_name}</p><label>Endpoint<input value={endpointUrl} onChange={(event) => setEndpointUrl(event.target.value)} aria-label="Endpoint" /></label><p><strong>Protocol</strong> {protocolDisplayName(connection.protocol_family)}</p><p><strong>Credential</strong> {connection.credential_configured ? "已配置 · ••••••••" : "未配置"}</p><label>替换 API Key<input type="password" value={secret} onChange={(event) => setSecret(event.target.value)} aria-label="API Key" autoComplete="new-password" placeholder={connection.credential_configured ? "•••••••• 已配置；输入新值以替换" : "输入 API Key"} /></label><button type="button" className="ide-primary-button" onClick={() => void saveDetail()} disabled={loading}>{loading ? "保存中…" : "保存配置"}</button></section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-health-heading"><h4 id="provider-health-heading">治理状态</h4><p>总体状态：{healthLabel(connection.health_status)}</p><p>阻断原因：{connection.block_reason ?? "—"}</p><dl><div><dt>接口状态</dt><dd>{safeStatus(connection.endpoint_status)} · 证据：{safeStatus(connection.endpoint_freshness)} · 观测：{safeTimestamp(connection.endpoint_observed_at)}</dd></div><div><dt>凭据</dt><dd>{safeStatus(connection.credential_status)} · 证据：{safeStatus(connection.credential_freshness)} · 修订：{connection.credential_revision ?? "未知"}</dd></div><div><dt>模型发现</dt><dd>{safeStatus(connection.discovery_state)} · 证据：{safeStatus(connection.discovery_freshness)} · 观测：{safeTimestamp(connection.discovery_observed_at)}</dd></div></dl></section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-discovery-heading"><h4 id="provider-discovery-heading">模型发现</h4><p>当前模型：{connection.current_model_count} 个</p>{modelsState === "LOADING" && <p role="status">模型列表加载中…</p>}{modelsState === "ERROR" && <p role="alert">模型列表读取失败。可重试读取，不会发起能力验证。</p>}{modelsState === "EMPTY" && <p role="status">当前连接没有可显示的模型；如需重新发现，请点击刷新模型。</p>}{modelsState === "ERROR" && <button type="button" className="ide-secondary-button" onClick={() => void readModels(connection.connection_id, detailSession.current)}>重试读取模型列表</button>}{modelsState === "READY" && models.length > 0 && <ul>{models.map((model) => <li key={model.model_profile_id}>{model.display_name === model.remote_model_id ? model.display_name : `${model.display_name} · ${model.remote_model_id}`}</li>)}</ul>}<button type="button" className="ide-secondary-button" onClick={() => void refreshModels()} disabled={loading}>刷新模型</button></section>
      {models.length > 0 && <section className="ide-provider-detail-section" aria-labelledby="provider-capability-heading"><h4 id="provider-capability-heading">能力与准入</h4><ul>{models.map((model) => <li key={model.model_profile_id}><strong>{model.display_name}</strong> · 声明能力：{capabilityText(model.capabilities)} · {model.capability_provenance && model.capability_provenance.length > 0 ? `能力证据：${model.capability_provenance.map((item) => `${capabilityLabels[item.capability] ?? item.capability}/${item.status}/${item.provenance}（${safeTimestamp(item.observed_at)}）`).join("；")}` : "能力证据：后端未提供"} · 允许使用：{model.policy_status ?? "后端未提供"}{model.policy_scope ? ` · 来源：${model.policy_scope}` : ""}{model.policy_block_reason ? ` · 原因：${model.policy_block_reason}` : ""}</li>)}</ul></section>}
      <section className="ide-provider-detail-section" aria-labelledby="provider-verification-heading"><h4 id="provider-verification-heading">连接验证</h4><label>测试模型<select value={testModelId} onChange={(event) => setTestModelId(event.target.value)} aria-label="测试模型" disabled={loading || models.length === 0}><option value="">选择测试模型</option>{models.map((model) => <option key={model.model_profile_id} value={model.remote_model_id}>{model.display_name === model.remote_model_id ? model.display_name : `${model.display_name} · ${model.remote_model_id}`}</option>)}</select></label><p className="ide-settings__hint">测试模型只来自当前 Connection 的 Current Endpoint Model Pool，不会改变运行时默认模型或治理 Allowed 状态。</p><button type="button" className="ide-secondary-button" onClick={() => void testConnection()} disabled={loading || selectedModel === null || testState === "TESTING"}>{testState === "TESTING" ? "测试中…" : "测试"}</button>{testState === "SUCCESS" && <p role="status">连接测试：PASS。模型能力与 Allowed 仍需单独验证。</p>}{testState === "FAILURE" && <p className="ide-form-error" role="alert">连接测试：FAIL。{testFailure}</p>}</section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-verification-history-heading"><h4 id="provider-verification-history-heading">连接测试历史</h4>{historyState === "LOADING" ? <p role="status">连接测试历史加载中…</p> : historyState === "ERROR" ? <><p role="alert">连接测试历史读取失败。</p><button type="button" className="ide-secondary-button" onClick={() => void readHistory(connection.connection_id, detailSession.current)}>重试读取测试历史</button></> : historyState === "EMPTY" ? <p role="status">暂无连接测试记录</p> : <ul>{verifications.map((item) => <li key={item.test_id}><span>{safeTimestamp(item.tested_at)}</span> · <strong>{safeStatus(item.status)}</strong> · {protocolDisplayName(item.protocol)} · 延迟：{safeLatency(item.latency_ms)}{item.error_code ? ` · ${item.error_code}` : " · —"}</li>)}</ul>}</section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-runtime-health-heading"><h4 id="provider-runtime-health-heading">运行健康</h4>{healthState === "LOADING" ? <p role="status">运行健康加载中…</p> : healthState === "ERROR" ? <><p role="alert">运行健康读取失败。</p><button type="button" className="ide-secondary-button" onClick={() => void readHealth(connection.connection_id, detailSession.current)}>重试读取运行健康</button></> : healthState === "EMPTY" ? <p role="status">暂无模型运行健康数据：未知</p> : <ul>{runtimeHealth.map((health) => { const model = models.find((item) => item.model_profile_id === health.identity.model_profile_id || item.remote_model_id === health.identity.model_id); return <li key={health.identity.model_profile_id ?? health.identity.model_id}><strong>{model?.display_name ?? health.identity.model_id}</strong> · 状态：{safeStatus(health?.endpoint_status)} · 证据：{safeStatus(health?.evidence_freshness)} · 配额：{safeStatus(health?.quota_status)} · 延迟：{safeLatency(health?.latency_ms)} · 错误率：{safePercent(health?.error_rate)} · 样本：{health && health.sample_count > 0 ? health.sample_count : "未知"} · 最近成功：{safeTimestamp(health?.last_success_at)} · 最近失败：{safeTimestamp(health?.last_failure_at)} · 观察：{safeTimestamp(health?.observed_at)}</li>; })}</ul>}</section>
      {usage.supported && <section className="ide-provider-detail-section" aria-labelledby="provider-account-heading"><h4 id="provider-account-heading">账户信息</h4>{usage.error || usage.value?.current_balance === null ? <p role="status">账户信息暂时不可用</p> : <p>余额：{usage.value?.current_balance}{usage.value?.currency ? ` ${usage.value.currency}` : ""}</p>}<button type="button" className="ide-secondary-button" onClick={() => void refreshUsage()} disabled={loading}>刷新账户信息</button></section>}
      <section className="ide-provider-detail-section ide-provider-detail-section--danger" aria-labelledby="provider-danger-heading"><h4 id="provider-danger-heading">危险操作</h4><p className="ide-settings__hint">删除连接会停用该 Connection 并保留历史记录，不会自动修改模型治理策略。</p><button type="button" className="ide-danger-button" onClick={() => void deleteConnection()} disabled={loading}>删除此连接</button></section>
    </>}
    {error && <p className="ide-form-error" role="alert">{error}</p>}
  </section>;
}
