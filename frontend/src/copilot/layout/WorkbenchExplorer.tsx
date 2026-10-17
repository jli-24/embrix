import { ChevronDown, ChevronRight, FileCode2, FileText, Folder, FolderOpen, HardDrive, MessageSquare, MoreHorizontal, Plus, TestTube2 } from "lucide-react";
import { useState, type MouseEvent } from "react";
import type { WorkbenchResourceNode } from "../api/workbenchClient";
import type { ProjectWorkspaceNavigationItem } from "../projectWorkspaceIdentity";

function icon(node: WorkbenchResourceNode) {
  if (node.node_type === "DIRECTORY") return node.parent_id ? <Folder size={14} /> : <FolderOpen size={14} />;
  if (node.category === "HARDWARE") return <HardDrive size={14} />;
  if (node.category === "FIRMWARE") return <FileCode2 size={14} />;
  if (node.category === "TEST") return <TestTube2 size={14} />;
  return <FileText size={14} />;
}

export type ResourceLoadState = "NO_PROJECT" | "NO_LOCATION" | "LOADING" | "API_ERROR" | "EMPTY_DIRECTORY" | "LOADED_ZERO" | "LOADED";
function lifecycleLabel(lifecycle: ProjectWorkspaceNavigationItem["identity"]["lifecycle"]): string {
  return lifecycle === "ready" ? "就绪" : "已创建";
}
type ConversationItem = { conversation_id: string; title: string; updated_at?: string | null };

export function WorkbenchExplorer({
  projects,
  activeProjectId,
  resources,
  activeResourceId,
  resourceState = "NO_PROJECT",
  resourceError = null,
  conversations = [],
  activeConversationId = null,
  onProjectChange,
  onResource,
  onNewProject,
  onNewConversation,
  onConversationChange,
  onRemoveProject,
  onRetry,
  catalogError,
  onReferenceResource,
  onRenameProject,
  onProjectSettings,
  onRenameConversation,
  onDeleteConversation,
}: {
  projects: ProjectWorkspaceNavigationItem[];
  activeProjectId: string;
  resources: WorkbenchResourceNode[];
  activeResourceId: string | null;
  resourceState?: ResourceLoadState;
  resourceError?: string | null;
  conversations?: ConversationItem[];
  activeConversationId?: string | null;
  onProjectChange(id: string): void;
  onResource(id: string): void;
  onNewProject(): void;
  onNewConversation?(): void;
  onConversationChange?(id: string): void;
  onRemoveProject?(id: string): void;
  onRetry?(): void;
  catalogError: string | null;
  onReferenceResource?(id: string): void;
  onRenameProject?(id: string): void;
  onProjectSettings?(id: string): void;
  onRenameConversation?(id: string): void;
  onDeleteConversation?(id: string): void;
}) {
  const [resourcesOpen, setResourcesOpen] = useState(true);
  const [projectMenuId, setProjectMenuId] = useState<string | null>(null);
  const [removeConfirmId, setRemoveConfirmId] = useState<string | null>(null);
  const [conversationMenuId, setConversationMenuId] = useState<string | null>(null);
  const [conversationDeleteId, setConversationDeleteId] = useState<string | null>(null);
  const [resourceMenuId, setResourceMenuId] = useState<string | null>(null);
  const emptyMessage = resourceState === "NO_PROJECT"
    ? "请选择项目"
    : resourceState === "NO_LOCATION"
      ? "当前项目尚未关联工程目录"
      : resourceState === "LOADING"
        ? "正在加载工程资源…"
        : resourceState === "API_ERROR"
          ? `工程资源加载失败${resourceError ? `：${resourceError}` : ""}`
          : resourceState === "EMPTY_DIRECTORY"
            ? "工程目录为空（当前项目尚未发现可加载的工程资源）"
            : resourceState === "LOADED_ZERO"
              ? "工程目录已加载，但未发现可投影的工程资源（当前项目尚未发现可加载的工程资源）"
              : "暂无工程资源";
  const openProjectMenu = (event: MouseEvent, id: string) => { event.preventDefault(); setProjectMenuId(id); setConversationMenuId(null); setResourceMenuId(null); };
  const openConversationMenu = (event: MouseEvent, id: string) => { event.preventDefault(); setConversationMenuId(id); setProjectMenuId(null); setResourceMenuId(null); };
  const conversationTitleCounts = new Map<string, number>();

  return <section className="embrix-explorer" data-surface-layer="base-supporting">
    <header className="embrix-explorer-heading"><div><h2>项目</h2></div><button className="ide-icon-button" type="button" aria-label="新建项目" title="新建项目" onClick={onNewProject}><Plus size={16} /></button></header>
    <div className="embrix-explorer-scroll">
      <div className="embrix-project-list">{projects.length ? projects.map((item) => <div key={item.identity.id} className={`embrix-project-row ${item.identity.id === activeProjectId ? "is-active" : ""}`} onContextMenu={(event) => openProjectMenu(event, item.identity.id)}>
        <button type="button" onClick={() => { setProjectMenuId(null); onProjectChange(item.identity.id); }}><span>{item.identity.name}</span><small>{item.identity.platform ?? "平台不可用"} · {lifecycleLabel(item.identity.lifecycle)}</small></button>
        <button type="button" className="embrix-row-menu-button" aria-label={`${item.identity.name} 项目菜单`} aria-expanded={projectMenuId === item.identity.id} onClick={() => { setProjectMenuId((current) => current === item.identity.id ? null : item.identity.id); setConversationMenuId(null); }}><MoreHorizontal size={15} /></button>
        <div hidden={projectMenuId !== item.identity.id} className="embrix-row-menu" role="menu" aria-label={`${item.identity.name} 项目操作`}>
          <button type="button" disabled={!onRenameProject} title={!onRenameProject ? "当前 Project Catalog 未提供重命名合同" : "重命名项目"} onClick={() => { onRenameProject?.(item.identity.id); setProjectMenuId(null); }}>Rename{!onRenameProject && " 不可用"}</button>
          <button type="button" disabled={!onProjectSettings} title={!onProjectSettings ? "当前 Project Catalog 未提供项目级设置合同" : "打开项目设置"} onClick={() => { onProjectSettings?.(item.identity.id); setProjectMenuId(null); }}>Project Settings{!onProjectSettings && " 不可用"}</button>
          {removeConfirmId === item.identity.id ? <><p>将仅移除 Embrix 项目记录，不删除本地工程目录。</p><button type="button" className="is-danger" aria-label="Confirm Remove from Embrix" onClick={() => { setRemoveConfirmId(null); setProjectMenuId(null); onRemoveProject?.(item.identity.id); }}>确认从 Embrix 移除</button><button type="button" onClick={() => setRemoveConfirmId(null)}>取消</button></> : <button type="button" aria-label="Remove from Embrix" title="仅移除 Embrix 项目记录；不删除本地工程目录" onClick={() => setRemoveConfirmId(item.identity.id)}>从 Embrix 移除</button>}
        </div>
      </div>) : <p className="muted">暂无 Project Catalog 项目。</p>}</div>
      {catalogError && <p className="ide-context-unavailable" role="alert">{catalogError}</p>}
      <section className="embrix-tree" data-resource-state={resourceState}>
        <button type="button" className="embrix-tree-heading" onClick={() => setResourcesOpen((value) => !value)}>{resourcesOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />} <strong>工程资源</strong>{resources.length > 0 && <span>{resources.length}</span>}</button>
        {resourcesOpen && <div className="embrix-tree-list">
          {resources.map((node) => <div className={`embrix-resource-row ${resourceMenuId === node.resource_id ? "is-context-open" : ""}`} key={node.resource_id}>
            <button type="button" className={`${node.node_type === "DIRECTORY" ? "is-directory" : ""} ${activeResourceId === node.resource_id ? "is-active" : ""}`} style={{ paddingLeft: `${8 + node.relative_path.split("/").length * 10}px` }} onClick={() => node.node_type === "FILE" && onResource(node.resource_id)} onContextMenu={(event) => { if (node.node_type !== "FILE") return; event.preventDefault(); setResourceMenuId(node.resource_id); setProjectMenuId(null); setConversationMenuId(null); }}>{icon(node)}<span>{node.name}</span></button>
            <div hidden={resourceMenuId !== node.resource_id} className="embrix-resource-context-menu" role="menu"><button type="button" disabled={!onReferenceResource} onClick={() => { onReferenceResource?.(node.resource_id); setResourceMenuId(null); }}>引用到当前对话</button>{!onReferenceResource && <p>当前引用发送链不可用。</p>}</div>
          </div>)}
          {!resources.length && <><p className="muted" role={resourceState === "API_ERROR" ? "alert" : undefined}>{emptyMessage}</p>{resourceState === "API_ERROR" && <button type="button" className="ide-command" onClick={onRetry}>重试</button>}</>}
        </div>}
      </section>
      <section className="embrix-conversation-history"><div className="embrix-tree-heading"><MessageSquare size={14} /><strong>对话历史</strong><button className="embrix-compact-add" type="button" onClick={onNewConversation} aria-label="新建对话" title="新建对话"><Plus size={14} /></button></div>
        {conversations.map((item) => {
          const baseTitle = item.title?.trim() || "新对话";
          const count = (conversationTitleCounts.get(baseTitle) ?? 0) + 1;
          conversationTitleCounts.set(baseTitle, count);
          const displayTitle = count > 1 ? `${baseTitle} ${count}` : baseTitle;
          return <div key={item.conversation_id} className={`embrix-conversation-row ${item.conversation_id === activeConversationId ? "is-active" : ""}`} onContextMenu={(event) => openConversationMenu(event, item.conversation_id)}>
            <button type="button" onClick={() => onConversationChange?.(item.conversation_id)}>{displayTitle}</button>
            <button type="button" className="embrix-row-menu-button" aria-label={`对话菜单：${displayTitle}`} aria-expanded={conversationMenuId === item.conversation_id} onClick={() => { setConversationMenuId((current) => current === item.conversation_id ? null : item.conversation_id); setProjectMenuId(null); }}><MoreHorizontal size={15} /></button>
            <div hidden={conversationMenuId !== item.conversation_id} className="embrix-row-menu" role="menu" aria-label={`${displayTitle} 操作`}>
              {onRenameConversation && <button type="button" title="重命名对话" onClick={() => { onRenameConversation(item.conversation_id); setConversationMenuId(null); }}>重命名</button>}
              {onDeleteConversation && <button type="button" title="删除对话" onClick={() => { setConversationDeleteId(item.conversation_id); setConversationMenuId(null); }}>删除</button>}
            </div>
            {conversationDeleteId === item.conversation_id && onDeleteConversation && <div className="embrix-conversation-delete-confirm" role="alertdialog" aria-label="删除对话确认"><strong>删除这个对话？</strong><span>只删除 Embrix 中的对话记录，不会删除本地工程文件。</span><div><button type="button" onClick={() => setConversationDeleteId(null)}>取消</button><button type="button" className="is-danger" onClick={() => { setConversationDeleteId(null); onDeleteConversation(item.conversation_id); }}>删除</button></div></div>}
          </div>;
        })}
      </section>
    </div>
  </section>;
}
