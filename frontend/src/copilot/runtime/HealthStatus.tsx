import { HeartPulse } from "lucide-react";

import type { RuntimeHealth } from "../api/environmentClient";
import { formatProjectionTime, runtimeHealthTone } from "../viewModels";

export function HealthStatus({ health }: { health: RuntimeHealth[] }) {
  return <section className="ide-runtime-section"><header><div><HeartPulse size={16} /><h3>Health</h3></div></header><div className="ide-health-list">{health.length === 0 ? <p>—</p> : health.map((item) => { const tone = runtimeHealthTone(item); return <div key={item.component}><span><i className={`ide-health-dot ide-health-dot--${tone}`} /><strong>{item.component}</strong></span><span>{item.status}<small>{formatProjectionTime(item.observed_at)}</small></span></div>; })}</div></section>;
}
