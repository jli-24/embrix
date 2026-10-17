import type { ComponentProps } from "react";

import type { CapabilityStatusView } from "../api/capabilityClient";
import type { ReviewView } from "../api/engineeringClient";
import type { DesktopWorkspaceProjection } from "../api/desktopWorkspaceClient";
import { ValidationProposalPanel } from "../validation/ValidationProposalPanel";
import { ReviewQueuePanel } from "../review/ReviewQueuePanel";
import { RuntimePanel } from "../runtime/RuntimePanel";
import { ControlledExecutionPanel } from "../execution/ControlledExecutionPanel";

export function RightRuntimePanel(props: ComponentProps<typeof RuntimePanel> & { capabilities?: CapabilityStatusView[]; capabilityError?: string | null; reviewQueue?: ReviewView[]; workspaceProjection?: DesktopWorkspaceProjection | null; onMutationSettled?: () => void | Promise<void> }) {
  const { capabilities = [], capabilityError = null, reviewQueue = [], workspaceProjection = null, onMutationSettled, ...runtimeProps } = props;
  const visibleCapabilities = ["PROVIDER_CONFIGURATION", "CHAT_MODEL", "ENGINEERING_ANALYSIS_COMPLETION", "KNOWLEDGE_EMBEDDING", "KNOWLEDGE_ANALYSIS", "FIRMWARE_ANALYSIS", "DEBUG_ANALYSIS", "ENGINEERING_MEMORY", "KNOWLEDGE_GOVERNANCE", "KNOWLEDGE_EVOLUTION", "ENGINEERING_VALIDATION", "ENGINEERING_FINDINGS", "REPAIR_PROPOSAL", "OPTIMIZATION_PROPOSAL", "PROPOSAL_REVIEW", "EXECUTION_WORKER", "CONTROLLED_BUILD", "CONTROLLED_FLASH", "UART_OBSERVATION", "HIL_EXECUTION", "TARGET_IDENTITY"];
  return <>
    <RuntimePanel {...runtimeProps} />
    <ValidationProposalPanel projectId={runtimeProps.activeProjectIdentity?.id ?? null} onMutationSettled={onMutationSettled} />
    <ReviewQueuePanel reviews={reviewQueue} />
    <ControlledExecutionPanel projectId={runtimeProps.activeProjectIdentity?.id ?? null} capabilities={capabilities} />
    {workspaceProjection && <section className="ide-document-section" data-component="v67-desktop-workspace-projection" aria-label="Desktop workspace projection">
      <h3>Desktop workspace projection</h3>
      <div className="ide-context-count"><span>Backend workflow</span><strong>{workspaceProjection.workflow_summary.status}</strong></div>
      <div className="ide-context-count"><span>Evidence · Memory · Reviews</span><strong>{workspaceProjection.evidence.items.length} · {workspaceProjection.memory.items.length} · {workspaceProjection.reviews.items.length}</strong></div>
      <div className="ide-context-count"><span>Validation · Findings · Proposals</span><strong>{workspaceProjection.validations.items.length} · {workspaceProjection.findings.items.length} · {workspaceProjection.proposals.items.length}</strong></div>
      <div className="ide-context-count"><span>Changes · Operations · Audit</span><strong>{workspaceProjection.changes.items.length} · {workspaceProjection.operations.items.length} · {workspaceProjection.audit.items.length}</strong></div>
      {(workspaceProjection.authoritative_facts ?? []).map((fact, index) => <div className="ide-context-count" key={`${fact.domain}:${fact.conversation_id ?? "project"}:${fact.object_id ?? index}`} data-component="s01-owner-fact">
        <span>{fact.domain}{fact.conversation_id ? ` · ${fact.conversation_id}` : ""}<br /><small className="muted">{fact.owner} · {fact.source_reference ?? "SOURCE_UNAVAILABLE"} · {fact.reason_code}</small></span>
        <strong>{fact.currentness}{fact.revision ? ` · ${fact.revision}` : ""}</strong>
      </div>)}
      <p className="muted">状态来自 backend projection；此面板不根据按钮、文件名或缓存对象推导批准、应用或执行结果。</p>
    </section>}
    <section className="ide-document-section" aria-label="Capability status">
      <h3>Capability status</h3>
      {capabilityError && <p className="ide-context-unavailable">Capability status is unavailable.</p>}
      {capabilities.filter((item) => visibleCapabilities.includes(item.capability)).map((item) => <div className="ide-context-count" key={item.capability}><span>{item.capability}</span><strong title={item.detail}>{item.status}</strong></div>)}
    </section>
  </>;
}
