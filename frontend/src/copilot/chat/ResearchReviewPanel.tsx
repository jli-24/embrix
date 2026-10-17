import type {
  EngineeringResearchSnapshot,
  FeatureDecisionAction,
  ReferenceProjectDecisionAction,
  RequirementChangeProposal,
} from "../api/projectClient";

type ResearchReviewPanelProps = {
  snapshot?: EngineeringResearchSnapshot | null;
  proposal?: RequirementChangeProposal | null;
  busy?: boolean;
  onReferenceDecision(review: EngineeringResearchSnapshot["reference_reviews"][number], decision: ReferenceProjectDecisionAction): void;
  onFeatureDecision(review: EngineeringResearchSnapshot["reference_reviews"][number], feature: string, action: FeatureDecisionAction): void;
  onConfirmProposal(proposal: RequirementChangeProposal, confirmed: boolean): void;
};

const decisionLabels: Record<ReferenceProjectDecisionAction, string> = {
  REFERENCE: "作为参考",
  PARTIAL_REFERENCE: "部分参考",
  EXCLUDE: "排除",
  DEFER: "稍后决定",
};

function researchStatus(snapshot: EngineeringResearchSnapshot): string {
  switch (snapshot.evidence_pack.status) {
    case "UNAVAILABLE": return "参考资料检索暂不可用";
    case "NO_RELEVANT_REFERENCE": return "未找到相关参考项目";
    case "PARTIAL": return snapshot.reference_reviews.length ? "部分参考资料可用" : "部分参考资料可用，暂未找到相关参考项目";
    case "COMPLETE": return snapshot.reference_reviews.length ? "已找到相关参考项目" : "未找到相关参考项目";
  }
}

export function ResearchReviewPanel({ snapshot, proposal, busy = false, onReferenceDecision, onFeatureDecision, onConfirmProposal }: ResearchReviewPanelProps) {
  if (!snapshot && !proposal) return null;
  const pendingProposal = proposal?.status === "PENDING";
  if (snapshot && snapshot.reference_reviews.length === 0 && !pendingProposal) {
    return <aside className="ide-research-review ide-research-review--compact" aria-label="参考项目审阅" data-g0r-production-review="true" data-research-empty="true"><strong>参考项目审阅</strong><span>{researchStatus(snapshot)}</span></aside>;
  }
  return <section className="ide-research-review" aria-label="工程调研审阅" data-g0r-production-review="true">
    {snapshot && <>
      <header className="ide-research-review__header">
        <strong>参考项目审阅</strong>
        <small>{researchStatus(snapshot)}</small>
      </header>
      {snapshot.reference_reviews.map((review) => <article className="ide-research-review__card" key={review.project_identity}>
        <div><strong>{review.title}</strong><small>{review.project_identity}</small></div>
        <p>{review.description}</p>
        <div className="ide-research-review__deltas">
          {review.feature_deltas.map((delta) => <span key={delta.delta_id} data-feature-delta={delta.delta} title={delta.description}>{delta.feature} · {delta.delta} · {delta.evidence_state}</span>)}
        </div>
        <div className="ide-research-review__actions">
          {(Object.keys(decisionLabels) as ReferenceProjectDecisionAction[]).map((decision) => <button key={decision} type="button" disabled={busy} onClick={() => onReferenceDecision(review, decision)}>{decisionLabels[decision]}</button>)}
          {review.feature_deltas.filter((delta) => delta.delta === "REFERENCE_EXTRA").map((delta) => <span key={`${review.project_identity}-${delta.feature}`}>
            <button type="button" disabled={busy} onClick={() => onFeatureDecision(review, delta.feature, "ADD")}>采纳 {delta.feature}</button>
            <button type="button" disabled={busy} onClick={() => onFeatureDecision(review, delta.feature, "REJECT")}>拒绝 {delta.feature}</button>
          </span>)}
        </div>
      </article>)}
    </>}
    {proposal && pendingProposal && <article className="ide-research-review__proposal" data-requirement-change-proposal="pending">
      <strong>需求变更待确认</strong>
      <p>{proposal.summary}</p>
      <div className="ide-research-review__actions">
        <button type="button" disabled={busy} onClick={() => onConfirmProposal(proposal, true)}>确认提交</button>
        <button type="button" disabled={busy} onClick={() => onConfirmProposal(proposal, false)}>暂不提交</button>
      </div>
    </article>}
  </section>;
}
