export type WorkbenchMode = "requirement" | "engineering";
export type InspectorMode = "hidden" | "floating" | "docked";

export type ProjectLayout = {
  explorerWidth: number;
  copilotWidth: number;
  inspectorWidth: number;
  inspectorMode: InspectorMode;
  bottomHeight: number;
};

export const DEFAULT_PROJECT_LAYOUT: ProjectLayout = {
  explorerWidth: 300,
  copilotWidth: 336,
  inspectorWidth: 360,
  inspectorMode: "hidden",
  bottomHeight: 220,
};

export const PROJECT_LAYOUT_KEY_PREFIX = "embrix.workbench.layout.v2.";

export function inferWorkbenchMode(input: { projectId?: string | null; resourceCount: number }): WorkbenchMode {
  return input.projectId && input.resourceCount > 0 ? "engineering" : "requirement";
}

export function projectLayoutKey(projectId: string | null | undefined): string | null {
  const normalized = projectId?.trim();
  return normalized ? `${PROJECT_LAYOUT_KEY_PREFIX}${normalized}` : null;
}

function clamp(value: unknown, min: number, max: number, fallback: number): number {
  const number = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.min(max, Math.max(min, number));
}

export function normalizeProjectLayout(value: Partial<ProjectLayout> | null | undefined): ProjectLayout {
  return {
    explorerWidth: clamp(value?.explorerWidth, 280, 360, DEFAULT_PROJECT_LAYOUT.explorerWidth),
    copilotWidth: clamp(value?.copilotWidth, 280, 430, DEFAULT_PROJECT_LAYOUT.copilotWidth),
    inspectorWidth: clamp(value?.inspectorWidth, 320, 420, DEFAULT_PROJECT_LAYOUT.inspectorWidth),
    inspectorMode: value?.inspectorMode === "floating" || value?.inspectorMode === "docked" ? value.inspectorMode : "hidden",
    bottomHeight: clamp(value?.bottomHeight, 120, 360, DEFAULT_PROJECT_LAYOUT.bottomHeight),
  };
}

export function readProjectLayout(storage: Pick<Storage, "getItem"> | null, key: string | null): ProjectLayout | null {
  if (!storage || !key) return null;
  try {
    const value = JSON.parse(storage.getItem(key) ?? "null") as Partial<ProjectLayout> | null;
    return value ? normalizeProjectLayout(value) : null;
  } catch {
    return null;
  }
}

export function writeProjectLayout(storage: Pick<Storage, "setItem"> | null, key: string | null, value: ProjectLayout): void {
  if (!storage || !key) return;
  storage.setItem(key, JSON.stringify(normalizeProjectLayout(value)));
}
