import { mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type DesktopUpdateCandidate = {
  product_version: string;
  build_id: string;
  sha256: string;
  size: number;
  notes: string[];
};

export type RunningBuildIdentity = {
  schema_version: 1;
  product_name: string;
  product_version: string;
  build_id: string;
  commit: string | null;
  dirty: boolean | null;
  source_fingerprint: string;
  build_time: string;
  mode: string;
  channel: string;
  platform: string;
  architecture: string;
};

export type DesktopUpdateProjection = {
  state: string;
  installed_identity?: {
    product_version: string;
    build_id: string;
    build_time: string;
    source_fingerprint: string;
    commit: string | null;
    dirty: boolean | null;
    mode: string;
    channel: string;
    platform: string;
    architecture: string;
  } | null;
  installed_verification?: string;
  previous_identity?: { product_version: string; build_id: string } | null;
  previous_verification?: string;
  channel?: string | null;
  candidate?: DesktopUpdateCandidate | null;
  notes: string[];
  error_code?: string | null;
};

export class DesktopUpdateRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "DesktopUpdateRequestError";
  }
}

export async function fetchRunningBuildIdentity(fetcher: typeof fetch = fetch): Promise<RunningBuildIdentity> {
  let response: Response;
  try {
    response = await fetcher("/desktop/build-identity.json", { method: "GET", cache: "no-store" });
  } catch {
    throw new DesktopUpdateRequestError("BUILD_IDENTITY_UNAVAILABLE");
  }
  if (!response.ok) throw new DesktopUpdateRequestError("BUILD_IDENTITY_UNAVAILABLE");
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new DesktopUpdateRequestError("BUILD_IDENTITY_INVALID");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new DesktopUpdateRequestError("BUILD_IDENTITY_INVALID");
  const identity = value as Record<string, unknown>;
  if (identity.schema_version !== 1
    || identity.product_name !== "Embrix"
    || ["product_version", "build_id", "source_fingerprint", "build_time", "mode", "channel", "platform", "architecture"]
      .some((field) => typeof identity[field] !== "string" || !(identity[field] as string).trim())
    || !(identity.commit === null || typeof identity.commit === "string")
    || !(identity.dirty === null || typeof identity.dirty === "boolean")) {
    throw new DesktopUpdateRequestError("BUILD_IDENTITY_INVALID");
  }
  return identity as RunningBuildIdentity;
}

async function requestUpdate(
  path: string,
  fetcher: typeof fetch,
  body?: Record<string, string>,
): Promise<DesktopUpdateProjection> {
  let response: Response;
  try {
    response = await fetcher(`/api/v1/copilot/desktop/update/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new DesktopUpdateRequestError("NETWORK_ERROR");
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new DesktopUpdateRequestError("INVALID_RESPONSE");
  }
  if (!response.ok) {
    const errorCode = payload && typeof payload === "object"
      ? ("error_code" in payload && typeof payload.error_code === "string" ? payload.error_code
        : "detail" in payload && typeof payload.detail === "string" ? payload.detail : `HTTP_${response.status}`)
      : `HTTP_${response.status}`;
    throw new DesktopUpdateRequestError(mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? errorCode);
  }
  if (!payload || typeof payload !== "object" || typeof (payload as { state?: unknown }).state !== "string") {
    throw new DesktopUpdateRequestError("INVALID_RESPONSE");
  }
  return payload as DesktopUpdateProjection;
}

export function fetchDesktopUpdateStatus(fetcher: typeof fetch = fetch): Promise<DesktopUpdateProjection> {
  return requestUpdate("status", fetcher);
}

export function checkDesktopUpdate(fetcher: typeof fetch = fetch): Promise<DesktopUpdateProjection> {
  return requestUpdate("check", fetcher, {});
}

export function installDesktopUpdate(
  candidate: Pick<DesktopUpdateCandidate, "build_id" | "sha256">,
  fetcher: typeof fetch = fetch,
): Promise<DesktopUpdateProjection> {
  return requestUpdate("install", fetcher, candidate);
}

export function restartDesktopApp(fetcher: typeof fetch = fetch): Promise<DesktopUpdateProjection> {
  return requestUpdate("restart", fetcher, {});
}
