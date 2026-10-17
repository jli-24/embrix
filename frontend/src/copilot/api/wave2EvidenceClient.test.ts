import { describe, expect, it, vi } from "vitest";
import { requestHardwareModelCandidate, createSchematicEvidenceOperation, readSchematicParameter, reviewSchematicParameter, recordSchematicVerification, fetchProjectSupply, reviewProjectSupply, fetchLibraryTerminalMaps, reviewLibraryTerminalMap, createLibraryReadbackOperation, fetchStandardApplicability, reviewStandardApplicability } from "./projectClient";

describe("Wave 2 canonical evidence clients", () => {
  it("uses existing bound hardware candidate route without submitting a stage or approval", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ status: "REVIEW" })));
    const input = { request_id: "request-1", plan_id: "plan-1", phase_id: "phase-1", plan_task_id: "task-1" };
    await requestHardwareModelCandidate("project/1", "conversation/1", input, fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/copilot/projects/project%2F1/conversations/conversation%2F1/hardware-selection/model-candidate-proposal");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(input);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("creates separate unapproved XML operations and reviews only an explicitly read fingerprint", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ operation_id: "parse-1" })));
    await createSchematicEvidenceOperation("p", "c", "PARSE", fetcher);
    await createSchematicEvidenceOperation("p", "c", "READBACK", fetcher);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ kind: "PARSE" });
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ kind: "READBACK" });
    await readSchematicParameter("p", "evidence/1", fetcher);
    expect(fetcher.mock.calls[2][0]).toBe("/api/v1/copilot/projects/p/schematic-parameters/evidence%2F1");
    await reviewSchematicParameter("p", "evidence/1", "sha256:current", "REJECTED", fetcher);
    expect(JSON.parse(String(fetcher.mock.calls[3][1]?.body))).toEqual({ evidence_fingerprint: "sha256:current", decision: "REJECTED" });
    expect(fetcher).toHaveBeenCalledTimes(4);
  });
  it("forwards complete existing D1-D4 selectors without client-authored claims", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ currentness: "CURRENT" })));
    const input = { intent_id: "intent-1", intent_revision: "r1", intent_fingerprint: "sha256:i", plan_id: "plan-1", plan_fingerprint: "sha256:p", artifact_fingerprint: "sha256:a", generation_operation_id: "write-1", erc_operation_id: "erc-1", parse_operation_id: "parse-1", readback_operation_id: "read-1", parameter_evidence_ids: ["parameter-1"] };
    await recordSchematicVerification("p", "c", input, fetcher);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(input);
    expect(String(fetcher.mock.calls[0][1]?.body)).not.toContain("claims");
  });
});

it("uses explicit pre-generation reads and separate fingerprint-bound P1/P2/P3 reviews", async () => {
  const fetcher=vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({})));
  await fetchProjectSupply("p/1","c/1",fetcher);
  await fetchStandardApplicability("p/1","c/1",fetcher);
  await fetchLibraryTerminalMaps("p/1","c/1",fetcher);
  expect(fetcher.mock.calls.map((call)=>call[1]?.method ?? "GET")).toEqual(["GET","GET","GET"]);
  await reviewProjectSupply("p/1","c/1","candidate/1","sha256:selected","REJECTED",fetcher);
  await reviewStandardApplicability("p/1","standard/1","sha256:standard","APPROVED",fetcher);
  await reviewLibraryTerminalMap("p/1","library/1","sha256:library","REJECTED",fetcher);
  expect(JSON.parse(String(fetcher.mock.calls[3][1]?.body))).toEqual({candidate_fingerprint:"sha256:selected",decision:"REJECTED"});
  expect(JSON.parse(String(fetcher.mock.calls[4][1]?.body))).toEqual({evidence_fingerprint:"sha256:standard",decision:"APPROVED"});
  expect(fetcher.mock.calls[5][0]).toBe("/api/v1/copilot/projects/p%2F1/library-terminal-maps/library%2F1/review");
});
it("sends only explicit approved library pair to READBACK and profile2 record",async()=>{
  const fetcher=vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({})));
  await createLibraryReadbackOperation("p","c","map-selected","sha256:map-selected",fetcher);
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({kind:"READBACK",library_map_evidence_id:"map-selected",library_map_evidence_fingerprint:"sha256:map-selected"});
  const input={intent_id:"i",intent_revision:"r1",intent_fingerprint:"sha256:i",plan_id:"plan",plan_fingerprint:"sha256:plan",artifact_fingerprint:"sha256:artifact",generation_operation_id:"write",readback_operation_id:"readback",library_map_evidence_ids:["map-selected"],library_map_evidence_fingerprints:["sha256:map-selected"]};
  await recordSchematicVerification("p","c",input,fetcher);
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual(input);
});
