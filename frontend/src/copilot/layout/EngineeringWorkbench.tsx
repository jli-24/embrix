import {
  Activity,
  Braces,
  CircleHelp,
  FileCode2,
  FileText,
  FlaskConical,
  FolderTree,
  GitBranch,
  HardDrive,
  MoreHorizontal,
  Play,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Table2,
  TestTube2,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";
import type { ChatEvidence } from "../api/projectClient";
import { useExecutionEvents } from "../execution/ExecutionEventProvider";
import type { WorkbenchFinding, WorkbenchProjection, WorkbenchResourceNode } from "../api/workbenchClient";
import type { InspectorMode } from "./adaptiveLayout";

const activities = [
  ["explorer", "Explorer", FolderTree],
  ["copilot", "Copilot", Activity],
  ["code", "Code", Braces],
  ["hardware", "Hardware", HardDrive],
  ["simulation", "Simulation", FlaskConical],
  ["control", "Control", SlidersHorizontal],
  ["test", "Test", TestTube2],
  ["reports", "Reports", FileText],
  ["settings", "Settings", Settings2],
] as const;

export function ActivityBar({ active, onChange }: { active: string; onChange(value: string): void }) {
  return <nav
    className="embrix-activity-bar"
    aria-label="Activity Bar"
    data-product-mode="LOCAL_FIRST"
    data-global-download-action="ABSENT"
    data-global-export-action="ABSENT"
    data-activity-bar-download-icon="ABSENT"
    data-activity-bar-cloud-action="ABSENT"
  >
    {activities.map(([key, label, Icon]) => <button
      key={key}
      type="button"
      className={active === key ? "is-active" : ""}
      aria-label={label}
      aria-current={active === key ? "page" : undefined}
      onClick={() => onChange(key)}
    ><Icon size={18} /><span>{label}</span></button>)}
  </nav>;
}

function statusLabel(value: string | null | undefined): { label: string; tone: string } {
  switch ((value ?? "UNAVAILABLE").toUpperCase()) {
    case "AVAILABLE": return { label: "可用", tone: "success" };
    case "DEGRADED": return { label: "部分可用", tone: "warning" };
    case "UNVERIFIED": return { label: "未验证", tone: "warning" };
    case "BETA": return { label: "Beta", tone: "info" };
    default: return { label: "不可用", tone: "muted" };
  }
}

function resourceIcon(node: WorkbenchResourceNode) {
  if (node.node_type === "DIRECTORY") return <FolderTree size={14} />;
  if (node.category === "HARDWARE") return <HardDrive size={14} />;
  if (node.category === "FIRMWARE") return <FileCode2 size={14} />;
  if (node.category === "TEST") return <TestTube2 size={14} />;
  return <FileText size={14} />;
}

export function EngineeringCopilotPanel({ workflowStatus, chat }: { projectName: string | null; workflowStatus: string; evidenceCount?: number; findings?: WorkbenchFinding[]; proposals?: WorkbenchProjection["repair_alternatives"]; chat: ReactNode; onFinding?: (id: string) => void }) {
  return <section
    className="embrix-copilot-panel"
    data-surface-layer="primary-raised"
    data-conversation-primary="true"
    data-fixed-analysis-dashboard-removed="true"
    data-internal-pipeline="hidden"
    data-copilot-collapse="removed"
  >
    <header className="embrix-panel-heading">
      <div><h2>工程 Copilot</h2></div>
    </header>
    <div className="embrix-copilot-conversation">{chat}</div>
  </section>;
}

export function EngineeringWorkspace({ projectName, resources, activeResourceId, openTabIds = [], onResource, activeActivity, capabilities, resourceState = "NO_PROJECT", onBuild, onOpenResource, onRevealResource }: { projectName: string | null; resources: WorkbenchResourceNode[]; activeResourceId: string | null; openTabIds?: string[]; onResource(id: string): void; activeActivity: string; capabilities: Array<{ capability: string; status: string }>; resourceState?: string; onBuild?(): void; onOpenResource?(id: string): void; onRevealResource?(id: string): void }) {
  const filtered = useMemo(() => activeActivity === "code"
    ? resources.filter((item) => item.category === "FIRMWARE")
    : activeActivity === "hardware"
      ? resources.filter((item) => item.category === "HARDWARE")
      : activeActivity === "reports"
        ? resources.filter((item) => item.category === "DOCUMENT")
        : resources, [activeActivity, resources]);
  const simulation = capabilities.find((item) => item.capability === "HIL_EXECUTION" || item.capability === "SIMULATION");
  const activeResource = activeActivity === "test" ? null : filtered.find((item) => item.resource_id === activeResourceId);
  const workspaceTitle = { explorer: "Explorer", code: "Code", hardware: "Hardware", reports: "Reports", test: "Verification" }[activeActivity] ?? "工程工作区";
  const emptyStatus = resourceState === "API_ERROR" ? "工程资源读取失败；可在左侧重试。已有验证信息仍可在下方查看。" : resourceState === "LOADING" ? "正在加载工程资源。" : !projectName ? "选择 Project 后查看工程资源。" : resourceState === "NO_LOCATION" ? "当前项目尚未关联工程目录。" : resourceState === "EMPTY_DIRECTORY" || resourceState === "LOADED_ZERO" ? "当前项目没有可投影的工程资源。" : "选择工程资源查看只读元数据。";
  const tabs = resources.filter((item) => openTabIds.includes(item.resource_id));
  return <section className="embrix-workspace" data-surface-layer="primary-raised" data-contextual-status={activeResource ? "visible" : "hidden"}>
    <header className="embrix-workspace-tabs">
      <div className="embrix-tab-list" role="tablist" aria-label="工程资源标签页" data-real-resource-tabs="true">
        {tabs.map((item) => <button key={item.resource_id} type="button" role="tab" aria-selected={activeResource?.resource_id === item.resource_id} className={activeResource?.resource_id === item.resource_id ? "is-active" : ""} onClick={() => onResource(item.resource_id)}>{resourceIcon(item)} {item.name}</button>)}
      </div>
      {activeResource && <span className="embrix-workspace-mode"><GitBranch size={14} /> 只读资源</span>}
    </header>
    <div className="embrix-workspace-body">
      {activeActivity === "simulation" ? <div className="embrix-unavailable" data-capability="SIMULATION"><FlaskConical size={34} /><h2>Virtual Lab</h2><p>{statusLabel(simulation?.status).label}：当前没有已验证的 Simulation backend。</p></div>
        : activeActivity === "control" ? <ControlLabSkeleton />
            : activeActivity === "test" ? <div className="embrix-workspace-empty"><h2>Verification</h2><p>{resourceState === "API_ERROR" ? "工程资源读取失败；已有验证信息仍可在下方查看。" : !projectName ? "选择 Project 后查看验证信息。" : "查看下方 Verification 面板中的现有结果；切换页面不会执行测试。"}</p></div>
            : activeResource ? <div className="embrix-resource-preview" data-preview-state="UNAVAILABLE"><div className="embrix-preview-toolbar"><span>{resourceIcon(activeResource)} {activeResource.relative_path}</span><span className="status-pill status-pending">Preview unavailable</span></div><div className="embrix-preview-canvas"><FileText size={44} /><h2>{activeResource.name}</h2><p>工程产物已从当前项目资源投影中找到；当前仅提供只读元数据。</p><div className="embrix-artifact-actions" aria-label="工程产物操作"><button type="button" disabled={!onOpenResource} title={!onOpenResource ? "当前本地打开能力未接入" : "打开本地产物"} onClick={() => onOpenResource?.(activeResource.resource_id)}>查看</button><button type="button" disabled={!onRevealResource} title={!onRevealResource ? "当前资源管理器能力未接入" : "在资源管理器中显示"} onClick={() => onRevealResource?.(activeResource.resource_id)}>在工程中定位</button></div></div></div>
            : <div className="embrix-workspace-empty" data-contextual-status="hidden"><h2>{workspaceTitle}</h2><p>{emptyStatus}</p>{activeActivity === "code" && <button type="button" onClick={onBuild} disabled={!onBuild}>打开受控 Firmware Build</button>}{filtered.length > 0 && <div className="embrix-resource-grid">{filtered.slice(0, 12).map((item) => <button type="button" key={item.resource_id} onClick={() => onResource(item.resource_id)}>{resourceIcon(item)}<span>{item.name}</span><small>{item.category}</small></button>)}</div>}</div>}
    </div>
  </section>;
}

export function EngineeringInspector({ finding, resource, evidence = null, mode = "docked", onModeChange, onClose }: { finding: WorkbenchFinding | null; resource: WorkbenchResourceNode | null; evidence?: ChatEvidence | null; mode?: InspectorMode; onModeChange?(mode: InspectorMode): void; onClose?(): void }) {
  return <section className="embrix-inspector" data-inspector-selection={finding ? "finding" : resource ? "resource" : evidence ? "evidence" : "none"}>
    <header className="embrix-panel-heading" data-inspector-drag-handle="true">
      <div><span className="eyebrow">INSPECTOR</span><h2>{finding ? "Finding" : resource ? "Object" : evidence ? "Evidence" : "Inspector"}</h2></div>
      <div className="embrix-inspector-actions"><button type="button" className="ide-icon-button" aria-label={mode === "floating" ? "停靠到右侧" : "浮动 Inspector"} title={mode === "floating" ? "停靠到右侧" : "浮动 Inspector"} onClick={() => onModeChange?.(mode === "floating" ? "docked" : "floating")}>{mode === "floating" ? "⇥" : "⇱"}</button><button type="button" className="ide-icon-button" aria-label="关闭 Inspector" title="隐藏 Inspector" onClick={onClose}><span aria-hidden="true">×</span></button></div>
    </header>
    <div className="embrix-inspector-scroll">
      {finding ? <><span className={`severity severity--${finding.severity.toLowerCase()}`}>{finding.severity}</span><h3>{finding.subject}</h3><p>{finding.summary}</p><dl><dt>ID</dt><dd>{finding.finding_id}</dd><dt>Evidence</dt><dd>{finding.evidence_ids.length || "暂无可用投影"}</dd><dt>Confidence</dt><dd>{finding.confidence === null ? "暂无可用投影" : `${Math.round(finding.confidence * 100)}%`}</dd><dt>Target Identity</dt><dd>{finding.target_identity ?? "暂无可用投影"}</dd><dt>Target Fingerprint</dt><dd>{finding.target_fingerprint ?? "暂无可用投影"}</dd><dt>Workspace 定位</dt><dd>{resource ? "已定位" : "定位不可用"}</dd><dt>Rule / Location</dt><dd>暂无可用投影</dd><dt>Root Blocker</dt><dd>暂无可用投影</dd><dt>Affected Features</dt><dd>暂无可用投影</dd></dl>{finding.limitations.length > 0 && <p className="muted">限制：{finding.limitations.join("；")}</p>}</>
        : resource ? <><div className="embrix-object-icon">{resourceIcon(resource)}</div><h3>{resource.name}</h3><dl><dt>Type</dt><dd>{resource.node_type}</dd><dt>Category</dt><dd>{resource.category}</dd><dt>Language</dt><dd>{resource.language ?? "暂无可用投影"}</dd><dt>Relative path</dt><dd>{resource.relative_path}</dd><dt>Fingerprint</dt><dd>{resource.source_fingerprint ?? "暂无可用投影"}</dd><dt>Provenance</dt><dd>资源投影</dd><dt>Evidence Links</dt><dd>暂无可用投影</dd></dl></>
          : evidence ? <><div className="embrix-object-icon"><CircleHelp size={18} /></div><h3>{evidence.label}</h3><p>{evidence.summary ?? "暂无可用投影"}</p><dl><dt>Source Type</dt><dd>{evidence.source_type}</dd><dt>Reference ID</dt><dd>{evidence.reference_id}</dd><dt>Status</dt><dd>{evidence.status}</dd><dt>Fingerprint</dt><dd>{evidence.source_fingerprint || "暂无可用投影"}</dd><dt>Observed At</dt><dd>{evidence.observed_at || "暂无可用投影"}</dd><dt>Provenance</dt><dd>{evidence.source_uri ?? "暂无可用投影"}</dd></dl></>
            : <p className="muted">选择 Finding 或工程资源查看详情（也可查看 Evidence）。</p>}
    </div>
  </section>;
}

export function ControlLabSkeleton() {
  const rows = ["Controller", "Telemetry", "Experiment", "Optimization", "Safety", "Verification"];
  return <div className="embrix-control-skeleton" data-control-state="UNAVAILABLE"><h2>Control Lab</h2><p>Control Engineering Runtime unavailable.</p><dl>{rows.map((row) => <div key={row}><dt>{row}</dt><dd>Unavailable</dd></div>)}</dl></div>;
}

function operationEventLabel(eventType: string): string {
  const labels: Record<string, string> = {
    OPERATION_QUEUED: "Queued",
    OPERATION_CLAIMED: "Running",
    OPERATION_STARTED: "Running",
    OPERATION_SUCCEEDED: "Completed",
    OPERATION_FAILED: "Failed",
    OPERATION_CANCELLED: "Cancelled",
    OPERATION_TIMED_OUT: "Timed Out",
    RECOVERY_REQUIRED: "Recovery Required",
    RECOVERY_RESOLVED: "Recovery Resolved",
    APPROVAL_ISSUED: "Approval Recorded",
    OPERATION_CREATED: "Created",
  };
  return labels[eventType] ?? "Operation Event";
}

function operationKindLabel(operationType?: string): string {
  const labels: Record<string, string> = {
    FIRMWARE_SOURCE_WRITE: "Firmware Source",
    KICAD_SCHEMATIC_WRITE: "Schematic",
    KICAD_SCHEMATIC_ERC: "Schematic ERC",
  };
  return labels[operationType ?? ""] ?? "Engineering Operation";
}


export function EngineeringBottomPanel({ projection, findings, activeTab, onTab, collapsed = false }: { projection: DesktopWorkspaceProjection | null; findings: WorkbenchFinding[]; activeTab: string; onTab(value: string): void; collapsed?: boolean }) {
  const executionEvents = useExecutionEvents();
  const [overflowOpen, setOverflowOpen] = useState(false);
  const tabs = [["verification", "Verification", ShieldCheck], ["output", "Output", Table2], ["uart", "UART", Activity], ["waveform", "Waveform", Activity], ["events", "Operation Events", Play]] as const;
  const verificationEmpty = !projection ? "验证投影不可用：验证结果投影不可用" : projection.validations.items.length === 0 ? "尚未运行验证" : "当前验证未发现问题";
  const unavailable = activeTab === "uart" ? "UNAVAILABLE — Virtual/Hardware UART capability unavailable" : activeTab === "waveform" ? "UNAVAILABLE — Observation backend unavailable" : "Output capability 当前不可用或尚未运行。";
  const renderTab = ([key, label, Icon]: typeof tabs[number]) => <button type="button" role="tab" aria-selected={activeTab === key} className={activeTab === key ? "is-active" : ""} key={key} onClick={() => { onTab(key); setOverflowOpen(false); }}><Icon size={14} /> {label}</button>;
  return <section className={`embrix-bottom-panel ${collapsed ? "is-collapsed" : ""}`} data-bottom-state={collapsed ? "secondary-collapsed" : "expanded"} data-surface-layer="supporting">
    <nav role="tablist" aria-label="验证与输出面板" data-bottom-tabs="responsive">{tabs.map(renderTab)}<div className="embrix-bottom-overflow" data-bottom-overflow-menu="true"><button type="button" className="embrix-bottom-overflow__trigger" aria-label="更多面板" aria-haspopup="menu" aria-expanded={overflowOpen} onClick={() => setOverflowOpen((value) => !value)}><MoreHorizontal size={15} /> <span>更多面板</span></button>{overflowOpen && <div className="embrix-bottom-overflow__menu" role="menu">{tabs.slice(2).map(([key, label, Icon]) => <button type="button" role="menuitem" key={key} onClick={() => { onTab(key); setOverflowOpen(false); }}><Icon size={14} /> {label}</button>)}</div>}</div></nav>
    {!collapsed && <div className="embrix-bottom-content">{activeTab === "verification" && <>{findings.length ? findings.map((finding) => <div className="embrix-verification-row" key={finding.finding_id}><span className={`severity severity--${finding.severity.toLowerCase()}`}>{finding.severity}</span><strong>{finding.finding_id}</strong><span>{finding.finding_type}</span><span>{finding.subject}</span><span>{finding.target_identity ?? "位置不可用"}</span></div>) : <p className="muted">{verificationEmpty}</p>}</>}{activeTab === "events" && <>{executionEvents.events.length ? executionEvents.events.slice().reverse().map((event, index) => <div className="embrix-verification-row" key={`${event.event_id ?? event.event_type}-${index}`}><span className="status-pill status-pending">{operationEventLabel(event.event_type)}</span><strong>{operationKindLabel(event.operation_type)}</strong><span>{event.status ?? "状态不可用"}</span><span>{event.reason_code}</span></div>) : <p className="muted">{executionEvents.error ? `Operation Events 不可用：${executionEvents.error}` : "Operation Events 使用 canonical execution worker 事件流；当前没有事件。"}</p>}</>}{activeTab !== "verification" && activeTab !== "events" && <p className="muted">{unavailable}</p>}</div>}
  </section>;
}
