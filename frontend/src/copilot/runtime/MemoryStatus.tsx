import { BrainCircuit } from "lucide-react";

import type { RuntimePanel } from "../api/chatClient";
import type { MemoryView } from "../api/environmentClient";
import { countMemoryStatuses } from "../viewModels";

export function MemoryStatus({ panel, memory }: { panel: RuntimePanel | null; memory: MemoryView[] }) {
  const counts = countMemoryStatuses(memory);
  const statuses = Array.from(new Set([...panel?.memory_statuses ?? [], ...Object.keys(counts)]));
  return <section className="ide-runtime-section"><header><div><BrainCircuit size={16} /><h3>Memory</h3></div></header><div className="ide-memory-list">{statuses.length === 0 ? <p>—</p> : statuses.map((status) => <div key={status}><span className={`ide-memory-mark ide-memory-mark--${status.toLowerCase()}`} /> <strong>{status}</strong><b>{counts[status] ?? "projected"}</b></div>)}</div></section>;
}
