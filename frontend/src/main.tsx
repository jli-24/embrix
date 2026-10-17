import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import CopilotApp from "./copilot/CopilotApp";
import { markDesktopSessionActive } from "./copilot/api/sessionState";
import { webViewDiagnosticOriginHeaders } from "./copilot/api/webviewSessionDiagnostic";
import "./styles.css";

// /desktop is the only formal Desktop product entry. Environment projection is
// rendered inside the Desktop shell; the old standalone route is not a product
// boundary anymore.
const Page = window.location.pathname.startsWith("/desktop") || window.location.pathname.startsWith("/environment") ? CopilotApp : App;

async function exchangeProductionBootstrap(): Promise<void> {
  if (!window.location.pathname.startsWith("/desktop") || !window.location.hash.startsWith("#")) return;
  const nonce = window.location.hash.slice(1);
  // Fragments never reach the server; remove it before the app renders or any
  // user action is possible. The nonce is deliberately never a query value.
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  const response = await fetch("/api/v1/copilot/desktop/system/bootstrap/exchange", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Embedded-Copilot-Native-Handoff": "H7_BOOTSTRAP_URL_READ",
      ...webViewDiagnosticOriginHeaders(),
    },
    body: JSON.stringify({ nonce }),
  }).catch(() => undefined);
  if (response?.ok) markDesktopSessionActive();
}

void exchangeProductionBootstrap().finally(() => createRoot(document.getElementById("root")!).render(<StrictMode><Page /></StrictMode>));
