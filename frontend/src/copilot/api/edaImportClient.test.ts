import { describe, expect, it, vi } from "vitest";
import { commitEdaImport, fetchEdaImportRevision, listEdaImportRevisions, previewEdaImport } from "./projectClient";

const response = new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } });

describe("C1 EDA import client", () => {
  it("sends only selected input and explicit CAS identity", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => response.clone());
    await previewEdaImport("project-1", {
      backend: "KICAD", native_entry_relative_path: "board.kicad_pcb", selected_native_entries: [],
      expected_head_revision_id: null, expected_head_revision_fingerprint: null,
    }, fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/project-1/eda-import-previews");
    const body = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(Object.keys(body).sort()).toEqual(["backend", "expected_head_revision_fingerprint", "expected_head_revision_id", "native_entry_relative_path", "selected_native_entries"]);
    await commitEdaImport("project-1", { preview_id: "preview-1", preview_fingerprint: `sha256:${"a".repeat(64)}` }, fetcher);
    expect(fetcher.mock.calls[1][0]).toBe("/api/v1/copilot/projects/project-1/eda-imports");
    expect(Object.keys(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).sort()).toEqual(["preview_fingerprint", "preview_id"]);
    await listEdaImportRevisions("project-1", "KICAD", "board.kicad_pcb", fetcher);
    expect(fetcher.mock.calls[2][0]).toBe("/api/v1/copilot/projects/project-1/eda-imports?backend=KICAD&native_entry_relative_path=board.kicad_pcb");
    await fetchEdaImportRevision("project-1", "revision-1", `sha256:${"b".repeat(64)}`, fetcher);
    expect(fetcher.mock.calls[3][0]).toContain("/eda-imports/revision-1?fingerprint=sha256%3A");
  });
});
