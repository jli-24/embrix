import type { KnowledgeSuggestion } from "../../types/knowledge";

export function RecommendationPanel({ suggestions, error, loading }: { suggestions: KnowledgeSuggestion[] | null; error: string | null; loading: boolean }) {
  return <section className="panel"><div className="panel-heading"><h2>Knowledge suggestions</h2><span className="status-pill status-pending">Projection</span></div>{loading && <p className="muted">Loading suggestions...</p>}{!loading && error && <p className="muted">Knowledge retrieval is unavailable.</p>}{!loading && !error && suggestions && <div className="review-list">{suggestions.map((suggestion) => <article key={suggestion.recommendation_id}><div className="panel-heading"><strong>{suggestion.matched_reference}</strong><span>{suggestion.confidence}</span></div><p>{suggestion.reason}</p><p className="muted">Evidence: {suggestion.evidence_reference}</p></article>)}</div>}</section>;
}
