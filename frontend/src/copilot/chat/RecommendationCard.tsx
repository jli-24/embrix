import type { ChatRecommendation } from "../api/projectClient";

// Compatibility name only. This is intentionally rendered as a document section, never as a card.
export function RecommendationCard({ recommendations }: { recommendations: ChatRecommendation[] }) {
  return <section className="ide-document-section ide-recommendations" data-component="recommendation-document" aria-label="建议"><h3>建议</h3>{recommendations.length === 0 ? <p className="ide-compact-empty">当前回答没有可投影的工程建议。</p> : <ul className="ide-recommendation-list">{recommendations.map((item) => <li key={`${item.reference_id}-${item.source_fingerprint}`}><strong>{item.review}</strong>{item.reason && <span>{item.reason}</span>}<small>Proposal</small></li>)}</ul>}</section>;
}
