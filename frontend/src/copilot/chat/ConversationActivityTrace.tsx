import { useState } from "react";

import type { ConversationActivityItem, ActivityStatus } from "./conversationActivity";

type Props = {
  activities: readonly ConversationActivityItem[];
  initiallyExpanded?: boolean;
};

function statusIcon(item: ConversationActivityItem): string {
  if (item.not_applicable || item.result_state === "NOT_APPLICABLE") return "—";
  if (item.result_state === "UNKNOWN") return "?";
  if (item.result_state === "STALE" || item.status === "STALE") return "↻";
  if (item.result_state === "UNAVAILABLE") return "!";
  if (item.status === "RUNNING") return "○";
  if (item.status === "AWAITING_APPROVAL") return "◇";
  if (item.status === "FAIL") return "✕";
  if (item.status === "WARNING") return "!";
  if (item.status === "BLOCKED") return "◇";
  if (item.status === "PASS") return "✓";
  return "○";
}

function statusLabel(item: ConversationActivityItem): string {
  if (item.not_applicable || item.result_state === "NOT_APPLICABLE") return "不适用";
  if (item.result_state === "UNKNOWN") return "尚无法验证";
  if (item.result_state === "STALE" || item.status === "STALE") return "需要重新验证";
  if (item.result_state === "UNAVAILABLE") return "暂不可用";
  const labels: Record<ActivityStatus, string> = {
    PENDING: "待执行",
    RUNNING: "正在执行",
    PASS: "已完成",
    WARNING: "需要注意",
    FAIL: "未通过",
    BLOCKED: "已阻塞",
    AWAITING_APPROVAL: "等待批准",
    STALE: "需要重新验证",
  };
  return labels[item.status];
}

function aggregateLabel(activities: readonly ConversationActivityItem[]): string {
  if (activities.some((item) => item.status === "AWAITING_APPROVAL")) return "等待你的批准";
  if (activities.some((item) => item.status === "RUNNING")) return "正在执行";
  if (activities.some((item) => item.status === "FAIL")) return "执行未通过";
  if (activities.some((item) => item.status === "STALE" || item.result_state === "STALE")) return "需要重新验证";
  if (activities.some((item) => item.result_state === "UNKNOWN")) return "部分结果尚无法验证";
  if (activities.some((item) => item.result_state === "UNAVAILABLE")) return "部分结果暂不可用";
  if (activities.every((item) => item.result_state === "NOT_APPLICABLE")) return "本次步骤均不适用";
  if (activities.every((item) => item.status === "PASS")) {
    const notApplicableCount = activities.filter((item) => item.result_state === "NOT_APPLICABLE").length;
    return `已完成 ${activities.length - notApplicableCount} 个步骤${notApplicableCount ? `，${notApplicableCount} 项不适用` : ""}`;
  }
  return `${activities.length} 个执行步骤`;
}

function DetailField({ label, value }: { label: string; value: string | null }) {
  return <div className="conversation-activity__field"><dt>{label}</dt><dd>{value ?? "UNAVAILABLE"}</dd></div>;
}

function ActivityDetail({ activity }: { activity: ConversationActivityItem }) {
  return <div className="conversation-activity__detail">
    <dl>
      <DetailField label="Capability" value={activity.capability} />
      <DetailField label="Tool" value={activity.tool} />
      <DetailField label="Operation" value={activity.operation} />
      <DetailField label="Status" value={statusLabel(activity)} />
      <DetailField label="Result" value={activity.summary ?? (activity.result_state === "UNKNOWN" ? "尚无法验证" : activity.result_state === "NOT_APPLICABLE" ? "不适用" : null)} />
    </dl>
    {activity.evidence_refs.length > 0 && <div className="conversation-activity__refs"><span>Evidence</span>{activity.evidence_refs.map((reference) => <code key={reference}>{reference}</code>)}</div>}
    {activity.artifact_refs.length > 0 && <div className="conversation-activity__refs"><span>Artifact</span>{activity.artifact_refs.map((reference) => <code key={reference}>{reference.split(/[\\/]/u).at(-1) ?? reference}</code>)}</div>}
    {activity.children.length > 0 && <div className="conversation-activity__children">{activity.children.map((child) => <ActivityRow key={child.id} activity={child} child />)}</div>}
    {activity.developer_detail && <details className="conversation-activity__developer"><summary>开发者详情</summary><code>{activity.developer_detail}</code></details>}
  </div>;
}

function ActivityRow({ activity, child = false }: { activity: ConversationActivityItem; child?: boolean }) {
  return <details className={`conversation-activity__row ${child ? "conversation-activity__row--child" : ""}`} data-activity-id={activity.id} data-activity-kind={activity.kind} data-activity-status={activity.status} data-result-state={activity.result_state ?? "UNAVAILABLE"}>
    <summary><span className={`conversation-activity__icon conversation-activity__icon--${activity.status.toLowerCase()}`} aria-hidden="true">{statusIcon(activity)}</span><span className="conversation-activity__title">{activity.title}</span><span className="conversation-activity__status">{statusLabel(activity)}</span></summary>
    <ActivityDetail activity={activity} />
  </details>;
}

export function ConversationActivityTrace({ activities, initiallyExpanded = false }: Props) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  if (!activities.length) return null;
  const toggleIcon = activities.some((item) => item.status === "RUNNING" || item.status === "PENDING") ? "○" :
    activities.some((item) => item.status === "FAIL") ? "✕" :
    activities.some((item) => item.status === "STALE") ? "↻" :
    activities.some((item) => item.status === "WARNING" || item.status === "BLOCKED" || item.status === "AWAITING_APPROVAL") ? "!" :
    activities.every((item) => item.result_state === "NOT_APPLICABLE") ? "—" : "✓";
  return <section className="conversation-activity" aria-label="执行活动" data-trace-collapsed={!expanded}>
    <button className="conversation-activity__toggle" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
      <span className="conversation-activity__toggle-icon" aria-hidden="true">{toggleIcon}</span>
      <span>{aggregateLabel(activities)}</span>
      <span className="conversation-activity__toggle-action">{expanded ? "收起" : "展开"}</span>
    </button>
    {expanded && <div className="conversation-activity__list" role="list">{activities.map((activity) => <ActivityRow key={activity.id} activity={activity} />)}</div>}
  </section>;
}
