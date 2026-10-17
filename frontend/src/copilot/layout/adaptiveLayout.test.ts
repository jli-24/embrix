import { describe, expect, it } from "vitest";

import { DEFAULT_PROJECT_LAYOUT, normalizeProjectLayout } from "./adaptiveLayout";

describe("responsive workbench layout bounds", () => {
  it("keeps Explorer and Context bounded while Conversation owns the remaining track", () => {
    expect(DEFAULT_PROJECT_LAYOUT.explorerWidth).toBeGreaterThanOrEqual(280);
    expect(DEFAULT_PROJECT_LAYOUT.explorerWidth).toBeLessThanOrEqual(360);
    expect(DEFAULT_PROJECT_LAYOUT.inspectorWidth).toBeGreaterThanOrEqual(320);
    expect(DEFAULT_PROJECT_LAYOUT.inspectorWidth).toBeLessThanOrEqual(420);
    expect(normalizeProjectLayout({ explorerWidth: 100, inspectorWidth: 900 })).toMatchObject({ explorerWidth: 280, inspectorWidth: 420 });
  });
});
