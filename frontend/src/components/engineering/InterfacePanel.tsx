import type { EngineeringInterfaceContract } from "../../types/engineering";

export function InterfacePanel({ interfaces }: { interfaces: EngineeringInterfaceContract[] }) {
  return <section className="panel"><div className="panel-heading"><h2>Interface contracts</h2><span className="status-pill status-pending">Read-only</span></div><div className="review-list">{interfaces.map((item) => <article key={item.interface_id}><div className="panel-heading"><strong>{item.protocol}</strong><span>{item.interface_id}</span></div><p>{item.producer} to {item.consumer}</p><p className="muted">Data: {item.data_reference}</p></article>)}</div></section>;
}
