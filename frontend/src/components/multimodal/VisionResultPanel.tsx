import type { EngineeringInterpretation } from "../../types/multimodal";

export function VisionResultPanel({ interpretation }: { interpretation: EngineeringInterpretation | null }) {
  return <section className="panel"><div className="panel-heading"><h2>Engineering interpretation</h2><span className="status-pill status-pending">Projection</span></div>{interpretation ? <><p>{interpretation.summary}</p><p className="muted">Risk: {interpretation.risk}</p><p className="muted">Confidence: {interpretation.confidence.toFixed(2)}</p></> : <p className="muted">Interpretation is unavailable.</p>}</section>;
}
