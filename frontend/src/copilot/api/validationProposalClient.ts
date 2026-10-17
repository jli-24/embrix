export type ValidationFinding = {
  finding_id: string;
  finding_type: string;
  severity: string;
  subject: string;
  summary: string;
  evidence_ids: string[];
  limitations: string[];
  confidence: number;
  finding_fingerprint: string;
};

import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type ValidationResult = {
  validation_id: string;
  validation_type: string;
  status: string;
  evidence_ids: string[];
  findings: ValidationFinding[];
  limitations: string[];
  validation_fingerprint: string;
  validated_at: string;
};

export type OperationProposal = {
  proposal_id: string;
  project_id?: string;
  proposal_type: string;
  subject: string;
  summary: string;
  proposed_changes: Array<{ change_kind: string; file_reference?: string | null; symbol?: string | null; logical_change: string; expected_effect: string }>;
  finding_ids: string[];
  evidence_ids: string[];
  status: "DRAFT" | "WAIT_REVIEW" | "APPROVED" | "REJECTED" | "STALE" | "REVOKED" | "SUPERSEDED";
  proposal_fingerprint: string;
  limitations: string[];
};

export type ReviewView = {
  review_id: string;
  project_id: string;
  target_type: string;
  target_id: string;
  target_fingerprint: string;
  status: string;
  decision: "APPROVE" | "REJECT" | null;
};

const headers = { "X-Copilot-Requester": "desktop-user", "X-Environment-Tenant": "desktop-tenant" };

async function getJson<T>(path: string, fetcher: typeof fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new Error(DESKTOP_SESSION_EXPIRED);
  const response = await fetcher(path, { credentials: "same-origin", headers });
  const payload = await response.json().catch(() => null) as { error?: string; detail?: string } | T | null;
  if (!response.ok) {
    throw new Error(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "VALIDATION_PROPOSAL_UNAVAILABLE");
  }
  if (payload === null) throw new Error("VALIDATION_PROPOSAL_UNAVAILABLE");
  return payload as T;
}

async function postJson<T>(path: string, body: unknown, fetcher: typeof fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new Error(DESKTOP_SESSION_EXPIRED);
  const response = await fetcher(path, { method: "POST", credentials: "same-origin", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const payload = await response.json().catch(() => null) as { error?: string; detail?: string } | T | null;
  if (!response.ok) {
    throw new Error(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "VALIDATION_PROPOSAL_REJECTED");
  }
  if (payload === null) throw new Error("VALIDATION_PROPOSAL_REJECTED");
  return payload as T;
}

export function fetchValidationResults(projectId: string, fetcher: typeof fetch = fetch): Promise<ValidationResult[]> {
  return getJson(`/api/v1/copilot/validations?project_id=${encodeURIComponent(projectId)}`, fetcher);
}

export function fetchOperationProposals(projectId: string, fetcher: typeof fetch = fetch): Promise<OperationProposal[]> {
  return getJson(`/api/v1/copilot/proposals?project_id=${encodeURIComponent(projectId)}`, fetcher);
}

export function fetchProposalReviews(projectId: string, fetcher: typeof fetch = fetch): Promise<ReviewView[]> {
  return getJson(`/api/v1/copilot/reviews?project_id=${encodeURIComponent(projectId)}&target_type=ENGINEERING_PROPOSAL`, fetcher);
}

export function submitProposalReview(proposal: OperationProposal, projectId: string, fetcher: typeof fetch = fetch): Promise<ReviewView> {
  const targetType = proposal.proposal_type === "REPAIR" ? "REPAIR_PROPOSAL" : proposal.proposal_type === "OPTIMIZATION" ? "OPTIMIZATION_PROPOSAL" : "ENGINEERING_PROPOSAL";
  return postJson("/api/v1/copilot/reviews", { project_id: projectId, target_type: targetType, target_id: proposal.proposal_id, target_fingerprint: proposal.proposal_fingerprint }, fetcher);
}

export function decideProposalReview(review: ReviewView, decision: "APPROVE" | "REJECT", fetcher: typeof fetch = fetch): Promise<ReviewView> {
  return postJson(`/api/v1/copilot/reviews/${encodeURIComponent(review.review_id)}/decision`, { project_id: review.project_id, decision, reviewer: "desktop-reviewer", comment: decision === "APPROVE" ? "Proposal reviewed for future controlled execution." : "Proposal rejected during human review.", decided_at: new Date().toISOString() }, fetcher);
}
