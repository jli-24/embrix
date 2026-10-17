import type { MultimodalAnalysisProjection } from "../../types/multimodal";

export function DocumentAnalysisPanel({ projection, error, loading }: { projection: MultimodalAnalysisProjection | null; error: string | null; loading: boolean }) {
  return <section className="panel"><div className="panel-heading"><h2>Document analysis</h2><span className="status-pill status-pending">Read-only</span></div>{loading && <p className="muted">Loading multimodal projection...</p>}{!loading && error && <p className="muted">Multimodal input is unavailable.</p>}{!loading && !error && projection && <p className="muted">Projection fingerprint: {projection.fingerprint}</p>}</section>;
}
