export type ChangeSetEntryView = {
  entry_id: string;
  relative_file_reference: string;
  change_type: string;
  target_symbol: string | null;
  before_fingerprint: string;
  after_fingerprint: string;
  safe_preview: string;
  reason: string;
};

export type ChangeSetView = {
  changeset_id: string;
  project_id: string;
  proposal_id: string;
  proposal_fingerprint: string;
  target_fingerprint: string;
  base_project_fingerprint: string;
  staging_fingerprint: string;
  entries: ChangeSetEntryView[];
  created_at: string;
  expires_at: string;
  status: string;
  fingerprint: string;
};

export type PreviewView = {
  changeset_id: string;
  changeset_fingerprint: string;
  project_id: string;
  affected_files: string[];
  diff: string;
  validation_status: string;
  validation_fingerprint: string;
  risk: string;
  generated_at: string;
  fingerprint: string;
};

export type ApprovalView = {
  grant_id: string;
  project_id: string;
  changeset_id: string;
  changeset_fingerprint: string;
  target_fingerprint: string;
  project_fingerprint: string;
  staging_fingerprint: string;
  reviewer_id: string;
  expires_at: string;
  status: string;
  fingerprint: string;
};

export type ReceiptView = {
  operation_id: string;
  project_id: string;
  changeset_id: string;
  changeset_fingerprint: string;
  status: string;
  recovery_status: string;
  recovery_id: string | null;
  targets: Array<{ relative_file_reference: string; recovery_object_reference: string; checksum: string; before_fingerprint: string; after_fingerprint: string }>;
  validation_status: string;
  before_project_fingerprint: string;
  after_project_fingerprint: string;
  created_at: string;
  fingerprint: string;
};

export class WorkspaceApplyRequestError extends Error {
  constructor(public readonly code: string) { super(code); }
}

async function request<T>(url: string, init?: RequestInit, fetcher: typeof fetch = fetch): Promise<T> {
  let response: Response;
  try { response = await fetcher(url, init); } catch { throw new WorkspaceApplyRequestError("WORKSPACE_APPLY_UNAVAILABLE"); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new WorkspaceApplyRequestError(body.error ?? "WORKSPACE_APPLY_REJECTED");
  return body as T;
}

export function fetchWorkspaceChangeset(projectId: string, changesetId: string, fetcher: typeof fetch = fetch) {
  return request<ChangeSetView>(`/api/v1/copilot/workspace/changesets/${encodeURIComponent(changesetId)}?project_id=${encodeURIComponent(projectId)}`, undefined, fetcher);
}

export function fetchWorkspacePreview(projectId: string, changesetId: string, changesetFingerprint: string, fetcher: typeof fetch = fetch) {
  return request<PreviewView>("/api/v1/copilot/workspace/previews", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: projectId, changeset_id: changesetId, changeset_fingerprint: changesetFingerprint }) }, fetcher);
}

export function approveWorkspaceApply(projectId: string, changesetId: string, changesetFingerprint: string, reviewerId: string, nonce: string, fetcher: typeof fetch = fetch) {
  return request<ApprovalView>("/api/v1/copilot/workspace/apply-approvals", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: projectId, changeset_id: changesetId, changeset_fingerprint: changesetFingerprint, reviewer_id: reviewerId, nonce }) }, fetcher);
}

export function applyWorkspaceChange(projectId: string, changesetId: string, changesetFingerprint: string, approvalId: string, fetcher: typeof fetch = fetch) {
  return request<ReceiptView>("/api/v1/copilot/workspace/operations", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: projectId, changeset_id: changesetId, changeset_fingerprint: changesetFingerprint, approval_id: approvalId, operation: "WORKSPACE_APPLY" }) }, fetcher);
}
