import { useEffect, useRef, useState } from "react";
import { createDrcBaselineOperation, fetchEdaImportRevision, type EdaImportRevision } from "../api/projectClient";
import { approveExecution, fetchExecutionReceipt, fetchExecutionStatus, type ExecutionOperationStatus, type ExecutionReceipt } from "../api/executionClient";

type Props = { projectId: string; revision: EdaImportRevision; reviewerId?: string; reviewerScope?: string; disabled?: boolean };
export function DrcBaselinePanel({ projectId, revision, reviewerId, reviewerScope, disabled = false }: Props) {
  const [pcb, setPcb] = useState("");
  const [operation, setOperation] = useState<ExecutionOperationStatus | null>(null);
  const [receipt, setReceipt] = useState<ExecutionReceipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const scope = JSON.stringify([projectId, revision.import_revision_id, revision.import_revision_fingerprint, revision.active,
    revision.source_currentness, revision.observation_currentness, pcb, reviewerId, reviewerScope]);
  const live = useRef({ mounted: true, scope, disabled, generation: 0 });
  if (live.current.scope !== scope || live.current.disabled !== disabled) live.current.generation += 1;
  live.current.scope = scope; live.current.disabled = disabled;
  useEffect(() => { live.current.mounted = true; return () => { live.current.mounted = false; live.current.generation += 1; }; }, []);
  useEffect(() => { setPcb(""); setOperation(null); setReceipt(null); setError(null); }, [projectId, revision.import_revision_id, revision.import_revision_fingerprint]);
  const currentRevision = revision.backend === "KICAD" && revision.active && revision.source_currentness === "CURRENT" && revision.observation_currentness === "CURRENT";
  const boards = revision.manifest.filter((item) => item.kind === "FILE" && item.relative_path.endsWith(".kicad_pcb"));
  async function action(work: (request: <T>(fn: () => Promise<T>) => Promise<T>) => Promise<void>) {
    if (disabled || lock.current) return;
    const generation = live.current.generation;
    const current = () => live.current.mounted && !live.current.disabled && live.current.scope === scope && live.current.generation === generation;
    const requireCurrent = () => { if (!current()) throw new Error("BASELINE_SCOPE_CHANGED"); };
    async function request<T>(fn: () => Promise<T>): Promise<T> { requireCurrent(); const value = await fn(); requireCurrent(); return value; }
    lock.current = true; setBusy(true); setError(null);
    try { await work(request); } catch (value) { if (current()) setError(value instanceof Error ? value.message : "DRC_BASELINE_UNAVAILABLE"); }
    finally { lock.current = false; if (live.current.mounted) setBusy(false); }
  }
  async function prepare() {
    if (!currentRevision || !pcb || !boards.some((item) => item.relative_path === pcb)) return;
    await action(async (request) => {
      const fresh = await request(() => fetchEdaImportRevision(projectId, revision.import_revision_id, revision.import_revision_fingerprint));
      if (!fresh.active || fresh.backend !== "KICAD" || fresh.source_currentness !== "CURRENT" || fresh.observation_currentness !== "CURRENT"
        || fresh.import_revision_id !== revision.import_revision_id || fresh.import_revision_fingerprint !== revision.import_revision_fingerprint
        || !fresh.manifest.some((item) => item.kind === "FILE" && item.relative_path === pcb)) throw new Error("SOURCE_STALE");
      const status = await request(() => createDrcBaselineOperation(projectId, { import_revision_id: fresh.import_revision_id,
        import_revision_fingerprint: fresh.import_revision_fingerprint, pcb_relative_path: pcb }));
      if (status.project_id !== projectId || status.operation_type !== "KICAD_PCB_DRC_BASELINE") throw new Error("BASELINE_SCOPE_CHANGED");
      setOperation(status); setReceipt(null);
    });
  }
  async function approve() {
    const selected = operation; const reviewer = reviewerId;
    if (!selected || !reviewer || !currentRevision || !pcb || !boards.some((item) => item.relative_path === pcb) || selected.status !== "WAIT_APPROVAL") return;
    await action(async (request) => {
      const fresh = await request(() => fetchExecutionStatus(projectId, selected.operation_id));
      if (fresh.operation_id !== selected.operation_id || fresh.project_id !== projectId || fresh.operation_type !== "KICAD_PCB_DRC_BASELINE" || fresh.status !== "WAIT_APPROVAL") throw new Error("APPROVAL_STALE");
      await request(() => approveExecution(projectId, selected.operation_id, reviewer, crypto.randomUUID(), new Date(Date.now() + 60_000).toISOString()));
      setOperation(await request(() => fetchExecutionStatus(projectId, selected.operation_id)));
    });
  }
  async function refresh() {
    const selected = operation;
    if (!selected) return;
    await action(async (request) => {
      const fresh = await request(() => fetchExecutionStatus(projectId, selected.operation_id));
      if (fresh.operation_id !== selected.operation_id || fresh.project_id !== projectId || fresh.operation_type !== "KICAD_PCB_DRC_BASELINE") throw new Error("BASELINE_SCOPE_CHANGED");
      setOperation(fresh);
      if (fresh.receipt_fingerprint) {
        const actual = await request(() => fetchExecutionReceipt(projectId, selected.operation_id));
        if (actual.operation_id !== selected.operation_id || actual.project_id !== projectId || actual.operation_type !== "KICAD_PCB_DRC_BASELINE" || actual.fingerprint !== fresh.receipt_fingerprint) throw new Error("BASELINE_RECEIPT_STALE");
        setReceipt(actual);
      }
    });
  }
  const observation = receipt?.status === "SUCCEEDED" ? /^DRC_BASELINE_OBSERVATION scope=BASELINE_COMPARISON_ONLY error_count=(0|[1-9][0-9]*) warning_count=(0|[1-9][0-9]*)$/.exec(receipt.safe_diagnostics_summary) : null;
  return <section aria-label="独立 DRC baseline 观察">
    <h3>独立 DRC baseline 观察</h3><p>BASELINE_COMPARISON_ONLY · NO_REFILL · schematic parity NOT_RUN。成功仅表示采集观察；设计仍未验证。</p>
    <label>观察 PCB<select aria-label="baseline PCB" value={pcb} disabled={disabled || busy} onChange={(event) => { setPcb(event.target.value); setOperation(null); setReceipt(null); }}><option value="">显式选择 PCB</option>{boards.map((item) => <option key={item.relative_path} value={item.relative_path}>{item.relative_path}</option>)}</select></label>
    <button type="button" disabled={disabled || busy || !currentRevision || !pcb} onClick={() => void prepare()}>准备 baseline operation</button>
    {!currentRevision && <p>BLOCKED：需要当前 active KiCad C1 revision。</p>}
    {!reviewerId && <p>审批身份：UNKNOWN</p>}
    {operation && <div><p>operation：{operation.operation_id} · {operation.status} · recovery {operation.recovery_state}</p>
      <button type="button" disabled={disabled || busy || !reviewerId || !currentRevision || !pcb || !boards.some((item) => item.relative_path === pcb) || operation.status !== "WAIT_APPROVAL"} onClick={() => void approve()}>批准并排队采集 baseline</button>
      <button type="button" disabled={disabled || busy} onClick={() => void refresh()}>刷新 baseline 观察</button></div>}
    {receipt && <div><p>Receipt：{receipt.status}</p>{observation ? <p>观察 scope：BASELINE_COMPARISON_ONLY；errors {observation[1]}；warnings {observation[2]}；设计未验证。</p> : <p>观察 counts：UNKNOWN</p>}<p>诊断成员：{receipt.evidence_references.join(", ")}</p></div>}
    {error && <p role="alert">BLOCKED：{error}</p>}
  </section>;
}
