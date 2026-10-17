import type { WorkspaceSnapshot } from "../api/workspaceClient";
import { buildEngineeringTree } from "../viewModels";
import { ProjectTree } from "./ProjectTree";

export function EngineeringTree({ workspace }: { workspace: WorkspaceSnapshot | null }) {
  return <ProjectTree groups={buildEngineeringTree(workspace)} />;
}
