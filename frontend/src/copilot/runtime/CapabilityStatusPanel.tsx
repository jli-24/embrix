import type { CapabilityStatusView } from "../api/capabilityClient";

function label(status: string): string {
  return ({
    AVAILABLE: "可用",
    DEGRADED: "降级",
    UNAVAILABLE: "不可用",
    UNVERIFIED: "未验证",
  } as Record<string, string>)[status] ?? status;
}

export function CapabilityStatusPanel({ values, error }: { values: CapabilityStatusView[]; error: string | null }) {
  const names = ["WORKSPACE_WRITE", "BUILD", "FLASH", "HIL", "EDA_APPLY", "REPAIR", "DEPLOY"];
  const selected = names.map((name) => values.find((item) => item.capability === name)).filter((item): item is CapabilityStatusView => Boolean(item));
  return <section className="ide-document-section" aria-label="Capability 状态">
    <h3>Capability 状态</h3>
    {error && <p className="ide-context-unavailable">Capability 状态暂不可用</p>}
    {selected.map((item) => <div className="ide-context-count" key={item.capability}><span>{item.capability}</span><strong>{label(item.status)}</strong></div>)}
  </section>;
}
