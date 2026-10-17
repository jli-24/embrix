import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";
import { webViewDiagnosticOriginHeaders } from "./webviewSessionDiagnostic";

export type ProductionReleaseStatus = {
  product_version: "7.1.0";
  release_status: string;
  clean_machine_acceptance: string;
  installer_signing: string;
  browser_automation: string;
  hardware_golden: string;
};

async function request(path: string, init?: RequestInit): Promise<Response> {
  if (path !== "about" && isDesktopSessionExpired()) throw new Error(DESKTOP_SESSION_EXPIRED);
  const method = String(init?.method ?? "GET").toUpperCase();
  const response = await fetch(`/api/v1/copilot/desktop/system/${path}`, { credentials: "same-origin", ...init, headers: { ...(method === "GET" ? webViewDiagnosticOriginHeaders() : {}), ...(init?.headers ?? {}) } });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "PRODUCTION_RELEASE_UNAVAILABLE");
  }
  return response;
}

export async function fetchProductionReleaseStatus(): Promise<ProductionReleaseStatus> {
  return await (await request("about")).json() as ProductionReleaseStatus;
}

export async function createProductionBackup(): Promise<{ backup_id: string }> {
  return await (await request("backups", { method: "POST" })).json() as { backup_id: string };
}

export async function exportProductionDiagnostics(): Promise<{ diagnostic_id: string }> {
  return await (await request("diagnostics", { method: "POST" })).json() as { diagnostic_id: string };
}
