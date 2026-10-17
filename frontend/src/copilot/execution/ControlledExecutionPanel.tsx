import { useState } from "react";

import { cancelExecution, ExecutionRequestError, fetchExecutionStatus, projectExecutionEvent, streamExecutionEvents, type ExecutionOperationStatus } from "../api/executionClient";
import type { CapabilityStatusView } from "../api/capabilityClient";
import { useDesktopSessionState } from "../api/sessionState";

const phases = [
  ["工作区已应用", "workspace"],
  ["Build Pending Approval", "BUILD"],
  ["Build Complete", "BUILD"],
  ["Test Pending Approval", "TEST"],
  ["Test Complete", "TEST"],
  ["Flash Pending Approval", "FLASH"],
  ["Flash Complete", "FLASH"],
  ["UART/HIL Pending Approval", "UART_OBSERVATION/HIL"],
] as const;

export function ControlledExecutionPanel({ projectId, capabilities = [] }: { projectId: string | null; capabilities?: CapabilityStatusView[] }) {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const [operationId, setOperationId] = useState("");
  const [status, setStatus] = useState<ExecutionOperationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [eventCursor, setEventCursor] = useState<string | null>(null);
  const [latestEvent, setLatestEvent] = useState<string | null>(null);

  async function refresh() {
    if (sessionExpired || !projectId || !operationId.trim()) return;
    setBusy(true); setError(null);
    try {
      let projected = await fetchExecutionStatus(projectId, operationId);
      setStatus(projected);
      let resyncRequired = false;
      const nextCursor = await streamExecutionEvents(projectId, eventCursor, (event) => {
        if (event.event_type === "RESYNC_REQUIRED") {
          resyncRequired = true;
          setError("EXECUTION_RESYNC_REQUIRED");
          return;
        }
        if (event.operation_id !== operationId) return;
        projected = projectExecutionEvent(projected, event, operationId);
        setLatestEvent(event.event_type);
        setStatus(projected);
      });
      setEventCursor(resyncRequired ? null : nextCursor);
    }
    catch (reason) { setError(reason instanceof ExecutionRequestError ? reason.code : "EXECUTION_UNAVAILABLE"); }
    finally { setBusy(false); }
  }

  async function cancel() {
    if (sessionExpired || !projectId || !operationId.trim()) return;
    setBusy(true); setError(null);
    try { setStatus(await cancelExecution(projectId, operationId, "operator requested cancellation")); }
    catch (reason) { setError(reason instanceof ExecutionRequestError ? reason.code : "CANCELLATION_REJECTED"); }
    finally { setBusy(false); }
  }

  const capabilityByOperation = new Map([
    ["BUILD", "CONTROLLED_BUILD"],
    ["TEST", "CONTROLLED_TEST"],
    ["FLASH", "CONTROLLED_FLASH"],
    ["UART_OBSERVATION", "UART_OBSERVATION"],
    ["HIL", "HIL_EXECUTION"],
  ]);
  const capability = (operationType: string) => capabilities.find((item) => item.capability === capabilityByOperation.get(operationType));
  const phaseValue = (key: string) => {
    if (status && (status.operation_type === key || key.includes(status.operation_type))) return status.status;
    if (key === "workspace") return "PENDING";
    const operationType = key === "UART_OBSERVATION/HIL" ? "UART_OBSERVATION" : key;
    return capability(operationType)?.status ?? "UNAVAILABLE";
  };

  return <section className="ide-document-section" aria-label="Controlled execution">
    <div className="ide-document-section__heading"><h3>Controlled execution</h3><span className="status-pill status-pending">Explicit approval</span></div>
    <p className="muted">Build、Test、Flash、UART 和 HIL 使用独立 Operation、Approval 与 Receipt；不会自动串联。</p>
    <p className="ide-context-unavailable">Hardware Golden Deferred：当前环境未配置可信工具链或已验证目标；执行能力由 backend capability 状态决定。</p>
    {sessionExpired && <p className="ide-context-unavailable" role="alert">Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。</p>}
    <label htmlFor="execution-operation-id">Operation ID</label>
    <div className="load-controls"><input id="execution-operation-id" value={operationId} onChange={(event) => setOperationId(event.target.value)} placeholder="Paste persisted operation ID" maxLength={160} /><button type="button" disabled={busy || sessionExpired || !projectId || !operationId.trim()} onClick={() => void refresh()}>Refresh</button></div>
    {status && <div className="ide-context-count"><span>{status.operation_type} · {status.status}</span><strong title={status.safe_detail}>{status.recovery_state}</strong></div>}
    {latestEvent && <p className="muted">Latest execution event: {latestEvent}</p>}
    {status?.status === "RUNNING" && <button type="button" disabled={busy || sessionExpired} onClick={() => void cancel()}>Request cancellation</button>}
    {error && <p className="ide-context-unavailable">Execution status unavailable: {error}</p>}
    <div className="execution-phase-list">{phases.map(([label, key]) => <div className="ide-context-count" key={label}><span>{label}</span><strong>{phaseValue(key)}</strong></div>)}</div>
  </section>;
}
