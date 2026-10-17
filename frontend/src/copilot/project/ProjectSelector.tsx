import { ChevronDown } from "lucide-react";

import type { ProjectView } from "../api/projectClient";

export function ProjectSelector({ projects, projectId, onChange }: { projects: ProjectView[]; projectId: string; onChange(projectId: string): void }) {
  return <label className="ide-project-selector"><span className="ide-visually-hidden">当前项目</span><select aria-label="当前项目" value={projectId} disabled={projects.length === 0} onChange={(event) => onChange(event.target.value)}>{projects.length === 0 && <option value="">尚未创建项目</option>}{projects.map((item) => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}</select><ChevronDown size={14} aria-hidden="true" /></label>;
}
