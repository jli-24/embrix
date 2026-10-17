import type { ComponentProps } from "react";
import { ProjectExplorer } from "../project/ProjectExplorer";
export function LeftSidebar(props: ComponentProps<typeof ProjectExplorer>) { return <ProjectExplorer {...props} />; }
