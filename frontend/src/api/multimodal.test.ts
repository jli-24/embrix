import { describe, expect, it } from "vitest";
import { analyzeMultimodal, MultimodalRequestError } from "./multimodal";

describe("v2.9 multimodal API", () => {
  it("uses the controlled analyze path", async () => {
    const calls: string[] = [];
    const fetcher = async (path: string) => { calls.push(path); return new Response(JSON.stringify({ observation: {}, interpretation: null, fingerprint: "sha256:" + "a".repeat(64) })); };
    await analyzeMultimodal("demo", "source:demo", "IMAGE", "sha256:" + "a".repeat(64), fetcher as typeof fetch);
    expect(calls).toEqual(["/api/multimodal/v29/analyze"]);
  });

  it("rejects unsafe references locally", async () => {
    await expect(analyzeMultimodal("demo", "C:\\private\\image.png", "IMAGE", "sha256:" + "a".repeat(64))).rejects.toBeInstanceOf(MultimodalRequestError);
  });
});
