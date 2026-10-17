import { useEffect, useState } from "react";

import { createProductionBackup, exportProductionDiagnostics, fetchProductionReleaseStatus, type ProductionReleaseStatus } from "../api/releaseClient";
import { useDesktopSessionState } from "../api/sessionState";

export function ProductionReleasePanel() {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const [status, setStatus] = useState<ProductionReleaseStatus | null>(null);
  const [operation, setOperation] = useState<string | null>(null);

  useEffect(() => { void fetchProductionReleaseStatus().then(setStatus).catch(() => setStatus(null)); }, []);
  if (!status) return null;
  return <section className="panel" aria-label="Production release status">
    <div className="panel-heading"><h2>Production Release</h2><span className="status-pill status-pending">{status.release_status}</span></div>
    <p>Version {status.product_version}; Clean-machine: {status.clean_machine_acceptance}; Signing: {status.installer_signing}.</p>
    <p className="muted">Hardware Golden: {status.hardware_golden}. Browser automation: {status.browser_automation}.</p>
    {sessionExpired && <p className="ide-form-error" role="alert">Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。</p>}
    <div className="load-controls"><button type="button" disabled={sessionExpired} onClick={() => void createProductionBackup().then((value) => setOperation(`Backup ${value.backup_id} created.`)).catch(() => setOperation(sessionExpired ? "Desktop session expired." : "Backup is unavailable."))}>Create backup</button><button type="button" disabled={sessionExpired} onClick={() => void exportProductionDiagnostics().then((value) => setOperation(`Diagnostics ${value.diagnostic_id} exported.`)).catch(() => setOperation(sessionExpired ? "Desktop session expired." : "Diagnostics export is unavailable."))}>Export diagnostics</button></div>
    {operation && <p className="muted">{operation}</p>}
  </section>;
}
