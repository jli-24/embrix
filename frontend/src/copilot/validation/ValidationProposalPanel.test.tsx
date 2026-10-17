import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ValidationProposalPanel } from "./ValidationProposalPanel";

describe("v6.4 validation and proposal desktop projection", () => {
  it("renders evidence, validation, finding and proposal areas without execution controls", () => {
    const markup = renderToStaticMarkup(<ValidationProposalPanel projectId={null} />);
    expect(markup).toContain("Current Task");
    expect(markup).toContain("Validation");
    expect(markup).toContain("No validation or proposal is available.");
    for (const forbidden of ["Build", "Flash", "HIL", "Deploy", "Write Workspace", "Program Device", "Apply"]) expect(markup).not.toContain(forbidden);
  });
});
