import { CircuitBoard, FileCheck2, ListChecks, Route } from "lucide-react";

import type { RuntimePanel } from "../api/chatClient";
import type { AuditView } from "../api/environmentClient";
import { findAuditDetail, formatProjectionTime } from "../viewModels";

const rows = [
  { label: "ERC", tokens: ["ERC"], Icon: FileCheck2 },
  { label: "DRC", tokens: ["DRC"], Icon: Route },
  { label: "BOM", tokens: ["BOM"], Icon: ListChecks },
];

export function EdaStatus({ panel, audits }: { panel: RuntimePanel | null; audits: AuditView[] }) {
  return <section className="ide-runtime-section"><header><div><CircuitBoard size={16} /><h3>EDA</h3></div><span className={`ide-status ide-status--${(panel?.eda_status ?? "UNAVAILABLE").toLowerCase()}`}>{panel?.eda_status ?? "UNAVAILABLE"}</span></header><div className="ide-runtime-list">{rows.map(({ label, tokens, Icon }) => { const audit = findAuditDetail(audits, tokens); return <div key={label}><span><Icon size={14} />{label}</span><strong>{audit?.status ?? "—"}</strong><time>{formatProjectionTime(audit?.timestamp)}</time></div>; })}</div></section>;
}
