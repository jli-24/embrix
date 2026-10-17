import { useState } from "react";

import { applyWorkspaceChange, approveWorkspaceApply, fetchWorkspaceChangeset, fetchWorkspacePreview, type ChangeSetView, type PreviewView, type ReceiptView, WorkspaceApplyRequestError } from "../../api/workspaceApply";

export function WorkspaceApplyPanel({ projectId, changesetId }: { projectId: string | null; changesetId: string | null }) {
  const [changeset, setChangeset] = useState<ChangeSetView | null>(null);
  const [preview, setPreview] = useState<PreviewView | null>(null);
  const [receipt, setReceipt] = useState<ReceiptView | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!projectId || !changesetId) return;
    try {
      const next = await fetchWorkspaceChangeset(projectId, changesetId);
      setChangeset(next);
      setPreview(await fetchWorkspacePreview(projectId, changesetId, next.fingerprint));
      setError(null);
    } catch (reason) {
      setError(reason instanceof WorkspaceApplyRequestError ? reason.code : "WORKSPACE_APPLY_UNAVAILABLE");
    }
  }

  async function approveAndApply() {
    if (!projectId || !changeset) return;
    try {
      const approval = await approveWorkspaceApply(projectId, changeset.changeset_id, changeset.fingerprint, "desktop-reviewer", crypto.randomUUID());
      setReceipt(await applyWorkspaceChange(projectId, changeset.changeset_id, changeset.fingerprint, approval.grant_id));
      setError(null);
    } catch (reason) {
      setError(reason instanceof WorkspaceApplyRequestError ? reason.code : "WORKSPACE_APPLY_REJECTED");
    }
  }

  return <section className="ide-document-section" aria-label="Workspace controlled apply">
    <h3>Workspace Controlled Apply</h3>
    <p>Proposal approval and apply approval are separate human decisions.</p>
    {!changesetId && <p className="ide-list-empty">No ChangeSet selected.</p>}
    {changeset && <>
      <div className="ide-context-count"><span>Affected files</span><strong>{changeset.entries.length}</strong></div>
      <div className="ide-context-count"><span>ChangeSet fingerprint</span><strong>{changeset.fingerprint.slice(0, 20)}</strong></div>
      <div className="ide-context-count"><span>Validation</span><strong>{preview?.validation_status ?? "Unavailable"}</strong></div>
      <p>Risk: {preview?.risk ?? "Unavailable"}</p>
      <pre aria-label="Change preview">{preview?.diff ?? "Preview unavailable."}</pre>
      <button type="button" onClick={() => void approveAndApply()}>Approve &amp; Apply ChangeSet</button>
    </>}
    <button type="button" onClick={() => void refresh()} disabled={!projectId || !changesetId}>Refresh Change Preview</button>
    {receipt && <p role="status">Operation: {receipt.status} · Recovery: {receipt.recovery_status}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
