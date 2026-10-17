import { describe, expect, it } from "vitest";
import { fetchKnowledgeSnapshot, queryKnowledge, KnowledgeRequestError } from "./knowledge";

describe("v2.7 knowledge API", () => {
  it("uses stable snapshot and query paths", async () => {
    const calls: string[] = [];
    const fetcher = async (path: string) => { calls.push(path); return new Response(path.includes("query") ? "[]" : JSON.stringify({})); };
    await fetchKnowledgeSnapshot("demo", fetcher as typeof fetch);
    await queryKnowledge("demo", "sha256:" + "a".repeat(64), fetcher as typeof fetch);
    expect(calls).toEqual(["/api/knowledge/v27/demo", "/api/knowledge/v27/query"]);
  });

  it("rejects unsafe project and context values locally", async () => {
    await expect(fetchKnowledgeSnapshot("../private")).rejects.toBeInstanceOf(KnowledgeRequestError);
    await expect(queryKnowledge("demo", "invalid")).rejects.toBeInstanceOf(KnowledgeRequestError);
  });
});
