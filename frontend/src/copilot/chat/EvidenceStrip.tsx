import { EvidenceReference } from "../../design-system/components";
import type { ChatEvidence } from "../api/projectClient";
import { shortFingerprint } from "../viewModels";

function formatObservedTime(value: string): string | undefined {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function EvidenceStrip({ evidence, display, onSelect }: { evidence: ChatEvidence[]; display: "expanded" | "compact"; onSelect?: (evidence: ChatEvidence) => void }) {
  return <section className="ide-document-section" aria-label="证据"><h3>证据</h3>{evidence.length === 0 ? <p className="ide-compact-empty">当前回答没有可投影的工程证据。</p> : <div className="ide-evidence-list">{evidence.map((item) => <div key={`${item.reference_id}-${item.source_fingerprint}`} data-evidence-selectable={onSelect ? "true" : "false"} role={onSelect ? "button" : undefined} tabIndex={onSelect ? 0 : undefined} onClick={() => onSelect?.(item)} onKeyDown={(event) => { if (onSelect && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); onSelect(item); } }}><EvidenceReference sourceType={item.source_type} label={item.label} referenceId={item.reference_id} status={item.status} summary={item.summary} fingerprint={shortFingerprint(item.source_fingerprint)} timestamp={formatObservedTime(item.observed_at)} compact={display === "compact"} />{item.source_uri && <a href={item.source_uri} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>官方来源</a>}</div>)}</div>}</section>;
}
