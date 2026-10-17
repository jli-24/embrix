import type { EngineeringKnowledgeSnapshot } from "../../types/knowledge";

export function KnowledgeGraphPanel({ snapshot, error, loading }: { snapshot: EngineeringKnowledgeSnapshot | null; error: string | null; loading: boolean }) {
  return <section className="panel"><div className="panel-heading"><h2>Engineering knowledge</h2><span className="status-pill status-pending">Read-only</span></div>{loading && <p className="muted">Loading knowledge graph...</p>}{!loading && error && <p className="muted">Engineering knowledge is unavailable.</p>}{!loading && !error && snapshot && <><p className="muted">Nodes: {snapshot.nodes.length} / Relations: {snapshot.relations.length}</p><div className="review-list">{snapshot.nodes.map((node) => <article key={node.node_id}><div className="panel-heading"><strong>{node.entity_type}</strong><span>{node.confidence}</span></div><p>{node.reference}</p><p className="muted">{node.attributes.join(", ")}</p></article>)}</div></>}</section>;
}
