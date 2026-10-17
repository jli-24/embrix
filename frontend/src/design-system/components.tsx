import type { ReactNode } from "react";
import type { DesignTone } from "./design-tokens";

export function Panel({ label, title, children, className = "" }: { label?: string; title: string; children: ReactNode; className?: string }) {
  return <section className={`ds-panel ${className}`.trim()}>{(label || title) && <header className="ds-panel__header">{label && <span>{label}</span>}{title && <h2>{title}</h2>}</header>}<div className="ds-panel__content">{children}</div></section>;
}

export function StatusIndicator({ label, tone = "neutral" }: { label: string; tone?: DesignTone }) {
  return <span className={`ds-status ds-status--${tone}`}><i aria-hidden="true" />{label}</span>;
}

export function ContextChip({ label, active = false, onClick, onRemove }: { label: string; active?: boolean; onClick?: () => void; onRemove?: () => void }) {
  const content = <><span>{label}</span>{onRemove && <span aria-hidden="true">×</span>}</>;
  if (!onClick && !onRemove) return <span className={`ds-context-chip ${active ? "is-active" : ""}`}>{content}</span>;
  return <button className={`ds-context-chip ${active ? "is-active" : ""}`} type="button" onClick={onRemove ?? onClick} title={onRemove ? `移除 ${label} 引用` : `添加 ${label} 引用`}>{content}</button>;
}

export function Message({ variant, label, children }: { variant: "user" | "assistant"; label: string; children: ReactNode }) {
  return <article className={`ds-message ds-message--${variant}`}><div className="ds-message__label">{label}</div><div className="ds-message__content">{children}</div></article>;
}

export function EvidenceReference({ sourceType, label, referenceId, status, summary, fingerprint, timestamp, compact = false }: { sourceType?: string; label: string; referenceId: string; status: string; summary?: string | null; fingerprint: string; timestamp?: string; compact?: boolean }) {
  return <article className={`ds-evidence ${compact ? "ds-evidence--compact" : ""}`}><div className="ds-evidence__identity">{sourceType && <span className="ds-evidence__type">{sourceType}</span>}<strong>{label}</strong>{summary && <p>{summary}</p>}</div><div className="ds-evidence__meta"><StatusIndicator label={status} tone={status.toUpperCase().includes("FAIL") ? "danger" : status.toUpperCase().includes("OBSERV") || status.toUpperCase().includes("VERIF") ? "success" : "neutral"} /><code>reference: {referenceId}</code>{timestamp && <time>{timestamp}</time>}<span>fingerprint <code>{fingerprint}</code></span></div></article>;
}

export function ContextStatus({ label, value, tone = "neutral" }: { label: string; value: string; tone?: DesignTone }) {
  return <div className="ds-context-status"><span>{label}</span><StatusIndicator label={value} tone={tone} /></div>;
}
