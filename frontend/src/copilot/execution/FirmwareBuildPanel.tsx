import { useEffect, useRef, useState } from "react";

import { approveExecution, fetchExecutionStatus, type ExecutionOperationStatus } from "../api/executionClient";
import { confirmFirmwareBuild, fetchFirmwareBuildContext, fetchFirmwareBuildReceipt, reviewFirmwareBuildProposal, type FirmwareBuildContext, type FirmwareBuildReceipt } from "../api/firmwareBuildClient";
import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";
import type { ProjectCreationMode } from "../api/projectClient";
import { useDesktopSessionState } from "../api/sessionState";
import { confirmBrownfieldFirmwareBuild, fetchBrownfieldFirmwareBuildContext, reviewBrownfieldFirmwareBuildProposal } from "../api/brownfieldBuildClient";
import { BrownfieldBuildWizard } from "./BrownfieldBuildWizard";

type Props = {
  projectId: string | null;
  conversationId: string | null;
  desktopProjection: DesktopWorkspaceProjection | null;
  creationMode: ProjectCreationMode | null;
  projectName: string | null;
};

const terminalStatuses = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]);

function displayIdentity(value: string): string {
  return value.length > 36 ? `${value.slice(0, 18)}…${value.slice(-8)}` : value;
}

function completeContext(context: FirmwareBuildContext | null): context is FirmwareBuildContext & { context_version: string } {
  return Boolean(context && context.status === "READY" && context.context_version && context.project && context.target && context.source_revision && context.configuration && context.toolchain && context.plan && context.proposal);
}

function errorCode(error: unknown): string {
  return error instanceof Error ? error.message : "FIRMWARE_BUILD_UNAVAILABLE";
}

function contextGuidance(code: string, brownfieldEligible: boolean): string {
  switch (code) {
    case "PROJECT_REQUIRED": return "请先选择项目，再查看构建上下文。";
    case "CONVERSATION_REQUIRED": return brownfieldEligible ? "当前项目缺少已批准的构建上下文。请检查工程上下文；已有工程可通过“已有工程首次构建”准备并批准 Build Plan。" : "当前项目缺少已批准的构建上下文。请先在工程上下文中准备并批准 Build Plan。";
    case "APPROVED_PLAN_MISSING": return brownfieldEligible ? "当前项目尚无已批准的 Build Plan。请打开“已有工程首次构建”审查并批准计划，然后刷新预览。" : "当前项目尚无已批准的 Build Plan。请先在工程上下文中准备并批准计划。";
    case "PROJECT_CATALOG_FORBIDDEN": return "当前身份无权访问该项目。请切换到有权限的项目，或联系项目管理员授权。";
    case "DESKTOP_SESSION_EXPIRED": return "桌面会话已过期。请重新登录后重试。";
    case "PROPOSAL_REQUIRED": return "请审查并准备 Proposal，然后再确认构建任务。";
    case "FIRMWARE_BUILD_UNAVAILABLE":
    case "FIRMWARE_BUILD_REJECTED":
    case "FIRMWARE_BUILD_RESPONSE_INVALID": return "暂时无法读取构建上下文。请检查服务连接后重试。";
    default: return "当前构建上下文不可用。请刷新预览；如果仍受阻，请查看技术详情。";
  }
}

function resultLabel(status: ExecutionOperationStatus | null, receipt: FirmwareBuildReceipt | null): string {
  if (!status) return "未开始或尚未选择历史操作";
  if (status.recovery_state !== "NONE" || status.status === "RECOVERY_REQUIRED") return `需要人工检查 · ${status.recovery_state}`;
  switch (status.status) {
    case "CREATED":
    case "WAIT_APPROVAL": return "等待 Operation Approval";
    case "QUEUED": return "排队中";
    case "RUNNING": return "构建中";
    case "CANCEL_REQUESTED":
    case "CANCEL_PENDING": return "取消处理中";
    case "CANCELLED": return "已取消";
    case "TIMED_OUT": return "已中断或超时";
    case "FAILED": return "构建失败";
    case "SUCCEEDED": return status.receipt_fingerprint && receipt?.fingerprint && status.receipt_fingerprint === receipt.fingerprint && receipt.status === "SUCCEEDED" && receipt.tool_execution_status === "SUCCEEDED" && receipt.source_revision && receipt.build_evidence?.tool_observed && receipt.build_evidence.result_status === "BUILD_PASS" && receipt.build_evidence.elf_verified && receipt.evidence_references.length > 0 && receipt.artifact && receipt.build_evidence.output_artifact_ids.includes(receipt.artifact.artifact_id) ? "历史构建成功，产物当前可用性未验证" : "操作完成；构建证据缺失或不可用";
    default: return status.status;
  }
}

export function FirmwareBuildPanel({ projectId, conversationId, desktopProjection, creationMode, projectName }: Props) {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const operationRead = useRef({ projectId, sequence: 0 });
  if (operationRead.current.projectId !== projectId) operationRead.current = { projectId, sequence: operationRead.current.sequence + 1 };
  const isCurrent = (scope: string, sequence: number) => operationRead.current.projectId === scope && operationRead.current.sequence === sequence;
  const [context, setContext] = useState<FirmwareBuildContext | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);
  const [status, setStatus] = useState<ExecutionOperationStatus | null>(null);
  const [receipt, setReceipt] = useState<FirmwareBuildReceipt | null>(null);
  const [receiptError, setReceiptError] = useState<string | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [approvalContext, setApprovalContext] = useState<FirmwareBuildContext | null>(null);
  const [showBrownfieldWizard, setShowBrownfieldWizard] = useState(false);
  const [brownfieldMode, setBrownfieldMode] = useState(!conversationId);

  useEffect(() => {
    let active = true;
    setBusy(false);
    setContext(null);
    setContextError(null);
    setTaskId(null);
    setOperationId(null);
    setStatus(null);
    setReceipt(null);
    setReceiptError(null);
    setOperationError(null);
    setApprovalContext(null);
    setShowBrownfieldWizard(false);
    setBrownfieldMode(!conversationId);
    if (projectId && !sessionExpired) {
      const read = conversationId ? fetchFirmwareBuildContext(projectId, conversationId) : fetchBrownfieldFirmwareBuildContext(projectId);
      void read.then((value) => {
        if (active) setContext(value);
      }).catch((error) => {
        if (active) setContextError(errorCode(error));
      });
    }
    return () => { active = false; };
  }, [projectId, conversationId, sessionExpired]);

  async function refreshContext() {
    if (!projectId || sessionExpired || busy) return;
    const sequence = operationRead.current.sequence;
    setBusy(true);
    setContextError(null);
    try {
      const next = brownfieldMode ? await fetchBrownfieldFirmwareBuildContext(projectId)
        : conversationId ? await fetchFirmwareBuildContext(projectId, conversationId) : null;
      if (!isCurrent(projectId, sequence)) return;
      if (!next) throw new Error("FIRMWARE_BUILD_CONTEXT_REQUIRED");
      setContext(next);
      if (approvalContext?.context_version !== next.context_version) setApprovalContext(null);
    } catch (error) { if (isCurrent(projectId, sequence)) { setContextError(errorCode(error)); setApprovalContext(null); } }
    finally { if (isCurrent(projectId, sequence)) setBusy(false); }
  }

  async function onBrownfieldPlanApproved() {
    if (!projectId || sessionExpired) return;
    const sequence = operationRead.current.sequence;
    if (!isCurrent(projectId, sequence)) return;
    setBrownfieldMode(true);
    setContext(null);
    setApprovalContext(null);
    setContextError(null);
    try { const next = await fetchBrownfieldFirmwareBuildContext(projectId); if (isCurrent(projectId, sequence)) setContext(next); }
    catch (error) { if (isCurrent(projectId, sequence)) setContextError(errorCode(error)); }
  }

  async function reviewProposal() {
    if (!projectId || !context?.context_version || sessionExpired || busy) return;
    const sequence = operationRead.current.sequence;
    setBusy(true);
    setContextError(null);
    try {
      const next = brownfieldMode ? await reviewBrownfieldFirmwareBuildProposal(projectId, context.context_version)
        : conversationId ? await reviewFirmwareBuildProposal(projectId, conversationId, context.context_version) : null;
      if (!isCurrent(projectId, sequence)) return;
      if (!next) throw new Error("FIRMWARE_BUILD_CONTEXT_REQUIRED");
      setContext(next);
      setApprovalContext(null);
    } catch (error) { if (isCurrent(projectId, sequence)) setContextError(errorCode(error)); }
    finally { if (isCurrent(projectId, sequence)) setBusy(false); }
  }

  async function confirmTask() {
    if (!projectId || !completeContext(context) || sessionExpired || busy) return;
    let sequence = operationRead.current.sequence;
    setBusy(true);
    setContextError(null);
    try {
      const confirmed = brownfieldMode ? await confirmBrownfieldFirmwareBuild(projectId, context.context_version)
        : conversationId ? await confirmFirmwareBuild(projectId, conversationId, context.context_version) : null;
      if (operationRead.current.projectId !== projectId || operationRead.current.sequence !== sequence) return;
      if (!confirmed) throw new Error("FIRMWARE_BUILD_CONTEXT_REQUIRED");
      if (!confirmed.operation_id || !confirmed.task_id || confirmed.status !== "CONFIRMED") {
        setContextError(confirmed.message || "FIRMWARE_BUILD_CONFIRMATION_BLOCKED");
        return;
      }
      setApprovalContext(context);
      const pending = loadOperation(confirmed.operation_id, projectId);
      sequence = operationRead.current.sequence;
      await pending;
      if (operationRead.current.projectId === projectId && operationRead.current.sequence === sequence) setTaskId(confirmed.task_id);
    } catch (error) { if (operationRead.current.projectId === projectId && operationRead.current.sequence === sequence) setContextError(errorCode(error)); }
    finally { if (operationRead.current.projectId === projectId && operationRead.current.sequence === sequence) setBusy(false); }
  }

  async function loadOperation(id: string, scope: string) {
    const sequence = ++operationRead.current.sequence;
    const current = () => operationRead.current.projectId === scope && operationRead.current.sequence === sequence;
    setOperationId(id);
    setTaskId(null);
    setStatus(null);
    setReceipt(null);
    setReceiptError(null);
    setOperationError(null);
    try {
      const next = await fetchExecutionStatus(scope, id);
      if (!current()) return;
      if (next.project_id !== scope || next.operation_id !== id || next.operation_type !== "BUILD") throw new Error("BUILD_OPERATION_SCOPE_MISMATCH");
      setStatus(next);
      setTaskId(next.correlation?.project_id === scope && (next.correlation.operation_id === null || next.correlation.operation_id === id)
        ? next.correlation.task_id : null);
      if (terminalStatuses.has(next.status)) {
        try {
          const value = await fetchFirmwareBuildReceipt(scope, id);
          if (!current()) return;
          if (value.operation_id !== id || value.project_id !== scope || value.operation_type !== "BUILD") throw new Error("BUILD_RECEIPT_SCOPE_MISMATCH");
          setReceipt(value);
        } catch (error) { if (current()) setReceiptError(errorCode(error)); }
      }
    } catch (error) { if (current()) setOperationError(errorCode(error)); }
  }

  async function selectHistorical(id: string) {
    if (!projectId || sessionExpired || busy) return;
    setApprovalContext(null);
    setTaskId(null);
    setBusy(true);
    const pending = loadOperation(id, projectId);
    const sequence = operationRead.current.sequence;
    try { await pending; }
    finally { if (operationRead.current.projectId === projectId && operationRead.current.sequence === sequence) setBusy(false); }
  }

  async function approveOperation() {
    if (!projectId || !operationId || !status || !approvalContext || !completeContext(approvalContext) || !desktopProjection || sessionExpired || busy) return;
    if (status.status !== "WAIT_APPROVAL" || status.operation_type !== "BUILD" || desktopProjection.identity.project_id !== projectId || !desktopProjection.identity.requester_id) return;
    let sequence = operationRead.current.sequence;
    setBusy(true);
    setOperationError(null);
    try {
      const current = brownfieldMode ? await fetchBrownfieldFirmwareBuildContext(projectId)
        : conversationId ? await fetchFirmwareBuildContext(projectId, conversationId) : null;
      if (!isCurrent(projectId, sequence)) return;
      if (!current) throw new Error("FIRMWARE_BUILD_CONTEXT_REQUIRED");
      setContext(current);
      if (!completeContext(current) || current.context_version !== approvalContext.context_version || current.proposal !== approvalContext.proposal) {
        setApprovalContext(null);
        throw new Error("FIRMWARE_BUILD_CONTEXT_CHANGED");
      }
      const latest = await fetchExecutionStatus(projectId, operationId);
      if (!isCurrent(projectId, sequence)) return;
      if (latest.operation_id !== operationId || latest.project_id !== projectId || latest.operation_type !== "BUILD" || latest.status !== "WAIT_APPROVAL") throw new Error("BUILD_OPERATION_NOT_APPROVABLE");
      await approveExecution(projectId, operationId, desktopProjection.identity.requester_id, crypto.randomUUID(), new Date(Date.now() + 5 * 60_000).toISOString());
      if (!isCurrent(projectId, sequence)) return;
      const pending = loadOperation(operationId, projectId);
      sequence = operationRead.current.sequence;
      await pending;
    } catch (error) { if (isCurrent(projectId, sequence)) setOperationError(errorCode(error)); }
    finally { if (isCurrent(projectId, sequence)) setBusy(false); }
  }

  const history = desktopProjection?.identity.project_id === projectId ? desktopProjection.operations.items : [];
  const brownfieldEligible = creationMode === "ATTACH_EXISTING";
  const blocked = !projectId || sessionExpired || !context || context.status !== "READY";
  const contextIssue = sessionExpired ? "DESKTOP_SESSION_EXPIRED" : !projectId ? "PROJECT_REQUIRED" : contextError ?? (context?.status !== "READY" ? context?.reason_code ?? null : null);
  const canApprove = Boolean(approvalContext && completeContext(approvalContext) && status?.status === "WAIT_APPROVAL" && desktopProjection?.identity.project_id === projectId && desktopProjection.identity.requester_id && !sessionExpired);
  const artifact = receipt?.artifact;
  const sourceRelationship = receipt?.source_revision && context?.source_revision
    ? receipt.source_revision !== context.source_revision
      ? "旧源码版本"
      : artifact?.workspace_fingerprint && context.workspace_fingerprint
        ? artifact.workspace_fingerprint === context.workspace_fingerprint
          ? "源码文件版本与 Workspace 指纹相同（仅核对这两项）"
          : "源码文件版本相同，Workspace 已变化"
        : "源码文件版本相同，Workspace 当前性未验证"
    : "当前性未验证";

  return <section className="ide-document-section" aria-label="Firmware Build">
    <div className="ide-document-section__heading"><h3>Firmware Build</h3><span className="status-pill status-pending">受控构建</span></div>
    <p className="muted">Context Preview 为只读。Proposal 审查、Task Confirmation 和 Operation Approval 分别确认；构建结果不代表硬件验证。</p>
    <button type="button" disabled={!projectId || sessionExpired || !brownfieldEligible} onClick={() => setShowBrownfieldWizard((open) => !open)}>{showBrownfieldWizard ? "收起已有工程首次构建" : "已有工程首次构建"}</button>
    {projectId && creationMode === "CREATE_NEW" && <p className="muted">仅附加已有工程的项目可使用“已有工程首次构建”；当前是新建项目，请先完成相应工程上下文与 Build Plan。</p>}
    {showBrownfieldWizard && <BrownfieldBuildWizard projectId={projectId} onApproved={() => void onBrownfieldPlanApproved()} />}
    <h4>Context Preview</h4>
    {projectId && !sessionExpired && !context && !contextError && <p role="status">正在读取构建上下文…</p>}
    {contextIssue && <><p className="ide-context-unavailable" role="status">{blocked ? "BLOCKED" : "刷新失败"} · {contextGuidance(contextIssue, brownfieldEligible)}</p><details><summary>技术详情</summary><code>{contextIssue}</code></details></>}
    {context && <dl>
      <div><dt>项目</dt><dd>{context.project ?? projectName ?? (projectId ? "已选择（名称未返回）" : "未选择")}</dd></div>
      <div><dt>Workspace</dt><dd>{desktopProjection?.identity.project_id === projectId ? desktopProjection.project?.relative_path ?? "未提供" : "未提供"}</dd></div>
      <div><dt>Target</dt><dd>{context.target ?? "缺失"}</dd></div>
      <div><dt>Source Revision</dt><dd title={context.source_revision ?? undefined}>{context.source_revision ? displayIdentity(context.source_revision) : "缺失"}</dd></div>
      <div><dt>Build Configuration</dt><dd>{context.configuration ?? "缺失"}</dd></div>
      <div><dt>Toolchain</dt><dd>{context.toolchain ?? "缺失"}</dd></div>
      <div><dt>Approved Plan</dt><dd>{context.plan ?? "缺失"}</dd></div>
      <div><dt>Proposal</dt><dd>{context.proposal ?? "待审查"}</dd></div>
    </dl>}
    <button type="button" disabled={busy || sessionExpired || !projectId} onClick={() => void refreshContext()}>刷新 Context Preview</button>
    <h4>Proposal 审查</h4>
    <button type="button" disabled={busy || sessionExpired || !projectId || !context?.context_version} onClick={() => void reviewProposal()}>审查并准备 Proposal</button>
    <h4>Task Confirmation</h4>
    <button type="button" disabled={busy || sessionExpired || !completeContext(context)} onClick={() => void confirmTask()}>确认 Firmware Build 任务</button>
    {operationId && <p>Task：{taskId ?? "UNKNOWN"}</p>}
    <h4>Operation Approval</h4>
    {operationId && <p>Operation：{operationId}</p>}
    {approvalContext && <dl><div><dt>操作</dt><dd>BUILD</dd></div><div><dt>项目</dt><dd>{approvalContext.project}</dd></div><div><dt>Target</dt><dd>{approvalContext.target}</dd></div><div><dt>源码</dt><dd title={approvalContext.source_revision ?? undefined}>{approvalContext.source_revision ? displayIdentity(approvalContext.source_revision) : "缺失"}</dd></div><div><dt>配置</dt><dd>{approvalContext.configuration}</dd></div><div><dt>工具链</dt><dd>{approvalContext.toolchain}</dd></div></dl>}
    {status && <p>状态：{resultLabel(status, receipt)} · {status.status}</p>}
    {status?.recovery_state && status.recovery_state !== "NONE" && <p role="alert">恢复状态：{status.recovery_state}；请人工检查，页面不会自动重试。</p>}
    {operationId && !approvalContext && status?.status === "WAIT_APPROVAL" && <p>历史待审批操作缺少可核对的上下文绑定，保持只读。</p>}
    <button type="button" disabled={busy || !canApprove} onClick={() => void approveOperation()}>批准当前 Build Operation</button>
    {operationId && <button type="button" disabled={busy || sessionExpired || !projectId} onClick={() => { if (projectId) void loadOperation(operationId, projectId); }}>刷新 Operation / Receipt</button>}
    {operationError && <p role="alert">Operation request: {operationError}</p>}
    <h4>Receipt / Evidence</h4>
    {receiptError && <p role="alert">证据缺失或不可用：{receiptError}</p>}
    {receipt && <div>
      <p>Receipt：{receipt.status} · {receipt.tool_execution_status}</p>
      <p>Source Revision：{receipt.source_revision ? displayIdentity(receipt.source_revision) : "不可用"} · {sourceRelationship}</p>
      <p>Workspace Fingerprint：{artifact?.workspace_fingerprint ? displayIdentity(artifact.workspace_fingerprint) : "不可用"}</p>
      <p>Artifact：{artifact?.artifact_id ?? "不可用"}</p>
      <p>Evidence：{receipt.evidence_references.length ? receipt.evidence_references.join(", ") : "缺失"}</p>
      <p>Compiler：{receipt.build_evidence?.compiler_exit_code ?? "未记录"} · Linker：{receipt.build_evidence?.linker_exit_code ?? "未记录"}</p>
      <p>Flash：{receipt.flash_verification_status} · Device：{receipt.target_runtime_verification_status}</p>
    </div>}
    <h4>历史操作</h4>
    {history.length === 0 ? <p>暂无可恢复操作。</p> : <ul>{history.map((item) => <li key={item.reference_id}><button type="button" disabled={busy || sessionExpired || !projectId} onClick={() => void selectHistorical(item.reference_id)}>{item.summary ?? item.reference_id}</button> · {item.status ?? "UNKNOWN"}</li>)}</ul>}
  </section>;
}
