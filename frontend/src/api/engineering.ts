import type { EngineeringCompletionSnapshot, EngineeringValidationResult } from "../types/engineering";

export type EngineeringApiError = "ENGINEERING_COMPLETION_UNAVAILABLE" | "ENGINEERING_COMPLETION_NOT_FOUND" | "QUERY_REJECTED";
export class EngineeringRequestError extends Error {
  readonly code: EngineeringApiError;
  constructor(code: EngineeringApiError) { super(code); this.name = "EngineeringRequestError"; this.code = code; }
}

const projectPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;
const fingerprintPattern = /^sha256:[a-f0-9]{64}$/;

async function read<T>(response: Response, notFound: EngineeringApiError): Promise<T> {
  if (response.ok) { try { return await response.json() as T; } catch { throw new EngineeringRequestError("QUERY_REJECTED"); } }
  throw new EngineeringRequestError(response.status === 404 ? notFound : response.status === 422 ? "QUERY_REJECTED" : "ENGINEERING_COMPLETION_UNAVAILABLE");
}

export async function fetchEngineeringCompletion(projectId: string, fetcher: typeof fetch = fetch): Promise<EngineeringCompletionSnapshot> {
  if (!projectPattern.test(projectId)) throw new EngineeringRequestError("QUERY_REJECTED");
  let response: Response;
  try { response = await fetcher(`/api/engineering/v28/${encodeURIComponent(projectId)}`); }
  catch { throw new EngineeringRequestError("ENGINEERING_COMPLETION_UNAVAILABLE"); }
  return read<EngineeringCompletionSnapshot>(response, "ENGINEERING_COMPLETION_NOT_FOUND");
}

export async function validateEngineeringCompletion(projectId: string, snapshot: EngineeringCompletionSnapshot, fetcher: typeof fetch = fetch): Promise<EngineeringValidationResult> {
  if (!projectPattern.test(projectId) || snapshot.project_id !== projectId || !fingerprintPattern.test(snapshot.fingerprint)) throw new EngineeringRequestError("QUERY_REJECTED");
  let response: Response;
  try {
    response = await fetcher("/api/engineering/v28/validate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ project_id: projectId, completion_snapshot: snapshot, context_fingerprint: snapshot.fingerprint }),
    });
  } catch { throw new EngineeringRequestError("ENGINEERING_COMPLETION_UNAVAILABLE"); }
  return read<EngineeringValidationResult>(response, "ENGINEERING_COMPLETION_NOT_FOUND");
}
