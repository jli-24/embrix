import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import EnvironmentApp from "./EnvironmentApp";

describe("v5.0 environment view", () => {
  it("renders read-only operating environment panels without engineering controls", () => {
    const markup = renderToStaticMarkup(<EnvironmentApp />);
    expect(markup).toContain("Engineering Environment");
    expect(markup).toContain("Projects");
    expect(markup).toContain("Runtime Health");
    expect(markup).not.toContain("Build");
    expect(markup).not.toContain("Flash");
    expect(markup).not.toContain("Approve");
    expect(markup).not.toContain("Deploy");
  });
});
