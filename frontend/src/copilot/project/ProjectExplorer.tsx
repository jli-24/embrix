import { ChevronDown, ChevronRight, Folder, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import type { ConversationSummary, ProjectView } from "../api/projectClient";
import { identityPlatform, toProjectWorkspaceNavigationItem, type ProjectWorkspaceNavigationItem } from "../projectWorkspaceIdentity";
import { taskStatusLabel, type EngineeringTaskHistorySummary } from "../task/engineeringTaskModel";

const PROJECTS_COLLAPSED_KEY = "embedded-copilot.sidebar.projects-collapsed";
const HISTORY_COLLAPSED_KEY = "embedded-copilot.sidebar.history-collapsed";

export type CatalogState = "loading" | "ready" | "unavailable";

function readCollapsed(key: string): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage?.getItem(key) === "true";
}

function activity(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

type HistoryItem = EngineeringTaskHistorySummary;

function mergeHistoryItems(conversations: ConversationSummary[], summaries: EngineeringTaskHistorySummary[], activeProjectId: string): HistoryItem[] {
  const merged = new Map<string, HistoryItem>();
  for (const conversation of conversations) {
    if (conversation.project_id === activeProjectId) merged.set(conversation.conversation_id, { projectId: conversation.project_id, conversationId: conversation.conversation_id, title: conversation.title, updatedAt: conversation.updated_at, turnCount: conversation.turn_count });
  }
  for (const summary of summaries) {
    if (summary.projectId === activeProjectId) merged.set(summary.conversationId, { ...merged.get(summary.conversationId), ...summary });
  }
  return [...merged.values()];
}

type ProjectExplorerProps = {
  projects: Array<ProjectWorkspaceNavigationItem | ProjectView>;
  activeProjectId: string;
  activeConversationId?: string | null;
  conversations: ConversationSummary[];
  taskSummaries?: EngineeringTaskHistorySummary[];
  catalogError: string | null;
  catalogState?: CatalogState;
  settingsOpen: boolean;
  onProjectChange(projectId: string): void;
  onNewProject(): void;
  onNewConversation(): void;
  onConversationChange(conversationId: string): void;
  onDeleteProject?(projectId: string): void;
  onToggleSettings(): void;
  protectedActionsEnabled?: boolean;
};

export function ProjectExplorer({ projects, activeProjectId, activeConversationId, conversations, taskSummaries = [], catalogState = "ready", onProjectChange, onNewProject, onNewConversation, onConversationChange, onDeleteProject, protectedActionsEnabled = true }: ProjectExplorerProps) {
  const [showAll, setShowAll] = useState(false);
  const [projectMenu, setProjectMenu] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProjectWorkspaceNavigationItem | null>(null);
  const [projectsCollapsed, setProjectsCollapsed] = useState(() => readCollapsed(PROJECTS_COLLAPSED_KEY));
  const [historyCollapsed, setHistoryCollapsed] = useState(() => readCollapsed(HISTORY_COLLAPSED_KEY));
  const historyItems = mergeHistoryItems(conversations, taskSummaries, activeProjectId);
  const visible = showAll ? historyItems : historyItems.slice(0, 5);
  const navigationProjects = projects.map((project) => "identity" in project ? project : toProjectWorkspaceNavigationItem(project));

  useEffect(() => { window.localStorage?.setItem(PROJECTS_COLLAPSED_KEY, String(projectsCollapsed)); }, [projectsCollapsed]);
  useEffect(() => { window.localStorage?.setItem(HISTORY_COLLAPSED_KEY, String(historyCollapsed)); }, [historyCollapsed]);

  return <div className="ide-sidebar">
    <div className="ide-sidebar__scroll">
      <section className={`ide-sidebar-section ${projectsCollapsed ? "is-collapsed" : ""}`} aria-labelledby="projects-heading">
        <header>
          <button className="ide-sidebar-section__toggle" type="button" aria-expanded={!projectsCollapsed} aria-controls="projects-list" onClick={() => setProjectsCollapsed((value) => !value)}><ChevronDown size={14} aria-hidden="true" /><h2 id="projects-heading">项目</h2></button>
          <button className="ide-sidebar-add" type="button" disabled={!protectedActionsEnabled} onClick={onNewProject} title="新建项目" aria-label="新建项目"><Plus size={16} aria-hidden="true" /></button>
        </header>
        {!projectsCollapsed && <div id="projects-list">
          {catalogState === "loading" && <p className="ide-list-empty">正在加载项目…</p>}
          {catalogState === "unavailable" && <div className="ide-sidebar-status ide-sidebar-status--unavailable"><strong>Project Catalog 不可用</strong><span>请检查项目服务连接后重试。</span></div>}
          {catalogState === "ready" && navigationProjects.length === 0 && <div className="ide-sidebar-status"><strong>尚未创建项目</strong><span>新建项目后即可开始工程对话。</span></div>}
          {catalogState === "ready" && navigationProjects.length > 0 && <div className="ide-project-list">{navigationProjects.map((project) => <div className={`ide-project-row ${project.identity.id === activeProjectId ? "is-active" : ""}`} key={project.identity.id}>
            <button className="ide-project-row__main" type="button" disabled={!protectedActionsEnabled} onClick={() => onProjectChange(project.identity.id)}><Folder size={16} aria-hidden="true" /><span><strong>{project.identity.name}</strong><small>{identityPlatform(project.identity)} · {activity(project.lastActivityAt)}</small></span></button>
            <button className="ide-row-menu-button" type="button" disabled={!protectedActionsEnabled} aria-label={`${project.identity.name} 更多操作`} aria-expanded={projectMenu === project.identity.id} onClick={() => setProjectMenu((value) => value === project.identity.id ? null : project.identity.id)}><MoreHorizontal size={16} /></button>
            {projectMenu === project.identity.id && <div className="ide-row-menu"><button type="button" onClick={() => setProjectMenu(null)}>查看详情</button><button type="button" onClick={() => { void navigator.clipboard?.writeText(project.identity.id); setProjectMenu(null); }}>复制 Project ID</button><button className="ide-row-menu__danger" type="button" onClick={() => { setDeleteTarget(project); setProjectMenu(null); }}><Trash2 size={14} aria-hidden="true" />删除项目</button></div>}
          </div>)}</div>}
        </div>}
      </section>
      <section className={`ide-sidebar-section ide-history ${historyCollapsed ? "is-collapsed" : ""}`} aria-labelledby="history-heading">
        <header>
          <button className="ide-sidebar-section__toggle" type="button" aria-expanded={!historyCollapsed} aria-controls="history-list" onClick={() => setHistoryCollapsed((value) => !value)}><ChevronDown size={14} aria-hidden="true" /><h2 id="history-heading">对话历史</h2></button>
          <button className="ide-sidebar-add" type="button" disabled={!protectedActionsEnabled || !activeProjectId} onClick={onNewConversation} title="新对话" aria-label="新对话"><Plus size={16} aria-hidden="true" /></button>
        </header>
        {!historyCollapsed && <div id="history-list">
          {activeProjectId && historyItems.length === 0 && <p className="ide-list-empty">当前项目还没有对话。</p>}
          {!activeProjectId && <p className="ide-list-empty">选择项目后查看 Conversation History。</p>}
          <div className="ide-history-list">{visible.map((item) => <button className={item.conversationId === activeConversationId ? "is-active" : ""} type="button" disabled={!protectedActionsEnabled} key={item.conversationId} onClick={() => onConversationChange(item.conversationId)}><strong>{item.title}</strong>{item.status ? <span className="ide-history-summary"><span>{taskStatusLabel(item.status)}</span>{item.evidenceCount !== undefined && item.evidenceCount > 0 && <span>Evidence {item.evidenceCount}</span>}{item.memoryCount !== undefined && item.memoryCount > 0 && <span>Memory {item.memoryCount}</span>}</span> : <span>{activity(item.updatedAt)} · {item.turnCount ?? 0} 条消息</span>}</button>)}</div>
          {historyItems.length > 5 && <button className="ide-view-all" type="button" onClick={() => setShowAll((value) => !value)}>{showAll ? "收起历史" : "查看全部历史"}<ChevronRight size={14} className={showAll ? "is-rotated" : ""} /></button>}
        </div>}
      </section>
    </div>
    {deleteTarget && onDeleteProject && <div className="ide-confirm-scrim" role="presentation"><section className="ide-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-project-title"><div className="ide-confirm-dialog__icon"><Trash2 size={18} aria-hidden="true" /></div><h2 id="delete-project-title">删除项目？</h2><p>将删除该项目的 Project Catalog 记录、对话、会话绑定和 Copilot 上下文。</p><p className="ide-confirm-dialog__warning">不会删除磁盘工程目录及其中的源代码、硬件文件、文档或测试文件。</p><footer><button className="ide-secondary-button" type="button" onClick={() => setDeleteTarget(null)}>取消</button><button className="ide-danger-button" type="button" disabled={!protectedActionsEnabled} onClick={() => { onDeleteProject(deleteTarget.identity.id); setDeleteTarget(null); }}>删除项目</button></footer></section></div>}
  </div>;
}
