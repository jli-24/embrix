import type { VisionObservation } from "../../types/multimodal";

export function ObservationPanel({ observation }: { observation: VisionObservation }) {
  return <section className="panel"><div className="panel-heading"><h2>Multimodal observation</h2><span className="status-pill status-pending">{observation.observation_type}</span></div><p className="muted">Source: {observation.source_reference}</p><p>Confidence: {observation.confidence.toFixed(2)}</p><div className="review-list">{observation.content.map((item, index) => <article key={`${observation.observation_id}:${index}`}>{item}</article>)}</div></section>;
}
