import { DrcBaselinePanel } from "./DrcBaselinePanel";
import { ArrowLeft, Folder, X } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import {
  confirmProject,
  commitEdaImport,
  fetchEdaImportRevision,
  fetchProjectDirectories,
  fetchProjectLocations,
  listEdaImportRevisions,
  previewEdaImport,
  registerProjectLocation,
  previewProject,
  projectLocationErrorMessage,
  type ProjectCreationMode,
  type ProjectDirectory,
  type ProjectLocation,
  type ProjectPreview,
  type ProjectType,
  type ProjectView,
  type ProjectCoverageV1,
  type EdaImportBackend,
  type EdaImportCommit,
  type EdaImportPreview,
  type EdaImportPreviewInput,
  type EdaImportRevision,
  type EdaImportRevisionSummary,
} from "../api/projectClient";
import { nativeProjectRootErrorMessage, selectProjectRoot, type NativeProjectRootSelection } from "../api/nativeProjectRoot";
import { useDesktopSessionState } from "../api/sessionState";

type NewProjectType = Exclude<ProjectType, "EXISTING_FIRMWARE">;

export type ProjectCreateDataSource = {
  locations: ProjectLocation[];
  directories?: Record<string, ProjectDirectory[]>;
  preview(input: {
    name: string;
    parent_location: Pick<ProjectLocation, "location_id" | "fingerprint">;
    project_type: NewProjectType;
    creation_mode: ProjectCreationMode;
    existing_directory: Pick<ProjectDirectory, "directory_id" | "fingerprint"> | null;
  }): Promise<ProjectPreview>;
  confirm(preview: ProjectPreview): Promise<ProjectView>;
  initial?: { name?: string; projectType?: NewProjectType; creationMode?: ProjectCreationMode };
  registerLocation?(path: string, displayLabel?: string): Promise<ProjectLocation>;
  selectProjectRoot?(): Promise<NativeProjectRootSelection>;
};

const types: Array<[NewProjectType, string]> = [
  ["ESP_IDF", "ESP-IDF"],
  ["STM32", "STM32"],
  ["ARDUINO", "Arduino"],
  ["GENERIC_FIRMWARE", "Generic Firmware"],
];

function projectLocationLabel(location: ProjectLocation): string {
  return `${location.display_label}${location.path_leaf ? ` · ${location.path_leaf}` : ""}`;
}

export function ProjectCreatePanel({ onClose, onCreated, dataSource }: { onClose(): void; onCreated(project: ProjectView): void | Promise<void>; dataSource?: ProjectCreateDataSource }) {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const [name, setName] = useState(dataSource?.initial?.name ?? "");
  const [projectType, setProjectType] = useState<NewProjectType>(dataSource?.initial?.projectType ?? "ESP_IDF");
  const [creationMode, setCreationMode] = useState<ProjectCreationMode>(dataSource?.initial?.creationMode ?? "CREATE_NEW");
  const [locations, setLocations] = useState<ProjectLocation[]>(dataSource?.locations ?? []);
  const [location, setLocation] = useState<ProjectLocation | null>(dataSource?.locations[0] ?? null);
  const [directories, setDirectories] = useState<ProjectDirectory[]>([]);
  const [directory, setDirectory] = useState<ProjectDirectory | null>(null);
  const [preview, setPreview] = useState<ProjectPreview | null>(null);
  const [pending, setPending] = useState(!dataSource);
  const [error, setError] = useState<string | null>(null);
  const [locationPending, setLocationPending] = useState(false);
  const [actionPending, setActionPending] = useState<"VALIDATING" | "CREATING" | null>(null);

  useEffect(() => {
    let active = true;
    const loadLocations = dataSource ? Promise.resolve(dataSource.locations) : fetchProjectLocations();
    void loadLocations.then((items) => {
      if (!active) return;
      const available = items.filter((item) => item.availability === "AVAILABLE");
      setLocations(available);
      setLocation(available[0] ?? null);
      if (!available.length) setError("当前没有可用的项目位置。");
    }).catch(() => { if (active) setError("无法加载受控项目位置。"); }).finally(() => { if (active) setPending(false); });
    return () => { active = false; };
  }, [dataSource]);

  useEffect(() => {
    setDirectory(null);
    setDirectories([]);
    if (creationMode !== "ATTACH_EXISTING" || !location) return;
    setPending(true);
    const loadDirectories = dataSource ? Promise.resolve(dataSource.directories?.[location.location_id] ?? []) : fetchProjectDirectories(location.location_id).then((value) => value.items);
    void loadDirectories.then((items) => {
      setDirectories(items);
      setDirectory(items[0] ?? null);
    }).catch(() => setError("无法加载可接入的项目目录。")).finally(() => setPending(false));
  }, [creationMode, dataSource, location]);

  async function review(event: FormEvent) {
    event.preventDefault();
    if (sessionExpired || actionPending || !name.trim() || !location || (creationMode === "ATTACH_EXISTING" && !directory)) return;
    setPending(true);
    setActionPending("VALIDATING");
    setError(null);
    try {
      const input = {
        name: name.trim(),
        parent_location: { location_id: location.location_id, fingerprint: location.fingerprint },
        project_type: projectType,
        creation_mode: creationMode,
        existing_directory: directory ? { directory_id: directory.directory_id, fingerprint: directory.fingerprint } : null,
      };
      setPreview(await (dataSource ? dataSource.preview(input) : previewProject(input)));
    } catch {
      setError("项目创建失败，请检查工程位置或项目名称。");
    } finally {
      setActionPending(null);
      setPending(false);
    }
  }

  async function create() {
    if (sessionExpired || !preview || actionPending) return;
    setPending(true);
    setActionPending("CREATING");
    setError(null);
    try {
      const created = await (dataSource ? dataSource.confirm(preview) : confirmProject(preview));
      await onCreated(created);
    } catch {
      setError("项目创建失败，请检查工程位置或项目名称。");
    } finally {
      setActionPending(null);
      setPending(false);
    }
  }

  async function addLocation() {
    if (sessionExpired) return;
    setLocationPending(true);
    setError(null);
    try {
      const selection = await (dataSource?.selectProjectRoot ? dataSource.selectProjectRoot() : selectProjectRoot());
      if (selection.status === "CANCELLED") return;
      if (selection.status !== "SELECTED" || !selection.path) {
        setError(nativeProjectRootErrorMessage(selection));
        return;
      }
      const created = await (dataSource?.registerLocation ? dataSource.registerLocation(selection.path) : registerProjectLocation(selection.path));
      setLocations((current) => [created, ...current.filter((item) => item.location_id !== created.location_id)]);
      setLocation(created);
    } catch (value) {
      setError(projectLocationErrorMessage(value));
    } finally {
      setLocationPending(false);
    }
  }

  return <div className="ide-dialog-scrim ide-dialog-scrim--panel" role="presentation"><section className="ide-project-create" role="dialog" aria-modal="true" aria-labelledby="new-project-title">
    <header><div><span>PROJECT</span><h2 id="new-project-title">新建嵌入式项目</h2></div><button className="ide-icon-button" type="button" aria-label="关闭新建项目" onClick={onClose}><X size={16} /></button></header>
    {sessionExpired && <p className="ide-form-error" role="alert">Desktop 会话已过期，请重新启动 Embrix 以重新建立安全桌面会话。</p>}
    {!preview ? <form onSubmit={review}>
      <p>创建一个新的嵌入式研发项目。</p>
      <p>系统将在指定位置创建 Project，并绑定工程上下文与 Copilot metadata。不会自动生成源码、构建文件或工程模板。</p>
      <label>项目名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：循迹智能小车" autoFocus /></label>
      <label>项目位置<div className="ide-folder-field"><button className="ide-folder-button" type="button" aria-label="选择工程位置" title="选择工程位置" onClick={() => void addLocation()} disabled={sessionExpired || locationPending}><Folder size={15} aria-hidden="true" /></button><select aria-label="选择文件夹" value={location?.location_id ?? ""} onChange={(event) => setLocation(locations.find((item) => item.location_id === event.target.value) ?? null)} disabled={sessionExpired || !locations.length}>{locations.length ? locations.map((item) => <option key={item.location_id} value={item.location_id}>{projectLocationLabel(item)}</option>) : <option value="">不可用</option>}</select></div></label>
      <label>项目类型<select value={projectType} onChange={(event) => setProjectType(event.target.value as NewProjectType)}>{types.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <fieldset className="ide-mode-select"><legend>创建方式</legend><div className="ide-mode-options"><label><input type="radio" checked={creationMode === "CREATE_NEW"} onChange={() => setCreationMode("CREATE_NEW")} />新工程</label><label><input type="radio" checked={creationMode === "ATTACH_EXISTING"} onChange={() => setCreationMode("ATTACH_EXISTING")} />接入已有工程</label></div></fieldset>
      {creationMode === "ATTACH_EXISTING" && <label>项目目录<select value={directory?.directory_id ?? ""} onChange={(event) => setDirectory(directories.find((item) => item.directory_id === event.target.value) ?? null)} disabled={!directories.length}>{directories.length ? directories.map((item) => <option key={item.directory_id} value={item.directory_id}>{item.display_name}</option>) : <option value="">没有可接入目录</option>}</select></label>}
      {error && <p className="ide-form-error" role="alert">{error}</p>}
      <footer><button className="ide-secondary-button" type="button" onClick={onClose}>取消</button><button className="ide-primary-button" type="submit" disabled={sessionExpired || pending || actionPending !== null || !name.trim() || !location || (creationMode === "ATTACH_EXISTING" && !directory)}>{actionPending === "VALIDATING" ? "正在检查…" : creationMode === "CREATE_NEW" ? "创建项目" : "接入项目"}</button></footer>
    </form> : <div className="ide-project-review">
      <p>创建确认</p>
      <p>系统已完成项目位置、身份和冲突校验。确认后将绑定工程上下文，不会自动烧录、构建或执行硬件操作。</p>
      <dl><div><dt>项目</dt><dd>{preview.name}</dd></div><div><dt>位置</dt><dd>{projectLocationLabel(preview.location)}</dd></div><div><dt>项目类型</dt><dd>{types.find(([value]) => value === preview.project_type)?.[1] ?? preview.project_type}</dd></div><div><dt>创建方式</dt><dd>{preview.creation_mode === "CREATE_NEW" ? "新工程" : "接入已有工程"}</dd></div></dl>
      <div className="ide-project-tree"><Folder size={15} /><strong>将创建</strong><span>{projectLocationLabel(preview.location)}/</span><span>└── {preview.directory_name}/</span></div>
      <div className="ide-project-scope"><strong>Copilot 将管理</strong><span>• 工程上下文</span><span>• 项目记忆</span><span>• 分析历史</span><strong>不会创建</strong><span>× 源码 · 构建文件 · 工程模板</span></div>
      {error && <p className="ide-form-error" role="alert">{error}</p>}
      <footer><button className="ide-secondary-button" type="button" onClick={() => setPreview(null)} disabled={sessionExpired || actionPending !== null}><ArrowLeft size={14} />返回修改</button><button className="ide-secondary-button" type="button" onClick={onClose} disabled={actionPending !== null}>取消</button><button className="ide-primary-button" type="button" disabled={sessionExpired || pending || actionPending !== null} onClick={() => void create()}>{actionPending === "CREATING" ? "正在创建…" : preview.creation_mode === "ATTACH_EXISTING" ? "确认接入" : "确认创建"}</button></footer>
    </div>}
  </section></div>;
}

export type EdaImportDataSource = {
  list(projectId: string, backend: EdaImportBackend, nativeEntry: string, cursor?: string): Promise<{ items: EdaImportRevisionSummary[]; next_cursor: string | null }>;
  detail(projectId: string, revisionId: string, fingerprint: string): Promise<EdaImportRevision>;
  preview(projectId: string, input: EdaImportPreviewInput): Promise<EdaImportPreview>;
  commit(projectId: string, input: { preview_id: string; preview_fingerprint: string }): Promise<EdaImportCommit>;
};

const edaImportSource: EdaImportDataSource = {
  list: (projectId, backend, nativeEntry, cursor) => listEdaImportRevisions(projectId, backend, nativeEntry, fetch, cursor),
  detail: fetchEdaImportRevision,
  preview: previewEdaImport,
  commit: commitEdaImport,
};

function edaImportError(error: unknown): string {
  const code = error instanceof Error ? error.message : "IMPORT_SOURCE_UNAVAILABLE";
  if (code === "IMPORT_HEAD_CHANGED") return "导入历史已变化。请重新查询并显式选择当前 revision。";
  if (code === "SOURCE_SET_CHANGED") return "原生文件集已变化。请重新创建只读预览。";
  if (code === "IMPORT_PREVIEW_EXPIRED") return "预览已过期。请重新创建只读预览。";
  if (code === "PROJECT_CATALOG_FORBIDDEN") return "当前 Desktop 会话不是该项目 Owner。";
  return `只读导入不可用：${code}`;
}

function ProjectCoverageView({ coverage }: { coverage: ProjectCoverageV1 | undefined }) {
  if (!coverage) return null;
  return <section aria-label="源对象结构覆盖">
    <h4>源对象结构覆盖</h4>
    <p>结构覆盖：{coverage.coverage_state}；依赖：{coverage.dependency_state}</p>
    <p>源文件：{coverage.files.length}；结构对象：{coverage.counts.objects_total}；已识别：{coverage.counts.recognized_total}；未知：{coverage.counts.unknown_total}</p>
    <p>源对象结构观察 · 未验证 · 不可修改</p>
    <details><summary>对象类别</summary><ul>{coverage.counts.by_kind.map((item) => <li key={item.native_kind}>{item.native_kind}: {item.count}</li>)}</ul></details>
    <details><summary>源文件</summary><ul>{coverage.files.map((item) => <li key={item.relative_path}>{item.relative_path} · {item.role} · {item.parse_status}</li>)}</ul></details>
    <details><summary>依赖引用（{coverage.dependencies.length}）</summary><ul>{coverage.dependencies.map((item) => <li key={`${item.source_file}:${item.source_locator}:${item.kind}:${item.reference}`}>{item.state} · {item.kind} · {item.reference} · {item.source_file}{item.source_locator}</li>)}</ul></details>
    <details><summary>未知对象（{coverage.unknowns.length}）</summary><ul>{coverage.unknowns.map((item) => <li key={`${item.source_file}:${item.locator}`}>{item.reason} · {item.source_file}{item.locator}</li>)}</ul></details>
    <details><summary>结构覆盖问题（{coverage.issues.length}）</summary><ul>{coverage.issues.map((item) => <li key={`${item.source_file}:${item.locator}:${item.code}`}>{item.code} · {item.source_file}{item.locator}</li>)}</ul></details>
  </section>;
}

export function EdaImportPanel({ projectId, onClose, dataSource = edaImportSource, reviewerId, reviewerScope }: { projectId: string; onClose(): void; dataSource?: EdaImportDataSource; reviewerId?: string; reviewerScope?: string }) {
  const sessionExpired = useDesktopSessionState() === "EXPIRED";
  const [backend, setBackend] = useState<EdaImportBackend>("KICAD");
  const [nativeEntry, setNativeEntry] = useState("");
  const [loadedEntry, setLoadedEntry] = useState<string | null>(null);
  const [revisions, setRevisions] = useState<EdaImportRevisionSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selectedRevision, setSelectedRevision] = useState<EdaImportRevision | null>(null);
  const [baseline, setBaseline] = useState<EdaImportRevisionSummary | "EMPTY" | null>(null);
  const [candidate, setCandidate] = useState<EdaImportPreview | null>(null);
  const [committed, setCommitted] = useState<EdaImportCommit | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function clearSelection() {
    setLoadedEntry(null);
    setRevisions([]);
    setNextCursor(null);
    setSelectedRevision(null);
    setBaseline(null);
    setCandidate(null);
    setCommitted(null);
    setError(null);
  }

  async function loadHistory(cursor?: string) {
    if (sessionExpired || pending || !nativeEntry.trim()) return;
    setPending(true);
    setError(null);
    try {
      const result = await dataSource.list(projectId, backend, nativeEntry.trim(), cursor);
      setLoadedEntry(nativeEntry.trim());
      setRevisions((current) => cursor ? [...current, ...result.items] : result.items);
      setNextCursor(result.next_cursor);
      if (!cursor) { setSelectedRevision(null); setBaseline(null); setCandidate(null); setCommitted(null); }
    } catch (value) { setError(edaImportError(value)); }
    finally { setPending(false); }
  }

  async function selectRevision(item: EdaImportRevisionSummary) {
    if (sessionExpired || pending) return;
    setPending(true);
    setError(null);
    try {
      const detail = await dataSource.detail(projectId, item.import_revision_id, item.import_revision_fingerprint);
      setSelectedRevision(detail);
      setBaseline(detail.active ? item : null);
      setCandidate(null);
      setCommitted(null);
    } catch (value) { setError(edaImportError(value)); }
    finally { setPending(false); }
  }

  async function makePreview() {
    if (sessionExpired || pending || !loadedEntry || !baseline) return;
    setPending(true);
    setError(null);
    try {
      setCandidate(await dataSource.preview(projectId, {
        backend,
        native_entry_relative_path: loadedEntry,
        selected_native_entries: [],
        expected_head_revision_id: baseline === "EMPTY" ? null : baseline.import_revision_id,
        expected_head_revision_fingerprint: baseline === "EMPTY" ? null : baseline.import_revision_fingerprint,
      }));
      setCommitted(null);
    } catch (value) { setError(edaImportError(value)); }
    finally { setPending(false); }
  }

  async function saveObservation() {
    if (sessionExpired || pending || !candidate) return;
    setPending(true);
    setError(null);
    try {
      const result = await dataSource.commit(projectId, { preview_id: candidate.preview_id, preview_fingerprint: candidate.preview_fingerprint });
      setCommitted(result);
      setCandidate(null);
      setBaseline(null);
      setSelectedRevision(null);
      try {
        const history = await dataSource.list(projectId, backend, loadedEntry ?? nativeEntry.trim());
        setRevisions(history.items);
        setNextCursor(history.next_cursor);
      } catch (value) {
        setLoadedEntry(null);
        setRevisions([]);
        setNextCursor(null);
        setError(`只读观察已保存（revision ${result.import_revision_id}），但历史列表刷新失败。请重新查询导入历史。${edaImportError(value)}`);
      }
    } catch (value) { setError(edaImportError(value)); }
    finally { setPending(false); }
  }

  const visibleObservation = committed ?? selectedRevision;
  return <div className="ide-dialog-scrim ide-dialog-scrim--panel" role="presentation"><section className="ide-project-create" role="dialog" aria-modal="true" aria-labelledby="eda-import-title">
    <header><div><span>EDA IMPORT</span><h2 id="eda-import-title">只读原生观察</h2></div><button className="ide-icon-button" type="button" aria-label="关闭只读导入" onClick={onClose}><X size={16} /></button></header>
    <p>选择项目内的原生入口。导入观察、来源当前性与原生验证分别记录；保存不会批准设计或修改原生工程。</p>
    <label>原生 backend<select value={backend} onChange={(event) => { setBackend(event.target.value as EdaImportBackend); clearSelection(); }} disabled={pending}><option value="KICAD">KiCad</option><option value="JLCEDA_PRO">JLCEDA Pro</option></select></label>
    <label>原生入口相对路径<input aria-label="原生入口相对路径" value={nativeEntry} onChange={(event) => { setNativeEntry(event.target.value); clearSelection(); }} placeholder="例如：hardware/board.kicad_pcb" disabled={pending} /></label>
    <button className="ide-secondary-button" type="button" onClick={() => void loadHistory()} disabled={sessionExpired || pending || !nativeEntry.trim()}>查询导入历史</button>
    {loadedEntry && <section aria-label="历史导入观察"><h3>历史 revision</h3>
      {revisions.length === 0 ? <p>此入口没有已保存的导入观察。</p> : <ul>{revisions.map((item) => <li key={item.import_revision_id}><button type="button" onClick={() => void selectRevision(item)} disabled={sessionExpired || pending}>{item.import_revision_id} · {item.active ? "当前 active" : "历史"}</button><small> {item.import_revision_fingerprint}</small></li>)}</ul>}
      {nextCursor && <button type="button" onClick={() => void loadHistory(nextCursor)} disabled={sessionExpired || pending}>更多历史</button>}
      {revisions.length === 0 && <button type="button" onClick={() => { setBaseline("EMPTY"); setSelectedRevision(null); setCandidate(null); }} disabled={sessionExpired || pending}>确认首次导入</button>}
      {baseline && <p>预览基线：{baseline === "EMPTY" ? "首次导入（无 active）" : baseline.import_revision_id}</p>}
      <button className="ide-secondary-button" type="button" onClick={() => void makePreview()} disabled={sessionExpired || pending || !baseline}>创建只读预览</button>
    </section>}
    {candidate && <section aria-label="导入预览"><h3>只读预览</h3><p>来源文件集：{candidate.source_set_fingerprint}</p><p>语义完整度：{candidate.semantic_completeness}</p><p>原生格式：{candidate.native_format_family}</p><p>原生版本：{Object.entries(candidate.native_format_versions).map(([path, version]) => `${path}: ${version}`).join("；")}</p><p>对象数：{candidate.objects.length}；文件数：{candidate.manifest.length}</p><p>未验证 · 不可修改</p><ul>{candidate.issues.map((issue, index) => <li key={`${issue.code}:${index}`}>{issue.kind}: {issue.code}</li>)}</ul><ProjectCoverageView coverage={candidate.project_coverage} /><button className="ide-primary-button" type="button" onClick={() => void saveObservation()} disabled={sessionExpired || pending}>保存只读观察</button></section>}
    {visibleObservation && <section aria-label="导入观察详情"><h3>导入观察</h3><p>revision：{visibleObservation.import_revision_id}</p><p>fingerprint：{visibleObservation.import_revision_fingerprint}</p>{committed && <p>提交结果：{committed.commit_outcome}</p>}<p>来源当前：{visibleObservation.source_currentness}</p><p>观察当前：{visibleObservation.observation_currentness}</p><p>语义完整度：{visibleObservation.semantic_completeness}</p><p>对象数：{visibleObservation.objects.length}；文件数：{visibleObservation.manifest.length}</p><p>未验证 · 不可修改</p><ul>{visibleObservation.issues.map((issue, index) => <li key={`${issue.code}:${index}`}>{issue.kind}: {issue.code}</li>)}</ul><ProjectCoverageView coverage={visibleObservation.project_coverage} /></section>}
    {selectedRevision && <DrcBaselinePanel key={`${projectId}:${selectedRevision.import_revision_id}:${selectedRevision.import_revision_fingerprint}`} projectId={projectId} revision={selectedRevision} reviewerId={reviewerId} reviewerScope={reviewerScope} disabled={sessionExpired || pending} />}
    {error && <p className="ide-form-error" role="alert">{error}</p>}
    <footer><button className="ide-secondary-button" type="button" onClick={onClose}>关闭</button></footer>
  </section></div>;
}
