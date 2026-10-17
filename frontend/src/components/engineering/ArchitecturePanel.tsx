import type { EngineeringArchitectureSnapshot } from "../../types/engineering";

export function ArchitecturePanel({ architecture }: { architecture: EngineeringArchitectureSnapshot }) {
  return <section className="panel"><div className="panel-heading"><h2>Architecture projection</h2><span className="status-pill status-pending">{architecture.confidence}</span></div><p className="muted">Components: {architecture.components.join(", ")}</p><p className="muted">Interfaces: {architecture.interfaces.join(", ")}</p><p className="muted">Decisions: {architecture.decision_references.join(", ")}</p></section>;
}
