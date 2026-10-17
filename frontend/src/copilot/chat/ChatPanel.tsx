import { useEffect, useState, type ReactNode } from "react";
import { fetchChatTrace, type ChatTraceEvent, type ContextReference } from "../api/chatClient";
import type { ChatEvidence, ConversationTurn, EngineeringResearchSnapshot, ReferenceProjectDecisionAction, RequirementChangeProposal, SchematicProductionState, SchematicRepairDecisionAction, SchematicRepairProposal } from "../api/projectClient";
import type { EngineeringMemoryProjection } from "../memory/memoryViewModel";
import type { ProjectWorkspaceIdentity } from "../projectWorkspaceIdentity";
import type { EngineeringTaskProjection } from "../task/engineeringTaskModel";
import type { WorkflowView as EngineeringWorkflowProjection } from "../workflow/engineeringWorkflowModel";
import { useExecutionEvents } from "../execution/ExecutionEventProvider";
import type { EngineeringContextProjection } from "../projectWorkspaceIdentity";
import type { WorkbenchResourceNode } from "../api/workbenchClient";
import { createConversationState } from "./conversationState";
import type { ResponseType } from "./conversationRenderer";
import { AssistantMessage } from "./AssistantMessage";
import { ChatInput } from "./ChatInput";
import { UserMessage } from "./UserMessage";
import { ResearchReviewPanel } from "./ResearchReviewPanel";
import { SchematicRepairReviewPanel } from "./SchematicRepairReviewPanel";
import { ConversationActivityTrace } from "./ConversationActivityTrace";
import { projectConversationActivities } from "./conversationActivity";
import { ModelInvocationReceiptPanel } from "./ModelInvocationReceiptPanel";

type ChatPanelProps = {
  title: string;
  chatSessionId?: string | null;
  projectName?: string | null;
  updatedAt?: string | null;
  activeProjectIdentity?: ProjectWorkspaceIdentity | null;
  taskProjection?: EngineeringTaskProjection | null;
  memoryProjection?: EngineeringMemoryProjection | null;
  workflowProjection?: EngineeringWorkflowProjection | null;
  canExport?: boolean;
  sessionReady: boolean;
  turns: ConversationTurn[];
  pendingQuestion: string | null;
  streamAnswer?: string | null;
  streamStatus?: "idle" | "started" | "streaming" | "clarification" | "completed" | "failed" | "resync";
  availableReferences: ContextReference[];
  pending: boolean;
  error: string | null;
  retryable?: boolean;
  selected: ContextReference[];
  evidenceDisplay: "expanded" | "compact";
  onToggleReference(reference: ContextReference): void;
  onAttach(file: File): void;
  onSubmit(question: string): void;
  onRetry(): void;
  onExport?(): void;
  onEvidenceSelect?(evidence: ChatEvidence): void;
  projectionCards?: ReactNode;
  pendingApproval?: ReactNode;
  contextProjection?: EngineeringContextProjection | null;
  activeArtifact?: WorkbenchResourceNode | null;
  executionReference?: string | null;
  responseType?: ResponseType | null;
  researchSnapshot?: EngineeringResearchSnapshot | null;
  requirementChangeProposal?: RequirementChangeProposal | null;
  onReferenceDecision?(review: EngineeringResearchSnapshot["reference_reviews"][number], decision: ReferenceProjectDecisionAction): void;
  onFeatureDecision?(review: EngineeringResearchSnapshot["reference_reviews"][number], feature: string, action: "ADD" | "REMOVE" | "MODIFY" | "KEEP" | "REJECT" | "DEFER"): void;
  onConfirmRequirementProposal?(proposal: RequirementChangeProposal, confirmed: boolean): void;
  schematicProduction?: SchematicProductionState | null;
  onSchematicRepairDecision?(proposal: SchematicRepairProposal, action: SchematicRepairDecisionAction, comment?: string, editedProposalFingerprint?: string): void;
  traceByTurn?: Record<string, ChatTraceEvent[] | null>;
};

function TraceReadNotice({ state }: { state: "loading" | "empty" | "failed" | null }) {
  if (!state) return null;
  const message = state === "loading" ? "正在读取调用记录…" : state === "empty" ? "本次调用记录为空。" : "调用记录读取失败，回答结果不受影响。";
  return <p className="ide-chat-trace-notice" role="status" data-chat-trace-state={state}>{message}</p>;
}

export function ChatPanel({ sessionReady, chatSessionId = null, activeProjectIdentity = null, turns, pendingQuestion, streamAnswer = null, streamStatus = "idle", availableReferences, pending, error, retryable = true, selected, evidenceDisplay, onToggleReference, onAttach, onSubmit, onRetry, pendingApproval = null, contextProjection = null, activeArtifact = null, executionReference = null, responseType = null, researchSnapshot = null, requirementChangeProposal = null, onReferenceDecision, onFeatureDecision, onConfirmRequirementProposal, schematicProduction = null, onSchematicRepairDecision, traceByTurn }: ChatPanelProps) {
  const statusLabel = streamStatus === "started" || streamStatus === "streaming" ? "正在分析…" : null;
  const currentQuestion = pendingQuestion ?? turns.at(-1)?.question ?? "";
  const executionEvents = useExecutionEvents();
  const conversationState = createConversationState({ messages: turns, contextProjection, activeArtifact, executionReference: executionReference ?? executionEvents.events.at(-1)?.operation_id ?? null, question: currentQuestion, route: turns.at(-1)?.route, previousAnswer: turns.at(-1)?.answer_text, responseType });
  const [loadedTraces, setLoadedTraces] = useState<{ key: string; byTurn: Record<string, ChatTraceEvent[] | null> }>({ key: "", byTurn: {} });
  const traceKey = conversationState.messages.map((turn) => `${turn.turn_id}:${turn.trace_id ?? ""}`).join("|");
  const traceLoadKey = `${chatSessionId ?? ""}|${traceKey}`;
  useEffect(() => {
    if (traceByTurn) return;
    const traceTargets = conversationState.messages.filter((turn) => Boolean(turn.trace_id));
    if (!chatSessionId || !traceTargets.length) {
      setLoadedTraces({ key: traceLoadKey, byTurn: {} });
      return;
    }
    let alive = true;
    void Promise.all(traceTargets.map(async (turn) => {
      try {
        return [turn.turn_id, await fetchChatTrace(chatSessionId, turn.trace_id!)] as const;
      } catch {
        return [turn.turn_id, null] as const;
      }
    })).then((entries) => {
      if (!alive) return;
      setLoadedTraces({ key: traceLoadKey, byTurn: Object.fromEntries(entries) });
    });
    return () => { alive = false; };
  }, [chatSessionId, traceByTurn, traceKey, traceLoadKey]);
  const traces = traceByTurn ?? (loadedTraces.key === traceLoadKey ? loadedTraces.byTurn : {});
  const traceStateForTurn = (turn: ConversationTurn): "loading" | "empty" | "failed" | null => {
    if (!turn.trace_id) return null;
    if (!chatSessionId && !traceByTurn) return "failed";
    const value = traces[turn.turn_id];
    if (value === undefined) return "loading";
    if (value === null) return "failed";
    return value.length === 0 ? "empty" : null;
  };
  const activitiesForTurn = (turn: ConversationTurn, isLatest: boolean) => projectConversationActivities({
    traceEvents: traces[turn.turn_id] ?? [],
    executionEvents: isLatest ? executionEvents.events : [],
    evidence: turn.evidence,
    artifactRefs: turn.artifact_refs ?? [],
    awaitingApproval: isLatest && Boolean(pendingApproval),
  });
  // Keep the composer intentionally blank: project scope is already visible in the
  // surrounding workbench and must never consume the user's input area.
  return <div className="ide-chat" data-conversation-primary="true" data-fixed-analysis-dashboard-removed="true" data-conversation-state-source="messages+context_projection+runtime_event_projection" data-conversation-mode={conversationState.mode}>
    {error && <section className="ide-service-notice" role="status" data-conversation-error="true"><div className="ide-conversation-rail ide-conversation-rail--notice" data-conversation-rail="notice"><AssistantMessage answer={null} evidence={[]} recommendations={[]} pending={false} failed errorCode={error} question={currentQuestion} evidenceDisplay={evidenceDisplay} /><div className="ide-service-notice__action">{retryable ? <button type="button" onClick={onRetry}>重试</button> : <small>请先按提示补充工程或配置</small>}</div></div></section>}
    <section className="ide-conversation" aria-live="polite">
      <div className="ide-conversation-rail ide-conversation-rail--messages" data-conversation-rail="messages">
        {conversationState.messages.length === 0 && !pendingQuestion && <div className="ide-conversation__empty"><strong>告诉 Embrix 你想做什么。</strong><small>从需求开始，我会和你一起完成工程。</small></div>}
        {conversationState.messages.map((turn, index) => <div className="ide-turn" key={turn.turn_id}><UserMessage question={turn.question} createdAt={turn.created_at} /><AssistantMessage answer={turn.answer_text} evidence={turn.evidence} recommendations={turn.recommendations} pending={turn.status === "PENDING"} failed={turn.status === "FAILED"} errorCode={turn.error_code} analysisState={turn.analysis_state} evidenceDisplay={evidenceDisplay} question={turn.question} route={turn.route} previousAnswer={conversationState.messages[index - 1]?.answer_text ?? null} responseType={turn.response_type ?? responseType} artifactRefs={turn.artifact_refs ?? []} /><ConversationActivityTrace activities={activitiesForTurn(turn, index === conversationState.messages.length - 1)} /><TraceReadNotice state={traceStateForTurn(turn)} />{activeProjectIdentity && turn.request_id && turn.status !== "PENDING" && <ModelInvocationReceiptPanel key={`${activeProjectIdentity.id}:${turn.request_id}`} projectId={activeProjectIdentity.id} chatRequestId={turn.request_id} />}</div>)}
        {pendingQuestion && <div className="ide-turn"><UserMessage question={pendingQuestion} createdAt={null} />{statusLabel && <div className="ide-analysis-status" role="status">{statusLabel}</div>}<AssistantMessage answer={streamAnswer} evidence={[]} recommendations={[]} pending={streamStatus !== "failed" && streamStatus !== "resync" && streamStatus !== "clarification"} failed={streamStatus === "failed" || streamStatus === "resync"} analysisState={streamStatus === "clarification" ? "WAITING_FOR_CLARIFICATION" : undefined} evidenceDisplay={evidenceDisplay} question={pendingQuestion} previousAnswer={conversationState.messages.at(-1)?.answer_text ?? null} responseType={responseType} /><ConversationActivityTrace activities={projectConversationActivities({ traceEvents: [], executionEvents: executionEvents.events, evidence: [], artifactRefs: [], pending: true, streamStatus, awaitingApproval: Boolean(pendingApproval) })} /></div>}
      </div>
    </section>
    {pendingApproval && <div className="ide-conversation-secondary" data-conversation-secondary="approval"><div className="ide-conversation-rail ide-conversation-rail--approval" data-conversation-rail="approval">{pendingApproval}</div></div>}
    <ResearchReviewPanel snapshot={researchSnapshot} proposal={requirementChangeProposal} busy={pending} onReferenceDecision={(review, decision) => onReferenceDecision?.(review, decision)} onFeatureDecision={(review, feature, action) => onFeatureDecision?.(review, feature, action)} onConfirmProposal={(proposal, confirmed) => onConfirmRequirementProposal?.(proposal, confirmed)} />
    <SchematicRepairReviewPanel production={schematicProduction} busy={pending} onDecision={(proposal, action, comment, editedProposalFingerprint) => onSchematicRepairDecision?.(proposal, action, comment, editedProposalFingerprint)} />
    <div className="ide-conversation-composer" data-conversation-secondary="composer"><div className="ide-conversation-rail ide-conversation-rail--composer" data-conversation-rail="composer"><ChatInput connected={sessionReady} pending={pending} references={availableReferences} selected={selected} onToggleReference={onToggleReference} onAttach={onAttach} onSubmit={onSubmit} /></div></div>
  </div>;
}
