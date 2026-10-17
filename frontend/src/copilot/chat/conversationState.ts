import type { WorkbenchResourceNode } from "../api/workbenchClient";
import type { ConversationTurn } from "../api/projectClient";
import type { EngineeringContextProjection } from "../projectWorkspaceIdentity";
import { resolveConversationMode, resolveResponseType, type ConversationMode, type ResponseType } from "./conversationRenderer";

/**
 * The chat surface is a projection of durable conversation messages plus the
 * current engineering/runtime projections. It is not a second mutable store.
 */
export type ConversationState = {
  messages: readonly ConversationTurn[];
  mode: ConversationMode;
  context_projection: EngineeringContextProjection | null;
  active_artifact: WorkbenchResourceNode | null;
  execution_reference: string | null;
};

export function createConversationState(input: {
  messages: readonly ConversationTurn[];
  contextProjection: EngineeringContextProjection | null;
  activeArtifact: WorkbenchResourceNode | null;
  executionReference: string | null;
  question?: string;
  route?: ConversationTurn["route"];
  previousAnswer?: string | null;
  responseType?: ResponseType | null;
}): ConversationState {
  const question = input.question ?? input.messages.at(-1)?.question ?? "";
  const responseType = resolveResponseType({ responseType: input.responseType, question, previousAnswer: input.previousAnswer });
  return {
    messages: [...input.messages],
    mode: resolveConversationMode(question, input.route, input.previousAnswer, responseType),
    context_projection: input.contextProjection,
    active_artifact: input.activeArtifact,
    execution_reference: input.executionReference,
  };
}
