import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ControlLabSkeleton, EngineeringBottomPanel, EngineeringInspector, EngineeringWorkspace } from "./EngineeringWorkbench";
import { DesktopShell } from "./DesktopShell";
import { EvidenceStrip } from "../chat/EvidenceStrip";

const resource = { resource_id: "resource-1", parent_id: null, name: "main.c", relative_path: "Core/main.c", node_type: "FILE" as const, category: "FIRMWARE", language: "C", source_fingerprint: "sha256:file" };

describe("engineering workbench projection UI", () => {
  it("renders metadata-only resources and unavailable Virtual Lab", () => {
    const markup = renderToStaticMarkup(<EngineeringWorkspace projectName="Controller" resources={[resource]} activeResourceId="resource-1" onResource={() => undefined} activeActivity="explorer" capabilities={[]} />);
    expect(markup).toContain("main.c");
    expect(markup).toContain("Preview unavailable");
    expect(markup).toContain('aria-label="工程产物操作"');
    expect(markup).toMatch(/<button[^>]+disabled=""[^>]*>查看<\/button>/u);
    expect(markup).toMatch(/<button[^>]+disabled=""[^>]*>在工程中定位<\/button>/u);
    const unavailable = renderToStaticMarkup(<EngineeringWorkspace projectName="Controller" resources={[]} activeResourceId={null} onResource={() => undefined} activeActivity="simulation" capabilities={[]} />);
    expect(unavailable).toContain("Virtual Lab");
    expect(unavailable).toContain("不可用");
    expect(renderToStaticMarkup(<EngineeringWorkspace projectName="Controller" resources={[]} activeResourceId={null} onResource={() => undefined} activeActivity="explorer" capabilities={[]} />)).toContain('data-contextual-status="hidden"');
    expect(renderToStaticMarkup(<EngineeringWorkspace projectName="Controller" resources={[resource]} activeResourceId="resource-1" onResource={() => undefined} activeActivity="explorer" capabilities={[]} />)).toContain('data-contextual-status="visible"');
  });

  it("keeps inspector fields explicit when backend projection is absent", () => {
    const markup = renderToStaticMarkup(<EngineeringInspector finding={null} resource={null} />);
    expect(markup).toContain("选择 Finding 或工程资源查看详情");
    const bottom = renderToStaticMarkup(<EngineeringBottomPanel projection={null} findings={[]} activeTab="verification" onTab={() => undefined} />);
    expect(bottom).toContain("验证投影不可用");
  });

  it("projects finding identity and truthful locate state", () => {
    const finding = { finding_id: "finding-1", finding_type: "RULE", severity: "HIGH", subject: "I2C review", summary: "Evidence-backed finding", evidence_ids: ["evidence-1"], validation_id: null, confidence: 0.8, limitations: ["未执行硬件验证"], target_identity: "Core/main.c", target_fingerprint: "sha256:target", finding_fingerprint: "sha256:finding" };
    const markup = renderToStaticMarkup(<EngineeringInspector finding={finding} resource={resource} />);
    for (const label of ["finding-1", "Target Identity", "Core/main.c", "Target Fingerprint", "sha256:target", "已定位"]) expect(markup).toContain(label);
  });

  it("projects selected evidence details without inventing missing provenance", () => {
    const evidence = { source_type: "WORKSPACE", label: "main.c", reference_id: "evidence-1", status: "Observed", summary: "Source observation", source_fingerprint: "sha256:evidence", observed_at: "2026-08-31T00:00:00Z", source_uri: null };
    const markup = renderToStaticMarkup(<EngineeringInspector finding={null} resource={null} evidence={evidence} />);
    for (const label of ["Evidence", "main.c", "evidence-1", "Observed", "sha256:evidence", "暂无可用投影"]) expect(markup).toContain(label);
  });

  it("makes real conversation evidence selectable without creating a second store", () => {
    const evidence = { source_type: "WORKSPACE", label: "main.c", reference_id: "evidence-1", status: "Observed", summary: "Source observation", source_fingerprint: "sha256:evidence", observed_at: "2026-08-31T00:00:00Z", source_uri: null };
    const markup = renderToStaticMarkup(<EvidenceStrip evidence={[evidence]} display="compact" onSelect={() => undefined} />);
    expect(markup).toContain('data-evidence-selectable="true"');
    expect(markup).toContain('role="button"');
    expect(markup).toContain('tabindex="0"');
  });

  it("supports adaptive Requirement and Engineering surface contracts", () => {
    const requirement = renderToStaticMarkup(<DesktopShell density="compact" theme="light" top={<header>Top</header>} left={<aside>Projects</aside>} center={<main>Fallback</main>} right={<aside>Context</aside>} activity={<div>Activity</div>} copilot={<div>Conversation</div>} workspace={<main>Workspace</main>} inspector={<aside>Inspector</aside>} bottom={<div>Bottom</div>} mode="requirement" inspectorVisible={false} leftOpen={false} rightOpen={false} onCloseLeft={() => undefined} onCloseRight={() => undefined} />);
    expect(requirement).toContain('data-workbench-mode="requirement"');
    expect(requirement).toContain('data-inspector-mode="hidden"');
    expect(requirement).toContain("ide-shell--requirement");
    const engineering = renderToStaticMarkup(<DesktopShell density="compact" theme="light" top={<header>Top</header>} left={<aside>Projects</aside>} center={<main>Fallback</main>} right={<aside>Context</aside>} activity={<div>Activity</div>} copilot={<div>Conversation</div>} workspace={<main>Workspace</main>} inspector={<aside>Inspector</aside>} bottom={<div>Bottom</div>} mode="engineering" inspectorMode="docked" inspectorVisible leftOpen={false} rightOpen={false} onCloseLeft={() => undefined} onCloseRight={() => undefined} />);
    expect(engineering).toContain('data-workbench-mode="engineering"');
    expect(engineering).toContain('data-inspector-mode="docked"');
  });

  it("keeps Conversation primary with a persistent Context projection", () => {
    const markup = renderToStaticMarkup(<DesktopShell density="comfortable" theme="dark" top={<header>Embrix Desktop</header>} left={<aside>Projects</aside>} center={<main>Conversation</main>} context={<aside>Engineering Context</aside>} activity={<div>Activity</div>} bottom={<div>Verification</div>} mode="engineering" leftOpen={false} rightOpen={false} onCloseLeft={() => undefined} onCloseRight={() => undefined} />);
    expect(markup).toContain("ide-shell--no-copilot");
    expect(markup).toContain("ide-shell--context-visible");
    expect(markup).toContain('aria-label="Conversation"');
    expect(markup).toContain('aria-label="Engineering Context"');
    expect(markup).toContain('data-layout-contract="fixed-explorer-flex-conversation-fixed-context"');
    expect(markup).toContain("Conversation");
    expect(markup).toContain("Engineering Context");
  });

  it("keeps Inspector docking controls and Control Lab fail-closed", () => {
    const inspector = renderToStaticMarkup(<EngineeringInspector finding={null} resource={resource} mode="docked" onModeChange={() => undefined} onClose={() => undefined} />);
    expect(inspector).toContain("浮动 Inspector");
    expect(inspector).toContain("关闭 Inspector");
    const control = renderToStaticMarkup(<ControlLabSkeleton />);
    expect(control).toContain("Control Lab");
    expect(control).toContain("Control Engineering Runtime unavailable.");
    for (const forbidden of ["Kp", "Ki", "Kd", "Auto Tune", "waveform"]) expect(control.toLowerCase()).not.toContain(forbidden.toLowerCase());
  });

  it("marks Bottom Panel collapsed as secondary until opened", () => {
    const markup = renderToStaticMarkup(<EngineeringBottomPanel projection={null} findings={[]} activeTab="verification" collapsed onTab={() => undefined} />);
    expect(markup).toContain('data-bottom-state="secondary-collapsed"');
    expect(markup).not.toContain("尚未运行验证");
  });

  it("provides a compact overflow menu for bottom tabs at narrow widths", () => {
    const markup = renderToStaticMarkup(<EngineeringBottomPanel projection={null} findings={[]} activeTab="verification" onTab={() => undefined} />);
    expect(markup).toContain('data-bottom-overflow-menu="true"');
    expect(markup).toContain("更多面板");
    for (const label of ["UART", "Waveform", "Operation Events"]) expect(markup).toContain(label);
  });
});
