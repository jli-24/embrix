import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type EngineeringEvidenceLevel = "OBSERVATION" | "INFERENCE" | "CANDIDATE" | "VERIFIED_FACT" | "RECOMMENDATION";

export type EngineeringEvidenceView = {
  evidence_id: string;
  project_id: string;
  level: EngineeringEvidenceLevel;
  summary: string;
  source_reference: string;
  source_fingerprint: string;
  confidence: number;
  needs_more_evidence: string[];
  fingerprint: string;
};

export type KnowledgeCitation = {
  citation_id: string;
  project_id: string;
  title: string;
  source: "LOCAL" | "GITHUB" | "WEB";
  score: number | null;
  metadata: Record<string, string | number>;
  fingerprint: string;
};

export type FirmwareAnalysisResponse = {
  project_id: string;
  facts: string[];
  evidence: EngineeringEvidenceView[];
  recommendations: string[];
  confidence: number;
  limitations: string[];
  source_fingerprint: string;
  fingerprint: string;
};

export type DebugAnalysisResponse = {
  project_id: string;
  failure_reference: string;
  root_cause_candidates: EngineeringEvidenceView[];
  confidence: number;
  needs_more_evidence: string[];
  recommendations: string[];
  fingerprint: string;
};

export type ReviewView = {
  review_id: string;
  project_id: string;
  target_type: "KNOWLEDGE" | "VALIDATION" | "OPTIMIZATION" | "EVOLUTION" | "MEMORY_CANDIDATE" | "KNOWLEDGE_CANDIDATE" | "KNOWLEDGE_EVOLUTION_PROPOSAL" | "REPAIR_PROPOSAL" | "OPTIMIZATION_PROPOSAL" | "ENGINEERING_PROPOSAL";
  target_id: string;
  target_fingerprint: string;
  requester: string;
  status: "OPEN" | "DECIDED" | "CLOSED" | "PENDING" | "APPROVED" | "REJECTED" | "STALE" | "EXPIRED" | "REVOKED";
  created_at: string;
  decision: "APPROVE" | "REJECT" | "REQUEST_CHANGE" | null;
  reviewer: string | null;
  decided_at: string | null;
  fingerprint: string;
};

export type V63MemoryView = {
  memory_id: string;
  memory_type: string;
  subject: string;
  statement: string;
  status: string;
  confidence: number;
  evidence_count: number;
  review_id: string;
  freshness: "CURRENT" | "HISTORICAL" | "STALE";
  provenance_references: string[];
};

export type V63KnowledgeView = {
  knowledge_id: string;
  subject: string;
  predicate: string;
  value: string;
  status: string;
  verification_status: string;
  evidence_count: number;
  review_id: string;
  provenance_references: string[];
};

export class EngineeringRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "EngineeringRequestError";
  }
}

const headers = {
  "Content-Type": "application/json",
  "X-Copilot-Requester": "desktop-user",
  "X-Environment-Tenant": "desktop-tenant",
};

async function postJson<T>(path: string, body: unknown, fetcher: typeof fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new EngineeringRequestError(DESKTOP_SESSION_EXPIRED);
  let response: Response;
  try {
    response = await fetcher(path, { method: "POST", credentials: "same-origin", headers, body: JSON.stringify(body) });
  } catch {
    throw new EngineeringRequestError("ENGINEERING_REQUEST_UNAVAILABLE");
  }
  const payload = await response.json().catch(() => null) as T | { error?: string; detail?: string } | null;
  if (!response.ok) {
    throw new EngineeringRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "ENGINEERING_REQUEST_REJECTED");
  }
  if (payload === null) throw new EngineeringRequestError("ENGINEERING_RESPONSE_INVALID");
  return payload as T;
}

async function getJson<T>(path: string, fetcher: typeof fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new EngineeringRequestError(DESKTOP_SESSION_EXPIRED);
  let response: Response;
  try {
    response = await fetcher(path, { credentials: "same-origin", headers });
  } catch {
    throw new EngineeringRequestError("ENGINEERING_REQUEST_UNAVAILABLE");
  }
  const payload = await response.json().catch(() => null) as T | { error?: string; detail?: string } | null;
  if (!response.ok) {
    throw new EngineeringRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "ENGINEERING_REQUEST_REJECTED");
  }
  if (payload === null) throw new EngineeringRequestError("ENGINEERING_RESPONSE_INVALID");
  return payload as T;
}

export function searchKnowledge(projectId: string, query: string, fetcher: typeof fetch = fetch): Promise<KnowledgeCitation[]> {
  return postJson<KnowledgeCitation[]>("/api/v1/copilot/knowledge/search", { project_id: projectId, query, sources: ["LOCAL"], top_k: 4 }, fetcher);
}

export function analyzeFirmware(projectId: string, question: string, fetcher: typeof fetch = fetch): Promise<FirmwareAnalysisResponse> {
  return postJson<FirmwareAnalysisResponse>("/api/v1/copilot/firmware/analyze", { project_id: projectId, question }, fetcher);
}

export function analyzeDebug(projectId: string, failureReference: string, failureType: string, summary: string, fetcher: typeof fetch = fetch): Promise<DebugAnalysisResponse> {
  return postJson<DebugAnalysisResponse>("/api/v1/copilot/debug/analyze", { project_id: projectId, failure_reference: failureReference, failure_type: failureType, summary }, fetcher);
}

export function createReview(body: { project_id: string; target_type: ReviewView["target_type"]; target_id: string; target_fingerprint: string }, fetcher: typeof fetch = fetch): Promise<ReviewView> {
  return postJson<ReviewView>("/api/v1/copilot/reviews", body, fetcher);
}

export function decideReview(reviewId: string, body: { project_id: string; decision: NonNullable<ReviewView["decision"]>; reviewer: string; comment: string; decided_at: string }, fetcher: typeof fetch = fetch): Promise<ReviewView> {
  return postJson<ReviewView>(`/api/v1/copilot/reviews/${encodeURIComponent(reviewId)}/decision`, body, fetcher);
}

export function fetchReviewQueue(projectId: string, filters: { status?: string; target_type?: ReviewView["target_type"] } = {}, fetcher: typeof fetch = fetch): Promise<ReviewView[]> {
  const params = new URLSearchParams({ project_id: projectId });
  if (filters.status) params.set("status", filters.status);
  if (filters.target_type) params.set("target_type", filters.target_type);
  return getJson<ReviewView[]>(`/api/v1/copilot/reviews?${params.toString()}`, fetcher);
}

export function fetchMemoryProjection(projectId: string, fetcher: typeof fetch = fetch): Promise<V63MemoryView[]> {
  return getJson<V63MemoryView[]>(`/api/v1/copilot/memory?project_id=${encodeURIComponent(projectId)}`, fetcher);
}

export function fetchGovernedKnowledgeProjection(projectId: string, fetcher: typeof fetch = fetch): Promise<V63KnowledgeView[]> {
  return getJson<V63KnowledgeView[]>(`/api/v1/copilot/knowledge/governed?project_id=${encodeURIComponent(projectId)}`, fetcher);
}
