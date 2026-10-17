import { describe, expect, it } from "vitest";

import {
  isWebViewSessionDiagnosticEnabled,
  webViewDiagnosticOriginHeaders,
} from "./webviewSessionDiagnostic";

describe("WebView session diagnostic marker", () => {
  it("does not emit a marker when the launcher did not opt in", () => {
    window.history.replaceState(null, "", "/desktop/");
    expect(isWebViewSessionDiagnosticEnabled()).toBe(false);
    expect(webViewDiagnosticOriginHeaders()).toEqual({});
  });

  it("emits only the fixed harmless origin marker when opted in", () => {
    window.history.replaceState(null, "", "/desktop/?webview_session_diagnostic=1");
    expect(isWebViewSessionDiagnosticEnabled()).toBe(true);
    expect(webViewDiagnosticOriginHeaders()).toEqual({
      "X-Embrix-Diagnostic-Origin": "webview",
    });
  });
});
