import { useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import {
  DEFAULT_PROJECT_LAYOUT,
  normalizeProjectLayout,
  readProjectLayout,
  writeProjectLayout,
  type InspectorMode,
  type ProjectLayout,
  type WorkbenchMode,
} from "./adaptiveLayout";

type ResizeTarget = "explorer" | "copilot" | "inspector" | "bottom";

export function DesktopShell({
  top,
  left,
  center,
  right,
  density,
  theme,
  leftOpen,
  rightOpen,
  onCloseLeft,
  onCloseRight,
  activity,
  copilot,
  workspace,
  inspector,
  bottom,
  mode,
  inspectorMode,
  inspectorVisible,
  onInspectorModeChange,
  layoutKey,
  bottomOpen,
  context,
}: {
  top: ReactNode;
  left: ReactNode;
  center: ReactNode;
  right?: ReactNode;
  density: "compact" | "comfortable";
  theme: "light" | "dark";
  leftOpen: boolean;
  rightOpen: boolean;
  onCloseLeft(): void;
  onCloseRight(): void;
  activity?: ReactNode;
  copilot?: ReactNode;
  workspace?: ReactNode;
  inspector?: ReactNode;
  bottom?: ReactNode;
  mode?: WorkbenchMode;
  inspectorMode?: InspectorMode;
  inspectorVisible?: boolean;
  onInspectorModeChange?(mode: InspectorMode): void;
  layoutKey?: string | null;
  bottomOpen?: boolean;
  onBottomOpenChange?(open: boolean): void;
  context?: ReactNode;
}) {
  const [layout, setLayout] = useState<ProjectLayout>(DEFAULT_PROJECT_LAYOUT);
  const [layoutLoaded, setLayoutLoaded] = useState(false);
  const [layoutUserAdjusted, setLayoutUserAdjusted] = useState(false);
  const loadedLayoutKeyRef = useRef<string | null>(null);
  const [dragging, setDragging] = useState<ResizeTarget | null>(null);
  const [floatingPosition, setFloatingPosition] = useState<{ x: number | null; y: number }>({ x: null, y: 76 });
  const [dockPreview, setDockPreview] = useState(false);
  const effectiveMode = mode ?? "engineering";
  const hasContext = Boolean(context);
  const effectiveInspectorMode = hasContext ? "docked" : inspectorVisible === false ? "hidden" : inspectorMode ?? (layoutKey ? layout.inspectorMode : "hidden");

  useEffect(() => {
    if (!dragging) return undefined;
    const move = (event: PointerEvent) => {
      setLayout((current) => {
        const viewportWidth = window.innerWidth;
        const workspaceMinimum = viewportWidth <= 1120 ? 360 : 520;
        const inspectorTrack = effectiveInspectorMode === "docked" && viewportWidth > 1350 ? current.inspectorWidth : 0;
        const panelBudget = Math.max(0, viewportWidth - 60 - workspaceMinimum - inspectorTrack);
        if (dragging === "explorer") {
          const maxExplorer = Math.min(360, Math.max(280, panelBudget - current.copilotWidth));
          return normalizeProjectLayout({ ...current, explorerWidth: Math.min(maxExplorer, Math.max(280, event.clientX - 60)) });
        }
        if (dragging === "copilot") {
          const maxCopilot = Math.min(430, Math.max(280, panelBudget - current.explorerWidth));
          return normalizeProjectLayout({ ...current, copilotWidth: Math.min(maxCopilot, Math.max(280, event.clientX - 60 - current.explorerWidth)) });
        }
        if (dragging === "inspector") {
          const maxInspector = Math.min(420, Math.max(320, viewportWidth - 60 - current.explorerWidth - current.copilotWidth - workspaceMinimum));
          return normalizeProjectLayout({ ...current, inspectorWidth: Math.min(maxInspector, Math.max(320, viewportWidth - event.clientX)) });
        }
        return normalizeProjectLayout({ ...current, bottomHeight: Math.min(360, Math.max(120, window.innerHeight - event.clientY)) });
      });
      setLayoutUserAdjusted(true);
    };
    const stop = () => setDragging(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
  }, [dragging, effectiveInspectorMode]);

  useEffect(() => {
    const storage = typeof window === "undefined" ? null : window.localStorage;
    setLayoutLoaded(false);
    const saved = readProjectLayout(storage, layoutKey ?? null);
    setLayout(saved ?? DEFAULT_PROJECT_LAYOUT);
    setLayoutUserAdjusted(Boolean(saved));
    loadedLayoutKeyRef.current = layoutKey ?? null;
    if (saved?.inspectorMode && onInspectorModeChange) onInspectorModeChange(saved.inspectorMode);
    setLayoutLoaded(true);
  }, [layoutKey, onInspectorModeChange]);

  useEffect(() => {
    if (!layoutLoaded || !layoutUserAdjusted || loadedLayoutKeyRef.current !== (layoutKey ?? null)) return;
    const storage = typeof window === "undefined" ? null : window.localStorage;
    writeProjectLayout(storage, layoutKey ?? null, { ...layout, inspectorMode: inspectorMode ?? layout.inspectorMode });
  }, [inspectorMode, layout, layoutKey, layoutLoaded, layoutUserAdjusted]);

  useEffect(() => {
    if (effectiveInspectorMode !== "floating" || typeof window === "undefined") return;
    setFloatingPosition((current) => current.x === null ? { x: Math.max(8, window.innerWidth - layout.inspectorWidth - 24), y: current.y } : current);
  }, [effectiveInspectorMode, layout.inspectorWidth]);

  const updateLayout = (next: Partial<ProjectLayout>) => {
    setLayoutUserAdjusted(true);
    setLayout((current) => normalizeProjectLayout({ ...current, ...next }));
  };
  const changeInspectorMode = (next: InspectorMode) => {
    setLayoutUserAdjusted(true);
    onInspectorModeChange?.(next);
  };
  const beginFloatingDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (!target.closest('[data-inspector-drag-handle="true"]')) return;
    const origin = { x: floatingPosition.x ?? Math.max(8, window.innerWidth - layout.inspectorWidth - 24), y: floatingPosition.y };
    const start = { x: event.clientX, y: event.clientY };
    const move = (next: PointerEvent) => {
      setFloatingPosition({
        x: Math.max(8, Math.min(window.innerWidth - layout.inspectorWidth - 8, origin.x + next.clientX - start.x)),
        y: Math.max(56, origin.y + next.clientY - start.y),
      });
      setDockPreview(next.clientX >= window.innerWidth - 260);
    };
    const stop = (next: PointerEvent) => {
      const shouldDock = next.clientX >= window.innerWidth - 260;
      setDockPreview(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      if (shouldDock) changeInspectorMode("docked");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  };

  if (activity || copilot || workspace || inspector || bottom || context) {
    const hasCopilot = Boolean(copilot);
    const gridStyle = {
      "--explorer-width": `${layout.explorerWidth}px`,
      "--copilot-width": hasCopilot ? `${layout.copilotWidth}px` : "0px",
      "--inspector-width": hasContext || effectiveInspectorMode === "docked" ? `${layout.inspectorWidth}px` : "0px",
      "--bottom-height": bottomOpen === false ? "0px" : `${layout.bottomHeight}px`,
    } as CSSProperties;
    return <div
      data-workbench-mode={effectiveMode}
      data-inspector-mode={effectiveInspectorMode}
      data-layout-contract="fixed-explorer-flex-conversation-fixed-context"
      data-topbar-resize-isolated="true"
      data-layout-priority={layoutUserAdjusted ? "USER_LAYOUT" : "AUTO_LAYOUT"}
      className={`ide-shell ide-shell--workbench ide-shell--${density} ide-shell--theme-${theme} ide-shell--${effectiveMode} ide-shell--inspector-${effectiveInspectorMode} ${hasCopilot ? "" : "ide-shell--no-copilot"} ${hasContext ? "ide-shell--context-visible" : ""} ${leftOpen ? "ide-shell--left-open" : ""} ${rightOpen ? "ide-shell--right-open" : ""}`}
    >
      <header className="ide-shell__top">{top}</header>
      <div style={gridStyle} className="ide-shell__main-grid">
        <aside className="ide-shell__activity" aria-label="活动栏">{activity}</aside>
        <aside className="ide-shell__left" aria-label="项目资源">{left}</aside>
        <div className="ide-shell__resize ide-shell__resize--explorer" role="separator" aria-label="调整项目资源宽度" tabIndex={0} onPointerDown={() => setDragging("explorer")} onKeyDown={(event) => { if (event.key === "ArrowLeft") updateLayout({ explorerWidth: layout.explorerWidth - 16 }); if (event.key === "ArrowRight") updateLayout({ explorerWidth: layout.explorerWidth + 16 }); }} />
        {copilot && <aside className="ide-shell__copilot" aria-label="工程 Copilot">{copilot}</aside>}
        {copilot && <div className="ide-shell__resize ide-shell__resize--copilot" role="separator" aria-label="调整工程 Copilot 宽度" tabIndex={0} onPointerDown={() => setDragging("copilot")} onKeyDown={(event) => { if (event.key === "ArrowLeft") updateLayout({ copilotWidth: layout.copilotWidth - 16 }); if (event.key === "ArrowRight") updateLayout({ copilotWidth: layout.copilotWidth + 16 }); }} />}
        <main className="ide-shell__center" aria-label="Conversation">{workspace ?? center}</main>
        <aside className="ide-shell__right" aria-label={hasContext ? "Engineering Context" : "检查器"}>{context ?? inspector ?? right}</aside>
        <div className="ide-shell__resize ide-shell__resize--inspector" role="separator" aria-label="调整 Inspector 宽度" tabIndex={0} onPointerDown={() => setDragging("inspector")} onKeyDown={(event) => { if (event.key === "ArrowLeft") updateLayout({ inspectorWidth: layout.inspectorWidth + 16 }); if (event.key === "ArrowRight") updateLayout({ inspectorWidth: layout.inspectorWidth - 16 }); }} />
        <section className="ide-shell__bottom" aria-label="Verification and operation output">{bottom}</section>
        <div className="ide-shell__resize ide-shell__resize--bottom" role="separator" aria-label="调整 Bottom Panel 高度" tabIndex={0} onPointerDown={() => setDragging("bottom")} onKeyDown={(event) => { if (event.key === "ArrowUp") updateLayout({ bottomHeight: layout.bottomHeight + 16 }); if (event.key === "ArrowDown") updateLayout({ bottomHeight: layout.bottomHeight - 16 }); }} />
      </div>
      {!hasContext && effectiveInspectorMode === "floating" && inspector && <>
        <div className="ide-inspector-floating" data-floating-inspector="true" style={{ left: floatingPosition.x ?? undefined, right: floatingPosition.x === null ? 24 : "auto", top: floatingPosition.y, width: `${layout.inspectorWidth}px` }} onPointerDown={beginFloatingDrag}>
          <div className="ide-inspector-floating__surface">{inspector}</div>
        </div>
        {dockPreview && <div className="ide-inspector-dock-preview" role="status" aria-label="Inspector Dock Preview">停靠到右侧</div>}
      </>}
      {(leftOpen || rightOpen) && <button className="ide-shell__scrim" type="button" aria-label="Close side panel" onClick={() => { onCloseLeft(); onCloseRight(); }} />}
    </div>;
  }
  return <div className={`ide-shell ide-shell--${density} ide-shell--theme-${theme} ${leftOpen ? "ide-shell--left-open" : ""} ${rightOpen ? "ide-shell--right-open" : ""}`}>
    <header className="ide-shell__top">{top}</header>
    <aside className="ide-shell__left" aria-label="项目资源">{left}</aside>
    <main className="ide-shell__center">{center}</main>
    <aside className="ide-shell__right" aria-label="工程上下文">{right}</aside>
    {(leftOpen || rightOpen) && <button className="ide-shell__scrim" type="button" aria-label="Close side panel" onClick={() => { onCloseLeft(); onCloseRight(); }} />}
  </div>;
}
