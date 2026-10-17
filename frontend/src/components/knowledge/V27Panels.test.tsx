import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { RecommendationPanel } from "./RecommendationPanel";
import { EngineeringHistoryPanel } from "./EngineeringHistoryPanel";

describe("v2.7 knowledge panels", () => {
  it("renders graph, history and evidence without mutation controls", () => {
    const markup = renderToStaticMarkup(<>
      <KnowledgeGraphPanel loading={false} error={null} snapshot={{ project_id: "demo", nodes: [{ node_id: "node:demo:1", project_id: "demo", entity_type: "VALIDATION", reference: "validation:demo", attributes: ["historical result"], confidence: "VERIFIED", fingerprint: "hidden" }], relations: [], fingerprint: "hidden" }} />
      <RecommendationPanel loading={false} error={null} suggestions={[{ recommendation_id: "recommendation:demo:1", project_id: "demo", matched_reference: "validation:demo", reason: "Historical evidence", evidence_reference: "evidence:demo", confidence: "PROJECTED", risk: "Review required", fingerprint: "hidden" }]} />
      <EngineeringHistoryPanel snapshot={{ project_id: "demo", nodes: [{ node_id: "node:demo:1", project_id: "demo", entity_type: "VALIDATION", reference: "validation:demo", attributes: [], confidence: "VERIFIED", fingerprint: "hidden" }], relations: [], fingerprint: "hidden" }} />
    </>);
    expect(markup).toContain("validation:demo");
    expect(markup).toContain("PROJECTED");
    expect(markup).not.toContain("Apply");
    expect(markup).not.toContain("Download");
    expect(markup).not.toContain("Build");
  });
});
