import { useEffect, useRef, useState } from "react";

import { bindChatAttachment, classifyQuestion, closeChatSession, CopilotChatRequestError, fetchProject, fetchReferences, fetchRuntimePanel, isRetryableChatErrorCode, reduceChatStreamEvent, streamChatEvents, submitQuestion, type ChatStreamProjection, type ContextReference, type ProjectReference, type RuntimePanel } from "./api/chatClient";
import { confirmRequirementChanges, createConversation, decideFeature, decideReferenceProject, decideExecutionAuthorization, decideFirmwareSourceGeneration, decideSchematicProduction, decideSchematicRepair as decideSchematicRepairRequest, deleteCatalogProject, deleteConversation as deletePersistedConversation, fetchArtifactIntelligence, fetchCatalogProject, fetchCatalogProjects, fetchConversation, fetchConversations, fetchProjectContext, resumeConversation, type ArtifactIntelligenceView, type ChatEvidence, type ConversationDetail, type ConversationTurn, type PredesignSourceExpectations, type ProjectContext, type ProjectView, type ReferenceProjectDecisionAction, type SchematicRepairDecisionAction, type SchematicRepairProposal } from "./api/projectClient";
import { ProjectCatalogRequestError, requestSchematicModelCandidate } from "./api/projectClient";
import { CenterWorkspace } from "./layout/CenterWorkspace";
import { CircuitIntentReviewPanel } from "./chat/CircuitIntentReviewPanel";
import { SchematicVerificationHistory } from "./chat/SchematicVerificationHistory";
import { SchematicEvidencePanel } from "./chat/SchematicEvidencePanel";
import { SchematicPreparationPanel } from "./chat/SchematicPreparationPanel";
import { SelectionRevisionPanel } from "./chat/SelectionRevisionPanel";
import { HardwareModelCandidatePanel } from "./chat/HardwareModelCandidatePanel";
import { DEFAULT_RESEARCH_MODE, type ResearchMode } from "./researchMode";
import { DesktopShell } from "./layout/DesktopShell";
import { inferWorkbenchMode, projectLayoutKey, type InspectorMode } from "./layout/adaptiveLayout";
import { buildConversationReport } from "./exportReport";
import type { EngineeringMemoryProjection } from "./memory/memoryViewModel";
import { TopNavigation } from "./layout/TopNavigation";
import { ActivityBar, EngineeringBottomPanel, EngineeringInspector, EngineeringWorkspace } from "./layout/EngineeringWorkbench";
import { EngineeringContextPanel, FirmwareSourceProposalCard, SchematicProductionProposalCard } from "./layout/AgentSurfacePanels";
import { ExecutionEventProvider } from "./execution/ExecutionEventProvider";
import { FirmwareBuildPanel } from "./execution/FirmwareBuildPanel";
import { WorkbenchExplorer, type ResourceLoadState } from "./layout/WorkbenchExplorer";
import { readUiPreferences, resolveUiTheme, writeUiPreferences, type ResolvedUiTheme, type UiPreferences } from "./preferences";
import { EdaImportPanel, ProjectCreatePanel, type ProjectCreateDataSource } from "./project/ProjectCreatePanel";
import type { CatalogState } from "./project/ProjectExplorer";
import { toEngineeringContextProjection, toProjectWorkspaceIdentity, toProjectWorkspaceNavigationItem, type EngineeringContextProjection, type ProjectWorkspaceIdentity, type ProjectWorkspaceNavigationItem } from "./projectWorkspaceIdentity";
import { SettingsPanel } from "./settings/SettingsPanel";
import type { CapabilityStatusView } from "./api/capabilityClient";
import type { ReviewView } from "./api/engineeringClient";
import { fetchDesktopWorkspace, type DesktopWorkspaceProjection } from "./api/desktopWorkspaceClient";
import { classifyWorkbenchResourceState, fetchWorkbench, type WorkbenchProjection } from "./api/workbenchClient";
import { useDesktopSessionState } from "./api/sessionState";
import { projectMemoryProjection, projectTaskProjection, projectTaskSummaries, projectWorkflow } from "./desktopWorkspaceProjection";
import type { EngineeringTaskHistorySummary, EngineeringTaskProjection } from "./task/engineeringTaskModel";
import type { WorkflowView } from "./workflow/engineeringWorkflowModel";
import "./styles.css";

type AcceptanceFixtureMode = "populated" | "unavailable" | "project-create" | "project-create-parent" | "project-created" | "project-context" | "project-ready" | "memory" | "workflow" | "task-continuity";
type ChatStreamStatus = ChatStreamProjection["status"];
type ProjectCreationFixtureWorkspace = {
  projects: ProjectView[];
  activeProjectId: string;
  project: ProjectView | null;
  context: ProjectContext | null;
  conversations: Awaited<ReturnType<typeof fetchConversations>>["items"];
  conversation: ConversationDetail | null;
  references: ContextReference[];
  runtimePanel: RuntimePanel | null;
  sessionId: string | null;
  chatAvailable: boolean;
  catalogError: string | null;
};
type ProjectCreationFixtures = {
  projectCreationWorkspaceFixture: { dataSource: ProjectCreateDataSource };
  projectParentCreationWorkspaceFixture: { dataSource: ProjectCreateDataSource };
  createdProjectWorkspaceFixture: ProjectCreationFixtureWorkspace;
  createdParentProjectWorkspaceFixture: ProjectCreationFixtureWorkspace;
};

function readAcceptanceFixtureMode(): AcceptanceFixtureMode | null {
  if (!import.meta.env.DEV || typeof window === "undefined") return null;
  const mode = new URLSearchParams(window.location.search).get("fixture");
  return mode === "populated" || mode === "unavailable" || mode === "project-create" || mode === "project-create-parent" || mode === "project-created" || mode === "project-context" || mode === "project-ready" || mode === "memory" || mode === "workflow" || mode === "task-continuity" ? mode : null;
}

function friendlyChatError(error: unknown): string {
  if (error instanceof CopilotChatRequestError) {
    switch (error.code) {
      case "COPILOT_CHAT_EMPTY_PROJECT": return "当前 Project 为空，没有可分析的工程文件。请接入源码，或改用 Greenfield 需求分析。";
      case "COPILOT_CHAT_NO_ANALYZABLE_SOURCE": return "当前 Project 中没有可分析的 C/C++ 源码。请接入包含 Core/Inc 或 Core/Src 的工程。";
      case "COPILOT_CHAT_INSUFFICIENT_EVIDENCE": return "当前工程证据不足，暂不能可靠回答。请补充源码、日志或已确认约束。";
      case "COPILOT_CHAT_PROVIDER_NOT_CONFIGURED": return "工程 Context/Evidence 已保留，但分析模型尚未配置。请在受控生产设置中配置 Provider。";
      case "COPILOT_CHAT_PROVIDER_UNAVAILABLE": return "工程 Context/Evidence 已保留，但分析模型当前不可用。";
      case "PROVIDER_AUTH_FAILED": return "DeepSeek API Key 无效或已被拒绝。";
      case "PROVIDER_NETWORK_UNAVAILABLE": return "DeepSeek 网络不可用，请稍后重试。";
      case "PROVIDER_MODEL_UNAVAILABLE": return "当前 DeepSeek Model 不可用。";
      case "PROVIDER_CONFIG_INVALID": return "Provider 配置无效。";
      case "PROVIDER_TRANSPORT_FAILED": return "DeepSeek 传输失败，请检查网络后重试。";
      case "PROVIDER_PARSE_FAILED": return "DeepSeek 已响应，但响应无法解析。";
      case "PROVIDER_CONNECTION_UNVERIFIED": return "Provider 尚未通过当前配置的连接测试。";
      case "DESKTOP_SESSION_EXPIRED": return "Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。";
      case "EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED": return "本次外发尚未获得独立批准。";
      case "EXTERNAL_TRANSMISSION_DENIED": return "本次外发已拒绝，未调用外部模型。";
      case "EXTERNAL_TRANSMISSION_APPROVAL_EXPIRED": return "本次外发批准已过期，请重新提交。";
      case "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH": return "本次外发批准与请求或上下文不匹配。";
      case "ANALYSIS_RUNTIME_FAILED": return "工程分析执行失败，可以稍后重试。";
      case "ANSWER_SCHEMA_INVALID": return "模型回答格式无效，未写入工程需求。";
      case "ANSWER_CONTRACT_MISMATCH": return "模型回答不符合当前任务合同，未写入工程需求。";
      case "ANSWER_EMPTY": return "模型返回了空回答，请重试。";
      case "ANSWER_REJECTED_UNSAFE": return "模型回答未通过安全校验，未写入工程需求。";
      case "ANSWER_REJECTED_EVIDENCE_MISMATCH": return "模型回答与当前工程证据不匹配，未采用该回答。";
      case "COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED": return "工程分析执行失败，可以稍后重试。";
      case "COPILOT_CHAT_APPLICATION_FAILED": return "工程分析服务未完成本次回答。";
    }
  }
  return "工程分析服务不可用。";
}

function retryableChatError(error: unknown): boolean {
  return error instanceof CopilotChatRequestError && isRetryableChatErrorCode(error.code);
}

function statusTone(value: string | null | undefined): string {
  const status = (value ?? "UNAVAILABLE").toUpperCase();
  return status === "AVAILABLE" ? "success" : status === "DEGRADED" || status === "UNVERIFIED" ? "warning" : status === "BETA" ? "info" : "muted";
}

type UiCapabilityStatus = "AVAILABLE" | "DEGRADED" | "UNAVAILABLE" | "UNVERIFIED";

/** Map backend capability fields without treating arbitrary non-empty strings as success. */
export function mapCapabilityStatus(value: string | null | undefined): UiCapabilityStatus {
  switch ((value ?? "").trim().toUpperCase()) {
    case "AVAILABLE":
    case "CONFIGURED":
    case "READY":
    case "TRUE":
      return "AVAILABLE";
    case "DEGRADED":
      return "DEGRADED";
    case "UNVERIFIED":
    case "NOT_CHECKED":
    case "PROVIDER_CONNECTION_UNVERIFIED":
      return "UNVERIFIED";
    default:
      return "UNAVAILABLE";
  }
}

/** Credential state requires an explicit backend projection; it is never inferred from provider configuration. */
export function mapCredentialSavedStatus(value: Partial<Pick<CapabilityStatusView, "credential_saved" | "provider_configured">> | null | undefined): UiCapabilityStatus {
  return mapCapabilityStatus(value?.credential_saved);
}

function providerConnectionStatus(status: string | null | undefined, reason: string | null | undefined): UiCapabilityStatus {
  const normalizedReason = (reason ?? "").trim().toUpperCase();
  if (normalizedReason.includes("UNVERIFIED")) return "UNVERIFIED";
  if (normalizedReason.includes("AUTH_FAILED") || normalizedReason.includes("NOT_CONFIGURED") || normalizedReason.includes("NETWORK_UNAVAILABLE") || normalizedReason.includes("MODEL_UNAVAILABLE") || normalizedReason.includes("RATE_LIMITED") || normalizedReason.includes("CONFIG_INVALID")) return "UNAVAILABLE";
  return mapCapabilityStatus(status);
}

function toTurn(question: string, answer: Awaited<ReturnType<typeof submitQuestion>>, requestId: string): ConversationTurn {
  return { turn_id: answer.answer_id, request_id: requestId, status: "COMPLETED", question, answer_text: answer.answer_text, response_type: answer.response_type, artifact_refs: answer.artifact_refs, evidence: answer.evidence_details, recommendations: answer.recommendations, context_references: answer.evidence_references, error_code: null, created_at: answer.created_at, completed_at: answer.created_at };
}

export function circuitReviewAvailability(conversation: ConversationDetail | null): {
  ercOperationId: string | null;
  predesignSource: PredesignSourceExpectations | null;
} {
  const ercOperationId = conversation?.schematic_production?.erc_receipt?.operation_id ?? null;
  const requirement = conversation?.requirement_context;
  const requirementReady = requirement && (
    requirement.status === "CONFIRMED"
    || ((requirement.status == null || requirement.status === "DRAFT") && (requirement.lifecycle === "REQUIREMENTS_READY" || requirement.lifecycle === "DESIGN_READY"))
  ) && !requirement.open_questions.length && !requirement.conflicts?.length;
  const predesignSource = requirementReady
    && conversation?.engineering_plan?.status === "APPROVED"
    && conversation.engineering_plan_review?.status === "APPROVED"
    && conversation.architecture_design?.status === "APPROVED"
    && conversation.architecture_review?.status === "APPROVED"
    && conversation.hardware_selection?.status === "APPROVED"
    && conversation.hardware_selection.approval
    && conversation.hardware_selection.bom
    && conversation.hardware_selection.hardware_truth
    && conversation.turns.some((turn) => turn.route === "GREENFIELD_DESIGN" && turn.status === "COMPLETED")
    ? {
      requirement_context_fingerprint: requirement.fingerprint,
      architecture_id: conversation.architecture_design.architecture_id,
      architecture_fingerprint: conversation.architecture_design.fingerprint,
      hardware_selection_approval_id: conversation.hardware_selection.approval.approval_id,
      hardware_truth_fingerprint: conversation.hardware_selection.hardware_truth.fingerprint,
    } : null;
  return { ercOperationId, predesignSource };
}

export default function CopilotApp() {
  const acceptanceMode = readAcceptanceFixtureMode();
  const desktopSessionState = useDesktopSessionState();
  const sessionExpired = desktopSessionState === "EXPIRED";
  const [projects, setProjects] = useState<ProjectView[]>([]);
  const [navigationProjects, setNavigationProjects] = useState<ProjectWorkspaceNavigationItem[]>([]);
  const [projectId, setProjectId] = useState("");
  const [project, setProject] = useState<ProjectView | null>(null);
  const [context, setContext] = useState<ProjectContext | null>(null);
  const [conversations, setConversations] = useState<Awaited<ReturnType<typeof fetchConversations>>["items"]>([]);
  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [artifactIntelligence, setArtifactIntelligence] = useState<ArtifactIntelligenceView | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [eventCursor, setEventCursor] = useState<string | null>(null);
  const [chatProject, setChatProject] = useState<ProjectReference | null>(null);
  const [references, setReferences] = useState<ContextReference[]>([]);
  const [selected, setSelected] = useState<ContextReference[]>([]);
  const [researchMode, setResearchMode] = useState<ResearchMode>(DEFAULT_RESEARCH_MODE);
  const [runtimePanel, setRuntimePanel] = useState<RuntimePanel | null>(null);
  const [chatAvailable, setChatAvailable] = useState(false);
  const [pending, setPending] = useState(false);
  const [approvalPending, setApprovalPending] = useState(false);
  const [schematicModelBusy, setSchematicModelBusy] = useState(false);
  const [schematicModelBlocked, setSchematicModelBlocked] = useState<{ projectId: string; conversationId: string; reason: string } | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [streamAnswer, setStreamAnswer] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<ChatStreamStatus>("idle");
  const [chatError, setChatError] = useState<string | null>(null);
  const [chatRetryable, setChatRetryable] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogState, setCatalogState] = useState<CatalogState>("loading");
  const [preferences, setPreferences] = useState<UiPreferences>(() => readUiPreferences());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [edaImportOpen, setEdaImportOpen] = useState(false);
  const [projectCreationFixtures, setProjectCreationFixtures] = useState<ProjectCreationFixtures | null>(null);
  const [fixtureIdentity, setFixtureIdentity] = useState<ProjectWorkspaceIdentity | null>(null);
  const [fixtureContextProjection, setFixtureContextProjection] = useState<EngineeringContextProjection | null | undefined>(undefined);
  const [memoryProjection, setMemoryProjection] = useState<EngineeringMemoryProjection | null>(null);
  const [workflowProjection, setWorkflowProjection] = useState<WorkflowView | null>(null);
  const [taskProjection, setTaskProjection] = useState<EngineeringTaskProjection | null>(null);
  const [taskSummaries, setTaskSummaries] = useState<EngineeringTaskHistorySummary[]>([]);
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedUiTheme>(() => resolveUiTheme(preferences.theme, typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-color-scheme: dark)").matches)));
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [capabilities, setCapabilities] = useState<CapabilityStatusView[]>([]);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [reviewQueue, setReviewQueue] = useState<ReviewView[]>([]);
  const [desktopProjection, setDesktopProjection] = useState<DesktopWorkspaceProjection | null>(null);
  const [workbenchProjection, setWorkbenchProjection] = useState<WorkbenchProjection | null>(null);
  const [resourceLoadState, setResourceLoadState] = useState<ResourceLoadState>("NO_PROJECT");
  const [resourceLoadError, setResourceLoadError] = useState<string | null>(null);
  const [activeActivity, setActiveActivity] = useState("copilot");
  const [buildOpen, setBuildOpen] = useState(false);
  const [selectedResourceId, setSelectedResourceId] = useState<string | null>(null);
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<ChatEvidence | null>(null);
  const [openWorkspaceTabs, setOpenWorkspaceTabs] = useState<string[]>([]);
  const [bottomTab, setBottomTab] = useState("verification");
  const [bottomOpen, setBottomOpen] = useState(false);
  const [inspectorMode, setInspectorMode] = useState<InspectorMode>("hidden");
  const sessionRef = useRef<string | null>(null);
  const activityBeforeSettingsRef = useRef("copilot");
  const generationRef = useRef(0);
  const wave2OwnerRef = useRef<string>("");
  wave2OwnerRef.current = JSON.stringify([projectId, conversation?.conversation_id, conversation?.fingerprint, conversation?.selection_head?.revision_id, conversation?.selection_head?.decision_fingerprint, conversation?.selection_head?.currentness, conversation?.selection_head?.reason]);
  const projectionAbortRef = useRef<AbortController | null>(null);

  useEffect(() => { writeUiPreferences(preferences); }, [preferences]);

  useEffect(() => {
    if (!sessionExpired) return;
    projectionAbortRef.current?.abort();
    setDesktopProjection(null);
    setWorkbenchProjection(null);
    setSelectedResourceId(null);
    setSelectedFindingId(null);
    setSelectedEvidence(null);
    setOpenWorkspaceTabs([]);
    setInspectorMode("hidden");
    setBottomOpen(false);
    setContext(null);
    setMemoryProjection(null);
    setWorkflowProjection(null);
    setTaskProjection(null);
    setTaskSummaries([]);
    setRuntimePanel(null);
    setCapabilities([]);
    setReviewQueue([]);
    setReferences([]);
    setSelected([]);
    setChatProject(null);
    setChatAvailable(false);
    setCreateOpen(false);
  }, [sessionExpired]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setResolvedTheme(resolveUiTheme(preferences.theme, media.matches));
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, [preferences.theme]);

  function resetConversationRuntime() {
    setSessionId(null);
    sessionRef.current = null;
    setEventCursor(null);
    setChatProject(null);
    setReferences([]);
    setSelected([]);
    setRuntimePanel(null);
    setChatAvailable(false);
    setPendingQuestion(null);
    setStreamAnswer(null);
    setStreamStatus("idle");
    setChatError(null);
    setChatRetryable(false);
    setArtifactIntelligence(null);
  }

  function setChatFailure(error: unknown, retryable = retryableChatError(error)) {
    setChatError(friendlyChatError(error));
    setChatRetryable(retryable);
  }

  async function refreshDesktopProjection(activeProjectId: string, generation: number, activeConversationId: string | null = null, locationAvailable: boolean | undefined = undefined): Promise<DesktopWorkspaceProjection | null> {
    projectionAbortRef.current?.abort();
    const controller = new AbortController();
    projectionAbortRef.current = controller;
    if (generation === generationRef.current) {
      setResourceLoadError(null);
      setResourceLoadState(locationAvailable === false ? "NO_LOCATION" : "LOADING");
    }
    try {
      const [next, activeConversation] = await Promise.all([
        fetchDesktopWorkspace(activeProjectId, { limit: 20, conversationId: activeConversationId, signal: controller.signal }),
        activeConversationId ? fetchConversation(activeProjectId, activeConversationId).catch(() => null) : Promise.resolve(null),
      ]);
      let workbench: WorkbenchProjection | null = null;
      if (locationAvailable !== false) {
        try { workbench = await fetchWorkbench(activeProjectId, { signal: controller.signal }); }
        catch (error) { if (generation === generationRef.current) { setResourceLoadError(error instanceof Error ? error.message : "WORKBENCH_REQUEST_FAILED"); setResourceLoadState("API_ERROR"); } }
      }
      if (generation !== generationRef.current) return null;
      setDesktopProjection(next);
      setWorkbenchProjection(workbench);
      if (locationAvailable === false) setResourceLoadState("NO_LOCATION");
      else if (workbench) { setResourceLoadError(null); setResourceLoadState(classifyWorkbenchResourceState(workbench)); }
      setMemoryProjection(projectMemoryProjection(next));
      setWorkflowProjection(projectWorkflow(next));
      setTaskProjection(activeConversation ? projectTaskProjection(next, activeConversationId, activeConversation) : null);
      setTaskSummaries(projectTaskSummaries(next));
      setCapabilities(next.capabilities.map((item) => ({ capability: item.capability, status: item.status, reason_code: item.reason_code, detail: item.detail, provider_configured: item.provider_configured ?? null, credential_saved: item.credential_saved ?? null, runtime_status: item.runtime_status ?? null, model_status: item.model_status ?? null, fingerprint: `${next.projection_fingerprint}:${item.capability}` })));
      setCapabilityError(next.capabilities.length ? null : "CAPABILITIES_UNAVAILABLE");
      setReviewQueue(next.reviews.items.map((item) => ({ review_id: item.reference_id, project_id: next.identity.project_id, target_type: "ENGINEERING_PROPOSAL", target_id: item.reference_id, target_fingerprint: item.fingerprint ?? "", requester: "desktop-user", status: (item.status ?? "OPEN") as ReviewView["status"], created_at: next.generated_at, decision: null, reviewer: null, decided_at: null, fingerprint: item.fingerprint ?? item.reference_id })));
      return next;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return null;
      if (generation === generationRef.current) { setDesktopProjection(null); setWorkbenchProjection(null); setResourceLoadState("API_ERROR"); setResourceLoadError(error instanceof Error ? error.message : "WORKBENCH_REQUEST_FAILED"); setMemoryProjection(null); setWorkflowProjection(null); setTaskProjection(null); setTaskSummaries([]); }
      return null;
    }
  }

  async function closeCurrentSession() {
    if (acceptanceMode) { resetConversationRuntime(); return; }
    const current = sessionRef.current;
    resetConversationRuntime();
    if (current && !sessionExpired) await closeChatSession(current).catch(() => undefined);
  }

  async function bindConversationSession(activeProjectId: string, activeConversationId: string) {
    if (acceptanceMode) return;
    const generation = generationRef.current;
    const resumed = await resumeConversation(activeProjectId, activeConversationId);
    if (generation !== generationRef.current) return;
    setConversation(resumed.conversation);
    setSessionId(resumed.session_id);
    sessionRef.current = resumed.session_id;
    setChatAvailable(resumed.chat_available);
    if (!resumed.chat_available) { setChatError("工程分析服务不可用；历史记录和输入框仍可查看。"); return; }
    const [projectResult, referencesResult, panelResult] = await Promise.allSettled([fetchProject(resumed.session_id), fetchReferences(resumed.session_id), fetchRuntimePanel(resumed.session_id)]);
    if (generation !== generationRef.current) return;
    const cursor = await streamChatEvents(resumed.session_id, null, () => undefined).catch(() => null);
    if (generation !== generationRef.current) return;
    setEventCursor(cursor);
    if (projectResult.status === "fulfilled") setChatProject(projectResult.value);
    if (referencesResult.status === "fulfilled") setReferences(referencesResult.value);
    if (panelResult.status === "fulfilled") setRuntimePanel(panelResult.value);
    if (projectResult.status === "rejected") { setChatAvailable(false); setChatError("工程分析服务不可用；历史记录和输入框仍可查看。"); }
  }

  async function activateProject(id: string) {
    if (!id || acceptanceMode || sessionExpired) return;
    const generation = ++generationRef.current;
    setProjectId(id);
    setProject(null);
    setMemoryProjection(null);
    setDesktopProjection(null);
    setWorkbenchProjection(null);
    setResourceLoadState("LOADING");
    setResourceLoadError(null);
    setSelectedResourceId(null);
    setSelectedFindingId(null);
    setSelectedEvidence(null);
    setOpenWorkspaceTabs([]);
    setInspectorMode("hidden");
    setBottomOpen(activeActivity === "test");
    setWorkflowProjection(null);
    setTaskProjection(null);
    setCapabilities([]);
    setCapabilityError(null);
    setReviewQueue([]);
    setConversation(null);
    setContext(null);
    setConversations([]);
    setTaskSummaries([]);
    setCatalogError(null);
    await closeCurrentSession();
    if (generation !== generationRef.current) return;
    try {
      const [nextProject, nextContext, history] = await Promise.all([fetchCatalogProject(id), fetchProjectContext(id), fetchConversations(id)]);
      if (generation !== generationRef.current) return;
      setProject(nextProject);
      setContext(nextContext);
      setConversations(history.items);
      const projection = await refreshDesktopProjection(id, generation, history.items[0]?.conversation_id ?? null, Boolean(nextProject.location_id));
      if (projection) setContext((current) => current);
      if (history.items[0]) await bindConversationSession(id, history.items[0].conversation_id);
    } catch { if (generation === generationRef.current) { setCatalogError("无法加载 Project 和工程上下文。"); setResourceLoadState("API_ERROR"); setResourceLoadError("Project 或工程上下文读取失败"); } }
  }

  useEffect(() => {
    let alive = true;
    if (import.meta.env.DEV && acceptanceMode) {
      void Promise.all([import("./acceptanceFixtures/workspaceFixtures"), import("./acceptanceFixtures/projectCreationFixtures"), import("./acceptanceFixtures/projectContextFixture"), import("./acceptanceFixtures/projectReadyFixture"), import("./acceptanceFixtures/memoryFixture"), import("./acceptanceFixtures/workflowFixture"), import("./acceptanceFixtures/taskContinuityFixture")]).then(([workspaceFixtures, creationFixtures, contextFixtures, readyFixture, memoryFixture, workflowFixture, taskContinuityFixture]) => {
        if (!alive) return;
        setProjectCreationFixtures(creationFixtures);
        if (acceptanceMode === "project-create" || acceptanceMode === "project-create-parent") {
          setProjects([]);
          setNavigationProjects([]);
          setProjectId("");
          setProject(null);
          setContext(null);
          setConversations([]);
          setConversation(null);
          resetConversationRuntime();
          setCatalogError(null);
          setCatalogState("ready");
          setCreateOpen(true);
          return;
        }
        if (acceptanceMode === "project-ready") {
          const fixture = readyFixture.projectReadyWorkspaceFixture;
          setProjects([]);
          setNavigationProjects([fixture.navigationItem]);
          setProjectId(fixture.activeProjectIdentity.id);
          setProject(null);
          setContext(null);
          setConversations([]);
          setConversation(null);
          setFixtureIdentity(fixture.activeProjectIdentity);
          setFixtureContextProjection(fixture.contextProjection);
          setMemoryProjection({ projectId: fixture.activeProjectIdentity.id, state: "empty", items: [] });
          resetConversationRuntime();
          setCatalogError(null);
          setCatalogState("ready");
          return;
        }
        if (acceptanceMode === "workflow") {
          const fixture = workflowFixture.workflowWorkspaceFixture;
          setProjects([]);
          setNavigationProjects([fixture.navigationItem]);
          setProjectId(fixture.projectId);
          setProject(null);
          setContext(null);
          setConversations(fixture.conversations);
          setConversation(fixture.conversation);
          setFixtureIdentity(fixture.activeProjectIdentity);
          setFixtureContextProjection(fixture.contextProjection);
          setMemoryProjection(fixture.memoryProjection);
          resetConversationRuntime();
          setConversation(fixture.conversation);
          setReferences(fixture.references);
          setRuntimePanel(null);
          setWorkflowProjection(fixture.workflowProjection);
          setChatAvailable(true);
          setCatalogError(null);
          setCatalogState("ready");
          return;
        }
        if (acceptanceMode === "task-continuity") {
          const fixture = taskContinuityFixture.taskContinuityWorkspaceFixture;
          setProjects([]);
          setNavigationProjects([fixture.navigationItem]);
          setProjectId(fixture.projectId);
          setProject(null);
          setContext(null);
          setConversations([]);
          setConversation(null);
          setFixtureIdentity(fixture.projectIdentity);
          setFixtureContextProjection(fixture.contextProjection);
          setMemoryProjection(null);
          resetConversationRuntime();
          setTaskProjection(fixture.taskProjection);
          setTaskSummaries(fixture.taskSummaries);
          setReferences([]);
          setRuntimePanel(null);
          setWorkflowProjection(fixture.workflowProjection);
          setChatAvailable(true);
          setCatalogError(null);
          setCatalogState("ready");
          return;
        }
        if (acceptanceMode === "memory") {
          const memory = memoryFixture.memoryWorkspaceFixture;
          const base = workspaceFixtures.populatedWorkspaceFixture;
          const conversation = base.conversation ? { ...base.conversation, project_id: memory.activeProjectIdentity.id, turns: base.conversation.turns.map((turn) => ({ ...turn })) } : null;
          setProjects([]);
          setNavigationProjects([memory.navigationItem]);
          setProjectId(memory.activeProjectIdentity.id);
          setProject(null);
          setContext(null);
          setConversations(base.conversations.map((item) => ({ ...item, project_id: memory.activeProjectIdentity.id })));
          setConversation(conversation);
          setFixtureIdentity(memory.activeProjectIdentity);
          setFixtureContextProjection({ projectId: memory.activeProjectIdentity.id, references: [{ category: "Firmware", count: 2 }, { category: "Hardware", count: 1 }, { category: "Documents", count: 1 }], evidenceSources: [{ source: "HIL", count: 1 }, { source: "EDA", count: 1 }, { source: "Firmware", count: 1 }], resources: [{ capability: "Project Files", status: "AVAILABLE" }, { capability: "Knowledge", status: "AVAILABLE" }, { capability: "HIL", status: "AVAILABLE" }, { capability: "EDA", status: "AVAILABLE" }] });
          setMemoryProjection(memory.memoryProjection);
          resetConversationRuntime();
          setConversation(conversation);
          setReferences(base.references.map((item) => ({ ...item, project_id: memory.activeProjectIdentity.id })));
          setRuntimePanel(base.runtimePanel);
          setChatAvailable(true);
          setCatalogError(null);
          setCatalogState("ready");
          return;
        }
        const fixture = acceptanceMode === "project-created" ? creationFixtures.createdProjectWorkspaceFixture : acceptanceMode === "project-context" ? contextFixtures.projectContextWorkspaceFixture : acceptanceMode === "populated" ? workspaceFixtures.populatedWorkspaceFixture : workspaceFixtures.unavailableWorkspaceFixture;
        setProjects(fixture.projects);
        setNavigationProjects(fixture.projects.map((item) => toProjectWorkspaceNavigationItem(item, fixture.conversation?.project_id === item.project_id ? fixture.conversation : null)));
        setProjectId(fixture.activeProjectId);
        setProject(fixture.project);
        setContext(fixture.context);
        setConversations(fixture.conversations);
        setConversation(fixture.conversation);
        setSessionId(fixture.sessionId);
        sessionRef.current = fixture.sessionId;
        setChatProject(fixture.chatProject);
        setReferences(fixture.references);
        setSelected([]);
        setRuntimePanel(fixture.runtimePanel);
        setFixtureIdentity(null);
        setFixtureContextProjection(undefined);
        setMemoryProjection(null);
        setWorkflowProjection(null);
        setTaskProjection(null);
        setTaskSummaries([]);
        setChatAvailable(fixture.chatAvailable);
        setCatalogError(fixture.catalogError);
        setCatalogState(acceptanceMode === "unavailable" ? "unavailable" : "ready");
      });
      return () => { alive = false; generationRef.current += 1; };
    }
    void fetchCatalogProjects().then((values) => { if (!alive) return; setCatalogState("ready"); setProjects(values); setNavigationProjects(values.map((item) => toProjectWorkspaceNavigationItem(item))); setFixtureIdentity(null); setFixtureContextProjection(undefined); setMemoryProjection(null); }).catch(() => { if (alive) { setCatalogState("unavailable"); setCatalogError("Project Catalog 不可用。"); } });
    return () => { alive = false; generationRef.current += 1; const current = sessionRef.current; if (current) void closeChatSession(current).catch(() => undefined); };
  }, []);

  async function openConversation(conversationId: string) {
    if (!projectId || acceptanceMode || sessionExpired) return;
    const generation = ++generationRef.current;
    await closeCurrentSession();
    if (generation !== generationRef.current) return;
    // Conversation-scoped findings and evidence must never bleed into another
    // persisted conversation; project resource selection may remain in scope.
    setSelectedFindingId(null);
    setSelectedEvidence(null);
    try {
      const restored = await fetchConversation(projectId, conversationId);
      if (generation !== generationRef.current) return;
      setConversation(restored);
      setArtifactIntelligence(await fetchArtifactIntelligence(projectId, conversationId).catch(() => null));
      await bindConversationSession(projectId, conversationId);
      await refreshDesktopProjection(projectId, generation, conversationId);
    } catch { if (generation === generationRef.current) setChatError("无法恢复该对话。"); }
  }

  async function newConversation() {
    if (!projectId || acceptanceMode || sessionExpired) return;
    const generation = ++generationRef.current;
    await closeCurrentSession();
    if (generation !== generationRef.current) return;
    setSelectedFindingId(null);
    setSelectedEvidence(null);
    try {
      const created = await createConversation(projectId);
      if (generation !== generationRef.current) return;
      setConversation(created.conversation);
       setSessionId(created.session_id);
       sessionRef.current = created.session_id;
       setEventCursor(null);
      setChatAvailable(created.chat_available);
      const history = await fetchConversations(projectId);
      if (generation !== generationRef.current) return;
      setConversations(history.items);
      await refreshDesktopProjection(projectId, generation, created.conversation.conversation_id);
       if (created.chat_available) { const [projectProjection, contextReferences, panel] = await Promise.all([fetchProject(created.session_id), fetchReferences(created.session_id), fetchRuntimePanel(created.session_id)]); if (generation !== generationRef.current) return; setChatProject(projectProjection); setReferences(contextReferences); setRuntimePanel(panel); }
      else setChatError("工程分析服务不可用；新对话已保存。");
    } catch { setChatError("无法新建对话。"); }
  }

  async function deleteConversation(conversationId: string) {
    if (!projectId || !conversationId || sessionExpired) return;
    // Acceptance fixtures are intentionally in-memory test data. Production
    // always uses the catalog DELETE contract below.
    if (acceptanceMode) {
      const remaining = conversations.filter((item) => item.conversation_id !== conversationId);
      setConversations(remaining);
      if (conversation?.conversation_id === conversationId) {
        setConversation(null);
        resetConversationRuntime();
      }
      return;
    }
    try {
      await deletePersistedConversation(projectId, conversationId);
      const history = await fetchConversations(projectId);
      setConversations(history.items);
      if (conversation?.conversation_id !== conversationId) return;
      await closeCurrentSession();
      const fallback = history.items[0]?.conversation_id ?? null;
      setSelectedResourceId(null);
      setSelectedFindingId(null);
      setSelectedEvidence(null);
      setOpenWorkspaceTabs([]);
      setInspectorMode("hidden");
      setBottomOpen(false);
      if (fallback) {
        await openConversation(fallback);
      } else {
        setConversation(null);
        setTaskProjection(null);
        setWorkflowProjection(null);
      }
    } catch (error) {
      setChatFailure(error, false);
    }
  }

  async function projectCreated(created: ProjectView) {
    if (sessionExpired) { setCreateOpen(false); return; }
    if (acceptanceMode === "project-create" || acceptanceMode === "project-create-parent") {
      const fixture = acceptanceMode === "project-create-parent" ? projectCreationFixtures?.createdParentProjectWorkspaceFixture : projectCreationFixtures?.createdProjectWorkspaceFixture;
      if (!fixture) { setCreateOpen(false); return; }
      setProjects(fixture.projects);
      setNavigationProjects(fixture.projects.map((item) => toProjectWorkspaceNavigationItem(item)));
      setProjectId(fixture.activeProjectId);
      setProject(fixture.project);
      setContext(fixture.context);
      setConversations(fixture.conversations);
      setConversation(fixture.conversation);
      setFixtureIdentity(null);
      setFixtureContextProjection(undefined);
      setMemoryProjection(null);
      setSelectedResourceId(null);
      setSelectedFindingId(null);
      setSelectedEvidence(null);
      setOpenWorkspaceTabs([]);
      setInspectorMode("hidden");
      resetConversationRuntime();
      setCatalogState("ready");
      setCreateOpen(false);
      return;
    }
    if (acceptanceMode) { setCreateOpen(false); return; }
    const generation = ++generationRef.current;
    resetConversationRuntime();
    // Confirm/Create has already persisted this Project. Keep it visible while
    // the authoritative catalog and projection refresh complete.
    setProjects((current) => [created, ...current.filter((item) => item.project_id !== created.project_id)]);
    setNavigationProjects((current) => [toProjectWorkspaceNavigationItem(created), ...current.filter((item) => item.identity.id !== created.project_id)]);
    setProjectId(created.project_id);
    setProject(created);
    setContext(null);
    setConversations([]);
    setSelectedResourceId(null);
    setSelectedFindingId(null);
    setSelectedEvidence(null);
    setOpenWorkspaceTabs([]);
    setInspectorMode("hidden");
    try {
      const refreshedProjects = await fetchCatalogProjects();
      if (generation !== generationRef.current) return;
      setProjects(refreshedProjects);
      setNavigationProjects(refreshedProjects.map((item) => toProjectWorkspaceNavigationItem(item)));
      for (let attempt = 0; attempt < 30; attempt += 1) { const latest = await fetchCatalogProject(created.project_id).catch(() => null); if (!latest) break; setProject(latest); setProjects((current) => current.map((item) => item.project_id === latest.project_id ? latest : item)); setNavigationProjects((current) => current.map((item) => item.identity.id === latest.project_id ? toProjectWorkspaceNavigationItem(latest) : item)); if (latest.initialization_status === "READY" || latest.initialization_status === "FAILED") break; await new Promise((resolve) => window.setTimeout(resolve, 1000)); }
      const [nextContext, history] = await Promise.all([fetchProjectContext(created.project_id), fetchConversations(created.project_id)]);
      if (generation !== generationRef.current) return;
      setContext(nextContext);
      setConversations(history.items);
      const projection = await refreshDesktopProjection(created.project_id, generation, null, Boolean(created.location_id));
      if (!projection) setCatalogError("工程上下文暂不可用，Project 已创建并保持选中。");
    } catch { setCatalogError("工程上下文暂不可用，Project 已创建并保持选中。"); }
    finally { if (generation === generationRef.current) setCreateOpen(false); }
  }

  async function removeProject(targetId: string) {
    if (acceptanceMode || sessionExpired) return;
    const target = projects.find((item) => item.project_id === targetId);
    if (!target) return;
    setCatalogError(null);
    try {
      await deleteCatalogProject(target.project_id, target.fingerprint);
      const remaining = projects.filter((item) => item.project_id !== targetId);
      setProjects(remaining);
      setNavigationProjects((current) => current.filter((item) => item.identity.id !== targetId));
      if (projectId !== targetId) return;
      ++generationRef.current;
      projectionAbortRef.current?.abort();
      await closeCurrentSession();
      setProjectId("");
      setProject(null);
      setContext(null);
      setConversations([]);
      setDesktopProjection(null);
      setWorkbenchProjection(null);
      setResourceLoadState("NO_PROJECT");
      setResourceLoadError(null);
      setConversation(null);
      setMemoryProjection(null);
      setWorkflowProjection(null);
      setTaskProjection(null);
      setTaskSummaries([]);
      setCapabilities([]);
      setCapabilityError(null);
      setReviewQueue([]);
      setSelectedResourceId(null);
      setSelectedFindingId(null);
      setSelectedEvidence(null);
      setInspectorMode("hidden");
      setBottomOpen(false);
      setOpenWorkspaceTabs([]);
    } catch {
      setCatalogError("删除项目失败，当前项目和会话状态已保留。");
    }
  }

  function toggleReference(reference: ContextReference) { setSelected((current) => current.some((item) => item.fingerprint === reference.fingerprint) ? current.filter((item) => item.fingerprint !== reference.fingerprint) : [...current, reference]); }

  async function attach(file: File) {
    if (sessionExpired || !sessionId || !chatAvailable || acceptanceMode) return;
    try { const reference = await bindChatAttachment(sessionId, file); setReferences((current) => current.some((item) => item.fingerprint === reference.fingerprint) ? current : [...current, reference]); setSelected((current) => current.some((item) => item.fingerprint === reference.fingerprint) ? current : [...current, reference]); } catch (error) { setChatFailure(error); }
  }

  async function ask(value: string) {
    if (!projectId || pending || acceptanceMode) return;
    if (sessionExpired) {
      setChatFailure(new CopilotChatRequestError("DESKTOP_SESSION_EXPIRED"), false);
      return;
    }
    const generation = generationRef.current;
    setPending(true); setPendingQuestion(value); setStreamAnswer(null); setStreamStatus("idle"); setChatError(null); setChatRetryable(false);
    try {
      let activeSessionId = sessionId;
      let activeChatProject = chatProject;
      let activeConversation = conversation;
      if (!activeSessionId || !activeChatProject || !activeConversation || !chatAvailable) {
        const created = await createConversation(projectId);
        activeSessionId = created.session_id;
        activeConversation = created.conversation;
        setSessionId(created.session_id);
        sessionRef.current = created.session_id;
        setConversation(created.conversation);
        setChatAvailable(created.chat_available);
        if (!created.chat_available) { setChatFailure(new CopilotChatRequestError("COPILOT_CHAT_PROVIDER_NOT_CONFIGURED"), false); return; }
        const [projectProjection, contextReferences, panel] = await Promise.all([fetchProject(created.session_id), fetchReferences(created.session_id), fetchRuntimePanel(created.session_id)]);
        activeChatProject = projectProjection;
        setChatProject(projectProjection);
        setReferences(contextReferences);
        setRuntimePanel(panel);
      }
      if (!activeSessionId || !activeChatProject || !activeConversation) return;
      let answer: Awaited<ReturnType<typeof submitQuestion>> | null = null;
      const requestId = crypto.randomUUID();
      const webRequired = researchMode === "WEB_REQUIRED";
      const preflight = await classifyQuestion(activeSessionId, activeChatProject, value, selected, preferences.answerDetail, webRequired, fetch, requestId);
      // Ordinary requirement conversations are local-first and uninterrupted.
      // Any future sensitive-scope escalation must be represented by the
      // canonical policy/approval projection, never a browser confirmation.
      let streamOutcome: ChatStreamProjection = { status: "idle", answer: "", errorCode: null, analysisState: null, traceId: null };
      const consumeEvents = async (): Promise<string | null> => {
        let lastError: unknown = null;
        for (let attempt = 0; attempt < 2; attempt += 1) {
          try {
            return await streamChatEvents(activeSessionId!, eventCursor, (event) => {
              if (generation !== generationRef.current) return;
              streamOutcome = reduceChatStreamEvent(streamOutcome, event);
              setStreamStatus(streamOutcome.status);
              if (event.event_type === "ANSWER_CHUNK") setStreamAnswer(streamOutcome.answer);
            });
          } catch (error) {
            lastError = error;
            if (generation !== generationRef.current || attempt === 1) break;
            setStreamStatus("resync");
          }
        }
        throw lastError ?? new Error("chat stream unavailable");
      };
      try {
        answer = await submitQuestion(activeSessionId, activeChatProject, value, selected, preferences.answerDetail, webRequired, fetch, requestId);
        if (generation !== generationRef.current) return;
      } catch (error) {
        const cursor = await consumeEvents().catch(() => null);
        if (cursor) setEventCursor(cursor);
        const failedConversation = await fetchConversation(projectId, activeConversation.conversation_id).catch(() => null);
        if (failedConversation) setConversation(failedConversation);
        const latestTurn = failedConversation?.turns[failedConversation.turns.length - 1];
        if (latestTurn?.analysis_state === "WAITING_FOR_CLARIFICATION" || error instanceof CopilotChatRequestError && error.code === "CLARIFICATION_REQUIRED") {
          setStreamStatus("clarification");
          setChatError(null);
          setChatRetryable(false);
          await refreshDesktopProjection(projectId, generation, activeConversation.conversation_id);
          return;
        }
        setChatFailure(error);
        await refreshDesktopProjection(projectId, generation, activeConversation.conversation_id);
        return;
      }
      const cursor = await consumeEvents().catch(() => null);
      if (generation !== generationRef.current) return;
      if (cursor) setEventCursor(cursor);
      if (streamOutcome.status === "resync") {
        const [nextProject, nextContext, nextConversation, nextReferences, nextPanel, nextHistory] = await Promise.all([
          fetchCatalogProject(projectId).catch(() => null),
          fetchProjectContext(projectId).catch(() => null),
          fetchConversation(projectId, activeConversation.conversation_id).catch(() => null),
          fetchReferences(activeSessionId).catch(() => null),
          fetchRuntimePanel(activeSessionId).catch(() => null),
          fetchConversations(projectId).catch(() => null),
        ]);
        if (generation !== generationRef.current) return;
        if (nextProject) setProject(nextProject);
        if (nextContext) setContext(nextContext);
        if (nextConversation) setConversation(nextConversation);
        if (nextReferences) setReferences(nextReferences);
        if (nextPanel) setRuntimePanel(nextPanel);
        if (nextHistory) setConversations(nextHistory.items);
        setChatFailure(new CopilotChatRequestError("COPILOT_CHAT_PROVIDER_UNAVAILABLE"));
        return;
      }
      if (streamOutcome.status !== "completed" || !answer) {
        setStreamStatus(streamOutcome.status === "idle" ? "failed" : streamOutcome.status);
        const incompleteConversation = await fetchConversation(projectId, activeConversation.conversation_id).catch(() => null);
        if (incompleteConversation) setConversation(incompleteConversation);
        const latestTurn = incompleteConversation?.turns[incompleteConversation.turns.length - 1];
        if (streamOutcome.status === "clarification" || latestTurn?.analysis_state === "WAITING_FOR_CLARIFICATION") {
          setChatError(null);
          setChatRetryable(false);
          await refreshDesktopProjection(projectId, generation, activeConversation.conversation_id);
          return;
        }
        const persistedCode = latestTurn?.error_code ?? null;
        const failureCode = streamOutcome.errorCode ?? persistedCode ?? "COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED";
        setChatFailure(new CopilotChatRequestError(failureCode));
        await refreshDesktopProjection(projectId, generation, activeConversation.conversation_id);
        return;
      }
      const [persisted, refreshedContext, refreshedReferences, refreshedPanel] = await Promise.all([
        fetchConversation(projectId, activeConversation.conversation_id).catch(() => null),
        fetchProjectContext(projectId).catch(() => null),
        fetchReferences(activeSessionId).catch(() => null),
        fetchRuntimePanel(activeSessionId).catch(() => null),
      ]);
      if (refreshedContext) setContext(refreshedContext);
      if (refreshedReferences) setReferences(refreshedReferences);
      if (refreshedPanel) setRuntimePanel(refreshedPanel);
      setConversation(persisted ?? { ...activeConversation, title: activeConversation.turns.length ? activeConversation.title : value.split("\n")[0].slice(0, 64), turns: [...activeConversation.turns, toTurn(value, answer, requestId)], updated_at: answer.created_at });
      setConversations((await fetchConversations(projectId)).items);
      await refreshDesktopProjection(projectId, generation, activeConversation.conversation_id);
      const refreshedProjects = await fetchCatalogProjects().catch(() => null); if (refreshedProjects) { setProjects(refreshedProjects); setNavigationProjects(refreshedProjects.map((item) => toProjectWorkspaceNavigationItem(item, item.project_id === projectId ? activeConversation : null))); }
    } catch (error) { setChatFailure(error); await refreshDesktopProjection(projectId, generation, conversation?.conversation_id ?? null); }
    finally { if (generation === generationRef.current) { setPending(false); setPendingQuestion(null); } }
  }

  function exportConversation() {
    if (!project || !conversation || !conversation.turns.some((turn) => turn.status === "COMPLETED")) return;
    const url = URL.createObjectURL(new Blob([buildConversationReport(conversation)], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "embedded-copilot-report.md"; anchor.click(); URL.revokeObjectURL(url);
  }

  async function decideAuthorizationRequest(requestId: string, decision: "APPROVED" | "REJECTED") {
    if (!projectId || !conversation || approvalPending || sessionExpired) return;
    setApprovalPending(true);
    setChatError(null);
    try {
      setConversation(await decideExecutionAuthorization(projectId, conversation.conversation_id, requestId, decision));
    } catch (error) {
      setChatFailure(error, false);
    } finally {
      setApprovalPending(false);
    }
  }

  async function decideReferenceReview(review: NonNullable<ConversationDetail["research_snapshot"]>["reference_reviews"][number], decision: ReferenceProjectDecisionAction) {
    if (!projectId || !conversation || pending || sessionExpired) return;
    try {
      const selected_features = decision === "PARTIAL_REFERENCE"
        ? review.feature_deltas.filter((delta) => delta.delta !== "USER_ONLY").map((delta) => delta.feature)
        : undefined;
      setConversation(await decideReferenceProject(projectId, conversation.conversation_id, { project_identity: review.project_identity, decision, selected_features }));
    } catch (error) {
      setChatFailure(error, false);
    }
  }

  async function decideReferenceFeature(review: NonNullable<ConversationDetail["research_snapshot"]>["reference_reviews"][number], feature: string, action: "ADD" | "REMOVE" | "MODIFY" | "KEEP" | "REJECT" | "DEFER") {
    if (!projectId || !conversation || pending || sessionExpired) return;
    try {
      setConversation(await decideFeature(projectId, conversation.conversation_id, { project_identity: review.project_identity, feature, action }));
    } catch (error) {
      setChatFailure(error, false);
    }
  }

  async function confirmRequirementProposal(proposal: NonNullable<ConversationDetail["requirement_change_proposal"]>, confirmed: boolean) {
    if (!projectId || !conversation || pending || sessionExpired) return;
    try {
      setConversation(await confirmRequirementChanges(projectId, conversation.conversation_id, proposal.proposal_id, proposal.fingerprint, confirmed));
    } catch (error) {
      setChatFailure(error, false);
    }
  }

  async function decideSourceProposal(decision: "APPROVED" | "REJECTED") {
    if (!projectId || !conversation || approvalPending || sessionExpired) return;
    setApprovalPending(true);
    setChatError(null);
    try {
      setConversation(await decideFirmwareSourceGeneration(projectId, conversation.conversation_id, decision));
    } catch (error) {
      setChatFailure(error, false);
    } finally {
      setApprovalPending(false);
    }
  }

  async function decideSchematicProposal(decision: "APPROVED" | "REJECTED") {
    if (!projectId || !conversation || approvalPending || sessionExpired) return;
    setApprovalPending(true);
    setChatError(null);
    try {
      setConversation(await decideSchematicProduction(projectId, conversation.conversation_id, decision));
    } catch (error) {
      setChatFailure(error, false);
    } finally {
      setApprovalPending(false);
    }
  }

  async function requestSchematicCandidate(binding: { phase_id: string; plan_task_id: string }) {
    if (!projectId || !conversation?.engineering_plan || schematicModelBusy || sessionExpired) return;
    const current = conversation;
    const plan = conversation.engineering_plan;
    setSchematicModelBusy(true);
    setSchematicModelBlocked(null);
    try {
      await requestSchematicModelCandidate(projectId, current.conversation_id, {
        request_id: `request-${globalThis.crypto.randomUUID()}`,
        plan_id: plan.plan_id,
        phase_id: binding.phase_id,
        plan_task_id: binding.plan_task_id,
      });
      setConversation(await fetchConversation(projectId, current.conversation_id));
    } catch (error) {
      if (error instanceof ProjectCatalogRequestError && error.blockingReason) {
        setSchematicModelBlocked({ projectId, conversationId: current.conversation_id, reason: error.blockingReason });
      } else {
        setChatFailure(error, false);
      }
    } finally {
      setSchematicModelBusy(false);
    }
  }

  async function decideSchematicRepair(proposal: SchematicRepairProposal, action: SchematicRepairDecisionAction, comment?: string, editedProposalFingerprint?: string) {
    if (!projectId || !conversation || approvalPending || sessionExpired) return;
    setApprovalPending(true);
    setChatError(null);
    try {
      const artifactFingerprint = conversation.schematic_production?.generation_receipt?.artifact_fingerprint ?? "";
      setConversation(await decideSchematicRepairRequest(projectId, conversation.conversation_id, {
        repair_id: proposal.repair_id,
        proposal_fingerprint: proposal.fingerprint,
        artifact_fingerprint: artifactFingerprint,
        action,
        comment,
        edited_proposal_fingerprint: editedProposalFingerprint ?? null,
      }));
    } catch (error) {
      setChatFailure(error, false);
    } finally {
      setApprovalPending(false);
    }
  }

  const activeProjectIdentity = fixtureIdentity ?? toProjectWorkspaceIdentity(project, conversation, pending);
  const contextProjection = fixtureContextProjection !== undefined ? fixtureContextProjection : toEngineeringContextProjection(activeProjectIdentity, context, runtimePanel);
  const visibleCapabilities = sessionExpired
    ? capabilities.map((item) => ({ ...item, status: "UNAVAILABLE" as const, reason_code: "DESKTOP_SESSION_EXPIRED", detail: "Desktop 会话已过期。" }))
    : capabilities;
  const sessionExpiredMessage = "Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。";
  const selectedFinding = workbenchProjection?.findings.find((item) => item.finding_id === selectedFindingId) ?? null;
  const selectedResource = workbenchProjection?.resources.find((item) => item.resource_id === selectedResourceId) ?? null;
  const workbenchMode = inferWorkbenchMode({ projectId: activeProjectIdentity?.id ?? null, resourceCount: workbenchProjection?.resources.length ?? 0 });
  const inspectorVisible = Boolean(selectedFinding || selectedResource || selectedEvidence);
  const selectResource = (id: string) => {
    const category = workbenchProjection?.resources.find((item) => item.resource_id === id)?.category;
    setActiveActivity(category === "FIRMWARE" ? "code" : category === "HARDWARE" ? "hardware" : category === "DOCUMENT" ? "reports" : "explorer");
    setBuildOpen(false);
    setSelectedResourceId(id);
    setSelectedFindingId(null);
    setSelectedEvidence(null);
    setOpenWorkspaceTabs((tabs) => tabs.includes(id) ? tabs : [...tabs, id]);
    setInspectorMode((current) => current === "hidden" ? "docked" : current);
  };
  const selectFinding = (id: string) => {
    const finding = workbenchProjection?.findings.find((item) => item.finding_id === id) ?? null;
    const target = finding?.target_identity ?? null;
    const resource = target ? workbenchProjection?.resources.find((item) => item.resource_id === target || item.relative_path === target) ?? null : null;
    setSelectedFindingId(id);
    setSelectedEvidence(null);
    setSelectedResourceId(resource?.resource_id ?? null);
    if (resource) setOpenWorkspaceTabs((tabs) => tabs.includes(resource.resource_id) ? tabs : [...tabs, resource.resource_id]);
    setInspectorMode((current) => current === "hidden" ? "docked" : current);
    setBottomOpen(true);
    setBottomTab("verification");
  };
  const selectEvidence = (evidence: ChatEvidence) => {
    setSelectedEvidence(evidence);
    setSelectedFindingId(null);
    setSelectedResourceId(null);
    setInspectorMode((current) => current === "hidden" ? "docked" : current);
  };
  const providerCapability = visibleCapabilities.find((item) => item.capability === "PROVIDER_CONFIGURATION");
  const chatModelCapability = visibleCapabilities.find((item) => item.capability === "CHAT_MODEL");
  const executionCapability = visibleCapabilities.find((item) => item.capability === "HIL_EXECUTION" || item.capability === "CONTROLLED_BUILD");
  const connectionStatus = providerConnectionStatus(chatModelCapability?.status, chatModelCapability?.reason_code ?? providerCapability?.reason_code);
  const topStatuses = [
    { label: "Project", value: activeProjectIdentity ? "AVAILABLE" : "UNAVAILABLE", tone: activeProjectIdentity ? "success" : "muted" },
    { label: "AI", value: connectionStatus, tone: statusTone(connectionStatus) },
    { label: "Execution", value: executionCapability?.status ?? "UNAVAILABLE", tone: statusTone(executionCapability?.status) },
  ];
  const inspectorPanel = inspectorVisible ? <EngineeringInspector finding={selectedFinding} resource={selectedResource} evidence={selectedEvidence} mode={inspectorMode} onModeChange={setInspectorMode} onClose={() => setInspectorMode("hidden")} /> : null;
  const sourceGeneration = conversation?.firmware_source_generation ?? null;
  const pendingSourceProposal = sourceGeneration ? <FirmwareSourceProposalCard generation={sourceGeneration} busy={approvalPending || sessionExpired} onDecision={(decision) => void decideSourceProposal(decision)} /> : null;
  const schematicProduction = conversation?.schematic_production ?? null;
  const pendingSchematicProposal = schematicProduction ? <SchematicProductionProposalCard production={schematicProduction} busy={approvalPending || sessionExpired} onDecision={(decision) => void decideSchematicProposal(decision)} /> : null;
  const { ercOperationId, predesignSource } = circuitReviewAvailability(conversation);
  const approvedPredesignSources = predesignSource && conversation?.requirement_context && conversation.architecture_design
    ? {
      requirements: [...conversation.requirement_context.requirements, ...conversation.requirement_context.constraints]
        .filter((item) => item.status === "CONFIRMED")
        .map((item) => ({ requirement_id: item.requirement_id, label: item.normalized_value })),
      architectureComponents: conversation.architecture_design.system_components
        .map((item) => ({ component_id: item.component_id, label: item.name })),
    } : null;
  const ercReview = activeProjectIdentity && conversation && ercOperationId
    ? <CircuitIntentReviewPanel key={`${activeProjectIdentity.id}:${conversation.conversation_id}:ERC_PUBLICATION`} mode="ERC_PUBLICATION" projectId={activeProjectIdentity.id} conversationId={conversation.conversation_id} ercOperationId={ercOperationId} turns={conversation.turns} projection={desktopProjection} busy={approvalPending || sessionExpired} onOwnerRefresh={async () => { const next = await refreshDesktopProjection(activeProjectIdentity.id, generationRef.current, conversation.conversation_id); if (!next) throw new Error("CIRCUIT_OWNER_REFRESH_UNAVAILABLE"); }} /> : null;
  const predesignReview = activeProjectIdentity && conversation && predesignSource
    ? <CircuitIntentReviewPanel key={`${activeProjectIdentity.id}:${conversation.conversation_id}:PREDESIGN_APPROVAL`} mode="PREDESIGN_APPROVAL" projectId={activeProjectIdentity.id} conversationId={conversation.conversation_id} predesignSourceExpectations={predesignSource} approvedPredesignSources={approvedPredesignSources} turns={conversation.turns} projection={desktopProjection} busy={approvalPending || sessionExpired} onOwnerRefresh={async () => { const next = await refreshDesktopProjection(activeProjectIdentity.id, generationRef.current, conversation.conversation_id); if (!next) throw new Error("CIRCUIT_OWNER_REFRESH_UNAVAILABLE"); }} /> : null;
  const circuitReview = ercReview || predesignReview ? <>{ercReview}{predesignReview}</> : null;
  const ownerGeneration = generationRef.current;
  const wave2Owner = wave2OwnerRef.current;
  const refreshWave2Owner = async () => {
    if (!activeProjectIdentity || !conversation || ownerGeneration !== generationRef.current || wave2Owner !== wave2OwnerRef.current) return;
    const next = await fetchConversation(activeProjectIdentity.id, conversation.conversation_id);
    if (ownerGeneration !== generationRef.current || wave2Owner !== wave2OwnerRef.current) return;
    setConversation(next);
    await refreshDesktopProjection(activeProjectIdentity.id, ownerGeneration, conversation.conversation_id);
  };
  const wave2Preparation = activeProjectIdentity && conversation
    ? <SchematicPreparationPanel key={`${activeProjectIdentity.id}:${conversation.conversation_id}:${conversation.engineering_plan?.fingerprint}:${conversation.hardware_selection?.proposal?.fingerprint}:${conversation.design_truth?.fingerprint}:${conversation.schematic_planning?.proposal?.fingerprint}:${desktopProjection?.projection_fingerprint}`} conversation={conversation} projection={desktopProjection} disabled={approvalPending || sessionExpired} onRefresh={refreshWave2Owner} /> : null;
  const wave2Evidence = activeProjectIdentity && conversation?.schematic_production?.generation_receipt
    ? <SchematicEvidencePanel key={`${activeProjectIdentity.id}:${conversation.conversation_id}:${conversation.schematic_production.generation_receipt.artifact_fingerprint}`} conversation={conversation} projection={desktopProjection} disabled={approvalPending || sessionExpired} onRefresh={refreshWave2Owner} /> : null;
  const hardwareModelControl = activeProjectIdentity && conversation
    ? <><SelectionRevisionPanel key={`${activeProjectIdentity.id}:${conversation.conversation_id}:${conversation.selection_head?.revision_id}:${conversation.selection_head?.decision_fingerprint}`} conversation={conversation} disabled={approvalPending || sessionExpired} onRefresh={refreshWave2Owner} /><HardwareModelCandidatePanel key={`${activeProjectIdentity.id}:${conversation.conversation_id}`} conversation={conversation} disabled={approvalPending || sessionExpired} onRefresh={refreshWave2Owner} /></> : null;
  const verificationHistory = activeProjectIdentity && conversation && !sessionExpired
    ? <SchematicVerificationHistory key={`${activeProjectIdentity.id}:${conversation.conversation_id}`} projectId={activeProjectIdentity.id} conversationId={conversation.conversation_id} /> : null;
  const chatPanel = <CenterWorkspace title={conversation?.title ?? "Conversation"} chatSessionId={sessionId} activeProjectIdentity={activeProjectIdentity} taskProjection={taskProjection} memoryProjection={memoryProjection} workflowProjection={workflowProjection} contextProjection={contextProjection} activeArtifact={selectedResource} updatedAt={conversation?.updated_at ?? null} canExport={!sessionExpired && Boolean(conversation?.turns.some((turn) => turn.status === "COMPLETED"))} onExport={exportConversation} sessionReady={Boolean(activeProjectIdentity && !pending && !sessionExpired)} turns={conversation?.turns ?? []} pendingQuestion={pendingQuestion} streamAnswer={streamAnswer} streamStatus={streamStatus} availableReferences={references} pending={pending} error={sessionExpired ? sessionExpiredMessage : chatError} retryable={chatRetryable && !sessionExpired} selected={selected} evidenceDisplay={acceptanceMode === "workflow" ? "compact" : preferences.evidenceDisplay} onToggleReference={toggleReference} onAttach={(file) => void attach(file)} onSubmit={(value) => void ask(value)} onRetry={() => { if (chatRetryable && conversation && !sessionExpired) void openConversation(conversation.conversation_id); }} onEvidenceSelect={selectEvidence} pendingApproval={pendingSourceProposal || pendingSchematicProposal || circuitReview || verificationHistory || wave2Evidence ? <>{pendingSourceProposal ?? pendingSchematicProposal}{circuitReview}{wave2Evidence}{verificationHistory}</> : null} researchSnapshot={conversation?.research_snapshot ?? null} requirementChangeProposal={conversation?.requirement_change_proposal ?? null} onReferenceDecision={decideReferenceReview} onFeatureDecision={decideReferenceFeature} onConfirmRequirementProposal={confirmRequirementProposal} schematicProduction={schematicProduction} onSchematicRepairDecision={(proposal, action, comment, editedProposalFingerprint) => void decideSchematicRepair(proposal, action, comment, editedProposalFingerprint)} />;
  const openBuildWorkspace = () => { setActiveActivity("code"); setBuildOpen(true); };
  const workspacePanel = <EngineeringWorkspace projectName={activeProjectIdentity?.name ?? null} resources={workbenchProjection?.resources ?? []} activeResourceId={selectedResourceId} openTabIds={openWorkspaceTabs} onResource={selectResource} activeActivity={activeActivity} capabilities={visibleCapabilities} resourceState={resourceLoadState} onBuild={openBuildWorkspace} />;
  const buildPanel = <section aria-label="受控 Firmware Build 工作区"><FirmwareBuildPanel key={`${activeProjectIdentity?.id ?? ""}:${conversation?.conversation_id ?? ""}`} projectId={activeProjectIdentity?.id ?? null} conversationId={conversation?.conversation_id ?? null} desktopProjection={desktopProjection} creationMode={project?.creation_mode ?? null} projectName={project?.name ?? null} /></section>;
  const centerSurface = buildOpen ? buildPanel : activeActivity === "copilot" || activeActivity === "settings" ? chatPanel : workspacePanel;
  const schematicBinding = conversation?.engineering_plan?.status === "APPROVED"
    && conversation.engineering_plan_review?.status === "APPROVED"
    && conversation.hardware_selection?.status === "APPROVED"
    ? conversation.engineering_plan.model_operation_bindings?.find(
      (item) => item.operation_id === "SCHEMATIC_DESIGN_PROPOSAL" && item.stage_id === "SCHEMATIC_DESIGN",
    ) : undefined;
  const visibleSchematicBlock = schematicModelBlocked?.projectId === projectId
    && schematicModelBlocked.conversationId === conversation?.conversation_id
    ? schematicModelBlocked.reason : null;
  const schematicBlockText: Record<string, string> = {
    POWER_SOURCE_UNRESOLVED: "项目供电来源与连接关系未批准，原理图候选已阻断。",
    POWER_NET_INCOMPLETE: "供电网络信息不完整，原理图候选已阻断。",
    INTERFACE_PROTOCOL_UNRESOLVED: "接口协议尚未确定，原理图候选已阻断。",
    PIN_BINDING_UNRESOLVED: "必要引脚尚未指派，原理图候选已阻断。",
  };
  const schematicModelControl = schematicBinding && conversation && projectId
    ? <section aria-label="原理图模型候选">
        <button type="button" className="ide-secondary-button" disabled={schematicModelBusy || sessionExpired}
          onClick={() => void requestSchematicCandidate(schematicBinding)}>请求原理图候选</button>
        {visibleSchematicBlock && <p role="status">{schematicBlockText[visibleSchematicBlock]}</p>}
      </section> : null;
  const contextPanel = <>{project?.creation_mode === "ATTACH_EXISTING" && !acceptanceMode && <button className="ide-secondary-button" type="button" onClick={() => setEdaImportOpen(true)} disabled={sessionExpired}>只读 EDA 导入</button>}{hardwareModelControl}{wave2Preparation}{schematicModelControl}<EngineeringContextPanel identity={activeProjectIdentity} project={project} context={context} projection={contextProjection} memoryProjection={memoryProjection} resources={workbenchProjection?.resources ?? []} workflow={workflowProjection} engineeringWorkflow={conversation?.engineering_workflow ?? null} requirement={conversation?.requirement_context ?? null} plan={conversation?.engineering_plan ?? null} architecture={conversation?.architecture_design ?? null} designTruth={conversation?.design_truth ?? null} engineeringKnowledgeModel={conversation?.engineering_knowledge_model ?? null} teachingContext={conversation?.teaching_context ?? null} artifactIntelligence={artifactIntelligence} hardwareDesignSpec={conversation?.hardware_design_spec ?? null} firmwareSpec={conversation?.firmware_spec ?? null} simulationSpec={conversation?.simulation_spec ?? null} verificationSpec={conversation?.verification_spec ?? null} implementationSpec={conversation?.implementation_spec ?? null} capabilityEvaluation={conversation?.capability_evaluation ?? null} executionAuthorization={conversation?.execution_authorization ?? null} schematicProduction={conversation?.schematic_production ?? null} authorizationDecisionBusy={approvalPending || sessionExpired} onAuthorizationDecision={(requestId, decision) => void decideAuthorizationRequest(requestId, decision)} onSchematicDecision={(decision) => void decideSchematicProposal(decision)} selectedDetails={inspectorPanel} /></>;
  const top = <TopNavigation projectName={activeProjectIdentity?.name} statuses={topStatuses} onToggleLeft={() => setLeftOpen((value) => !value)} onToggleRight={() => setRightOpen((value) => !value)} onVerify={() => { setActiveActivity("test"); setBuildOpen(false); setBottomOpen(true); setBottomTab("verification"); }} onBuild={openBuildWorkspace} />;
  return <ExecutionEventProvider projectId={activeProjectIdentity?.id ?? null} enabled={Boolean(sourceGeneration?.operation_id || schematicProduction?.operation_id) || visibleCapabilities.some((item) => item.capability === "CONTROLLED_BUILD" && item.status === "AVAILABLE")}><>
    {sessionExpired && <p className="ide-form-error" role="alert">{sessionExpiredMessage}</p>}
    <DesktopShell
      density={preferences.density}
      theme={resolvedTheme}
      top={top}
      left={<WorkbenchExplorer projects={navigationProjects} activeProjectId={projectId} resources={workbenchProjection?.resources ?? []} activeResourceId={selectedResourceId} resourceState={!projectId ? "NO_PROJECT" : !project ? resourceLoadState : project.location_id ? resourceLoadState : "NO_LOCATION"} resourceError={resourceLoadError} conversations={conversations} activeConversationId={conversation?.conversation_id ?? null} onProjectChange={(id) => void activateProject(id)} onResource={selectResource} onNewProject={() => { if (!acceptanceMode && !sessionExpired) setCreateOpen(true); }} onNewConversation={() => void newConversation()} onConversationChange={(id) => void openConversation(id)} onDeleteConversation={(id) => void deleteConversation(id)} onRemoveProject={(id) => void removeProject(id)} onRetry={() => { if (projectId && !sessionExpired) { const generation = ++generationRef.current; void refreshDesktopProjection(projectId, generation, conversation?.conversation_id ?? null, Boolean(project?.location_id)); } }} catalogError={catalogError} />}
      center={centerSurface}
      right={contextPanel}
       activity={<ActivityBar active={activeActivity} onChange={(value) => { if (value === "settings") { activityBeforeSettingsRef.current = activeActivity; setSettingsOpen(true); } setActiveActivity(value); setBuildOpen(false); if (value === "test") { setBottomOpen(true); setBottomTab("verification"); } }} />}
        context={contextPanel}
       bottom={<EngineeringBottomPanel projection={desktopProjection} findings={workbenchProjection?.findings ?? []} activeTab={bottomTab} onTab={(value) => { setBottomTab(value); setBottomOpen(true); }} collapsed={!bottomOpen} />}
       mode={bottomOpen ? "engineering" : workbenchMode}
       inspectorMode={inspectorMode}
       inspectorVisible={inspectorVisible}
       onInspectorModeChange={setInspectorMode}
       layoutKey={projectLayoutKey(activeProjectIdentity?.id)}
       bottomOpen={bottomOpen}
       onBottomOpenChange={setBottomOpen}
      leftOpen={leftOpen}
      rightOpen={rightOpen}
      onCloseLeft={() => setLeftOpen(false)}
      onCloseRight={() => setRightOpen(false)}
    />
    {(settingsOpen || createOpen) && <div className={`ide-overlay-root ide-overlay-root--theme-${resolvedTheme}`}>{settingsOpen && <SettingsPanel preferences={preferences} onChange={setPreferences} onClose={() => { setSettingsOpen(false); setActiveActivity(activityBeforeSettingsRef.current); if (projectId && !sessionExpired) void refreshDesktopProjection(projectId, generationRef.current, conversation?.conversation_id ?? null); }} researchMode={researchMode} onResearchModeChange={setResearchMode} capabilities={visibleCapabilities} modelGatewayFetcher={fetch} canonicalProviderConnections projectId={projectId || undefined} projectName={project?.name ?? undefined} />}{createOpen && <ProjectCreatePanel dataSource={acceptanceMode === "project-create" ? projectCreationFixtures?.projectCreationWorkspaceFixture.dataSource : acceptanceMode === "project-create-parent" ? projectCreationFixtures?.projectParentCreationWorkspaceFixture.dataSource : undefined} onClose={() => setCreateOpen(false)} onCreated={projectCreated} />}</div>}
    {edaImportOpen && project?.creation_mode === "ATTACH_EXISTING" && !acceptanceMode && <div className={`ide-overlay-root ide-overlay-root--theme-${resolvedTheme}`}><EdaImportPanel key={project.project_id} projectId={project.project_id} onClose={() => setEdaImportOpen(false)} reviewerId={conversation?.project_id === project.project_id && conversation.tenant_id === project.tenant_id ? conversation.requester : undefined} reviewerScope={conversation?.project_id === project.project_id && conversation.tenant_id === project.tenant_id ? conversation.fingerprint : undefined} /></div>}
  </></ExecutionEventProvider>;
}
