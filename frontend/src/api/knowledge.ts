import type { EngineeringKnowledgeSnapshot, KnowledgeSuggestion } from "../types/knowledge";

export type KnowledgeApiError = "KNOWLEDGE_UNAVAILABLE" | "KNOWLEDGE_NOT_FOUND" | "RETRIEVAL_UNAVAILABLE" | "QUERY_REJECTED";
export class KnowledgeRequestError extends Error { readonly code: KnowledgeApiError; constructor(code: KnowledgeApiError) { super(code); this.name = "KnowledgeRequestError"; this.code = code; } }
const projectPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;
const fingerprintPattern = /^sha256:[a-f0-9]{64}$/;

async function read<T>(response: Response, notFound: KnowledgeApiError): Promise<T> {
  if (response.ok) { try { return await response.json() as T; } catch { throw new KnowledgeRequestError("QUERY_REJECTED"); } }
  throw new KnowledgeRequestError(response.status === 404 ? notFound : response.status === 422 ? "QUERY_REJECTED" : "KNOWLEDGE_UNAVAILABLE");
}

export async function fetchKnowledgeSnapshot(projectId: string, fetcher: typeof fetch = fetch): Promise<EngineeringKnowledgeSnapshot> {
  if (!projectPattern.test(projectId)) throw new KnowledgeRequestError("QUERY_REJECTED");
  let response: Response;
  try { response = await fetcher(`/api/knowledge/v27/${encodeURIComponent(projectId)}`); } catch { throw new KnowledgeRequestError("KNOWLEDGE_UNAVAILABLE"); }
  return read<EngineeringKnowledgeSnapshot>(response, "KNOWLEDGE_NOT_FOUND");
}

export async function queryKnowledge(projectId: string, contextFingerprint: string, fetcher: typeof fetch = fetch): Promise<KnowledgeSuggestion[]> {
  if (!projectPattern.test(projectId) || !fingerprintPattern.test(contextFingerprint)) throw new KnowledgeRequestError("QUERY_REJECTED");
  let response: Response;
  try {
    response = await fetcher("/api/knowledge/v27/query", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: projectId, requirement_reference: `requirement:${projectId}`, context_fingerprint: contextFingerprint }) });
  } catch { throw new KnowledgeRequestError("RETRIEVAL_UNAVAILABLE"); }
  return read<KnowledgeSuggestion[]>(response, "KNOWLEDGE_NOT_FOUND");
}
