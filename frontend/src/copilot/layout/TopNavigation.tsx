import { Menu, PanelRight, ShieldCheck } from "lucide-react";
import { useState } from "react";

export function brandAsset(path: string): string { return `${import.meta.env.BASE_URL.replace(/\/$/, "")}/brand/${path}`; }

function statusText(value: string): string {
  const normalized = value.toUpperCase();
  return normalized === "AVAILABLE" ? "可用" : normalized === "DEGRADED" ? "部分可用" : normalized === "UNVERIFIED" ? "未验证" : normalized === "BETA" ? "Beta" : normalized === "LOADING" ? "加载中" : "不可用";
}

function statusTooltip(label: string, value: string): string {
  const normalized = value.toUpperCase();
  const detail = normalized === "AVAILABLE"
    ? "状态：可用；原因：后端已声明 capability；影响：可进入对应只读界面；建议动作：按需继续操作。"
    : normalized === "DEGRADED"
      ? "状态：部分可用；原因：后端报告降级；影响：部分数据可能缺失；建议动作：查看 Settings 与 Advanced Diagnostics。"
      : normalized === "UNVERIFIED"
        ? "状态：未验证；原因：尚无足够验证证据；影响：不会自动启用高影响操作；建议动作：查看 Verification。"
        : normalized === "BETA"
          ? "状态：Beta；原因：能力仍受限；影响：不可推导为已验证执行；建议动作：等待受控 capability。"
          : "状态：不可用；原因：后端未提供可用 capability；影响：入口保持禁用；建议动作：查看 Settings 与 Advanced Diagnostics。";
  return `${label}：${detail}`;
}

function shortStatusLabel(label: string): string {
  if (label === "AI" || label === "AI Connection") return "AI";
  if (label === "Provider Configured") return "Provider";
  if (label === "Credential Saved") return "Credential";
  if (label === "Model Readiness") return "Model";
  if (label === "Project Location") return "Project";
  return label;
}

export function TopNavigation({ onToggleLeft, onToggleRight, onVerify, onBuild, projectName, statuses = [] }: { onToggleLeft(): void; onToggleRight(): void; onVerify?(): void; onBuild?(): void; projectName?: string | null; statuses?: Array<{ label: string; value: string; tone: string }> }) {
  const [systemStatusOpen, setSystemStatusOpen] = useState(false);
  return <nav className="ide-topbar" aria-label="桌面导航" data-product-mode="LOCAL_FIRST" data-global-download-action="ABSENT" data-global-export-action="ABSENT">
    <div className="ide-topbar__brand"><button className="ide-icon-button ide-mobile-only" type="button" aria-label="打开项目列表" onClick={onToggleLeft}><Menu size={18} /></button><img className="embrix-brand-mark" src={brandAsset("embrix-mark-transparent-hd.png")} alt="Embrix" /><strong>Embrix</strong><span className="ide-topbar__subtitle">AI EMBEDDED ENGINEERING</span></div>
    <div className="ide-topbar__project"><span>{projectName ?? "选择 Project"}</span><small>工程工作台</small></div>
    <div className="ide-topbar__statuses"><div className="ide-topbar__status-strip">{statuses.map((status) => <span className={`top-status top-status--${status.tone}`} key={status.label} title={statusTooltip(status.label, status.value)}><i /> <span className="top-status__full">{status.label} {statusText(status.value)}</span><span className="top-status__short">{shortStatusLabel(status.label)} {statusText(status.value)}</span></span>)}</div><div className="ide-topbar__status-overflow"><button type="button" className="ide-command" aria-haspopup="menu" aria-expanded={systemStatusOpen} onClick={() => setSystemStatusOpen((value) => !value)}>System Status <span>⌄</span></button>{systemStatusOpen && <div className="ide-topbar__status-menu" role="menu">{statuses.map((status) => <span className={`top-status top-status--${status.tone}`} role="menuitem" key={status.label} title={statusTooltip(status.label, status.value)}><i /> {status.label} {statusText(status.value)}</span>)}</div>}</div><div className="ide-topbar__actions"><button type="button" className="ide-command ide-command--primary" aria-label="查看验证结果" data-verification-semantics="VIEW_VERIFICATION" onClick={onVerify}><ShieldCheck size={14} /> Verification</button><button type="button" className="ide-command" onClick={onBuild} disabled={!onBuild} title="打开受控 Firmware Build 工作区；执行仍需逐步确认与审批">Build</button></div></div>
    <button className="ide-icon-button ide-mobile-only ide-topbar__right" type="button" aria-label="打开工程上下文" onClick={onToggleRight}><PanelRight size={18} /></button>
  </nav>;
}
