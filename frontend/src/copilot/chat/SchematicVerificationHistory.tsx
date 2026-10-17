import { useEffect, useState } from "react";

import { listSchematicVerifications, type SchematicVerificationView } from "../api/projectClient";

type Props = { projectId: string; conversationId: string };

export function SchematicVerificationHistory({ projectId, conversationId }: Props) {
  const [records, setRecords] = useState<SchematicVerificationView[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    setRecords([]);
    setSelectedId("");
    setError(null);
    void listSchematicVerifications(projectId, conversationId).then((items) => {
      if (active) setRecords(items);
    }).catch(() => {
      if (active) setError("VERIFICATION_HISTORY_UNAVAILABLE");
    });
    return () => { active = false; };
  }, [projectId, conversationId, refresh]);

  const selected = records.find((item) => item.record.verification_id === selectedId);
  return <section aria-label="原理图验证记录" className="ide-review-card">
    <h3>原理图验证记录</h3>
    <button type="button" onClick={() => setRefresh((value) => value + 1)}>从 Owner 重新读取</button>
    {error && <p role="alert">{error}</p>}
    <label>选择历史记录<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
      <option value="">请选择记录</option>
      {records.map(({ record, currentness }) => <option key={record.verification_id} value={record.verification_id}>
        {record.verification_id} · {currentness} · ERC {record.erc_status} · {record.aggregate_status}
      </option>)}
    </select></label>
    {selected && <div>
      <p>当前性：{selected.currentness}；系统 ERC 局部结果：{selected.record.erc_status}；整体：{selected.record.aggregate_status}</p>
      {selected.currentness === "STALE" && <p>历史记录已过期；历史 PASS 不代表当前通过。</p>}
      <p>Intent：{selected.record.intent_id} · {selected.record.intent_revision}；Plan：{selected.record.plan_id}</p>
      <p>Artifact fingerprint：{selected.record.artifact_fingerprint}</p>
      {selected.record.claims.map((claim) => <p key={claim.claim_id}>
        {claim.claim_id}：{claim.status}{selected.currentness === "STALE" && claim.status === "PASS" ? "（历史）" : ""}（{claim.reason}）
      </p>)}
    </div>}
  </section>;
}
