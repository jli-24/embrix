import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ControlledExecutionPanel } from "./ControlledExecutionPanel";

describe("v6.6 controlled execution projection", () => {
  it("shows explicit approval and no shell controls", () => {
    const html = renderToStaticMarkup(<ControlledExecutionPanel projectId="project-1" />);
    expect(html).toContain("Explicit approval");
    expect(html).toContain("不会自动串联");
    expect(html).not.toContain("Shell");
    expect(html).not.toContain("command");
    expect(html).not.toContain("cwd");
    expect(html).not.toContain("executable");
  });

  it("projects backend-unavailable execution capabilities without action controls", () => {
    const html = renderToStaticMarkup(<ControlledExecutionPanel projectId="project-1" capabilities={[
      { capability: "CONTROLLED_BUILD", status: "UNAVAILABLE", reason_code: "WORKER_OR_TOOLCHAIN_UNAVAILABLE", detail: "toolchain unavailable", fingerprint: `sha256:${"a".repeat(64)}` },
      { capability: "CONTROLLED_TEST", status: "UNAVAILABLE", reason_code: "TEST_ADAPTER_NOT_CONFIGURED", detail: "test adapter unavailable", fingerprint: `sha256:${"e".repeat(64)}` },
      { capability: "CONTROLLED_FLASH", status: "UNAVAILABLE", reason_code: "WORKER_OR_HARDWARE_UNAVAILABLE", detail: "target unavailable", fingerprint: `sha256:${"b".repeat(64)}` },
      { capability: "UART_OBSERVATION", status: "UNAVAILABLE", reason_code: "WORKER_OR_UART_UNAVAILABLE", detail: "UART unavailable", fingerprint: `sha256:${"c".repeat(64)}` },
      { capability: "HIL_EXECUTION", status: "UNAVAILABLE", reason_code: "WORKER_OR_HIL_UNAVAILABLE", detail: "HIL unavailable", fingerprint: `sha256:${"d".repeat(64)}` },
    ]} />);
    expect(html).toContain("Hardware Golden Deferred");
    expect(html).toContain("Test Pending Approval");
    expect((html.match(/>UNAVAILABLE</g) ?? []).length).toBeGreaterThanOrEqual(5);
    expect(html).not.toContain("Build now");
    expect(html).not.toContain("Flash now");
  });
});
