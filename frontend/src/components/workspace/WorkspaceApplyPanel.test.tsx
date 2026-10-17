import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WorkspaceApplyPanel } from "./WorkspaceApplyPanel";

describe("v6.5 workspace controlled apply projection", () => {
  it("does not expose source paths or forbidden execution controls", () => {
    const markup = renderToStaticMarkup(<WorkspaceApplyPanel projectId="project-1" changesetId={null} />);
    expect(markup).toContain("Proposal approval and apply approval are separate");
    expect(markup).toContain("No ChangeSet selected.");
    for (const forbidden of ["Build", "Flash", "HIL", "Deploy", "Open Shell", "Run Command", "Program Device"]) expect(markup).not.toContain(forbidden);
    expect(markup).not.toContain("C:\\");
  });
});
