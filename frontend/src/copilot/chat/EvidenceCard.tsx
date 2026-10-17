import { EvidenceReference } from "../../design-system/components";
import type { ContextReference } from "../api/chatClient";
import type { AuditView } from "../api/environmentClient";
import { findReferenceAudit, formatProjectionTime, shortFingerprint } from "../viewModels";

export function EvidenceCard({ reference, audits, compact }: { reference: ContextReference; audits: AuditView[]; compact: boolean }) {
  const audit = findReferenceAudit(reference, audits);
  return <EvidenceReference label={reference.display_label} referenceId={reference.reference_id} status={audit?.status ?? "Observed"} fingerprint={shortFingerprint(reference.source_fingerprint)} timestamp={audit?.timestamp ? formatProjectionTime(audit.timestamp) : undefined} compact={compact} />;
}
