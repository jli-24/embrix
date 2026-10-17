import { describe, expect, it } from "vitest";
import { EngineeringRequestError, fetchEngineeringCompletion, validateEngineeringCompletion } from "./engineering";
import type { EngineeringCompletionSnapshot } from "../types/engineering";

const snapshot = { project_id: "demo", fingerprint: "sha256:" + "a".repeat(64) } as EngineeringCompletionSnapshot;

describe("v2.8 engineering completion API", () => {
  it("uses stable read-only paths", async () => {
    const calls: string[] = [];
    const fetcher = async (path: string) => {
      calls.push(path);
      return new Response(path.endsWith("/validate") ? JSON.stringify({ status: "VALID" }) : JSON.stringify(snapshot));
    };
    await fetchEngineeringCompletion("demo", fetcher as typeof fetch);
    await validateEngineeringCompletion("demo", snapshot, fetcher as typeof fetch);
    expect(calls).toEqual(["/api/engineering/v28/demo", "/api/engineering/v28/validate"]);
  });

  it("rejects unsafe identifiers before network calls", async () => {
    await expect(fetchEngineeringCompletion("../private")).rejects.toBeInstanceOf(EngineeringRequestError);
    await expect(validateEngineeringCompletion("demo", { ...snapshot, project_id: "other" })).rejects.toBeInstanceOf(EngineeringRequestError);
  });
});
