export type EngineeringMemorySource = "conversation" | "evidence" | "manual";

export type EngineeringMemoryConfidence = "confirmed" | "candidate";

export type MemoryState = "empty" | "available" | "stale";

export type EngineeringMemoryView = {
  id: string;
  title: string;
  summary: string;
  source: EngineeringMemorySource;
  createdAt: string;
  fingerprint?: string;
  confidence?: EngineeringMemoryConfidence;
  relatedEvidenceIds?: string[];
};

export type EngineeringMemoryProjection = {
  projectId: string;
  state: MemoryState;
  items: EngineeringMemoryView[];
};

export function normalizeEngineeringMemoryProjection(
  projectId: string | null,
  projection: EngineeringMemoryProjection | null | undefined,
): EngineeringMemoryProjection {
  if (!projectId || !projection || projection.projectId !== projectId) return { projectId: projectId ?? "", state: "empty", items: [] };
  const items = projection.items.filter((item) => Boolean(item.id && item.title && item.summary && item.createdAt));
  return { projectId, state: items.length ? projection.state === "stale" ? "stale" : "available" : "empty", items };
}

export function formatMemoryDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

export function memorySourceLabel(source: EngineeringMemorySource): string {
  return source === "conversation" ? "Conversation" : source === "evidence" ? "Evidence" : "Manual";
}
