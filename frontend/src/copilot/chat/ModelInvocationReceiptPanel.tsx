import { useEffect, useState } from "react";

import { fetchModelInvocationAttempt, fetchModelInvocationReceipts, ModelGatewayRequestError, type ModelInvocationAttempt, type ModelInvocationReceiptPage } from "../api/modelGatewayClient";

type ReceiptState = { key: string; sequence: number; status: "closed" | "loading" | "empty" | "ready" | "error" | "forbidden" | "not-found"; page: ModelInvocationReceiptPage | null; attempt: ModelInvocationAttempt | null };

export function ModelInvocationReceiptPanel({ projectId, chatRequestId, fetcher = fetch }: { projectId: string; chatRequestId: string; fetcher?: typeof fetch }) {
  const key = `${projectId}:${chatRequestId}`;
  const [state, setState] = useState<ReceiptState>({ key, sequence: 0, status: "closed", page: null, attempt: null });
  const visibleState = state.key === key ? state : { ...state, status: "closed" as const, page: null, attempt: null };

  useEffect(() => {
    if (state.key !== key || state.status !== "loading") return;
    let active = true;
    void fetchModelInvocationReceipts(projectId, chatRequestId, fetcher).then(async (page) => {
      if (!active) return;
      const attempt = await fetchModelInvocationAttempt(projectId, chatRequestId, fetcher);
      if (active) setState((current) => current.key === key ? { ...current, status: page.items.length || attempt ? "ready" : "empty", page, attempt } : current);
    }).catch((error: unknown) => {
      if (!active) return;
      const code = error instanceof ModelGatewayRequestError ? error.code : "MODEL_INVOCATION_RECEIPTS_UNAVAILABLE";
      setState((current) => current.key === key ? { ...current, status: code === "MODEL_INVOCATION_RECEIPTS_FORBIDDEN" || code === "MODEL_INVOCATION_ATTEMPTS_FORBIDDEN" ? "forbidden" : code === "MODEL_INVOCATION_RECEIPTS_NOT_FOUND" ? "not-found" : "error", page: null, attempt: null } : current);
    });
    return () => { active = false; };
  }, [projectId, chatRequestId, fetcher, key, state.key, state.sequence, state.status]);

  return <section aria-label="工程模型调用凭证" data-model-invocation-receipt="true">
    <button type="button" onClick={() => setState((current) => ({ key, sequence: current.sequence + 1, status: "loading", page: null, attempt: null }))}>{visibleState.status === "closed" ? "查看模型调用凭证" : "刷新模型调用凭证"}</button>
    {visibleState.status === "loading" && <p role="status">正在读取模型调用凭证…</p>}
    {visibleState.status === "empty" && <p role="status">暂无模型调用凭证</p>}
    {visibleState.status === "error" && <p role="alert">读取模型调用凭证失败</p>}
    {visibleState.status === "forbidden" && <p role="alert">无权查看模型调用凭证</p>}
    {visibleState.status === "not-found" && <p role="alert">模型调用凭证记录不存在</p>}
    {visibleState.status === "ready" && !!visibleState.page?.items.length && <p>历史调用记录，不代表当前连接健康</p>}
    {visibleState.status === "ready" && visibleState.attempt && <dl data-model-invocation-attempt="true">
      <dt>项目</dt><dd>{visibleState.attempt.project_id}</dd>
      <dt>请求</dt><dd>{visibleState.attempt.request_id}</dd>
      <dt>阶段</dt><dd>{visibleState.attempt.stage_id}</dd>
      <dt>调用类型</dt><dd>{visibleState.attempt.invocation_mode}</dd>
      {visibleState.attempt.authority_reference && <><dt>Task</dt><dd>{visibleState.attempt.authority_reference.engineering_task_id}</dd><dt>Plan</dt><dd>{visibleState.attempt.authority_reference.plan_id}</dd><dt>Plan 内容指纹</dt><dd>{visibleState.attempt.authority_reference.plan_content_fingerprint}</dd><dt>Phase</dt><dd>{visibleState.attempt.authority_reference.phase_id}</dd><dt>绑定指纹</dt><dd>{visibleState.attempt.authority_reference.operation_binding_fingerprint}</dd></>}
      <dt>操作</dt><dd>{visibleState.attempt.authority_reference?.operation_id ?? "—"}</dd>
      <dt>策略来源</dt><dd>{visibleState.attempt.policy_source}</dd>
      <dt>Connection ID</dt><dd>{visibleState.attempt.connection_id}</dd>
      <dt>尝试状态</dt><dd>{visibleState.attempt.status}</dd>
      <dt>逻辑尝试</dt><dd>1 次</dd>
      <dt>Gateway 调用</dt><dd>{visibleState.attempt.gateway_call_count === null ? "UNKNOWN" : `${visibleState.attempt.gateway_call_count} 次`}</dd>
      <dt>Gateway 响应</dt><dd>{visibleState.attempt.gateway_response_observed === null ? "UNKNOWN" : visibleState.attempt.gateway_response_observed ? "已观察" : "未观察"}</dd>
      {visibleState.attempt.budget_phase !== undefined && <><dt>预算角色阶段</dt><dd>{visibleState.attempt.budget_phase}</dd><dt>计数操作</dt><dd>{visibleState.attempt.counter_status} / {visibleState.attempt.counter_transport_send_count == null ? "UNKNOWN" : `${visibleState.attempt.counter_transport_send_count} 次`}</dd><dt>生成操作</dt><dd>{visibleState.attempt.generation_transport_send_count == null ? "UNKNOWN" : `${visibleState.attempt.generation_transport_send_count} 次`}</dd><dt>外部请求合计</dt><dd>{visibleState.attempt.total_external_request_count == null ? "UNKNOWN" : `${visibleState.attempt.total_external_request_count} 次`}</dd></>}
      <dt>Transport 发送</dt><dd>{visibleState.attempt.transport_send_count === null ? "UNKNOWN" : `${visibleState.attempt.transport_send_count} 次`}</dd>
      <dt>发送风险</dt><dd>{visibleState.attempt.status === "PREPARED" ? "尚未进入发送流程" : "可能已发送；UNKNOWN 结果不会自动重试"}</dd>
      <dt>用量</dt><dd>UNKNOWN</dd>
      <dt>额度</dt><dd>UNKNOWN</dd>
      <dt>费用</dt><dd>UNKNOWN</dd>
    </dl>}
    {visibleState.status === "ready" && visibleState.page?.items.map((item) => <dl key={item.fingerprint}>
      <dt>项目</dt><dd>{item.project_id}</dd>
      <dt>请求</dt><dd>{item.request_id}</dd>
      <dt>Trace</dt><dd>{item.trace_id ?? "—"}</dd>
      <dt>阶段</dt><dd>{item.stage_id}</dd>
      <dt>调用类型</dt><dd>{item.invocation_mode}</dd>
      {item.authority_reference && <><dt>Task</dt><dd>{item.authority_reference.engineering_task_id}</dd><dt>Plan</dt><dd>{item.authority_reference.plan_id}</dd><dt>Plan 内容指纹</dt><dd>{item.authority_reference.plan_content_fingerprint}</dd><dt>Phase</dt><dd>{item.authority_reference.phase_id}</dd><dt>绑定指纹</dt><dd>{item.authority_reference.operation_binding_fingerprint}</dd></>}
      <dt>操作</dt><dd>{item.authority_reference?.operation_id ?? "—"}</dd>
      <dt>策略来源</dt><dd>{item.policy_source}</dd>
      <dt>Connection ID</dt><dd>{item.connection_id ?? "未知（旧记录）"}</dd>
      <dt>Provider</dt><dd>{item.selected_model?.provider_id ?? "—"}</dd>
      <dt>Endpoint</dt><dd>{item.selected_model?.endpoint_id ?? "—"}</dd>
      <dt>Model Profile</dt><dd>{item.selected_model?.model_profile_id ?? "—"}</dd>
      <dt>Model</dt><dd>{item.selected_model?.model_id ?? "—"}</dd>
      <dt>结果</dt><dd>{item.result}</dd>
      <dt>Gateway 调用</dt><dd>{item.gateway_call_count === null ? "UNKNOWN" : `${item.gateway_call_count} 次`}</dd>
      <dt>Gateway 响应</dt><dd>{item.gateway_response_observed === null ? "UNKNOWN" : item.gateway_response_observed ? "已观察" : "未观察"}</dd>
      {item.budget_phase !== undefined && <><dt>预算角色阶段</dt><dd>{item.budget_phase}</dd><dt>计数操作</dt><dd>{item.counter_status} / {item.counter_transport_send_count == null ? "UNKNOWN" : `${item.counter_transport_send_count} 次`}</dd><dt>计数响应</dt><dd>{item.counter_response_observed ? `HTTP ${item.counter_http_status} / ${item.counter_input_tokens ?? "无有效 token 数"}` : "未观察"}</dd><dt>生成操作</dt><dd>{item.generation_transport_send_count == null ? "UNKNOWN" : `${item.generation_transport_send_count} 次`}</dd><dt>外部请求合计</dt><dd>{item.total_external_request_count == null ? "UNKNOWN" : `${item.total_external_request_count} 次`}</dd></>}
      <dt>Transport 发送</dt><dd>{item.transport_send_count === null ? "UNKNOWN" : `${item.transport_send_count} 次`}</dd>
      <dt>错误码</dt><dd>{item.error_code ?? "—"}</dd>
      <dt>用量</dt><dd>{item.usage ? `输入 ${item.usage.input_tokens} / 输出 ${item.usage.output_tokens} / 总计 ${item.usage.total_tokens} tokens（${item.usage.usage_source}）` : "UNKNOWN"}</dd>
      <dt>额度</dt><dd>UNKNOWN</dd>
      {item.budget_reconciliation_state !== undefined && <><dt>单次预算证据</dt><dd>{item.budget_envelope_id ? "绑定已保存" : "未绑定"}</dd><dt>费用状态</dt><dd>{item.budget_reconciliation_state}</dd></>}
      <dt>费用</dt><dd>{item.budget_reconciliation_state === "REPORTED" && item.usage?.provider_reported_cost != null ? `Provider 报告 ${item.usage.currency ?? "UNKNOWN"} ${item.usage.provider_reported_cost}` : item.budget_reconciliation_state === "ESTIMATED" && item.usage?.estimated_cost != null ? `估算 ${item.usage.currency ?? "UNKNOWN"} ${item.usage.estimated_cost}` : "UNKNOWN"}</dd>
      <dt>开始</dt><dd>{item.started_at}</dd>
      <dt>完成</dt><dd>{item.completed_at ?? "—"}</dd>
      <dt>Fingerprint</dt><dd>{item.fingerprint}</dd>
    </dl>)}
  </section>;
}
