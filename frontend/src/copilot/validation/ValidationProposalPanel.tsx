import { useEffect, useState } from "react";

import { decideProposalReview, fetchOperationProposals, fetchProposalReviews, fetchValidationResults, submitProposalReview, type OperationProposal, type ReviewView, type ValidationResult } from "../api/validationProposalClient";
import { useDesktopSessionState } from "../api/sessionState";

export function ValidationProposalPanel({ projectId, onMutationSettled }: { projectId: string | null; onMutationSettled?: () => void | Promise<void> }) {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const [validations, setValidations] = useState<ValidationResult[]>([]);
  const [proposals, setProposals] = useState<OperationProposal[]>([]);
  const [reviews, setReviews] = useState<ReviewView[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!projectId) return;
    if (sessionExpired) {
      setValidations([]); setProposals([]); setReviews([]); setError("Desktop 会话已过期。");
      return;
    }
    try {
      const [nextValidations, nextProposals, nextReviews] = await Promise.all([fetchValidationResults(projectId), fetchOperationProposals(projectId), fetchProposalReviews(projectId)]);
      setValidations(nextValidations); setProposals(nextProposals); setReviews(nextReviews); setError(null);
    } catch { setError("Validation and proposal projection is unavailable."); }
  }

  useEffect(() => { setValidations([]); setProposals([]); setReviews([]); void refresh(); }, [projectId]);
  useEffect(() => { if (sessionExpired) { setValidations([]); setProposals([]); setReviews([]); } }, [sessionExpired]);

  async function review(proposal: OperationProposal) { if (sessionExpired) return; try { await submitProposalReview(proposal, projectId ?? ""); await refresh(); await onMutationSettled?.(); } catch { setError("Proposal review request was rejected."); } }
  async function decide(reviewItem: ReviewView, decision: "APPROVE" | "REJECT") { if (sessionExpired) return; try { await decideProposalReview(reviewItem, decision); await refresh(); await onMutationSettled?.(); } catch { setError("Proposal review decision was rejected."); } }

  return <section className="ide-document-section" aria-label="Validation and proposal">
    <h3>Validation &amp; Proposal</h3>
    {sessionExpired && <p className="ide-form-error" role="alert">Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。</p>}
    {error && !sessionExpired && <p className="ide-context-unavailable">{error}</p>}
    <div className="ide-context-count"><span>Current Task</span><strong>{projectId ?? "Unavailable"}</strong></div>
    <div className="ide-context-count"><span>Validation</span><strong>{validations.length ? validations[validations.length - 1].status : "Unavailable"}</strong></div>
    {validations.flatMap((item) => item.findings).map((finding) => <article className="ide-history-list" key={finding.finding_id}><strong>Finding: {finding.subject}</strong><span>{finding.finding_type} · {finding.severity}</span><span>Evidence: {finding.evidence_ids.join(", ") || "None"}</span></article>)}
    {proposals.map((proposal) => { const reviewItem = reviews.find((item) => item.target_id === proposal.proposal_id); return <article className="ide-history-list" key={proposal.proposal_id}><strong>Proposal: {proposal.subject}</strong><span>{proposal.proposal_type} · {proposal.status}</span><span>{proposal.summary}</span>{proposal.status === "DRAFT" && <button type="button" disabled={sessionExpired} onClick={() => void review(proposal)}>Review proposal</button>}{reviewItem?.status === "WAIT_REVIEW" && <div><button type="button" disabled={sessionExpired} onClick={() => void decide(reviewItem, "APPROVE")}>Approve proposal for future controlled execution.</button><button type="button" disabled={sessionExpired} onClick={() => void decide(reviewItem, "REJECT")}>Reject proposal</button></div>}</article>; })}
    {!validations.length && !proposals.length && <p className="ide-list-empty">No validation or proposal is available.</p>}
  </section>;
}
