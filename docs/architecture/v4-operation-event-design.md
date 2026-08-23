# v4 OperationEvent Design

## 1. Background

PR-002 已将 `TEST` 接入 `execution_worker` 的 canonical lifecycle。PR-003 在同一生命周期中增加持久化、可回放的 `OperationEvent`，解决 `OperationStatusView` 只能表达最新快照、Queue/Approval/Receipt/Recovery 缺少统一时间线的问题。

```text
Operation
  -> Approval
  -> Queue
  -> Worker Claim
  -> RUNNING
  -> Typed Adapter
  -> Receipt
  -> Terminal Status
  -> Recovery
```

本设计不创建 Event Bus、Message Broker、Event Sourcing runtime 或第二套执行框架，也不连接 MCP、Simulation、Verification、Repair 或 Engineering Memory。

## 2. Current Lifecycle Audit

`ExecutionOperationService` 创建 Operation、签发 Approval、入队、处理 cancellation 与 recovery decision；`ExecutionWorker` claim Queue、原子消费 Approval、进入 RUNNING、调用 typed Adapter 并生成 Receipt；`SQLiteExecutionStore` 是 lifecycle state 与 event 的唯一持久化所有者。

```text
API
  -> ExecutionOperationService
  -> SQLiteExecutionStore atomic transition
       +-- v66_objects snapshot
       +-- v66_queue state
       `-- v66_operation_events append-only event
  -> ExecutionWorker
  -> typed Adapter Port
  -> SQLiteExecutionStore atomic terminal transition
```

`OperationStatusView` 继续是当前状态来源；`OperationEvent` 只提供不可变的 lifecycle timeline、audit identity 和 replay cursor。

## 3. Event Model

`OperationEvent` 是 strict、frozen、fingerprinted contract，包含：

- tenant、project、operation identity 与 `operation_fingerprint`
- project-scoped monotonic `sequence` 和 opaque `event_id`
- `operation_type`、`event_type`、resulting `status`、`recovery_state`
- 可选 `approval_id`、`receipt_fingerprint`
- stable `reason_code`、UTC `occurred_at` 和 canonical fingerprint

首批事件：

```text
OPERATION_CREATED
APPROVAL_ISSUED
OPERATION_QUEUED
OPERATION_CLAIMED
OPERATION_STARTED
CANCELLATION_REQUESTED
CANCELLATION_PENDING
OPERATION_SUCCEEDED
OPERATION_FAILED
OPERATION_CANCELLED
OPERATION_TIMED_OUT
RECOVERY_REQUIRED
RECOVERY_RESOLVED
```

Event 不允许 command、executable、cwd、environment、shell、script、filesystem path、raw stdout/stderr、UART dump、credential、provider payload 或任意 telemetry。

## 4. Storage Design

现有 v6.6 表保持不变，启动时只执行 additive migration：

```sql
CREATE TABLE IF NOT EXISTS v66_operation_events (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    event_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (tenant_id, project_id, sequence),
    UNIQUE (tenant_id, project_id, event_id)
);
```

`body` 保存 canonical JSON，读取时必须重新通过 `OperationEvent.model_validate_json()` 验证。Store 不提供 event update/delete；sequence 在 `BEGIN IMMEDIATE` transaction 内按 tenant/project 分配。

旧 Operation、Status、Approval、Receipt 和 Queue 不重写、不删除、不 backfill 虚构事件。旧 terminal operation 的 timeline 可以为空；旧 RUNNING operation 在真实 restart recovery 时产生第一条 `RECOVERY_REQUIRED`。

## 5. Transaction Boundary

Service 或 Worker 只决定 transition 语义；Store 负责 expected-state 验证、sequence 分配和原子持久化：

| Transition | 单一 SQLite transaction |
|---|---|
| Create | Operation + WAIT_APPROVAL + OPERATION_CREATED |
| Approval | Approval + APPROVAL_ISSUED |
| Queue | WAIT_APPROVAL -> QUEUED + queue row + OPERATION_QUEUED |
| Claim | queue QUEUED -> CLAIMED + OPERATION_CLAIMED |
| Start | consume Approval + RUNNING + OPERATION_STARTED |
| Cancellation | request + cancellation status + cancellation event |
| Terminal | Receipt（如存在）+ queue terminal + status terminal + terminal event |
| Restart recovery | RECOVERY_REQUIRED status + RECOVERY_REQUIRED event |
| Recovery resolution | decision + terminal status + RECOVERY_RESOLVED |

任一步失败会 rollback；正常路径不允许出现 status 已提交而 event 缺失，或 Receipt 已提交而 terminal status/event 缺失。Canonical transitions 使用 stored Operation fingerprint 和 current Status body 执行 compare-and-set，防止跨请求 identity replacement 与丢失更新。

## 6. Scoped Replay

Store 和 Service 提供：

```text
list_operation_events(tenant_id, project_id, operation_id, limit)
read_operation_events_after(tenant_id, project_id, last_event_id, limit)
```

结果固定按 sequence 升序。合法 cursor 后无事件返回空 tuple；未知、篡改、跨 tenant 或跨 project cursor 返回 `None`，上层统一投影为 `RESYNC_REQUIRED`，不得通过错误差异探测其他 scope。

## 7. SSE Integration

Execution API 在现有 router 上提供：

```http
GET /api/v1/copilot/operations/events/stream?project_id={project_id}
Last-Event-ID: {event_id}
Accept: text/event-stream
```

SSE 使用 `id` 作为 cursor、具体 `OperationEventType` 作为 `event`，`data` 为脱敏 typed projection。它是固定上限的持久化 replay，不是跨进程实时总线；无效 cursor 返回单一 `RESYNC_REQUIRED` frame。

Frontend 复用 fetch-stream/chunk parser 以携带 requester/tenant Header。Panel 先读取 Status snapshot，再 replay events，只投影与当前 Operation ID 匹配的事件。Event 不会自动创建、批准、入队、重试、取消或执行 Operation。

## 8. Existing and Future Relations

- Receipt 仍是 tool result、artifact 和 bounded evidence reference 的来源；terminal event 只绑定 receipt fingerprint。
- Approval 权限不因 Event replay 扩展，且不能通过 replay 再消费。
- BUILD、TEST、UART、HIL 中断恢复为 `INTERRUPTED`；FLASH 为 `TARGET_STATE_UNKNOWN`，不会自动 retry。
- Future Verification 可以通过 terminal event 定位 Receipt，但 PR-003 不触发 Verification。
- Future Simulation/HIL observation 必须保存为独立 typed evidence；不得把 raw telemetry 塞入 lifecycle event。
- Event 不自动写 Audit、Engineering Memory，不创建 Repair proposal。

## 9. Compatibility and Non-Goals

- 保留 `v66_` table naming 和旧 payload reader。
- BUILD、TEST、FLASH、UART、HIL 继续共享同一 Worker/Queue/Receipt/Recovery lifecycle。
- `TEST` 默认 adapter 和真实工具链/硬件 capability 仍为 `UNAVAILABLE/UNVERIFIED`。
- 不新增 Agent，不修改 ApplicationRuntime，不引入 MCP、Simulation、Virtual Hardware Lab、CTest、pytest runner 或 Hardware Test Platform。
