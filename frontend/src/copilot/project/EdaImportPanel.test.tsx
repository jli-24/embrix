import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { EdaImportPanel, type EdaImportDataSource } from "./ProjectCreatePanel";
import type { EdaImportPreview, EdaImportRevision, EdaImportRevisionSummary } from "../api/projectClient";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fingerprint = `sha256:${"a".repeat(64)}`;
const summary: EdaImportRevisionSummary = {
  import_revision_id: "revision-1", import_revision_fingerprint: fingerprint, active: true,
  created_at: "2026-09-30T00:00:00Z", backend: "KICAD", native_entry_relative_path: "board.kicad_pcb",
  source_set_fingerprint: fingerprint,
};
const preview: EdaImportPreview = {
  status: "PREVIEW", preview_id: "preview-1", preview_fingerprint: fingerprint,
  expires_at: "2026-09-30T00:10:00Z", backend: "KICAD", native_entry_relative_path: "board.kicad_pcb",
  native_format_family: "KICAD_PROJECT_DIRECTORY", native_format_versions: { "board.kicad_pcb": "UNKNOWN" },
  source_set_fingerprint: fingerprint, parser_ruleset_id: "inventory-only-r1", supported_subset_id: "opaque-inventory-r1",
  semantic_completeness: "UNKNOWN", manifest: [], objects: [], issues: [{ kind: "UNKNOWN", code: "NATIVE_FORMAT_VERSION_UNKNOWN", locator: "board.kicad_pcb", blocks_mutation: true }],
  verified: false, can_modify: false,
};
const revision: EdaImportRevision = {
  import_revision_id: summary.import_revision_id, import_revision_fingerprint: fingerprint, active: true,
  created_at: summary.created_at, backend: "KICAD", native_entry_relative_path: "board.kicad_pcb",
  native_format_family: "KICAD_PROJECT_DIRECTORY", native_format_versions: { "board.kicad_pcb": "UNKNOWN" },
  parser_ruleset_id: "inventory-only-r1", supported_subset_id: "opaque-inventory-r1",
  source_set_fingerprint: fingerprint, source_currentness: "CURRENT", observation_currentness: "CURRENT",
  semantic_completeness: "UNKNOWN", manifest: [], objects: [], issues: preview.issues, verified: false, can_modify: false,
};

function button(container: HTMLElement, name: string): HTMLButtonElement {
  const found = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(name));
  if (!found) throw new Error(`missing button ${name}`);
  return found;
}

describe("C1 read-only EDA import panel", () => {
  it("requires explicit head selection before preview and never treats CURRENT as verified", async () => {
    const source: EdaImportDataSource = {
      list: vi.fn(async () => ({ items: [summary], next_cursor: null })),
      detail: vi.fn(async () => revision),
      preview: vi.fn(async () => preview),
      commit: vi.fn(async () => ({ ...revision, status: "IMPORTED_OBSERVATION" as const, commit_outcome: "REUSED_ACTIVE" as const })),
    };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<EdaImportPanel projectId="project-1" onClose={() => undefined} dataSource={source} />); });
      const input = container.querySelector<HTMLInputElement>('input[aria-label="原生入口相对路径"]');
      if (!input) throw new Error("missing input");
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "board.kicad_pcb");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await act(async () => { button(container, "查询导入历史").click(); });
      expect(source.list).toHaveBeenCalledWith("project-1", "KICAD", "board.kicad_pcb", undefined);
      expect(source.preview).not.toHaveBeenCalled();
      expect(button(container, "创建只读预览").disabled).toBe(true);
      await act(async () => { button(container, "revision-1").click(); });
      expect(source.detail).toHaveBeenCalledWith("project-1", "revision-1", fingerprint);
      expect(container.textContent).toContain("来源当前：CURRENT");
      expect(container.textContent).toContain("未验证");
      expect(container.textContent).toContain("UNKNOWN: NATIVE_FORMAT_VERSION_UNKNOWN");
      expect(container.textContent).not.toContain("允许修改");
      await act(async () => { button(container, "创建只读预览").click(); });
      expect(source.preview).toHaveBeenCalledWith("project-1", {
        backend: "KICAD", native_entry_relative_path: "board.kicad_pcb", selected_native_entries: [],
        expected_head_revision_id: "revision-1", expected_head_revision_fingerprint: fingerprint,
      });
      await act(async () => { button(container, "保存只读观察").click(); });
      expect(source.commit).toHaveBeenCalledWith("project-1", { preview_id: "preview-1", preview_fingerprint: fingerprint });
      expect(container.textContent).toContain("REUSED_ACTIVE");
      expect(container.textContent).toContain("未验证");
      expect([...container.querySelectorAll("button")].every((item) => !/写回|应用修改|执行验证/.test(item.textContent ?? ""))).toBe(true);
    } finally { await act(async () => { root.unmount(); }); container.remove(); }
  });

  it("does not choose a history head on restart and requires explicit first-import confirmation", async () => {
    const source: EdaImportDataSource = {
      list: vi.fn(async () => ({ items: [], next_cursor: null })),
      detail: vi.fn(async () => revision),
      preview: vi.fn(async () => preview),
      commit: vi.fn(async () => ({ ...revision, status: "IMPORTED_OBSERVATION" as const, commit_outcome: "CREATED" as const })),
    };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<EdaImportPanel projectId="project-1" onClose={() => undefined} dataSource={source} />); });
      const input = container.querySelector<HTMLInputElement>('input[aria-label="原生入口相对路径"]');
      if (!input) throw new Error("missing input");
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "board.kicad_pcb");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await act(async () => { button(container, "查询导入历史").click(); });
      expect(button(container, "创建只读预览").disabled).toBe(true);
      await act(async () => { button(container, "确认首次导入").click(); });
      await act(async () => { button(container, "创建只读预览").click(); });
      expect(source.preview).toHaveBeenCalledWith("project-1", {
        backend: "KICAD", native_entry_relative_path: "board.kicad_pcb", selected_native_entries: [],
        expected_head_revision_id: null, expected_head_revision_fingerprint: null,
      });
    } finally { await act(async () => { root.unmount(); }); container.remove(); }
  });

  it("keeps a successful commit visible when the history refresh fails", async () => {
    let listCalls = 0;
    const source: EdaImportDataSource = {
      list: vi.fn(async () => {
        listCalls += 1;
        if (listCalls === 2) throw new Error("IMPORT_SOURCE_UNAVAILABLE");
        return { items: [], next_cursor: null };
      }),
      detail: vi.fn(async () => revision),
      preview: vi.fn(async () => preview),
      commit: vi.fn(async () => ({ ...revision, status: "IMPORTED_OBSERVATION" as const, commit_outcome: "CREATED" as const })),
    };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<EdaImportPanel projectId="project-1" onClose={() => undefined} dataSource={source} />); });
      const input = container.querySelector<HTMLInputElement>('input[aria-label="原生入口相对路径"]');
      if (!input) throw new Error("missing input");
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "board.kicad_pcb");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await act(async () => { button(container, "查询导入历史").click(); });
      await act(async () => { button(container, "确认首次导入").click(); });
      await act(async () => { button(container, "创建只读预览").click(); });
      await act(async () => { button(container, "保存只读观察").click(); });

      expect(source.commit).toHaveBeenCalledTimes(1);
      expect(container.textContent).toContain("revision：revision-1");
      expect(container.textContent).toContain("提交结果：CREATED");
      expect(container.textContent).toContain("只读观察已保存（revision revision-1）");
      expect(container.textContent).toContain("历史列表刷新失败");
      expect(container.textContent).not.toContain("保存只读观察");
      expect(container.textContent).toContain("未验证 · 不可修改");
    } finally { await act(async () => { root.unmount(); }); container.remove(); }
  });
});


it("shows coverage and dependency axes with unknowns while keeping all actions explicit", async () => {
  const coverage: NonNullable<EdaImportRevision["project_coverage"]> = {
    schema: "kicad-project-source-coverage-v1", profile_id: "KICAD_PROJECT_SOURCE_20240108_V1", source_set_fingerprint: fingerprint,
    entry_files: { project: "board.kicad_pro", schematic_root: "board.kicad_sch", board: "board.kicad_pcb" },
    files: [{ relative_path: "model.wrl", byte_size: 20, sha256: "a".repeat(64), role: "MODEL_3D", format_marker: "VRML2", parse_status: "OPAQUE" }],
    objects: [{ source_file: "model.wrl", locator: "/model", parent_locator: null, native_kind: "UNKNOWN", native_id: null, keyword: "geometry", values: [], child_locators: [], source_span: null }],
    relations: [], dependencies: [{ source_file: "model.wrl", source_locator: "/model", reference: "external-model", kind: "MODEL_3D", resolved_relative_path: null, state: "EXTERNAL_UNVERIFIED" }],
    unknowns: [{ source_file: "model.wrl", locator: "/model", native_keyword: "geometry", source_file_sha256: "a".repeat(64), source_span: null, reason: "OPAQUE_GEOMETRY" }],
    counts: { objects_total: 1, recognized_total: 0, unknown_total: 1, by_kind: [{ native_kind: "UNKNOWN", count: 1 }] },
    coverage_state: "PARTIAL", dependency_state: "EXTERNAL_UNVERIFIED", issues: [{ code: "MODEL_GEOMETRY_OPAQUE", source_file: "model.wrl", locator: "/model", blocks_complete: true }], fingerprint,
  };
  const source: EdaImportDataSource = {
    list: vi.fn(async () => ({ items: [summary], next_cursor: null })),
    detail: vi.fn(async () => ({ ...revision, project_coverage: coverage })),
    preview: vi.fn(async () => ({ ...preview, project_coverage: { ...coverage, dependency_state: "UNKNOWN" as const } })),
    commit: vi.fn(async () => ({ ...revision, status: "IMPORTED_OBSERVATION" as const, commit_outcome: "CREATED" as const })),
  };
  const container = document.createElement("div"); document.body.appendChild(container); const root = createRoot(container);
  try {
    await act(async () => { root.render(<EdaImportPanel projectId="project-1" onClose={() => undefined} dataSource={source} />); });
    expect(source.list).not.toHaveBeenCalled(); expect(source.detail).not.toHaveBeenCalled();
    const input = container.querySelector<HTMLInputElement>('input[aria-label="原生入口相对路径"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "board.kicad_pcb"); input.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => { button(container, "查询导入历史").click(); });
    await act(async () => { button(container, "revision-1").click(); });
    expect(container.textContent).toContain("结构覆盖：PARTIAL；依赖：EXTERNAL_UNVERIFIED");
    expect(container.textContent).toContain("OPAQUE_GEOMETRY");
    expect(container.textContent).toContain("external-model");
    expect(container.textContent).toContain("未知：1");
    expect(container.textContent).toContain("未验证 · 不可修改");
    expect(source.commit).not.toHaveBeenCalled();
    await act(async () => { button(container, "创建只读预览").click(); });
    expect(container.textContent).toContain("结构覆盖：PARTIAL；依赖：UNKNOWN");
    expect(source.commit).not.toHaveBeenCalled();
    expect([...container.querySelectorAll("button")].every((item) => !/approve|写回|执行验证|应用修改/.test(item.textContent ?? ""))).toBe(true);
  } finally { await act(async () => { root.unmount(); }); container.remove(); }
});
