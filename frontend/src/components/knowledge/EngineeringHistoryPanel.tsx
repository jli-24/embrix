import type { EngineeringKnowledgeSnapshot } from "../../types/knowledge";

export function EngineeringHistoryPanel({ snapshot }: { snapshot: EngineeringKnowledgeSnapshot | null }) {
  const history = snapshot?.nodes.filter((node) => node.entity_type === "DECISION" || node.entity_type === "VALIDATION" || node.entity_type === "OPTIMIZATION") ?? [];
  return <section className="panel"><div className="panel-heading"><h2>Engineering history</h2><span className="status-pill status-pending">Projection</span></div>{history.length === 0 ? <p className="muted">No historical projection is available.</p> : <div className="review-list">{history.map((node) => <article key={node.node_id}><div className="panel-heading"><strong>{node.entity_type}</strong><span>{node.confidence}</span></div><p>{node.reference}</p></article>)}</div>}</section>;
}
