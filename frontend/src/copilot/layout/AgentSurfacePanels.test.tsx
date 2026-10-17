import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { EngineeringKnowledgeModel } from "../api/projectClient";
import type { ProjectWorkspaceIdentity } from "../projectWorkspaceIdentity";
import { EngineeringContextPanel } from "./AgentSurfacePanels";

const identity: ProjectWorkspaceIdentity = {
  id: "project-1",
  name: "31313",
  platform: "ESP32-S3",
  lifecycle: "ready",
  conversationState: "none",
};

function renderContext(overrides: Partial<React.ComponentProps<typeof EngineeringContextPanel>> = {}): string {
  return renderToStaticMarkup(<EngineeringContextPanel
    identity={identity}
    project={null}
    context={null}
    projection={null}
    memoryProjection={null}
    resources={[]}
    workflow={null}
    {...overrides}
  />);
}

describe("Engineering Context empty-state projection", () => {
  it("renders one compact global empty state and suppresses empty domain sections", () => {
    const markup = renderContext();

    expect(markup).toContain('data-engineering-context-empty="true"');
    expect(markup).toContain("当前项目");
    expect(markup).not.toContain("CURRENT REQUIREMENT");
    expect(markup).not.toContain("HARDWARE DESIGN");
    expect(markup).not.toContain("FIRMWARE DESIGN");
    expect(markup).not.toContain("ENGINEERING ROADMAP");
    expect(markup).not.toContain("暂无");
    expect(markup).not.toContain("PROJECT KNOWLEDGE");
    expect(markup).not.toContain("System</dt><dd>31313");
  });

  it("keeps a populated domain and suppresses unrelated empty domains", () => {
    const markup = renderContext({
      resources: [{
        resource_id: "hardware-1",
        parent_id: null,
        name: "motor.kicad_sch",
        relative_path: "motor.kicad_sch",
        node_type: "FILE",
        category: "HARDWARE",
        language: null,
        source_fingerprint: "sha256:hardware",
      }],
    });

    expect(markup).toContain('data-context-domain="hardware"');
    expect(markup).toContain("motor.kicad_sch");
    expect(markup).not.toContain('data-engineering-context-empty="true"');
    expect(markup).not.toContain('data-context-domain="firmware"');
    expect(markup).not.toContain('data-context-domain="documents"');
    expect(markup).not.toContain("暂无");
  });

  it("does not use the project identity as fake project knowledge", () => {
    const model: EngineeringKnowledgeModel = {
      schema_version: "1.0",
      knowledge_id: "knowledge-1",
      project_id: "project-1",
      conversation_id: "conversation-1",
      source_truth_fingerprint: "sha256:truth",
      title: "Engineering knowledge",
      summary: "Confirmed subsystem context",
      system_context: [],
      subsystems: ["subsystem-1"],
      entities: [{
        entity_id: "subsystem-1",
        knowledge_id: "knowledge-1",
        entity_type: "SUBSYSTEM",
        name: "Motor Control",
        description: "Motor control subsystem",
        responsibility: "Control motors",
        truth_level: "EXPLICIT",
        confidence: "HIGH",
        source_truth_refs: [],
        evidence_bindings: [],
        fingerprint: "sha256:entity",
      }],
      relationships: [],
      learning_topics: [],
      modification_points: [],
      design_decisions: [],
      risks: [],
      evidence_bindings: [],
      provenance: [],
      fingerprint: "sha256:knowledge",
    };

    const markup = renderContext({ engineeringKnowledgeModel: model });

    expect(markup).toContain("PROJECT KNOWLEDGE");
    expect(markup).toContain("Motor Control");
    expect(markup).not.toContain("System</dt><dd>31313");
  });
});
