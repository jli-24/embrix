import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";
import { webViewDiagnosticOriginHeaders } from "./webviewSessionDiagnostic";

export type CapabilityStatus = "AVAILABLE" | "DEGRADED" | "UNAVAILABLE" | "UNVERIFIED";

export type CapabilityStatusView = {
  capability: string;
  status: CapabilityStatus;
  reason_code: string;
  detail: string;
  provider_configured?: string | null;
  /** Optional explicit credential projection; absence must not be inferred as saved. */
  credential_saved?: string | null;
  runtime_status?: string | null;
  model_status?: string | null;
  fingerprint: string;
};

export class CapabilityRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "CapabilityRequestError";
  }
}

export async function fetchCapabilityStatus(fetcher: typeof fetch = fetch): Promise<CapabilityStatusView[]> {
  if (isDesktopSessionExpired()) throw new CapabilityRequestError(DESKTOP_SESSION_EXPIRED);
  let response: Response;
  try {
    response = await fetcher("/api/v1/copilot/capabilities", {
      credentials: "same-origin",
      headers: {
        "X-Copilot-Requester": "desktop-user",
        "X-Environment-Tenant": "desktop-tenant",
        ...webViewDiagnosticOriginHeaders(),
      },
    });
  } catch {
    throw new CapabilityRequestError("CAPABILITIES_UNAVAILABLE");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const code = mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "CAPABILITIES_UNAVAILABLE";
    throw new CapabilityRequestError(code);
  }
  const value = await response.json().catch(() => null);
  if (!Array.isArray(value)) throw new CapabilityRequestError("CAPABILITIES_INVALID");
  return value as CapabilityStatusView[];
}
