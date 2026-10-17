import type { EngineeringRequirementProjection } from "../../types/engineering";

export function RequirementPanel({ requirement }: { requirement: EngineeringRequirementProjection }) {
  return <section className="panel"><div className="panel-heading"><h2>Engineering requirement</h2><span className="status-pill status-pending">{requirement.confidence}</span></div><h3>{requirement.title}</h3><p>{requirement.description}</p><p className="muted">Functional: {requirement.functional_requirements.join(", ")}</p><p className="muted">Constraints: {requirement.constraints.join(", ")}</p></section>;
}
