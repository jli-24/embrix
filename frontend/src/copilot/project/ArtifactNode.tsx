import { File, PackageSearch } from "lucide-react";

import type { WorkspaceArtifactView } from "../api/workspaceClient";

export function ArtifactNode({ artifact }: { artifact: WorkspaceArtifactView }) {
  return <div className="ide-tree__artifact">
    {artifact.filenames.length > 0 ? <div><PackageSearch size={14} /><span>{artifact.filenames[0]}</span></div> : <div><PackageSearch size={14} /><span>{artifact.artifact_id}</span></div>}
    {artifact.filenames.slice(1).map((filename) => <div className="ide-tree__file" key={`${artifact.artifact_id}-${filename}`}><File size={13} /><span>{filename}</span></div>)}
  </div>;
}
