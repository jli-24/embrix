import { ContextStatus, Panel } from "../../design-system/components";
import type { RuntimePanel as RuntimePanelView } from "../api/chatClient";
import type { ProjectContext, ProjectView } from "../api/projectClient";
import { formatMemoryDate, memorySourceLabel, normalizeEngineeringMemoryProjection, type EngineeringMemoryProjection } from "../memory/memoryViewModel";
import { identityPlatform, toEngineeringContextProjection, toProjectWorkspaceIdentity, type EngineeringContextProjection, type ProjectWorkspaceIdentity } from "../projectWorkspaceIdentity";
import { taskStatusLabel, type EngineeringTaskProjection } from "../task/engineeringTaskModel";

function tone(status: string): "neutral" | "success" | "warning" | "danger" | "primary" {
  const value = status.toUpperCase();
  if (value === "UNAVAILABLE") return "neutral";
  if (value === "CONNECTED") return "success";
  if (["AVAILABLE", "READY"].includes(value)) return "primary";
  if (value.includes("FAIL") || value.includes("ERROR")) return "danger";
  if (value.includes("REVIEW") || value.includes("INITIAL") || value.includes("PENDING")) return "warning";
  return "neutral";
}

function displayStatus(value: string): string {
  const upper = value.toUpperCase();
  if (upper === "CONNECTED") return "已连接";
  if (upper === "AVAILABLE" || upper === "READY") return "可用";
  if (["PENDING", "INITIALIZING", "UNAVAILABLE"].includes(upper)) return "等待连接";
  if (upper.includes("FAIL") || upper.includes("ERROR")) return "不可用";
  return "不可用";
}

export function RuntimePanel({ activeProjectIdentity = null, project = null, taskProjection = null, context, projection, memoryProjection, panel, serviceError }: { activeProjectIdentity?: ProjectWorkspaceIdentity | null; project?: ProjectView | null; taskProjection?: EngineeringTaskProjection | null; context: ProjectContext | null; projection?: EngineeringContextProjection | null; memoryProjection?: EngineeringMemoryProjection | null; panel: RuntimePanelView | null; serviceError: string | null }) {
  const identity = activeProjectIdentity ?? toProjectWorkspaceIdentity(project, null);
  const contextProjection = projection ?? toEngineeringContextProjection(identity, context, panel);
  const memory = normalizeEngineeringMemoryProjection(identity?.id ?? null, memoryProjection);
  const projectedMemoryCount = memory.items.length > 0 ? memory.items.length : taskProjection?.memoryCount ?? 0;
  const resources = new Map((contextProjection?.resources ?? []).map((item) => [item.capability, item.status]));
  const evidenceSources = new Map((contextProjection?.evidenceSources ?? []).map((item) => [item.source.toUpperCase(), item.count]));
  const evidenceRows = ["HIL", "EDA", "Firmware", "Memory"].filter((source) => (evidenceSources.get(source.toUpperCase()) ?? 0) > 0);

  return <Panel title="工程上下文" className="ide-project-context">
    {serviceError && <p className="ide-context-unavailable">{serviceError}</p>}
    <section>
      <h3>当前项目</h3>
      {identity ? <dl className="ide-project-facts">
        <div><dt>项目名称</dt><dd>{identity.name}</dd></div>
        <div><dt>平台</dt><dd>{identityPlatform(identity)}</dd></div>
        <div><dt>版本</dt><dd>{identity.version ?? "不可用"}</dd></div>
        <div><dt>分支</dt><dd>{identity.branch ?? "不可用"}</dd></div>
        <div><dt>状态</dt><dd>{identity.lifecycle === "ready" ? "已创建" : "初始化中"}</dd></div>
      </dl> : <p className="ide-list-empty">未选择项目。</p>}
    </section>
    {taskProjection && <section>
      <h3>当前任务</h3>
      <div className="ide-task-context"><strong>{taskProjection.identity.title}</strong><span>状态</span><span>{taskStatusLabel(taskProjection.conversationStatus)}</span></div>
    </section>}
    <section>
      <h3>当前引用</h3>
      {contextProjection?.references.length ? <div className="ide-context-groups">
        {contextProjection.references.map((group) => <div className="ide-context-count" key={group.category}><span>{group.category}</span><strong>{group.count} references</strong></div>)}
      </div> : <p className="ide-list-empty">暂无引用</p>}
    </section>
    <section>
      <h3>工程记忆</h3>
      {memory.items.length ? <div className="ide-memory-list"><p className="ide-memory-count">已形成 {memory.items.length} 条工程摘要</p>{memory.items.map((item) => <article className="ide-memory-item" key={item.id}>
        <strong>{item.title}</strong>
        <p>{item.summary}</p>
        <div><span>来源</span><span>{memorySourceLabel(item.source)}</span><time>{formatMemoryDate(item.createdAt)}</time></div>
      </article>)}</div> : projectedMemoryCount > 0 ? <div className="ide-memory-empty"><p>已形成 {projectedMemoryCount} 条工程摘要</p></div> : <div className="ide-memory-empty"><p>暂无工程记忆</p><span>完成当前工程任务后，Copilot 将保存安全工程摘要。</span></div>}
    </section>
    <section>
      <h3>证据来源</h3>
      {evidenceRows.length > 0 ? evidenceRows.map((source) => <div className="ide-context-count" key={source}><span>{source}</span><strong>{evidenceSources.get(source.toUpperCase())}</strong></div>) : <p className="ide-list-empty">尚未产生工程证据</p>}
    </section>
    <section>
      <h3>工程资源</h3>
      {["Project Files", "Knowledge", "HIL", "EDA"].map((name) => {
        const status = resources.get(name) ?? "UNAVAILABLE";
        return <ContextStatus key={name} label={name} value={displayStatus(status)} tone={tone(status)} />;
      })}
    </section>
  </Panel>;
}
