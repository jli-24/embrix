import { Check, Cpu } from "lucide-react";

import type { DemoProject } from "../demo/demoProject";

export function ProjectCard({ project, active, onSelect }: { project: DemoProject; active: boolean; onSelect(): void }) {
  return <button className={active ? "is-active" : ""} type="button" title={`打开 ${project.projectName}`} onClick={onSelect}>
    <Cpu size={15} />
    <span><strong>{project.projectName}</strong><small><i className="ide-project-status-dot" />最近更新: {project.lastUpdate}</small></span>
    {active && <Check size={14} />}
  </button>;
}
