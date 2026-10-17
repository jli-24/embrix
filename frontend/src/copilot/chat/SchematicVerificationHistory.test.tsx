import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import { SchematicVerificationHistory } from "./SchematicVerificationHistory";

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

it("reads bounded Owner history and requires explicit record selection", async () => {
  const records = [
    { record: { verification_id: "record-old", intent_id: "circuit-1", intent_revision: "r1", plan_id: "plan-1", artifact_fingerprint: "sha256:old", erc_status: "PASS", aggregate_status: "BLOCKED", claims: [{ claim_id: "parse", status: "PASS", reason: "TRUSTED_CANONICAL_PROOF", evidence_ids: [] }, { claim_id: "thermal", status: "BLOCKED", reason: "NO_TRUSTED_RESOLVER", evidence_ids: [] }] }, currentness: "STALE" },
    { record: { verification_id: "record-new", intent_id: "circuit-1", intent_revision: "r1", plan_id: "plan-1", artifact_fingerprint: "sha256:new", erc_status: "PASS", aggregate_status: "BLOCKED", claims: [{ claim_id: "power", status: "BLOCKED", reason: "NO_TRUSTED_RESOLVER", evidence_ids: [] }] }, currentness: "CURRENT" },
  ];
  const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(records), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => { root.render(<SchematicVerificationHistory projectId="project-1" conversationId="conversation-1" />); });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(String(fetcher.mock.calls[0][0])).toContain("schematic-verifications?limit=50");
  expect((container.querySelector("select") as HTMLSelectElement)?.value).toBe("");
  expect(container.textContent).not.toContain("Artifact fingerprint");
  await act(async () => {
    const select = container.querySelector("select") as HTMLSelectElement;
    select.value = "record-old";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(container.textContent).toContain("STALE");
  expect(container.textContent).toContain("整体：BLOCKED");
  expect(container.textContent).toContain("历史 PASS 不代表当前通过");
  expect(container.textContent).toContain("parse：PASS（历史）");
  expect(container.textContent).toContain("thermal：BLOCKED");
  expect(fetcher.mock.calls.every(([, init]) => !init || init.method !== "POST")).toBe(true);
  await act(async () => { root.unmount(); });
});
