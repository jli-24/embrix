# Embedded Copilot Agent

Embedded Copilot v7.0.0 是面向嵌入式工程师的可追踪工程分析系统。项目通过
LangGraph Multi-Agent workflow、RAG、Tool Calling、结构化 evidence、FastAPI 与 Streamlit，
把工程需求、领域分析、证据来源和最终 `EngineeringReport` 连接为离线可测、边界清晰、
可审计的 Engineering Copilot 工作流。

当前 package 与 runtime version literal 为 `v7.0.0`。VS Code MCP Integration Layer 提供
transport-neutral 工具适配契约；不安装 MCP SDK、不启动 MCP Server，也不修改 FastAPI contract、
Agent API 或 `EngineeringReport` schema。

v0.37.0 新增 security-first Tool Execution Layer，保持 Mock-only build/test、无 Shell、
无 Flash、无 hardware control 和无文件写入。

v0.38.0 新增 Verification Agent Layer，对 Firmware、Hardware 与 Tool Result 执行确定性
候选验证。`FAIL` 仅表示当前提案未通过验证规则，不表示已确认真实硬件故障；任一 Checker
异常会使整批验证失败，不返回部分结果。该层不执行工具、不自动修复，也不改变 Workspace
Runtime 的唯一写边界。

v0.39.0 新增 Engineering Memory Layer，以 strict frozen contract、revision、operation
receipt 和最小披露权限保存工程记忆候选。只有兼容 Verification 或受限 Human Approval
可以推进可信状态；首版仅提供同步线程安全的 InMemory reference Store，不提供真实持久化、
Agent/LLM/RAG 调用、Tool execution、文件或硬件操作。Workspace Runtime 仍是唯一写边界。

## 项目介绍

系统接收工程需求与 metadata-only 附件描述，由 Supervisor 调度现有领域 Agent，并通过
FastAPI 返回唯一的跨 API 分析结果 `EngineeringReport`。它不是通用聊天机器人，也不替代
EDA、真实构建、设备调试或人工工程审核。

当前已覆盖的产品分析方向包括：

- Datasheet analysis
- PCB review
- Firmware analysis
- Debug diagnosis

## 系统架构

```text
User
  -> Streamlit Web
  -> ProductApiClient
  -> FastAPI
  -> AnalysisService
  -> Supervisor
  -> Knowledge / Planning / Domain Agents
  -> EngineeringReport
```

RAG 通过受控知识边界向 Knowledge Agent 提供检索结果；Runtime Agent 的外部能力只能通过
Tool Layer 使用。Web 不直接 import 或调用 Runtime Agent implementation。

Evaluation 位于生产 workflow 外部，只消费 synthetic benchmark case 与结构化结果：

```text
Synthetic BenchmarkDataset
  -> EvaluationRunner
  -> injected Supervisor
  -> EngineeringReport
  -> deterministic metrics
  -> EvaluationReport
```

## 核心能力

- Supervisor 驱动的 LangGraph Multi-Agent orchestration
- 基于 RAG 的知识检索与 source citation
- HardwareAgent、FirmwareAgent、PCBAgent、DebugAgent 领域分析
- metadata-only 附件输入与类型识别
- 可追踪的 `EngineeringReport`，保留 `source_agent` 与 `source_id`
- FastAPI Product API 与 Streamlit 工程工作台
- 离线 Evaluation metrics 与确定性 Benchmark 投影
- ProductApiClient 固定中文安全错误映射
- Coding Runtime：代码结构、构建日志、Diff 与硬件/软件风险候选分析
- Workspace Runtime：可信根快照、diff 验证、审批后受控写入与无内容审计
- VS Code MCP Integration Layer：MCP 工具参数/DTO 适配与既有 Runtime Port 调用
- Local/GitHub Knowledge Provider architecture
- Metadata-only multimodal input foundation
- Deterministic PCB and Datasheet evidence parsing
- Engineering Memory：隔离 CANDIDATE 记录与确定性 VERIFIED projection

## Demo 运行

要求 Python 3.11，并先安装项目依赖。启动本地 API：

```powershell
python -m uvicorn embedded_copilot.api.main:app --host 127.0.0.1 --port 8765
```

启动 Web：

```powershell
python -m streamlit run web/app.py --server.address 127.0.0.1 --server.port 8501
```

浏览器打开 `http://127.0.0.1:8501`，进入“工程工作台”：

1. 点击 `Load Demo`，载入 ESP32 Camera 中文需求与附件 metadata。
2. 检查工程需求、附件类型、MIME、`size_bytes` 与 Agent 选择。
3. 点击 `Analyze`，通过 FastAPI 提交任务。
4. 使用“刷新”观察 `Queued`、`Running`、`Completed` 状态。
5. 完成后查看并下载 `EngineeringReport` Markdown。

附件正文不会由 Web 读取；Demo 和上传文件只向 Product API 提交文件名、类型、MIME、大小
与受控 metadata。Docker Compose 场景通过 `EMBEDDED_COPILOT_API_URL` 将 Web 指向容器内
API，不改变既有 Docker runtime contract。

## 技术栈

- Python 3.11
- LangGraph / LangChain
- RAG / Chroma
- FastAPI / Pydantic
- Streamlit
- pytest / ruff

默认 `runtime_mode=offline`。测试不依赖真实硬件、串口、在线模型或公网；API Key、Token
和密码只能通过环境配置注入，不得写入仓库。

## Benchmark

当前 v0.20.0 deterministic release snapshot 复用 3 个 synthetic end-to-end integration
cases，覆盖 ESP32 Camera、Firmware/Debug 与 PCB review workflow。指标包括：

- Task Routing Accuracy
- Agent Success Rate
- Report Completeness
- Evidence Traceability
- Total Execution Latency

固定时钟样本为 1/2/3 ms，平均值为 2 ms，仅验证 Evaluation 与 Web 投影 contract，不代表
生产性能或硬件实时性。当前公共边界无法可靠观测 per-Agent latency，因此明确显示为
`unavailable`，不估算或补造数据。

## 安全边界

- Web 只通过 ProductApiClient 调用 FastAPI，不绕过 Product API。
- ProductApiClient 不向 UI 暴露 request、URL path、token、异常原文或未知服务端 `detail`。
- connection、timeout、HTTP、invalid JSON 与 schema validation failure 使用固定中文提示。
- 附件只处理 metadata，不读取正文、不扫描目录、不创建索引。
- `EngineeringReport` 是分析 API 的唯一结果，工程字段保留 provenance。
- Evaluation 不保存 request、附件正文或 `AgentResult`，不创建在线 judge。
- 未经明确授权，不执行文件系统、Shell、串口、编译器或设备操作。

## Limitations

- 不自动修改 PCB。
- 不自动烧录设备。
- 不替代 EDA DRC、ERC 或 connectivity verification。
- 不替代人工审核和真实硬件验证。
- Datasheet complex table 缺少可靠结构时安全失败或忽略，不使用 LLM 猜测。
- PCB/Datasheet parsing 只支持已验证的有限格式和 evidence 范围。
- Benchmark 延迟是 deterministic contract snapshot，不是生产性能承诺。
- Coding Runtime 不写入工程、不执行构建或 Git、不控制 IDE 或硬件；候选结论需要工程师审核。
- Workspace Runtime 不执行命令、构建、Git、IDE 或 Agent 编排；文件变更必须通过已验证 proposal 与人工审批。
- VS Code MCP Integration Layer 不包含 MCP SDK、server transport 或真实 VS Code 连接；Workspace Runtime 保持唯一写入口。

- 制造包闭环读取 `ManufacturingSpecification`、需求、工程决策、DesignTruth、PCB/BOM artifact 和既有 evidence。板厚、表面处理、铜厚、阻焊和丝印只有在需求、工程决策或用户批准的 `ManufacturingParameterDecision` 中明确给出时才会关闭；否则保留为 `UNSPECIFIED` 并返回 `BLOCKED_MANUFACTURING_PARAMETER_REQUIRED`。
- `PCB_ONLY` 仅通过既有 `ExecutionWorker` 和 KiCad CLI 生成 Gerber、Drill 与制造包。成功包包含 `gerber/`、`drill/`、`pcb_source/`、`manifest.json` 和 `evidence.json`，并绑定项目、PCB、DesignTruth、制造规格 fingerprint 与 revision；CPL 和 Pick-and-Place 不在该模式生成。
