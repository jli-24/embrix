import { Activity, CircleDot, Gauge, Radio, Usb } from "lucide-react";

import type { RuntimePanel } from "../api/chatClient";
import type { AuditView } from "../api/environmentClient";
import { findAuditDetail, formatProjectionTime } from "../viewModels";

const rows = [
  { label: "UART", tokens: ["UART"], Icon: Radio },
  { label: "GPIO", tokens: ["GPIO"], Icon: CircleDot },
  { label: "I2C", tokens: ["I2C", "SENSOR"], Icon: Usb },
  { label: "Power", tokens: ["POWER"], Icon: Gauge },
];

export function HilStatus({ panel, audits }: { panel: RuntimePanel | null; audits: AuditView[] }) {
  return <section className="ide-runtime-section"><header><div><Activity size={16} /><h3>HIL</h3></div><span className={`ide-status ide-status--${(panel?.hil_status ?? "UNAVAILABLE").toLowerCase()}`}>{panel?.hil_status ?? "UNAVAILABLE"}</span></header><div className="ide-runtime-list">{rows.map(({ label, tokens, Icon }) => { const audit = findAuditDetail(audits, tokens); return <div key={label}><span><Icon size={14} />{label}</span><strong>{audit?.status ?? "—"}</strong><time>{formatProjectionTime(audit?.timestamp)}</time></div>; })}</div></section>;
}
