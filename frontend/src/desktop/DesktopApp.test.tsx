import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import DesktopApp from "./DesktopApp";

describe("v4.9 desktop product view", () => {
  it("renders a read-only desktop entry without engineering control actions", () => {
    const markup = renderToStaticMarkup(<DesktopApp />);
    expect(markup).toContain("Engineering Desktop");
    expect(markup).toContain("Open project");
    expect(markup).not.toContain("Build");
    expect(markup).not.toContain("Flash");
    expect(markup).not.toContain("Apply");
    expect(markup).not.toContain("Approve");
  });
});
