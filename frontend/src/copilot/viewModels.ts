import type { ContextReference } from "./api/chatClient";
import type { AuditView, MemoryView, RuntimeHealth } from "./api/environmentClient";
import type { WorkspaceArtifactView, WorkspaceSnapshot } from "./api/workspaceClient";

export type EngineeringGroup = {
  key: "firmware" | "hardware" | "documents" | "tests" | "other";
  label: "Firmware" | "Hardware" | "Documents" | "Tests" | "Other";
  artifacts: WorkspaceArtifactView[];
};

const groups: Array<Omit<EngineeringGroup, "artifacts">> = [
  { key: "firmware", label: "Firmware" },
  { key: "hardware", label: "Hardware" },
  { key: "documents", label: "Documents" },
  { key: "tests", label: "Tests" },
  { key: "other", label: "Other" },
];

function artifactGroup(type: string): EngineeringGroup["key"] {
  const value = type.toUpperCase();
  if (value.includes("FIRMWARE") || value.includes("SOURCE") || value.includes("CODE")) return "firmware";
  if (["HARDWARE", "PCB", "EDA", "SCHEMATIC", "BOM"].some((token) => value.includes(token))) return "hardware";
  if (["DOCUMENT", "REQUIREMENT", "ARCHITECTURE", "DATASHEET"].some((token) => value.includes(token))) return "documents";
  if (["TEST", "HIL", "VALIDATION"].some((token) => value.includes(token))) return "tests";
  return "other";
}

export function buildEngineeringTree(workspace: WorkspaceSnapshot | null): EngineeringGroup[] {
  return groups.map((group) => ({
    ...group,
    artifacts: (workspace?.artifacts ?? []).filter((artifact) => artifactGroup(artifact.artifact_type) === group.key),
  })).filter((group) => group.artifacts.length > 0);
}

export function findReferenceAudit(reference: ContextReference, audits: AuditView[]): AuditView | null {
  return audits.find((item) => item.object_reference === reference.reference_id) ?? null;
}

export function proposalReferences(references: ContextReference[]): ContextReference[] {
  return references.filter((reference) => reference.reference_kind === "PROPOSAL");
}

export function countMemoryStatuses(memory: MemoryView[]): Record<string, number> {
  return memory.reduce<Record<string, number>>((counts, item) => {
    counts[item.status] = (counts[item.status] ?? 0) + 1;
    return counts;
  }, {});
}

export type HealthTone = "healthy" | "degraded" | "unavailable";

export function runtimeHealthTone(item: RuntimeHealth): HealthTone {
  const status = item.status.toUpperCase();
  if (["HEALTHY", "AVAILABLE", "READY", "RUNNING"].includes(status)) return "healthy";
  if (["DEGRADED", "FAILED"].includes(status)) return "degraded";
  return "unavailable";
}

export function findAuditDetail(audits: AuditView[], tokens: string[]): AuditView | null {
  return audits.find((item) => {
    const material = `${item.event_type} ${item.object_reference}`.toUpperCase();
    return tokens.some((token) => material.includes(token));
  }) ?? null;
}

export function shortFingerprint(value: string): string {
  const normalized = value.startsWith("sha256:") ? value.slice(7) : value;
  return normalized.slice(0, 8) || "Unavailable";
}

export function formatProjectionTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}

export function buildSafeMarkdown(input: {
  projectName: string;
  question: string;
  answer: string;
  createdAt: string;
  references: ContextReference[];
}): string {
  const lines = [
    `# Embedded Copilot — ${input.projectName}`,
    "",
    `Timestamp: ${input.createdAt}`,
    "",
    "## Question",
    "",
    input.question,
    "",
    "## Answer",
    "",
    input.answer,
    "",
    "## References",
    "",
  ];
  if (!input.references.length) lines.push("No projected references.");
  for (const reference of input.references) {
    lines.push(`- ${reference.reference_id}`);
    lines.push(`  - Fingerprint: ${reference.source_fingerprint}`);
  }
  return `${lines.join("\n")}\n`;
}
