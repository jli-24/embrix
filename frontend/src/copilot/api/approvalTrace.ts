export type ApprovalTraceStage =
  | "C0_APPROVAL_DIALOG_SHOWN"
  | "C1_APPROVAL_CONFIRM_RESULT"
  | "C2_CREATE_APPROVAL_ENTER"
  | "C3_APPROVAL_HTTP_REQUEST_SENT"
  | "C4_APPROVAL_HTTP_RESPONSE"
  | "C5_APPROVAL_RECEIPT_CREATED";

export type ApprovalTraceStatus = "PASS" | "FAIL";

export type ApprovalTraceEvent = {
  event_name: "approval_runtime_trace";
  stage: ApprovalTraceStage;
  status: ApprovalTraceStatus;
  reason_code?: string;
  http_status?: number;
  decision?: "APPROVED" | "DENIED";
};

export type ApprovalTraceSink = (event: ApprovalTraceEvent) => void;

const defaultSink: ApprovalTraceSink = (event) => {
  if (typeof console !== "undefined" && typeof console.info === "function") {
    console.info("approval_runtime_trace", event);
  }
};

export function recordApprovalTrace(
  stage: ApprovalTraceStage,
  status: ApprovalTraceStatus,
  details: Omit<ApprovalTraceEvent, "event_name" | "stage" | "status"> = {},
  sink: ApprovalTraceSink = defaultSink,
): void {
  sink({ event_name: "approval_runtime_trace", stage, status, ...details });
}
