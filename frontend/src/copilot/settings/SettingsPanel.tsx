import { X } from "lucide-react";
import { type MouseEvent as ReactMouseEvent, useEffect, useRef, useState } from "react";

import type { CapabilityStatusView } from "../api/capabilityClient";
import { VersionUpdatePanel } from "./VersionUpdatePanel";
import type { ModelGatewayProviderTemplate } from "../api/modelGatewayClient";
import { ModelProvidersPanel, protocolDisplayName } from "./ModelProvidersPanel";
import { ModelGovernancePanel } from "./ModelGovernancePanel";
import { ProviderConnectionSettingsPanel } from "./ProviderConnectionSettingsPanel";
import {
  fetchProjectLocations,
  ProjectCatalogRequestError,
  type ProjectLocation,
} from "../api/projectClient";
import {
  fetchProviderConfiguration,
  fetchAccountUsage,
  ProviderRequestError,
  refreshAccountUsage,
  refreshProviderModelPool,
  removeProviderConnection,
  removeProviderCredential,
  saveProviderConfiguration,
  saveProviderCredential,
  testProviderConnection,
  type ProviderProjection,
  type ProviderModelProjection,
  type AccountUsageProjection,
} from "../api/providerClient";
import { DESKTOP_SESSION_EXPIRED, useDesktopSessionState } from "../api/sessionState";
import {
  createProviderProjectionDiagnosticSession,
  recordFrontendProviderDiagnostic,
  type FrontendProviderDiagnosticSink,
  type ProviderProjectionDiagnosticSession,
} from "../api/webviewSessionDiagnostic";
import type { UiPreferences } from "../preferences";
import { RESEARCH_MODE_LABELS, RESEARCH_MODE_OPTIONS, type ResearchMode } from "../researchMode";
import { brandAsset } from "../layout/TopNavigation";

const DESKTOP_IDENTITY_REQUIRED = "DESKTOP_IDENTITY_REQUIRED";

const groups = [
  { key: "theme", label: "外观", values: [["light", "浅色"], ["dark", "深色"], ["system", "跟随系统"]] },
  { key: "density", label: "密度", values: [["compact", "紧凑"], ["comfortable", "舒适"]] },
  { key: "answerDetail", label: "回答详细度", values: [["concise", "简洁"], ["standard", "标准"], ["detailed", "详细"]] },
  { key: "evidenceDisplay", label: "证据显示", values: [["expanded", "展开"], ["compact", "紧凑"]] },
] as const;

type SettingsSection = "general" | "appearance" | "models" | "provider" | "advanced" | "version-update";

type ProviderSettingsView =
  | { type: "list" }
  | { type: "add"; step: "template" | "connection" | "result"; templateId?: string }
  | { type: "detail"; connectionId: string };

const settingsSections: Array<{ id: SettingsSection; label: string; description: string }> = [
  { id: "general", label: "常规", description: "回答与证据" },
  { id: "appearance", label: "外观", description: "主题与密度" },
  { id: "models", label: "模型", description: "允许池与阶段策略" },
  { id: "provider", label: "提供商管理", description: "模型连接" },
  { id: "advanced", label: "高级", description: "工程位置与凭据" },
  { id: "version-update", label: "版本与更新", description: "检查与安装更新" },
];

type AsyncProjection<T> =
  | { phase: "LOADING" }
  | { phase: "READY"; value: T }
  | { phase: "ERROR"; code: string };

type ConnectionFailureCategory = "Authentication Failed" | "Network Error" | "Timeout" | "Rate Limit" | "Configuration Error" | "Model Unavailable";

type ConnectionTestState =
  | { phase: "IDLE" }
  | { phase: "TESTING" }
  | { phase: "SUCCESS"; projection: ProviderProjection; latencyMs: number }
  | { phase: "FAILURE"; category: ConnectionFailureCategory; code: string; projection?: ProviderProjection };

type ModelStatus = "AVAILABLE" | "CONFIGURED" | "MODEL_RATE_LIMITED" | "UNAVAILABLE" | "UNKNOWN" | "PROVIDER_NOT_CONFIGURED";

type SettingsPanelProps = {
  preferences: UiPreferences;
  onChange(value: UiPreferences): void;
  onClose(): void;
  researchMode?: ResearchMode;
  onResearchModeChange?(value: ResearchMode): void;
  capabilities?: CapabilityStatusView[];
  diagnosticSink?: FrontendProviderDiagnosticSink;
  providerFetcher?: typeof fetch;
  locationFetcher?: typeof fetch;
  modelGatewayFetcher?: typeof fetch;
  canonicalProviderConnections?: boolean;
  projectId?: string;
  projectName?: string;
  desktopUpdateFetcher?: typeof fetch;
};

function requestErrorCode(error: unknown): string {
  if (error instanceof ProviderRequestError || error instanceof ProjectCatalogRequestError) return error.code;
  return "UNKNOWN";
}

function providerErrorMessage(code: string): string {
  if (code === DESKTOP_IDENTITY_REQUIRED) return "Desktop 身份未建立，Provider 配置状态暂不可用。";
  if (code === DESKTOP_SESSION_EXPIRED) return "Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。";
  return "Provider 配置暂不可用。";
}

function locationErrorMessage(code: string): string {
  if (code === DESKTOP_IDENTITY_REQUIRED) return "Desktop 身份未建立，工程位置暂不可用。";
  if (code === DESKTOP_SESSION_EXPIRED) return "Desktop 会话已过期，工程位置暂不可用。";
  return "无法加载工程位置。";
}

function statusLabel(value: string | null | undefined): string {
  return (value ?? "UNKNOWN").toUpperCase();
}

function displayStatus(value: string): string {
  return value === "AVAILABLE" ? "可用" : value === "CONFIGURED" ? "已配置" : value === "UNAVAILABLE" ? "不可用" : value === "DEGRADED" ? "部分可用" : value === "UNVERIFIED" || value === "PROVIDER_CONNECTION_UNVERIFIED" ? "已配置，未验证" : value === "PROVIDER_NOT_CONFIGURED" ? "未配置" : value === "PROVIDER_AUTH_FAILED" ? "认证失败" : value === "PROVIDER_NETWORK_UNAVAILABLE" ? "网络不可用" : value === "PROVIDER_MODEL_UNAVAILABLE" ? "模型不可用" : value === "PROVIDER_RATE_LIMITED" ? "请求受限" : value === "PROVIDER_CONFIG_INVALID" ? "配置无效" : value === "SESSION_EXPIRED" ? "会话已过期" : value === "LOADING" ? "加载中" : value === "ERROR" ? "状态不可用" : value === "UNKNOWN" ? "未知" : "不可用";
}

function modelStatusDisplay(value: string): string {
  return value === "MODEL_RATE_LIMITED" ? "可用" : displayStatus(value);
}

function connectionStatusDisplay(value: string): string {
  if (value === "PROVIDER_RATE_LIMITED") return "⚠ 请求受限";
  if (value === "PROVIDER_CONNECTION_UNVERIFIED") return "未验证";
  return displayStatus(value);
}

function providerProjectionStatus(value: ProviderProjection): string {
  if (value.credential_status === "UNAVAILABLE") return "UNKNOWN";
  if (value.credential_status === "NOT_CONFIGURED") return "PROVIDER_NOT_CONFIGURED";
  return value.connection_status;
}

function providerDisplayStatus(value: ProviderProjection): string {
  return providerProjectionStatus(value) === "PROVIDER_RATE_LIMITED"
    ? "CONFIGURED"
    : providerProjectionStatus(value);
}

function modelStatusFromProviderProjection(
  value: ProviderProjection,
  capability?: CapabilityStatusView,
): ModelStatus | string {
  if (value.credential_status === "UNAVAILABLE") return "UNKNOWN";
  if (value.credential_status === "NOT_CONFIGURED") return "PROVIDER_NOT_CONFIGURED";
  if (value.connection_status === "PROVIDER_RATE_LIMITED") return "MODEL_RATE_LIMITED";
  if (value.model_available !== undefined && value.model_available !== null) {
    return value.model_available
      && value.tested_configuration_fingerprint === value.configuration_fingerprint
      && Boolean(value.verified_model)
      ? "AVAILABLE"
      : "UNAVAILABLE";
  }
  if (value.connection_status === "AVAILABLE") {
    return value.tested_configuration_fingerprint === value.configuration_fingerprint
      ? "AVAILABLE"
      : "CONFIGURED";
  }
  if (value.connection_status === "PROVIDER_CONNECTION_UNVERIFIED") return "CONFIGURED";
  if (value.connection_status === "PROVIDER_NETWORK_UNAVAILABLE") return "UNKNOWN";
  if (
    value.connection_status === "PROVIDER_AUTH_FAILED"
    || value.connection_status === "PROVIDER_MODEL_UNAVAILABLE"
    || value.connection_status === "PROVIDER_CONFIG_INVALID"
  ) return "UNAVAILABLE";
  return statusLabel(capability?.status);
}

function modelStatusFromConnectionTest(
  state: ConnectionTestState,
  provider: ProviderProjection | null,
  capability?: CapabilityStatusView,
): ModelStatus | string {
  if (state.phase === "SUCCESS") return modelStatusFromProviderProjection(state.projection, capability);
  if (state.phase === "FAILURE") {
    if (state.projection !== undefined) return modelStatusFromProviderProjection(state.projection, capability);
    return state.category === "Network Error" || state.category === "Timeout"
      ? "UNKNOWN"
      : "UNAVAILABLE";
  }
  return provider === null ? statusLabel(capability?.status) : modelStatusFromProviderProjection(provider, capability);
}

function connectionFailureCategory(code: string): ConnectionFailureCategory {
  if (code.includes("AUTH")) return "Authentication Failed";
  if (code.includes("TIMEOUT")) return "Timeout";
  if (code.includes("RATE_LIMIT")) return "Rate Limit";
  if (code.includes("CONFIG")) return "Configuration Error";
  if (code.includes("MODEL_UNAVAILABLE")) return "Model Unavailable";
  return "Network Error";
}

function connectionFailureCategoryLabel(value: ConnectionFailureCategory): string {
  switch (value) {
    case "Authentication Failed": return "认证失败";
    case "Network Error": return "网络错误";
    case "Timeout": return "请求超时";
    case "Rate Limit": return "请求受限";
    case "Configuration Error": return "配置错误";
    case "Model Unavailable": return "模型不可用";
  }
}

function connectionDiagnosticStageLabel(value: string): string {
  switch (value) {
    case "CONFIGURATION": return "配置校验";
    case "CREDENTIAL": return "凭据解析";
    case "DNS": return "DNS";
    case "TCP": return "TCP";
    case "TLS": return "TLS";
    case "HTTP": return "HTTP 请求";
    case "PROVIDER_RESPONSE": return "Provider 响应";
    default: return value;
  }
}

function connectionFailureSuggestion(
  diagnostic: NonNullable<ProviderProjection["connection_diagnostic"]>,
): string {
  if (diagnostic.stage === "PROVIDER_RESPONSE" && diagnostic.http_status === 429) return "稍后重试。";
  if (diagnostic.stage === "PROVIDER_RESPONSE" && (diagnostic.http_status === 401 || diagnostic.http_status === 403)) return "检查 API Key 或权限后重试。";
  if (diagnostic.stage === "PROVIDER_RESPONSE" && diagnostic.http_status === 404) return "检查 Model 或 Endpoint 后重试。";
  if (diagnostic.stage === "TLS") return "检查证书、系统时间和 TLS 配置。";
  if (diagnostic.stage === "DNS") return "检查 DNS 和网络解析。";
  if (diagnostic.stage === "TCP") return "检查网络连通性和防火墙。";
  return "检查 Provider 配置后重试。";
}

const editableProviderIds = new Set(["openai-compatible", "anthropic-compatible", "custom", "local"]);

function isOfficialProvider(providerId: string): boolean {
  return !editableProviderIds.has(providerId) && !providerId.startsWith("custom-");
}

function providerTemplateCategory(template: ModelGatewayProviderTemplate): string {
  if (template.provider_kind === "local") return "本地";
  if (template.provider_id === "custom-relay") return "自定义接入";
  return "官方提供商";
}

function isAddableProviderTemplate(template: ModelGatewayProviderTemplate): boolean {
  return !["openai-compatible", "anthropic-compatible", "custom"].includes(template.provider_id);
}

function providerProtocolText(template: ModelGatewayProviderTemplate): string {
  return template.supported_protocols.map(protocolDisplayName).join(" · ");
}

function providerKindText(template: ModelGatewayProviderTemplate): string {
  if (template.provider_kind === "local") return "本地连接";
  if (template.provider_kind === "custom") return "自定义 API / 中转站";
  return "云端提供商";
}

function providerIsCredentialless(template: ModelGatewayProviderTemplate): boolean {
  return template.provider_kind === "local";
}

function canonicalModelProfileId(providerId: string, modelId: string): string {
  const slug = modelId.replace(/[^A-Za-z0-9._:-]+/g, "-").replace(/^-+|-+$/g, "") || "model";
  return `model-${providerId}-${slug}`.slice(0, 128);
}

function usableProviderModels(
  value: ProviderProjection | null,
  providerId: string,
): ProviderModelProjection[] {
  const billingRank = (item: ProviderModelProjection): number => {
    const billing = item.billing_class ?? "UNKNOWN";
    const availability = item.availability_status ?? item.availability;
    const availabilityRank = availability === "RATE_LIMITED" ? 1 : 0;
    const classRank = billing === "FREE" ? 0 : billing === "PAID" || billing === "METERED" || billing === "SUBSCRIPTION" ? 2 : billing === "MIXED" ? 3 : 4;
    return classRank + availabilityRank;
  };
  return (value?.model_pool ?? [])
    .filter((item) =>
      item.provider_id === providerId
      && ((item.availability_status ?? item.availability) === "AVAILABLE" || (item.availability_status ?? item.availability) === "RATE_LIMITED")
      && !item.deprecated
    )
    .slice()
    .sort((left, right) => billingRank(left) - billingRank(right) || left.model_id.localeCompare(right.model_id));
}

function billingLabel(item: ProviderModelProjection): string {
  if (item.billing_class === "FREE") return "免费";
  if (item.billing_class === "PAID" || item.billing_class === "METERED" || item.billing_class === "SUBSCRIPTION") return "付费";
  return "费用未知";
}

function modelForProjection(value: ProviderProjection, providerId: string): string {
  const options = usableProviderModels(value, providerId);
  if (options.length === 0) return value.model_pool === undefined ? value.model : "";
  if (options.some((item) => item.model_id === value.model)) return value.model;
  const available = options.filter((item) => (item.availability_status ?? item.availability) === "AVAILABLE");
  return (available.length === 1 ? available[0] : options.length === 1 ? options[0] : undefined)?.model_id ?? "";
}

function locationStatusLabel(value: string | null | undefined): string {
  switch (value) {
    case "AVAILABLE":
    case "ACTIVE":
    case "READY":
      return "已加载";
    case "LOADING":
    case "INITIALIZING":
    case "PENDING":
      return "加载中";
    case "STALE":
      return "需要重新加载";
    case "FAILED":
    case "REVOKED":
    case "UNAVAILABLE":
      return "不可用";
    default:
      return "状态不可用";
  }
}

function projectTypeLabel(value: string | null | undefined): string {
  switch (value) {
    case "STM32": return "STM32";
    case "ESP_IDF": return "ESP-IDF";
    case "ARDUINO": return "Arduino";
    case "GENERIC_FIRMWARE": return "通用固件";
    case "EXISTING_FIRMWARE": return "已有固件";
    default: return "嵌入式工程";
  }
}

function safeLocationPath(location: ProjectLocation): string {
  return location.path?.trim() || location.path_leaf?.trim() || "已注册工程位置";
}

function validationStatusLabel(value: string | null | undefined): string {
  return value?.trim() || "PROVIDER_CONNECTION_UNVERIFIED";
}

export function isFrontendCredentialInputInvalid(value: string): boolean {
  const normalized = value.trim();
  return Boolean(normalized) && (normalized.length < 8 || /\s/u.test(normalized) || /[\p{Cc}\p{Cf}]/u.test(normalized));
}

export function SettingsPanel({
  preferences,
  onChange,
  onClose,
  researchMode = "AUTO",
  onResearchModeChange,
  capabilities = [],
  diagnosticSink = recordFrontendProviderDiagnostic,
  providerFetcher = fetch,
  locationFetcher = fetch,
  modelGatewayFetcher,
  canonicalProviderConnections,
  projectId,
  projectName,
  desktopUpdateFetcher,
}: SettingsPanelProps) {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const [locationsState, setLocationsState] = useState<AsyncProjection<ProjectLocation[]>>({ phase: "LOADING" });
  const [locationsRetry, setLocationsRetry] = useState(0);
  const [providerState, setProviderState] = useState<AsyncProjection<ProviderProjection>>({ phase: "LOADING" });
  const [selectedProvider, setSelectedProvider] = useState("deepseek");
  const [model, setModel] = useState("deepseek-chat");
  const [baseUrl, setBaseUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [activeSection, setActiveSection] = useState<SettingsSection>("provider");
  const [providerSettingsView, setProviderSettingsView] = useState<ProviderSettingsView>({ type: "list" });
  const [providerTemplates, setProviderTemplates] = useState<ModelGatewayProviderTemplate[]>([]);
  const [providerUsage, setProviderUsage] = useState<AccountUsageProjection[]>([]);
  const [providerListRefreshSignal, setProviderListRefreshSignal] = useState(0);
  const [addEndpoint, setAddEndpoint] = useState("");
  const [addConnectionName, setAddConnectionName] = useState("");
  const [addProtocol, setAddProtocol] = useState("");
  const [addSavedProviderId, setAddSavedProviderId] = useState<string | null>(null);
  const [addApiKey, setAddApiKey] = useState("");
  const [addSaving, setAddSaving] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [addDiscoveryFailure, setAddDiscoveryFailure] = useState(false);
  const [addDiscoveryError, setAddDiscoveryError] = useState<string | null>(null);
  const [addDiscoveryProjection, setAddDiscoveryProjection] = useState<ProviderProjection | null>(null);
  const [detailProviderName, setDetailProviderName] = useState<string | null>(null);
  const [detailBalanceBusy, setDetailBalanceBusy] = useState(false);
  const [providerBusy, setProviderBusy] = useState(false);
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerMessage, setProviderMessage] = useState<string | null>(null);
  const [connectionTestState, setConnectionTestState] = useState<ConnectionTestState>({ phase: "IDLE" });
  const useCanonicalProviderConnections = canonicalProviderConnections ?? (modelGatewayFetcher !== undefined && modelGatewayFetcher === providerFetcher);
  const providerDiagnosticSession = useRef<ProviderProjectionDiagnosticSession | undefined>(undefined);
  const providerSelectionRequest = useRef(0);
  const renderGeneration = useRef(0);
  const lastRenderSemanticState = useRef<string | null>(null);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    if (sessionExpired) {
      setLocationsState({ phase: "ERROR", code: DESKTOP_SESSION_EXPIRED });
      return () => { active = false; };
    }
    setLocationsState({ phase: "LOADING" });
    void fetchProjectLocations(locationFetcher, diagnosticSink)
      .then((value) => {
        if (active) setLocationsState({ phase: "READY", value });
      })
      .catch((error: unknown) => {
        if (active) setLocationsState({ phase: "ERROR", code: requestErrorCode(error) });
      });
    return () => { active = false; };
  }, [diagnosticSink, locationFetcher, sessionExpired, locationsRetry]);

  useEffect(() => {
    let active = true;
    if (sessionExpired) {
      providerDiagnosticSession.current = undefined;
      setProviderState({ phase: "ERROR", code: DESKTOP_SESSION_EXPIRED });
      return () => { active = false; };
    }
    const diagnostic = createProviderProjectionDiagnosticSession(diagnosticSink);
    providerDiagnosticSession.current = diagnostic;
    renderGeneration.current = 0;
    lastRenderSemanticState.current = null;
    setProviderState({ phase: "LOADING" });
    setProviderMessage(null);
    setConnectionTestState({ phase: "IDLE" });
    void fetchProviderConfiguration(providerFetcher, diagnostic)
      .then((value) => {
        if (!active) return;
        setProviderState({ phase: "READY", value });
        diagnostic.stateCommit(value);
        setSelectedProvider(value.provider);
        setModel(modelForProjection(value, value.provider));
        setBaseUrl(value.base_url);
        setProviderMessage(null);
        diagnostic.fetchSuccess();
      })
      .catch((error: unknown) => {
        if (!active) return;
        const code = requestErrorCode(error);
        setProviderState({ phase: "ERROR", code });
        setProviderMessage(providerErrorMessage(code));
      });
    return () => { active = false; };
  }, [diagnosticSink, providerFetcher, sessionExpired, useCanonicalProviderConnections]);

  const provider = providerState.phase === "READY" ? providerState.value : null;
  const addTemplate = providerSettingsView.type === "add" && providerSettingsView.templateId
    ? providerTemplates.find((template) => template.provider_id === providerSettingsView.templateId) ?? null
    : null;

  async function selectProvider(value: string) {
    const request = providerSelectionRequest.current + 1;
    providerSelectionRequest.current = request;
    setSelectedProvider(value);
    setSecret("");
    setDiscoveryFailed(false);
    setModel("");
    setBaseUrl("");
    setConnectionTestState({ phase: "IDLE" });
    setProviderMessage(null);
    try {
      const next = await fetchProviderConfiguration(
        providerFetcher,
        createProviderProjectionDiagnosticSession(diagnosticSink),
        value,
      );
      if (providerSelectionRequest.current !== request) return;
      setProviderState({ phase: "READY", value: next });
      setSelectedProvider(value);
      setModel(modelForProjection(next, value));
      setBaseUrl(next.base_url || "");
    } catch (error: unknown) {
      if (providerSelectionRequest.current !== request) return;
      setProviderState({ phase: "ERROR", code: requestErrorCode(error) });
      setProviderMessage(providerErrorMessage(requestErrorCode(error)));
    }
  }

  function setSettingsSection(section: SettingsSection): void {
    setActiveSection(section);
    if (section !== "provider") setProviderSettingsView({ type: "list" });
  }

  function openAddProvider(): void {
    setProviderSettingsView({ type: "add", step: "template" });
    setAddEndpoint("");
    setAddConnectionName("");
    setAddProtocol("");
    setAddSavedProviderId(null);
    setAddApiKey("");
    setAddError(null);
    setAddDiscoveryFailure(false);
    setAddDiscoveryError(null);
    setAddDiscoveryProjection(null);
  }

  function returnToProviderList(): void {
    setProviderSettingsView({ type: "list" });
    setAddEndpoint("");
    setAddConnectionName("");
    setAddProtocol("");
    setAddSavedProviderId(null);
    setAddApiKey("");
    setAddError(null);
    setAddDiscoveryFailure(false);
    setAddDiscoveryError(null);
    setAddDiscoveryProjection(null);
  }

  function chooseProviderTemplate(template: ModelGatewayProviderTemplate): void {
    setProviderSettingsView({ type: "add", step: "connection", templateId: template.provider_id });
    setAddEndpoint(template.default_endpoint_template ?? "");
    setAddConnectionName(template.provider_id === "custom-relay" ? "" : template.display_name);
    setAddProtocol(template.supported_protocols[0] ?? "");
    setAddSavedProviderId(null);
    setAddApiKey("");
    setAddError(null);
    setAddDiscoveryFailure(false);
    setAddDiscoveryError(null);
    setAddDiscoveryProjection(null);
  }

  async function saveAddedProvider(): Promise<void> {
    if (addTemplate === null) return;
    const endpoint = addEndpoint.trim();
    const apiKey = addApiKey.trim();
    const customRelay = addTemplate.provider_id === "custom-relay";
    const connectionName = addConnectionName.trim();
    if (customRelay && !connectionName) {
      setAddError("请输入连接名称。");
      return;
    }
    if (!endpoint) {
      setAddError("请输入接口地址。");
      return;
    }
    if (!providerIsCredentialless(addTemplate) && !apiKey) {
      setAddError("请输入 API Key。");
      return;
    }
    if (isFrontendCredentialInputInvalid(apiKey)) {
      setAddError("API Key 格式明显不完整，请检查后再保存。");
      return;
    }
    setAddSaving(true);
    setAddError(null);
    setAddDiscoveryFailure(false);
    try {
      let saved = await saveProviderConfiguration(undefined, providerFetcher, {
        provider: addTemplate.provider_id,
        connectionName: customRelay ? connectionName : undefined,
        baseUrl: addTemplate.provider_kind === "custom" || addTemplate.provider_kind === "local" ? endpoint : undefined,
        protocolFamily: addProtocol || addTemplate.supported_protocols[0],
      });
      const savedProviderId = saved.provider;
      setAddSavedProviderId(savedProviderId);
      if (apiKey) {
        saved = await saveProviderCredential(apiKey, providerFetcher, savedProviderId);
      }
      setAddApiKey("");
      try {
        saved = await refreshProviderModelPool(providerFetcher, savedProviderId);
        setAddDiscoveryFailure(saved.discovery_state === "FAILED" || saved.discovery_state === "STALE_WITH_CACHE");
        setAddDiscoveryError(saved.discovery_state === "FAILED" ? "MODEL_DISCOVERY_UNAVAILABLE" : null);
      } catch (reason: unknown) {
        setAddDiscoveryFailure(true);
        setAddDiscoveryError(requestErrorCode(reason));
      }
      setAddDiscoveryProjection(saved);
      setProviderSettingsView({ type: "add", step: "result", templateId: addTemplate.provider_id });
    } catch (reason: unknown) {
      setAddError(providerErrorMessage(requestErrorCode(reason)));
    } finally {
      setAddSaving(false);
    }
  }

  function finishAddedProvider(): void {
    returnToProviderList();
    setProviderListRefreshSignal((value) => value + 1);
  }

  function openProviderDetail(providerId: string, displayName: string): void {
    setDetailProviderName(displayName);
    setProviderSettingsView({ type: "detail", connectionId: providerId });
    if (!(providerState.phase === "LOADING" && providerId === selectedProvider)
      && (providerState.phase !== "READY" || providerState.value.provider !== providerId)) void selectProvider(providerId);
  }

  async function refreshDetailModels(): Promise<void> {
    if (providerSettingsView.type !== "detail") return;
    setProviderBusy(true);
    setProviderMessage(null);
    setDiscoveryFailed(false);
    try {
      const next = await refreshProviderModelPool(providerFetcher, providerSettingsView.connectionId);
      setProviderState({ phase: "READY", value: next });
      setModel(modelForProjection(next, providerSettingsView.connectionId));
      setBaseUrl(next.base_url);
    } catch {
      setDiscoveryFailed(true);
      setProviderMessage("模型发现失败，已保留当前连接和已有模型目录。");
    } finally {
      setProviderBusy(false);
    }
  }

  async function refreshDetailBalance(): Promise<void> {
    const endpointId = provider?.endpoint_id;
    if (endpointId === null || endpointId === undefined || endpointId === "") return;
    setDetailBalanceBusy(true);
    try {
      const next = await refreshAccountUsage(endpointId, providerFetcher);
      setProviderUsage((current) => [...current.filter((item) => item.endpoint_id !== endpointId), next]);
    } catch {
      setProviderMessage("余额暂不可获取。");
    } finally {
      setDetailBalanceBusy(false);
    }
  }

  const modelOptions = usableProviderModels(provider, selectedProvider);
  const selectedModelOption = modelOptions.find((item) => item.model_id === model.trim());
  const selectableModelIds = modelOptions.map((item) => item.model_id);
  const testModelProfileId = selectedModelOption?.model_profile_id
    ?? (provider?.provider === selectedProvider ? provider.model_profile_id : undefined)
    ?? (model.trim() ? canonicalModelProfileId(selectedProvider, model.trim()) : undefined);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryFailed, setDiscoveryFailed] = useState(false);
  const modelPlaceholder = savingProvider ? "正在保存凭据…"
    : discovering ? "正在获取可用模型…"
    : provider?.credential_status !== "CONFIGURED" ? "请先保存 API Key"
    : discoveryFailed || provider.discovery_state === "FAILED" || provider.discovery_state === "STALE_WITH_CACHE"
      ? modelOptions.length ? "使用上次模型目录" : "暂时无法获取可用模型"
    : modelOptions.length ? "选择测试模型" : "暂无可用测试模型";

  useEffect(() => {
    const diagnostic = providerDiagnosticSession.current;
    if (diagnostic === undefined) return;
    const key = `${providerState.phase}:${provider?.credential_status ?? "UNKNOWN"}`;
    if (lastRenderSemanticState.current === key) return;
    lastRenderSemanticState.current = key;
    renderGeneration.current += 1;
    diagnostic.renderState(provider, renderGeneration.current);
  }, [provider, providerState.phase]);

  const embedding = capabilities.find((item) => item.capability === "KNOWLEDGE_EMBEDDING");
  const runtimeCapability = capabilities.find((item) => item.capability === "CHAT_ANALYSIS");
  const providerCapability = capabilities.find((item) => item.capability === "PROVIDER_CONFIGURATION");
  const modelCapability = capabilities.find((item) => item.capability === "CHAT_MODEL");

  let runtimeStatus: string;
  let providerStatus: string;
  let modelStatus: string;
  if (sessionExpired) {
    runtimeStatus = "SESSION_EXPIRED";
    providerStatus = "SESSION_EXPIRED";
    modelStatus = "SESSION_EXPIRED";
  } else if (providerState.phase === "LOADING") {
    runtimeStatus = "LOADING";
    providerStatus = "LOADING";
    modelStatus = "LOADING";
  } else if (providerState.phase === "ERROR") {
    runtimeStatus = "ERROR";
    providerStatus = "ERROR";
    modelStatus = "ERROR";
  } else {
    runtimeStatus = statusLabel(runtimeCapability?.runtime_status ?? runtimeCapability?.status);
    providerStatus = provider ? providerDisplayStatus(provider) : statusLabel(providerCapability?.status);
    modelStatus = modelStatusFromConnectionTest(connectionTestState, provider, modelCapability);
  }

  const ollamaStatus = embedding?.status === "AVAILABLE"
    ? "已就绪"
    : embedding?.model_status === "unavailable"
      ? "模型缺失"
      : embedding?.runtime_status === "unavailable"
        ? "未安装或未运行"
        : "未配置";

  async function saveProvider() {
    if (sessionExpired) {
      setProviderMessage("Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。");
      return;
    }
    const normalizedSecret = secret.trim();
    if (providerState.phase !== "READY") return;
    const providerChanged = selectedProvider !== providerState.value.provider;
    const baseUrlChanged = baseUrl.trim() !== providerState.value.base_url;
    if (!providerChanged && !baseUrlChanged && !normalizedSecret) return;
    if (isFrontendCredentialInputInvalid(normalizedSecret)) {
      setProviderMessage("API Key 格式明显不完整，请检查后再保存。");
      return;
    }
    setProviderBusy(true);
    setSavingProvider(true);
    setProviderMessage(null);
    try {
      let next = providerState.value;
      if (providerChanged || baseUrlChanged) {
        next = await saveProviderConfiguration(undefined, providerFetcher, {
          provider: selectedProvider,
          baseUrl: isOfficialProvider(selectedProvider) ? undefined : baseUrl.trim(),
          endpointId: providerChanged ? undefined : providerState.value.endpoint_id ?? undefined,
          credentialRef: providerChanged ? undefined : providerState.value.credential_ref ?? undefined,
          protocolFamily: providerChanged ? undefined : providerState.value.protocol_family ?? undefined,
        });
      }
      if (normalizedSecret) {
        next = await saveProviderCredential(normalizedSecret, providerFetcher, selectedProvider);
        setSecret("");
      }
      setProviderState({ phase: "READY", value: next });
      setSelectedProvider(next.provider);
      setModel(modelForProjection(next, next.provider));
      setBaseUrl(next.base_url);
      setConnectionTestState({ phase: "IDLE" });
      setProviderMessage("✓ 已保存");
      if (normalizedSecret && isOfficialProvider(selectedProvider)) {
        setSavingProvider(false);
        setDiscovering(true);
        setDiscoveryFailed(false);
        try {
          const discovered = await refreshProviderModelPool(providerFetcher, selectedProvider);
          setProviderState({ phase: "READY", value: discovered });
          setModel(modelForProjection(discovered, selectedProvider));
        } catch {
          setDiscoveryFailed(true);
        } finally {
          setDiscovering(false);
        }
      }
    } catch {
      setProviderMessage("Provider 配置保存失败。");
    } finally {
      setSavingProvider(false);
      setProviderBusy(false);
    }
  }

  async function removeCredential() {
    if (sessionExpired) {
      setProviderMessage("Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。");
      return;
    }
    setProviderBusy(true);
    setProviderMessage(null);
    try {
      const next = await removeProviderCredential(providerFetcher, selectedProvider);
      setProviderState({ phase: "READY", value: next });
      setConnectionTestState({ phase: "IDLE" });
      setSecret("");
      setProviderMessage("Credential 已删除。");
    } catch {
      setProviderMessage("Credential 删除失败。");
    } finally {
      setProviderBusy(false);
    }
  }

  async function removeConnection(): Promise<void> {
    if (sessionExpired || providerSettingsView.type !== "detail") return;
    setProviderBusy(true);
    setProviderMessage(null);
    try {
      const next = await removeProviderConnection(providerFetcher, providerSettingsView.connectionId);
      setProviderState({ phase: "READY", value: next });
      setSelectedProvider(next.provider);
      setModel(modelForProjection(next, next.provider));
      setBaseUrl(next.base_url);
      setConnectionTestState({ phase: "IDLE" });
      returnToProviderList();
      setProviderListRefreshSignal((value) => value + 1);
    } catch (reason: unknown) {
      setProviderMessage(`连接删除失败 · ${requestErrorCode(reason)}`);
    } finally {
      setProviderBusy(false);
    }
  }

  async function testConnection() {
    if (sessionExpired) {
      setProviderMessage("Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。");
      return;
    }
    if (providerState.phase !== "READY" || providerState.value.credential_status !== "CONFIGURED") return;
    const modelId = model.trim();
    const endpointId = provider?.endpoint_id;
    const credentialRef = provider?.credential_ref;
    if (!modelId || !endpointId || !credentialRef || !testModelProfileId) return;

    setProviderBusy(true);
    setProviderMessage(null);
    setConnectionTestState({ phase: "TESTING" });
    const startedAt = Date.now();
    try {
      const next = await testProviderConnection(providerFetcher, {
        providerId: selectedProvider,
        endpointId,
        credentialRef,
        modelProfileId: testModelProfileId,
        modelId,
        baseUrl: isOfficialProvider(selectedProvider) ? undefined : baseUrl.trim(),
        protocolFamily: provider?.protocol_family ?? undefined,
        configurationFingerprint: undefined,
      });
      const latencyMs = Math.max(0, Date.now() - startedAt);
      if (next.connection_status === "AVAILABLE") {
        setConnectionTestState({ phase: "SUCCESS", projection: next, latencyMs });
      } else {
        setConnectionTestState({ phase: "FAILURE", category: connectionFailureCategory(next.connection_status), code: next.connection_status, projection: next });
      }
    } catch (error: unknown) {
      const code = requestErrorCode(error);
      setConnectionTestState({ phase: "FAILURE", category: connectionFailureCategory(code), code });
    } finally {
      setProviderBusy(false);
    }
  }

  const providerErrorCode = providerState.phase === "ERROR" ? providerState.code : null;
  const credentialLabel = sessionExpired
    ? "会话已过期"
    : providerState.phase === "LOADING"
      ? "加载中"
      : providerState.phase === "ERROR"
        ? providerErrorCode === DESKTOP_IDENTITY_REQUIRED ? "身份状态不可用" : "配置状态暂不可用"
        : providerState.value.credential_status === "CONFIGURED"
          ? "已保存"
          : providerState.value.credential_status === "NOT_CONFIGURED"
            ? "未配置"
            : "不可用";
  const credentialExists = provider?.credential_status === "CONFIGURED";
  const hasNewCredential = Boolean(secret.trim());
  const providerDirty = providerState.phase === "READY" && selectedProvider !== providerState.value.provider;
  const baseUrlDirty = providerState.phase === "READY" && baseUrl.trim() !== providerState.value.base_url;
  const configurationDirty = providerDirty || baseUrlDirty || hasNewCredential;
  const connectionRaw = sessionExpired
    ? "SESSION_EXPIRED"
    : providerState.phase === "LOADING"
      ? "LOADING"
      : providerState.phase === "ERROR"
        ? providerErrorCode === DESKTOP_IDENTITY_REQUIRED ? DESKTOP_IDENTITY_REQUIRED : "ERROR"
        : providerProjectionStatus(providerState.value);
  const displayedProviderMessage = sessionExpired
    ? "Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。"
    : providerMessage;
  const connectionLabel = connectionTestState.phase === "TESTING" ? "测试中" : connectionRaw === "AVAILABLE" ? "已验证" : connectionStatusDisplay(connectionRaw);
  const saveEnabled = providerState.phase === "READY"
    && !providerBusy
    && !sessionExpired
    && configurationDirty;
  const saveDirty = configurationDirty;
  const credentialExistsLabel = providerState.phase === "LOADING"
    ? "加载中"
    : providerState.phase === "ERROR"
      ? "状态不可用"
      : credentialExists ? "已配置" : "未配置";
  const credentialUpdatedLabel = providerState.phase === "LOADING"
    ? "加载中"
    : providerState.phase === "ERROR"
      ? "状态不可用"
      : hasNewCredential ? "待保存" : credentialExists ? "已保存" : "—";
  const providerValidationStatus = provider?.validation_status ?? provider?.connection_status;
  const connectionValidatedLabel = providerValidationStatus === "AVAILABLE" ? "已验证" : "未验证";
  const connectionFailedLabel = connectionTestState.phase === "FAILURE"
    ? connectionTestState.code
    : providerValidationStatus?.includes("FAILED") ? providerValidationStatus : "—";
  const detailUsage = providerUsage.find((item) => item.provider_id === selectedProvider);
  const discoveredModels = (addDiscoveryProjection?.model_pool ?? []).map(
    (item) => item.display_name || item.model_id,
  );

  const renderProviderAddView = () => {
    if (providerSettingsView.type !== "add") return null;
    if (providerSettingsView.step === "template") {
      const categories = ["官方提供商", "自定义接入", "本地"];
      return <div className="ide-provider-internal-view" data-provider-internal-view="add" data-provider-add-step="template">
        <button type="button" className="ide-settings__back-button" onClick={returnToProviderList}>← 返回提供商管理</button>
        <h3>添加模型提供商</h3>
        <p className="ide-settings__intro">选择提供商模板</p>
        {categories.map((category) => {
           const categoryTemplates = providerTemplates.filter((template) => template.enabled && isAddableProviderTemplate(template) && providerTemplateCategory(template) === category);
          if (categoryTemplates.length === 0) return null;
          return <section key={category} className="ide-provider-template-group" aria-labelledby={`provider-template-${category}`}>
            <h4 id={`provider-template-${category}`}>{category}</h4>
            <div className="ide-provider-template-grid" aria-label={`${category}列表`}>
              {categoryTemplates.map((template) => <button key={template.provider_id} type="button" className="ide-provider-template" data-provider-template={template.provider_id} onClick={() => chooseProviderTemplate(template)}>
                <strong>{template.display_name}</strong>
                <span>{providerKindText(template)}</span>
                <small>{providerProtocolText(template)}</small>
              </button>)}
            </div>
          </section>;
        })}
        {providerTemplates.filter((template) => template.enabled && isAddableProviderTemplate(template)).length === 0 && <p role="status">提供商模板暂不可用。</p>}
      </div>;
    }
    if (providerSettingsView.step === "connection") {
      const credentialless = addTemplate !== null && providerIsCredentialless(addTemplate);
      return <div className="ide-provider-internal-view" data-provider-internal-view="add" data-provider-add-step="connection">
        <button type="button" className="ide-settings__back-button" onClick={() => setProviderSettingsView({ type: "add", step: "template" })} disabled={addSaving}>← 选择提供商</button>
        <h3>添加 {addTemplate?.display_name ?? "模型提供商"}</h3>
        {addTemplate?.provider_id === "custom-relay" && <label>连接名称<input aria-label="连接名称" value={addConnectionName} onChange={(event) => setAddConnectionName(event.target.value)} disabled={addSaving} placeholder="例如：团队中转站" autoComplete="organization" /></label>}
        <label>接口地址<input aria-label="接口地址" value={addEndpoint} readOnly={addTemplate !== null && addTemplate.provider_kind !== "custom" && addTemplate.provider_kind !== "local"} onChange={(event) => setAddEndpoint(event.target.value)} disabled={addSaving} autoComplete="url" /></label>
        {addTemplate?.provider_id === "custom-relay" && <label><span>接口格式</span>{addTemplate.supported_protocols.length === 1 ? <span role="status" data-provider-protocol-readonly>{protocolDisplayName(addTemplate.supported_protocols[0])}</span> : <select aria-label="接口格式" value={addProtocol} onChange={(event) => setAddProtocol(event.target.value)} disabled={addSaving}>{addTemplate.supported_protocols.map((protocol) => <option key={protocol} value={protocol}>{protocolDisplayName(protocol)}</option>)}</select>}</label>}
        <label>API Key<input aria-label="API Key" type="password" value={addApiKey} onChange={(event) => setAddApiKey(event.target.value)} disabled={addSaving || credentialless} placeholder={credentialless ? "本地连接无需 API Key" : "输入 API Key"} autoComplete="new-password" /></label>
        <p className="ide-settings__hint">保存后将创建连接并发现可用模型，不会自动启用任何模型。</p>
        {addError && <p className="ide-form-error" role="alert">{addError}</p>}
        <div className="ide-settings__actions ide-settings__provider-actions">
          <button type="button" className="ide-secondary-button" onClick={returnToProviderList} disabled={addSaving}>取消</button>
          <button type="button" className="ide-primary-button" aria-label="保存连接" onClick={() => void saveAddedProvider()} disabled={addSaving}>{addSaving ? "保存中…" : "保存连接"}</button>
        </div>
      </div>;
    }
    return <div className="ide-provider-internal-view" data-provider-internal-view="add" data-provider-add-step="result">
      <button type="button" className="ide-settings__back-button" onClick={returnToProviderList}>← 提供商管理</button>
      <h3>{addTemplate?.display_name ?? "模型提供商"}</h3>
      <div className="ide-provider-result" data-provider-save-result={addDiscoveryFailure ? "discovery-failed" : "success"}>
        <h4>✓ 连接已保存</h4>
        {addDiscoveryFailure
          ? <><p className="ide-form-error" role="alert">模型发现失败：{addDiscoveryError ?? "MODEL_DISCOVERY_UNAVAILABLE"}</p><p>你可以稍后在提供商详情中重新刷新模型。</p></>
          : <><p role="status">模型发现已完成，共 {discoveredModels.length} 个模型。</p>{discoveredModels.length > 0 && <ul>{discoveredModels.map((name) => <li key={name}>{name}</li>)}</ul>}</>}
        {addSavedProviderId && <p className="ide-settings__hint">连接 ID：{addSavedProviderId}</p>}
      </div>
      <button type="button" className="ide-primary-button" aria-label="完成" onClick={finishAddedProvider}>完成</button>
    </div>;
  };

  const renderProviderDetailView = () => {
    if (providerSettingsView.type !== "detail") return null;
    const currentProviderId = providerSettingsView.connectionId;
    return <div className="ide-provider-internal-view" data-provider-internal-view="detail" data-provider-connection-id={currentProviderId}>
      <button type="button" className="ide-settings__back-button" onClick={returnToProviderList}>← 提供商管理</button>
      <div className="ide-provider-detail-heading"><div><span className="eyebrow">连接详情</span><h3>{detailProviderName ?? currentProviderId}</h3></div><span className="ide-settings__provider-ready">{providerStatus === "AVAILABLE" ? "已配置 · 已验证" : "已配置 · 未验证"}</span></div>
      <section className="ide-settings__connection-status" aria-labelledby="connection-status-heading">
        <h4 id="connection-status-heading">连接状态</h4>
        <div className="ide-settings__status-grid" aria-label="连接与模型状态">
          <p><strong>运行时</strong> {runtimeStatus === "AVAILABLE" ? <span className="ide-settings__status-value--verified">可用</span> : displayStatus(runtimeStatus)}</p>
          <p><strong>提供商</strong> {providerStatus === "AVAILABLE" || providerStatus === "CONFIGURED" ? <span className="ide-settings__status-value--verified">已配置</span> : displayStatus(providerStatus)}</p>
          <p><strong>测试模型</strong> {providerState.phase !== "READY" ? displayStatus(modelStatus) : !model.trim() ? "未选择" : modelStatus === "AVAILABLE" || modelStatus === "MODEL_RATE_LIMITED" ? <span className="ide-settings__status-value--verified">可用</span> : modelStatusDisplay(modelStatus)}</p>
        </div>
        <p><strong>凭据</strong> {credentialLabel === "已保存" ? <span className="ide-settings__status-value--verified">已保存</span> : credentialLabel}</p>
        <p><strong>连接</strong> {connectionRaw === "AVAILABLE" ? <span className="ide-settings__status-value--verified">{connectionLabel}</span> : connectionLabel}</p>
      </section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-connection-config-heading">
        <h4 id="provider-connection-config-heading">连接配置</h4>
        <label>接口地址<input aria-label="Base URL" value={baseUrl} readOnly={isOfficialProvider(currentProviderId)} disabled={sessionExpired} onChange={(event) => setBaseUrl(event.target.value)} /></label>
        <p><strong>API 凭据</strong> {credentialLabel}</p>
        <label>替换 API Key<input type="password" value={secret} disabled={sessionExpired} onChange={(event) => setSecret(event.target.value)} placeholder={providerState.phase === "LOADING" ? "正在加载凭据状态" : providerState.phase === "ERROR" ? "凭据状态暂不可用" : credentialExists ? "•••••••• 已配置；输入新值以替换" : "输入 API Key"} autoComplete="new-password" aria-label="API Key" /></label>
        <div className="ide-settings__actions ide-settings__provider-actions">
          <button className={savingProvider || saveDirty ? "ide-primary-button" : "ide-secondary-button"} type="button" data-provider-actions="near-credential" disabled={!saveEnabled} aria-busy={savingProvider} aria-label={savingProvider ? "保存中…" : "保存配置"} onClick={() => void saveProvider()}>{savingProvider ? "保存中…" : "更新配置"}</button>
        </div>
      </section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-discovery-heading">
        <h4 id="provider-discovery-heading">模型发现</h4>
        <p>当前 Endpoint 已发现：{modelOptions.length} 个可用模型</p>
        {modelOptions.length > 0 && <ul>{modelOptions.map((item) => <li key={`${item.endpoint_id}:${item.model_id}`}>{item.display_name || item.model_id}</li>)}</ul>}
        {(discoveryFailed || provider?.discovery_state === "STALE_WITH_CACHE") && <p role="status">目录刷新失败{modelOptions.length > 0 ? " / 使用缓存" : ""}</p>}
        <button type="button" className="ide-secondary-button" onClick={() => void refreshDetailModels()} disabled={providerBusy || sessionExpired}>刷新模型</button>
      </section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-verification-heading">
        <h4 id="provider-verification-heading">连接验证</h4>
        <label>测试模型<select value={model} disabled={sessionExpired || providerBusy || provider?.credential_status !== "CONFIGURED" || selectableModelIds.length === 0} onChange={(event) => setModel(event.target.value)} aria-label="测试模型"><option value="">{modelPlaceholder}</option>{modelOptions.map((item) => <option key={`${item.provider_id}:${item.endpoint_id}:${item.model_id}`} value={item.model_id}>{item.display_name} · {billingLabel(item)}{(item.availability_status ?? item.availability) === "RATE_LIMITED" ? " · 当前繁忙" : ""}</option>)}</select></label>
        <p className="ide-settings__hint">仅可验证当前 Endpoint 已发现的模型；不会根据模型名称猜测提供商，也不会自动成为 Embrix 的运行模型。</p>
        <button className="ide-secondary-button" type="button" disabled={providerBusy || sessionExpired || provider?.credential_status !== "CONFIGURED" || !model.trim() || !selectableModelIds.includes(model.trim())} aria-busy={connectionTestState.phase === "TESTING"} onClick={() => void testConnection()}>{connectionTestState.phase === "TESTING" ? "测试中…" : "测试连接"}</button>
        {connectionTestState.phase === "TESTING" && <p role="status" aria-live="polite">Connection 测试中…</p>}
        {connectionTestState.phase === "SUCCESS" && <div role="status" aria-label="Connection 验证结果" data-connection-result="verified"><p><strong>连接 Connection</strong> 已验证</p><p><strong>提供商 Provider</strong> {detailProviderName ?? connectionTestState.projection.provider}</p><p><strong>模型 Model</strong> {connectionTestState.projection.model}</p><p><strong>延迟 Latency</strong> {connectionTestState.latencyMs} ms</p><p><strong>时间 Verified At</strong> {connectionTestState.projection.tested_at ?? "—"}</p></div>}
        {connectionTestState.phase === "FAILURE" && <div role="alert" aria-label="Connection 验证失败" data-connection-result="failed"><p><strong>连接 Connection</strong> 验证失败</p><p><strong>原因 Reason</strong> {connectionFailureCategoryLabel(connectionTestState.category)} · {connectionTestState.code}</p>{connectionTestState.projection?.connection_diagnostic && <><p><strong>提供商 Provider</strong> {detailProviderName ?? connectionTestState.projection.provider}</p><p><strong>阶段 Stage</strong> {connectionDiagnosticStageLabel(connectionTestState.projection.connection_diagnostic.stage)}</p><p><strong>HTTP</strong> {connectionTestState.projection.connection_diagnostic.http_status ?? "—"}</p><p><strong>Endpoint</strong> {connectionTestState.projection.endpoint_id ?? "—"}</p><p><strong>建议</strong> {connectionFailureSuggestion(connectionTestState.projection.connection_diagnostic)}</p></>}</div>}
      </section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-latest-verification-heading">
        <h4 id="provider-latest-verification-heading">最近验证</h4>
        <p>状态：{provider?.last_test_status ? displayStatus(provider.last_test_status) : "—"}</p><p>HTTP：{provider?.http_status ?? "—"}</p><p>延迟：{provider?.latency_ms ?? "—"} ms</p><p>时间：{provider?.tested_at ?? "—"}</p>
      </section>
      <section className="ide-provider-detail-section" aria-labelledby="provider-account-heading">
        <h4 id="provider-account-heading">账户</h4>
        <p>{detailUsage === undefined ? "余额：不可获取" : detailUsage.balance_status === "UNSUPPORTED" ? "余额：厂商未提供查询接口" : detailUsage.current_balance === null ? "余额：不可获取" : `余额：${detailUsage.current_balance}${detailUsage.currency ? ` ${detailUsage.currency}` : ""}`}</p>
        {detailUsage !== undefined && detailUsage.balance_status !== "UNSUPPORTED" && <button type="button" className="ide-secondary-button" onClick={() => void refreshDetailBalance()} disabled={detailBalanceBusy || sessionExpired}>{detailBalanceBusy ? "刷新中…" : "刷新余额"}</button>}
      </section>
      <section className="ide-provider-detail-section ide-provider-detail-section--danger" aria-labelledby="provider-danger-heading">
        <h4 id="provider-danger-heading">危险操作</h4>
        <p className="ide-settings__hint">删除连接会移除凭据、停用 Endpoint，并保留历史模型记录；不会自动修改模型治理策略或执行工程操作。</p>
        <button type="button" className="ide-danger-button" onClick={() => void removeConnection()} disabled={providerBusy || sessionExpired}>删除此连接</button>
        <button type="button" className="ide-danger-button" onClick={() => void removeCredential()} disabled={providerBusy || sessionExpired || !credentialExists}>删除 API 凭据</button>
      </section>
      {displayedProviderMessage && <p className="ide-form-error" role="status">{displayedProviderMessage}</p>}
      {sessionExpired && <p className="ide-form-error" role="alert">Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。</p>}
    </div>;
  };

  const renderPreferenceGroups = (keys: string[]) => groups.filter((group) => keys.includes(group.key)).map((group) => <fieldset key={group.key}>
    <legend>{group.label}</legend>
    <div className="ide-segmented">{group.values.map(([value, label]) => <button key={value} type="button" className={preferences[group.key] === value ? "is-active" : ""} aria-pressed={preferences[group.key] === value} onClick={() => onChange({ ...preferences, [group.key]: value })}>{label}</button>)}</div>
  </fieldset>);

  return <div className="ide-settings-modal-backdrop" role="presentation" onMouseDown={(event: ReactMouseEvent<HTMLDivElement>) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="ide-settings ide-settings--open" role="dialog" aria-modal="true" aria-labelledby="embrix-settings-title" data-settings-layout="modal">
      <header className="ide-settings__header">
        <div className="ide-settings__brand"><img className="ide-settings__brand-logo ide-settings__brand-logo--light" src={brandAsset("embrix-lockup-light.png")} alt="Embrix" /><img className="ide-settings__brand-logo ide-settings__brand-logo--dark" src={brandAsset("embrix-lockup-dark.png")} alt="" aria-hidden="true" /><span>设置</span><h2 id="embrix-settings-title">Embrix 设置</h2></div>
        <button className="ide-icon-button" type="button" onClick={onClose} aria-label="关闭设置"><X size={16} /></button>
      </header>
      <div className="ide-settings__layout">
        <nav className="ide-settings__navigation" aria-label="设置导航" data-settings-navigation>
          {settingsSections.map((section) => <button key={section.id} type="button" className={activeSection === section.id ? "is-active" : ""} aria-label={section.label} aria-current={activeSection === section.id ? "page" : undefined} aria-controls={`settings-section-${section.id}`} onClick={() => setSettingsSection(section.id)}><span>{section.label}</span><small>{section.description}</small></button>)}
        </nav>
        <main className="ide-settings__content">
          <section id="settings-section-general" className="ide-settings__section" data-settings-section="general" hidden={activeSection !== "general"} aria-labelledby="settings-heading-general">
            <h3 id="settings-heading-general">常规</h3>
            <p className="ide-settings__intro">调整 Embrix 的默认回答方式与工程证据呈现。</p>
            {renderPreferenceGroups(["answerDetail", "evidenceDisplay"])}
            <fieldset data-auto-research-backend-dispatch="NOT_IMPLEMENTED">
              <legend>Research mode</legend>
              <div className="ide-segmented" role="group" aria-label="Research mode">
                {RESEARCH_MODE_OPTIONS.map((option) => <button key={option} type="button" className={researchMode === option ? "is-active" : ""} aria-pressed={researchMode === option} onClick={() => onResearchModeChange?.(option)}>{RESEARCH_MODE_LABELS[option]}</button>)}
              </div>
              <p className="ide-settings__hint">AUTO 与 LOCAL_ONLY 当前都不请求外部搜索；WEB_REQUIRED 会在发送问题时请求外部搜索，并可能要求外传审批。未配置搜索服务时请求会被拒绝。选择仅在当前窗口有效，自动研究调度尚未接入。</p>
            </fieldset>
          </section>
          <section id="settings-section-appearance" className="ide-settings__section" data-settings-section="appearance" hidden={activeSection !== "appearance"} aria-labelledby="settings-heading-appearance">
            <h3 id="settings-heading-appearance">外观</h3>
            <p className="ide-settings__intro">保持清晰、专注的工程工作台外观。</p>
            {renderPreferenceGroups(["theme", "density"])}
          </section>
          <section id="settings-section-models" className="ide-settings__section ide-settings__models" data-settings-section="models" hidden={activeSection !== "models"} aria-labelledby="model-governance-heading">
            <ModelGovernancePanel projectId={projectId} projectName={projectName} fetcher={modelGatewayFetcher} />
          </section>
          <section id="settings-section-provider" className="ide-settings__section ide-settings__provider" data-settings-section="provider" data-provider-current-id={!useCanonicalProviderConnections ? provider?.provider ?? "" : undefined} hidden={activeSection !== "provider"} aria-labelledby="settings-heading-provider">
            {useCanonicalProviderConnections && <ProviderConnectionSettingsPanel fetcher={modelGatewayFetcher} actionsDisabled={sessionExpired} />}
            {!useCanonicalProviderConnections && providerSettingsView.type === "list" && <ModelProvidersPanel
              fetcher={modelGatewayFetcher}
              refreshPoolFetcher={providerFetcher}
              configuration={provider}
              refreshSignal={providerListRefreshSignal}
              onUsageChange={setProviderUsage}
              onAddProvider={openAddProvider}
              onManageProvider={openProviderDetail}
              actionsDisabled={sessionExpired}
              onConfigurationRefresh={(value) => {
                setProviderState({ phase: "READY", value });
                setSelectedProvider(value.provider);
                setModel(modelForProjection(value, value.provider));
                setBaseUrl(value.base_url);
                setConnectionTestState({ phase: "IDLE" });
              }}
              onTemplatesChange={setProviderTemplates}
            />}
            {providerSettingsView.type === "add" && renderProviderAddView()}
            {providerSettingsView.type === "detail" && renderProviderDetailView()}
          </section>
          <section id="settings-section-advanced" className="ide-settings__section ide-settings__advanced" data-settings-section="advanced" hidden={activeSection !== "advanced"} aria-labelledby="settings-heading-advanced">
            <h3 id="settings-heading-advanced">高级</h3>
            <p className="ide-settings__intro">工程位置、帮助与凭据管理。敏感操作默认收起。</p>
            <section className="ide-settings__locations">
              <h4>工程位置</h4>
              {locationsState.phase === "LOADING" && <p role="status">工程位置加载中。</p>}
              {locationsState.phase === "READY" && locationsState.value.map((location) => <div key={location.location_id} className="ide-settings__location" data-project-location>
                <dl>
                  <div><dt>项目</dt><dd>{location.project_name?.trim() || location.display_label}</dd></div>
                  <div><dt>路径</dt><dd>{safeLocationPath(location)}</dd></div>
                  <div><dt>状态</dt><dd>{locationStatusLabel(location.availability)}</dd></div>
                  <div><dt>类型</dt><dd>{projectTypeLabel(location.project_type)}</dd></div>
                </dl>
              </div>)}
              {locationsState.phase === "READY" && locationsState.value.length === 0 && <p role="status">暂无工程位置。请在新建项目中选择工程文件夹。</p>}
              <p className="ide-settings__hint">请在新建项目中使用 Native 文件夹选择器添加工程位置。</p>
              {locationsState.phase === "ERROR" && <p className="ide-form-error" role="alert">{locationErrorMessage(locationsState.code)}</p>}
              {locationsState.phase !== "LOADING" && !sessionExpired && <button type="button" className="ide-secondary-button" onClick={() => setLocationsRetry((value) => value + 1)}>重试读取工程位置</button>}
            </section>
            <section className="ide-settings__help">
              <h4>帮助</h4>
              <p><strong>Ollama</strong> {ollamaStatus}</p>
              <p><strong>Project</strong> 绑定工程目录与只读上下文。</p>
              <p><strong>路径安全</strong> Native Picker 选择的 exact root 经过后端安全校验。</p>
              <p><strong>禁止</strong> 系统目录、用户目录、数据库目录、符号链接和测试 fixture。</p>
            </section>
            <details className="ide-settings__advanced-group" data-credential-management>
              <summary>Credential Management</summary>
              <div className="ide-settings__credential-management">
                {useCanonicalProviderConnections && <p className="ide-settings__hint">以下是旧版 Provider 配置诊断，不代表“提供商管理”中的连接与模型状态；请在那里查看当前连接。</p>}
                <p className="ide-settings__hint">仅显示安全 metadata，凭据内容不会显示。</p>
                <dl>
                  <div><dt>Credential Exists</dt><dd>{credentialExistsLabel}</dd></div>
                  <div><dt>Credential Updated</dt><dd>{credentialUpdatedLabel}</dd></div>
                  <div><dt>Provider</dt><dd>{provider ? detailProviderName ?? provider.provider : "—"}</dd></div>
                  <div><dt>Fingerprint</dt><dd>{provider?.fingerprint ?? "—"}</dd></div>
                  <div><dt>Updated At</dt><dd>{provider?.updated_at ?? "—"}</dd></div>
                  <div><dt>Validation Status</dt><dd>{validationStatusLabel(providerValidationStatus)}</dd></div>
                  <div><dt>Connection Validated</dt><dd>{connectionValidatedLabel}</dd></div>
                  <div><dt>Connection Failed</dt><dd>{connectionFailedLabel}</dd></div>
                </dl>
              </div>
              <details className="ide-settings__danger-zone">
                <summary>Danger Zone</summary>
                <p>删除凭据会移除当前 Provider 的保存状态。</p>
                <button type="button" disabled={providerBusy || sessionExpired || provider?.credential_status !== "CONFIGURED"} onClick={() => void removeCredential()}>删除 Credential</button>
              </details>
            </details>
            <details className="ide-settings__diagnostics"><summary>Developer diagnostics</summary><p>{useCanonicalProviderConnections ? "旧版 Provider 诊断（不代表提供商管理中的连接状态）" : "Provider 诊断"}：Provider raw: {providerStatus} · Connection raw: {connectionRaw} · Model raw: {modelStatus}</p><p>Model Request Readiness 由上方 Connection Status 展示。</p></details>
          </section>
          <section id="settings-section-version-update" className="ide-settings__section" data-settings-section="version-update" hidden={activeSection !== "version-update"} aria-labelledby="version-update-heading">
            <VersionUpdatePanel fetcher={desktopUpdateFetcher} />
          </section>
        </main>
      </div>
    </section>
  </div>;
}
