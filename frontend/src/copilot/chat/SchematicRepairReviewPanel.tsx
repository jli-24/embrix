import { useState } from "react";

import type {
  SchematicProductionState,
  SchematicRepairDecisionAction,
  SchematicRepairProposal,
} from "../api/projectClient";

type SchematicRepairReviewPanelProps = {
  production?: SchematicProductionState | null;
  busy?: boolean;
  onDecision(proposal: SchematicRepairProposal, action: SchematicRepairDecisionAction, comment?: string, editedProposalFingerprint?: string): void;
};

const terminalActions = new Set<SchematicRepairDecisionAction>([
  "APPROVE_FIX",
  "REJECT_FIX",
  "EDIT_FIX",
  "DEFER",
  "WAIVE_WITH_REASON",
]);

export function SchematicRepairReviewPanel({ production, busy = false, onDecision }: SchematicRepairReviewPanelProps) {
  const [reason, setReason] = useState("");
  const proposals = production?.repair_proposals ?? [];
  if (!production?.erc_receipt && proposals.length === 0) return null;

  const decisions = new Map((production?.repair_decisions ?? []).map((decision) => [decision.repair_id, decision]));
  const gate = production?.erc_gate_status ?? "BLOCKED";
  return <section className="ide-research-review" aria-label="SCHEMATIC REPAIR REVIEW" data-g4c-repair-review="true">
    <header className="ide-research-review__header">
      <strong>原理图 ERC Gate</strong>
      <small>{gate} · {production?.erc_receipt?.result.status ?? "UNAVAILABLE"}</small>
    </header>
    {production?.erc_receipt && <p>保留真实 KiCad ERC evidence；当前问题 {production.erc_receipt.result.issues.length} 条，未将 warning 或 inference 提升为 PASS。</p>}
    {proposals.length === 0 && <p>当前 ERC 结果没有可由该受控修复边界提出的候选。</p>}
    {proposals.map((proposal) => {
      const decision = decisions.get(proposal.repair_id);
      const alternative = proposals.find((candidate) => candidate.fingerprint !== proposal.fingerprint);
      const allowWaive = proposal.classification === "NON_BLOCKING_WARNING";
      const canDecide = !decision && terminalActions.size > 0;
      const issueRefs = new Set(proposal.issue_refs ?? []);
      const issues = (production?.erc_receipt?.result.issues ?? []).filter((issue) => issueRefs.has(issue.issue_id));
      return <article className="ide-research-review__card" key={proposal.repair_id} data-g4c-repair-id={proposal.repair_id}>
        <div><strong>{proposal.repair_id}</strong><small>{proposal.classification} · {proposal.status}</small></div>
        {issues.map((issue) => <div key={issue.issue_id} data-g4c-erc-issue={issue.issue_id}>
          <p>问题：{issue.message}</p>
          <p>严重度：{issue.severity} · KiCad rule：{issue.rule_id} · 影响对象：{issue.component_ref ?? "未绑定"}{issue.pin_ref ? ` / ${issue.pin_ref}` : ""}{issue.location ? ` · ${issue.location}` : ""}</p>
          <p>Evidence：{issue.raw_evidence_ref} · 分类：{issue.classification}</p>
        </div>)}
        <p>{proposal.root_cause || proposal.proposed_change}</p>
        <p>建议修复：{proposal.proposed_change}</p>
        {proposal.expected_effect && <p>预期效果：{proposal.expected_effect}</p>}
        {proposal.risk && <p>风险：{proposal.risk}</p>}
        {decision && <p data-g4c-decision={decision.action}>已记录：{decision.action}{decision.comment ? ` · ${decision.comment}` : ""}</p>}
        {canDecide && <div className="ide-research-review__actions">
          <button type="button" disabled={busy} onClick={() => onDecision(proposal, "APPROVE_FIX")}>APPROVE_FIX</button>
          <button type="button" disabled={busy} onClick={() => onDecision(proposal, "REJECT_FIX")}>REJECT_FIX</button>
          <button type="button" disabled={busy || !alternative} title={alternative ? "指向已持久化的替代候选" : "EDIT_FIX 需要已持久化的替代候选"} onClick={() => alternative && onDecision(proposal, "EDIT_FIX", undefined, alternative.fingerprint)}>EDIT_FIX</button>
          <button type="button" disabled={busy} onClick={() => onDecision(proposal, "DEFER")}>DEFER</button>
          {allowWaive && <>
            <input aria-label="WAIVE_WITH_REASON reason" value={reason} maxLength={2048} placeholder="WAIVE_WITH_REASON reason" onChange={(event) => setReason(event.target.value)} />
            <button type="button" disabled={busy || !reason.trim()} onClick={() => onDecision(proposal, "WAIVE_WITH_REASON", reason.trim())}>WAIVE_WITH_REASON</button>
          </>}
        </div>}
      </article>;
    })}
    <p>自然语言反馈只生成新的 typed repair intent；任何写入仍需通过本面板的 decision authority 与 G3 → G3-V → ERC 链路。</p>
  </section>;
}
