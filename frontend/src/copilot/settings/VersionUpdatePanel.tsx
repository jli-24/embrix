import { useEffect, useState } from "react";

import {
  checkDesktopUpdate,
  DesktopUpdateRequestError,
  fetchDesktopUpdateStatus,
  fetchRunningBuildIdentity,
  installDesktopUpdate,
  restartDesktopApp,
  type DesktopUpdateProjection,
  type RunningBuildIdentity,
} from "../api/desktopUpdateClient";
import { useDesktopSessionState } from "../api/sessionState";

type VersionUpdatePanelProps = { fetcher?: typeof fetch };

function errorCode(error: unknown): string {
  return error instanceof DesktopUpdateRequestError ? error.code : "UNKNOWN";
}

function stateLabel(state: string): string {
  switch (state.toUpperCase()) {
    case "UNCHECKED": return "未检查";
    case "NO_UPDATE": return "无更新";
    case "CANDIDATE": return "发现候选";
    case "CHECK_FAILED": return "检查失败";
    case "VALIDATION_FAILED": return "校验失败";
    case "WAIT_RESTART": return "等待重启";
    case "BOOT_VERIFIED": return "启动验证成功";
    case "ROLLED_BACK": return "已回滚";
    case "UNAVAILABLE": return "状态不可用";
    default: return "状态不可用";
  }
}

export function VersionUpdatePanel({ fetcher = fetch }: VersionUpdatePanelProps) {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const [projection, setProjection] = useState<DesktopUpdateProjection | null>(null);
  const [runningIdentity, setRunningIdentity] = useState<RunningBuildIdentity | null>(null);
  const [runningIdentityFailure, setRunningIdentityFailure] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const [pendingInstall, setPendingInstall] = useState<{ build_id: string; sha256: string } | null>(null);

  useEffect(() => {
    let active = true;
    if (sessionExpired) return () => { active = false; };
    void fetchRunningBuildIdentity(fetcher).then((value) => {
      if (active) { setRunningIdentity(value); setRunningIdentityFailure(false); }
    }).catch(() => {
      if (active) setRunningIdentityFailure(true);
    });
    void fetchDesktopUpdateStatus(fetcher).then((value) => {
      if (active) { setProjection(value); setFailure(null); }
    }).catch((error: unknown) => {
      if (active) setFailure(errorCode(error));
    });
    return () => { active = false; };
  }, [fetcher, sessionExpired]);

  async function run(action: () => Promise<DesktopUpdateProjection>): Promise<void> {
    if (sessionExpired || busy) return;
    setPendingInstall(null);
    setBusy(true);
    setFailure(null);
    try { setProjection(await action()); }
    catch (error: unknown) { setFailure(errorCode(error)); }
    finally { setBusy(false); }
  }

  const candidate = projection?.candidate;
  const showCandidate = projection?.state.toUpperCase() === "CANDIDATE"
    && Boolean(candidate?.build_id && candidate.sha256);
  const showInstallConfirmation = showCandidate && pendingInstall?.build_id === candidate?.build_id
    && pendingInstall?.sha256 === candidate?.sha256;
  const notes = [...new Set([...(candidate?.notes ?? []), ...(projection?.notes ?? [])])];
  const updateUnavailable = failure === "DESKTOP_UPDATE_UNAVAILABLE" || failure === "HTTP_503";
  const disabled = sessionExpired || busy || updateUnavailable;

  return <section className="ide-settings__section" data-version-update-panel aria-labelledby="version-update-heading">
    <h3 id="version-update-heading">版本与更新</h3>
    <p><strong>当前状态</strong> {sessionExpired ? "Desktop 会话已过期" : failure ? "检查失败" : projection ? stateLabel(projection.state) : "未检查"}</p>
    {failure && <p role="alert">{updateUnavailable ? "此包更新不可用" : "更新状态暂不可用"}（{failure}）。</p>}
    {runningIdentity && <><h4>当前运行包</h4><dl>
      <div><dt>运行包版本</dt><dd>{runningIdentity.product_version}</dd></div>
      <div><dt>运行包 Build</dt><dd>{runningIdentity.build_id}</dd></div>
      <div><dt>运行包 Commit</dt><dd>{runningIdentity.commit ?? "未知"}</dd></div>
      <div><dt>运行包源码指纹</dt><dd>{runningIdentity.source_fingerprint}</dd></div>
      <div><dt>运行包构建时间</dt><dd>{runningIdentity.build_time}</dd></div>
    </dl></>}
    {runningIdentityFailure && <p>运行包身份不可用。</p>}
    {projection && <><h4>固定安装槽</h4><dl>
      <div><dt>固定安装槽版本</dt><dd>{projection.installed_identity?.product_version ?? "未知"}</dd></div>
      <div><dt>固定安装槽 Build</dt><dd>{projection.installed_identity?.build_id ?? "未知"}</dd></div>
      <div><dt>当前验证</dt><dd>{projection.installed_verification ?? "UNVERIFIED"}</dd></div>
      <div><dt>回滚 Build</dt><dd>{projection.previous_identity?.build_id ?? "无"}</dd></div>
      <div><dt>回滚验证</dt><dd>{projection.previous_verification ?? "UNVERIFIED"}</dd></div>
      <div><dt>固定安装槽源码指纹</dt><dd>{projection.installed_identity?.source_fingerprint ?? "未知"}</dd></div>
      <div><dt>固定安装槽构建时间</dt><dd>{projection.installed_identity?.build_time ?? "未知"}</dd></div>
      <div><dt>模式</dt><dd>{projection.installed_identity?.mode ?? "未知"}</dd></div>
      <div><dt>更新通道</dt><dd>{projection.channel || "未知"}</dd></div>
    </dl></>}
    {showCandidate && candidate && <div data-update-candidate>
      <h4>发现候选版本</h4>
      {candidate.product_version && <p>版本：{candidate.product_version}</p>}
      <p>Build：{candidate.build_id}</p>
      <p>SHA-256：{candidate.sha256}</p>
      <p>大小：{candidate.size} 字节</p>
      <button type="button" className="ide-secondary-button" disabled={notes.length === 0} onClick={() => setShowNotes((value) => !value)}>查看说明</button>
      {showNotes && notes.map((note, index) => <p key={`${index}:${note}`}>{note}</p>)}
      <button type="button" className="ide-secondary-button" disabled={disabled} onClick={() => setPendingInstall({ build_id: candidate.build_id, sha256: candidate.sha256 })}>安装更新</button>
      {showInstallConfirmation && pendingInstall && <div className="ide-settings__update-confirmation" role="group" aria-label="确认安装更新" data-update-confirmation>
        <strong>确认安装此更新？</strong>
        <p>Build：{pendingInstall.build_id}</p>
        <p>SHA-256：{pendingInstall.sha256}</p>
        <div className="ide-settings__actions">
          <button type="button" className="ide-secondary-button" disabled={disabled} onClick={() => setPendingInstall(null)}>取消</button>
          <button type="button" className="ide-primary-button" disabled={disabled} onClick={() => void run(() => installDesktopUpdate(pendingInstall, fetcher))}>确认安装</button>
        </div>
      </div>}
    </div>}
    <div className="ide-settings__actions">
      <button type="button" className="ide-secondary-button" disabled={disabled} onClick={() => void run(() => checkDesktopUpdate(fetcher))}>检查更新</button>
      <button type="button" className="ide-secondary-button" disabled={disabled || projection?.state.toUpperCase() !== "WAIT_RESTART"} onClick={() => void run(() => restartDesktopApp(fetcher))}>立即重启</button>
    </div>
  </section>;
}
