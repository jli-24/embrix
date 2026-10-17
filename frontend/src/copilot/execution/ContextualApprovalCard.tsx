import { AlertTriangle, Check, X } from "lucide-react";
import { useState } from "react";
import { Message } from "../../design-system/components";

export type ContextualApprovalOperation = {
  operation_id: string;
  action: string;
  target: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | string;
  reason: string;
  summary: string;
  approval_scope?: string;
  safety_limits?: string[];
  changes?: string[];
};

type Props = {
  operation: ContextualApprovalOperation;
  onApprove(operation: ContextualApprovalOperation): Promise<unknown> | unknown;
  onCancel(operation: ContextualApprovalOperation): Promise<unknown> | unknown;
};

function approvalActionLabel(action: string) {
  const normalized = action.trim().toUpperCase();
  if (normalized === "FLASH") return "烧录";
  if (normalized === "BUILD") return "构建";
  if (normalized === "TEST" || normalized === "VERIFY") return "验证";
  if (normalized === "RUN") return "运行";
  return "这项工程操作";
}

/**
 * A projection-only approval card. It never creates or executes an operation;
 * callbacks must be wired to the canonical approval/execution runtime.
 */
export function ContextualApprovalCard({ operation, onApprove, onCancel }: Props) {
  const [state, setState] = useState<"PENDING" | "APPROVED" | "CANCELLED">("PENDING");
  const [busy, setBusy] = useState(false);
  const transition = async (next: "APPROVED" | "CANCELLED", action: () => Promise<unknown> | unknown) => {
    if (busy || state !== "PENDING") return;
    setBusy(true);
    try {
      await action();
      setState(next);
    } finally {
      setBusy(false);
    }
  };
  return <section className={`ide-pending-approval ide-pending-approval--${operation.risk_level.toLowerCase()}`} aria-label="待处理审批" data-operation-id={operation.operation_id} data-approval-state={state}>
    <div className="ide-pending-approval__heading"><AlertTriangle size={15} aria-hidden="true" /><strong>高风险操作</strong><span>{operation.risk_level}</span></div>
    {state === "PENDING" ? <>
      <Message variant="assistant" label="Embrix"><p className="ide-pending-approval__conversation">我已经准备好执行{approvalActionLabel(operation.action)}，请确认。</p></Message>
      <p className="ide-pending-approval__summary">{operation.summary}</p>
      <dl><div><dt>动作</dt><dd>{operation.action}</dd></div><div><dt>目标</dt><dd>{operation.target}</dd></div><div><dt>原因</dt><dd>{operation.reason}</dd></div></dl>
      {operation.changes?.length ? <details><summary>查看变更</summary><ul>{operation.changes.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
      {operation.safety_limits?.length ? <details><summary>安全限制</summary><ul>{operation.safety_limits.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
      <footer><button type="button" disabled={busy} onClick={() => void transition("CANCELLED", () => onCancel(operation))}><X size={14} />取消</button><button type="button" className="ide-pending-approval__allow" disabled={busy} onClick={() => void transition("APPROVED", () => onApprove(operation))}><Check size={14} />允许本次</button></footer>
    </> : <p className="ide-pending-approval__result">{state === "APPROVED" ? "✓ 已批准；后续执行由受控 Runtime 处理。" : "✕ 已取消；未执行任何设备操作。"}</p>}
  </section>;
}
