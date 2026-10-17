import { ChevronDown, ChevronRight, Folder, FolderCode, Microscope, PackageSearch, PanelsTopLeft } from "lucide-react";
import { useState } from "react";

import type { EngineeringGroup } from "../viewModels";
import { ArtifactNode } from "./ArtifactNode";

const icons = { firmware: FolderCode, hardware: PanelsTopLeft, documents: Folder, tests: Microscope, other: PackageSearch };

export function ProjectTree({ groups }: { groups: EngineeringGroup[] }) {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  if (groups.length === 0) return <p className="ide-tree__empty">No workspace artifacts projected</p>;
  return <div className="ide-tree" role="tree" aria-label="Engineering tree">{groups.map((group) => {
    const open = !closed.has(group.key);
    const GroupIcon = icons[group.key];
    return <section className="ide-tree__group" key={group.key}><button className="ide-tree__toggle" type="button" title={`${open ? "Collapse" : "Expand"} ${group.label}`} aria-expanded={open} onClick={() => setClosed((current) => { const next = new Set(current); next.has(group.key) ? next.delete(group.key) : next.add(group.key); return next; })}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<GroupIcon size={15} /><strong>{group.label}</strong></button>{open && <div className="ide-tree__children">{group.artifacts.map((artifact) => <ArtifactNode artifact={artifact} key={artifact.artifact_id} />)}</div>}</section>;
  })}</div>;
}
