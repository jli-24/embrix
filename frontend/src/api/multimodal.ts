import type { MultimodalAnalysisProjection, MultimodalInputType } from "../types/multimodal";

export type MultimodalApiError = "MULTIMODAL_UNAVAILABLE" | "QUERY_REJECTED";
export class MultimodalRequestError extends Error { readonly code: MultimodalApiError; constructor(code: MultimodalApiError) { super(code); this.name = "MultimodalRequestError"; this.code = code; } }
const projectPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;
const referencePattern = /^[A-Za-z0-9][A-Za-z0-9._:#-]{0,159}$/;
const fingerprintPattern = /^sha256:[a-f0-9]{64}$/;
const inputTypes: MultimodalInputType[] = ["PCB", "SCHEMATIC", "DATASHEET", "DOCUMENT", "IMAGE"];

export async function analyzeMultimodal(projectId: string, sourceReference: string, inputType: MultimodalInputType, contextFingerprint: string, fetcher: typeof fetch = fetch): Promise<MultimodalAnalysisProjection> {
  if (!projectPattern.test(projectId) || !referencePattern.test(sourceReference) || !inputTypes.includes(inputType) || !fingerprintPattern.test(contextFingerprint)) throw new MultimodalRequestError("QUERY_REJECTED");
  let response: Response;
  try { response = await fetcher("/api/multimodal/v29/analyze", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: projectId, source_reference: sourceReference, input_type: inputType, context_fingerprint: contextFingerprint }) }); }
  catch { throw new MultimodalRequestError("MULTIMODAL_UNAVAILABLE"); }
  if (!response.ok) throw new MultimodalRequestError(response.status === 422 ? "QUERY_REJECTED" : "MULTIMODAL_UNAVAILABLE");
  try { return await response.json() as MultimodalAnalysisProjection; } catch { throw new MultimodalRequestError("QUERY_REJECTED"); }
}
