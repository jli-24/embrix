# Embedded Copilot Agent Engineering Guide

本文件是 Codex 和其他 AI Coding Agent 在本仓库中的工程开发规范。它定义当前开发边界、Runtime Agent 架构、Engineering Service 职责、知识与执行边界、代码与测试要求，以及长期 Roadmap。

除代码、变量名、类名、函数名、提交信息和技术关键词外，默认使用中文交流与编写项目文档。

## 1. Project Overview

### 1.1 项目定位

Embedded Copilot 是面向嵌入式工程师的 **AI Embedded Engineering Platform**。

项目不是：

- 通用聊天机器人
- 自动 EDA 替代系统
- 普通代码生成工具
- 单 Agent Demo
- 无限扩张的 Agent 集合
- 未经批准即可修改工程或控制设备的自动化系统

项目的核心价值是把以下能力组合成可追踪、可测试、可验证、可审计的嵌入式研发平台：

- LangGraph Multi-Agent Workflow
- RAG 与 Knowledge Gateway
- Runtime 与 Engineering Services
- Projection、Snapshot 与 typed DTO
- Tool Calling 与受控 Execution Boundary
- FastAPI 与 Engineering Interface
- Verification、Human Approval 与 Engineering Memory

### 1.2 核心目标

项目目标是覆盖从需求理解、知识检索、工程分析、受控执行、验证到工程经验沉淀的研发链路：

```text
Requirement Understanding
  -> Knowledge Retrieval
  -> Engineering Analysis
  -> Controlled Execution
  -> Verification
  -> Engineering Memory
  -> Knowledge Evolution
```

项目长期辅助完成：

- Datasheet 知识检索
- MCU 与外设知识分析
- Requirement 与 System Architecture 分析
- Firmware 开发辅助
- 编译错误与 Debug 日志分析
- PCB 与 Hardware 设计分析
- Build、Flash、HIL 与 Device Observation 的受控连接
- Digital Twin、Validation 与 Optimization 分析
- 工程决策、约束、失败、解决方案和验证经验沉淀

长期目标不代表当前已经实现。任何能力只有在 Source code、Tests、Security boundary verification 和 Documentation 同时存在并完成验证后，才能被描述为已实现。

### 1.3 当前项目边界

当前项目状态以用户批准的版本范围和仓库中的完成证据为准：

- 已完成：v3.4 Knowledge Governance Enhancement
- 当前阶段：v3.5 Engineering Knowledge Evolution
- 下一阶段：v3.6 Validation Intelligence
- 未来阶段：v3.7 Optimization Loop

“代码文件存在”不单独构成完成证据。处于 dirty worktree、设计阶段、测试未完成或安全边界未验证的能力，不得因为目录、类名、API 或 Roadmap 条目存在而被描述为已发布或已完成。

未经用户分别批准 scope、architecture、permission model、test plan 和 completion criteria，不得提前实现未来能力。

### 1.4 术语边界

本文中的 **Runtime Agent** 指项目运行时的 Supervisor Agent 和 Specialized Agents。本文中的 **AI Coding Agent** 指负责分析、修改和验证本仓库的 Codex 或其他开发助手。

本文中的 **Engineering Service** 指确定性能力、Projection、Adapter 或 Analysis 服务。Engineering Service 不是 Runtime Agent，不拥有独立 Agent loop 或隐式执行权限。

“Runtime Agent 不得直接访问文件系统或执行 Shell”是产品架构约束，不禁止 AI Coding Agent 在获准的工程任务中使用开发工具修改本仓库。

## 2. System Architecture

### 2.1 当前架构

```text
User
  |
  v
Engineering Interface
  |
  v
Supervisor Workflow
  |
  v
Runtime Agents
  |-- Supervisor Agent
  |-- Knowledge Agent
  |-- Firmware Agent
  `-- Debug Agent
  |
  v
Engineering Services
  |-- Hardware Service
  |-- PCB Service
  |-- Validation Service
  |-- Multimodal Service
  |-- Memory Service
  `-- Optimization Service
  |
  v
Knowledge Gateway
  |-- External Knowledge
  |-- Local RAG
  |-- Engineering Memory
  `-- Conversation Memory
  |
  v
Execution Boundary
  |-- Build
  |-- Flash
  |-- HIL
  `-- Device Observation
```

该图表达职责和边界关系，不表示所有层必须按单一同步链路执行，也不赋予上层绕过 Port、Tool、Approval 或 Workspace Boundary 的权限。

### 2.2 数据流与职责

- Engineering Interface：负责请求验证、依赖注入、调用应用服务，以及将内部结果映射为稳定的 API 或 UI Projection。
- Supervisor Workflow：负责意图识别、路由、显式 Workflow State、终止条件和结果整合。
- Runtime Agents：负责 reasoning、planning、workflow decision 和 structured result generation。
- Engineering Services：负责确定性分析、Projection、Adapter 与可独立测试的领域能力。
- Knowledge Gateway：统一提供带来源、可信度和验证状态的外部知识、Local RAG、Engineering Memory 与 Conversation Memory candidate。
- Execution Boundary：负责 Build、Flash、HIL 和 Device Observation 等外部能力的受控连接。
- Tool Layer：负责外部能力访问、输入验证、timeout、cancellation、permission、错误封装和审计。
- Workspace Runtime：保持受控文件写入边界；其他 Agent、Service、Projection 或 Tool 不得创建第二个 Workspace 写入口。
- Output：必须通过结构化状态返回，失败不得伪装成成功，不确定结论不得伪装成已验证事实。

API、Agent、Engineering Service、Knowledge Gateway、Runtime 和 Tool 之间不得通过隐式全局状态耦合。

### 2.3 Projection 与 Runtime 边界

Projection、Snapshot 和 DTO 用于交换最小、可验证的工程状态，不得暴露 Runtime internals、provider object、设备句柄或隐式执行能力。

公共边界应优先使用：

- frozen、strict、typed DTO
- 明确的 project、artifact、evidence 和 approval identity
- immutable collection
- deep-copy 与 revalidation
- deterministic fingerprint
- 固定、脱敏的错误状态

Observation、Projection、Recommendation、Suggestion 或 Proposal 均不等于 Execution。未经 Verification 与 Approval，不得把它们直接转换为 Workspace、Firmware、Hardware 或设备操作。

## 3. Agent Rules

### 3.1 Supervisor Agent

Supervisor Agent 负责：

- 识别用户意图和任务类型
- 选择合适的 Specialized Agent
- 管理显式 Workflow State
- 控制路由、重试和终止条件
- 整合 Specialized Agent、Engineering Service 与 Tool 的结构化结果

Supervisor Agent 禁止：

- 直接实现 Datasheet、Firmware 或 Debug 等领域业务逻辑
- 直接访问文件系统、网络、串口、编译器或其他外部资源
- 绕过 Tool Layer 调用外部能力
- 仅依赖自由文本 Prompt 隐式决定路由
- 创建无界循环或没有终止状态的 Workflow
- 将 Proposal、Projection 或 Recommendation 直接提升为执行权限

### 3.2 Specialized Agent

每个 Specialized Agent 必须：

- 保持单一职责
- 使用有类型约束的结构化输入与输出
- 只读取任务所需的 Workflow State
- 通过结构化 State update 返回结果或错误
- 通过 Tool Layer 使用外部能力
- 保留必要的来源、Tool 调用和错误信息，支持追踪
- 区分 Observation、Inference、Candidate 与 Verified Fact

Specialized Agent 禁止：

- 绕过 Tool Layer
- 直接修改其他 Agent 私有状态
- 隐式共享可变全局状态
- 扩展到未经批准的其他 Agent 职责
- 将不确定推断表达为已验证事实
- 直接修改 Workspace、Firmware、Hardware 或 Memory

### 3.3 LangGraph Workflow

每个 Workflow 必须：

- 定义显式、带类型约束的 State model
- 定义可检查、可测试的节点与条件边
- 定义结构化成功、失败和终止状态
- 为每个循环规定用途、最大迭代次数和终止条件
- 支持 State 检查，并为安全中断或恢复保留清晰边界
- 使 request、Agent selection、Tool call 和 result 之间可追踪
- 对需要人工判断或高影响操作的状态提供明确 `WAIT_HUMAN` 或等价边界

## 4. Current Runtime Agent Specification

当前 Runtime Agent 架构只包含 Supervisor Agent 和三个 Specialized Agents。Engineering Service、Runtime、Adapter、Projection 或 Workflow view 不得仅因包含领域名称而被重新命名为 Agent。

### 4.1 Supervisor Agent

职责：

- Task classification
- Agent routing
- Workflow State management
- Structured result aggregation
- Failure、timeout 与终止状态管理

Supervisor Agent 只负责 orchestration，不拥有领域执行器或外部资源访问权限。

### 4.2 Knowledge Agent

职责：

- Datasheet 检索
- Embedded 文档检索
- 基于 RAG 的问答
- Source citation

Knowledge Agent 必须保留：

- 文档来源标识
- Source metadata，包括可用时的文件名、章节、页码或 URI
- 原始 Retrieval results 或其可追踪的结构化记录
- Retrieval result 与最终结论之间的引用关系

当检索结果为空、相关性不足、来源冲突或 metadata 缺失时，必须明确报告限制并建议下一步检索。禁止补造 Datasheet 参数、寄存器、引脚、单位、来源或引用。

### 4.3 Firmware Agent

知识范围：

- C/C++
- ESP-IDF
- STM32 HAL
- FreeRTOS

职责：

- 代码解释
- 代码生成
- 代码改进建议
- Firmware 与 RTOS 架构建议

输出必须明确关键假设，例如 MCU family、SDK/HAL version、clock、pin、peripheral、interrupt context 和 RTOS execution context。

除非存在真实硬件测量、构建或测试证据，否则禁止声称代码已经通过硬件验证。生成代码必须区分示例、可编译实现和已验证实现。

### 4.4 Debug Agent

职责：

- 编译错误分析
- ESP32 Guru Meditation 分析
- STM32 HardFault 分析
- Serial Log 分析

输出必须包含以下结构化字段：

- **Evidence**：日志、错误信息、寄存器值或调用栈中直接观察到的事实
- **Root Cause**：基于 Evidence 得出的原因；无法唯一确定时列出候选原因
- **Confidence**：对 Root Cause 可信程度的明确表达，并说明影响判断的缺失证据
- **Recommendation**：按优先级给出定位、修复和验证步骤

禁止在缺少证据时断言唯一 Root Cause。无法定位时必须说明还需要哪些日志、map file、ELF、register dump、源码位置或复现条件。

## 5. Agent vs Engineering Service Boundary

### 5.1 Runtime Agent

Runtime Agent 负责：

- reasoning
- planning
- workflow decision
- structured result generation

Runtime Agent 必须：

- 使用 typed input/output
- 使用 explicit state
- 提供可独立测试的 behavior
- 通过 Port、Protocol、typed State 或 typed Tool result 与其他层通信

Runtime Agent 禁止：

- 直接访问文件系统
- 直接执行 Shell
- 直接控制硬件
- 绕过 Tool Layer
- 持有 provider、transport、device 或 executor runtime object

### 5.2 Engineering Service

Engineering Service 负责：

- deterministic capability
- projection
- adapter
- analysis

典型 Engineering Service 包括：

- Hardware Service
- PCB Service
- Validation Service
- Multimodal Service
- Memory Service
- Optimization Service

Engineering Service 必须：

- 使用窄 Port 或 Protocol 接收依赖
- 对输入与输出执行边界验证
- 返回结构化、可测试且可脱敏的结果
- 保持 deterministic 或明确标记不确定性
- 将外部能力留在 Tool、Runtime 或 injected adapter 边界

Engineering Service 禁止：

- 自己创建 Agent loop
- 绕过 Approval Boundary
- 修改 Workspace
- 自动执行工程操作
- 自动触发 Build、Flash、Repair 或设备控制
- 将 Recommendation 或 Projection 当作已批准操作

### 5.3 领域边界约束

- Hardware 与 PCB 分析必须保留来源、假设和 evidence；不得自动修改 EDA 工程，也不得替代 DRC、ERC、connectivity verification 或电气测量。
- Multimodal 分析必须区分 Observation、Interpretation 与工程事实；图片理解不得替代结构化 EDA 数据或真实测量。
- Validation 的 `PASS` 只表示已执行规则的结果，不自动等同于真实设备通过。
- Optimization 只能生成带 evidence、risk 和 confidence 的 Proposal，不得自动应用修改。
- Computer Use、IDE、Terminal、Build 或 Debug 控制能力只有在独立 scope、permission、timeout、cancellation、audit 和 recovery 设计获批后才能接入。

## 6. Tool Layer Rules

Runtime Agent 的所有外部能力必须通过 Tool Layer 暴露。

Runtime Agent 禁止直接：

- 读取或修改文件
- 执行 Shell 命令
- 调用编译器、构建系统或 EDA 工具
- 访问串口、调试器、网络或外部服务

每个 Tool 必须：

- 定义明确的 input schema 和 output schema
- 在信任边界验证输入
- 返回结构化成功结果和结构化错误
- 显式处理 timeout、cancellation 和 permission
- 隐藏 provider、设备和环境实现细节
- 可独立注入、替换和测试
- 覆盖正常路径与异常路径测试

Tool 不得吞掉异常、返回含糊字符串代替错误类型，或记录 API Key、Token、密码、私有文档内容和敏感本地路径。

Tool 调用至少应产生可关联的结构化事件：

- `tool_called`
- `tool_completed`
- `error_occurred`

Build、Flash、HIL、Device Observation 和其他外部操作必须通过显式 Tool/Adapter Port 调用。高影响操作必须绑定 Approval、目标身份和当前 fingerprint；端口缺失、身份不匹配或审批无效时必须 fail closed。

## 7. Knowledge Gateway Architecture

Knowledge Gateway 负责把不同来源投影为带 provenance、confidence 和 verification status 的工程上下文。它不直接授予 Agent、Service 或 Tool 执行权限。

```text
External Knowledge Layer
  + Local Knowledge Layer
  + Engineering Memory Layer
  + Conversation Memory Layer
  -> Knowledge Gateway
  -> Verified / Projected Engineering Context
```

### 7.1 External Knowledge Layer

负责：

- Web Research source
- Datasheet Retrieval
- GitHub source
- Paper source

外部知识必须保留 URI、文档版本、发布日期、source metadata 和可用的 license 信息。网络、provider 和认证细节必须隔离在受控 adapter 后；默认离线测试不得依赖公网。

### 7.2 Local Knowledge Layer

负责：

- Local RAG
- Project documents
- Datasheet、Reference Manual、SDK 与 Application Note 的本地检索

Local Knowledge 不得扫描未经授权的目录，也不得把机器相关索引、私有正文或未经授权的版权材料提交到 Git。

### 7.3 Engineering Memory Layer

负责保存可追踪的工程候选、事实与历史：

- Decision
- Constraint
- Failure
- Solution
- Validation
- Optimization

Engineering Memory 必须区分 Candidate 与 Verified。未经 Verification 或 Human Approval 的内容不得升级为工程事实。

### 7.4 Conversation Memory Layer

负责：

```text
Conversation extraction
  -> Memory Candidate
```

Conversation Memory 只抽取与工程决策、约束、失败、解决方案、验证或优化相关的结构化候选，不等于保存完整聊天记录。

### 7.5 Knowledge Evolution

Knowledge Evolution 负责：

- Knowledge Node
- Knowledge Relation
- Recommendation/Suggestion projection

Recommendation 或 Suggestion 必须保留 evidence、confidence 和 project identity。它表示历史参考或匹配结果，不表示 AI 已替工程师做出决定。

## 8. Memory Architecture Rules

### 8.1 Memory Flow

正确流程固定为：

```text
Conversation
  -> Conversation Memory Service
  -> Memory Candidate
  -> Existing Memory Approval Flow
  -> Engineering Memory
  -> Knowledge Evolution
  -> Projection
```

Conversation Memory Service 只生成安全、结构化的 Memory Candidate。Memory Candidate 必须经过既有 Memory Approval Flow，才能进入 Engineering Memory 的受信状态。

禁止：

- 自动保存全部聊天
- 把聊天记录直接当作 Engineering Memory
- 绕过 Approval
- 自动写入 Obsidian
- 自动修改代码、硬件、Workspace 或其他工程资产
- 保存 raw model output、prompt、CoT、credential、command、path、log 或 runtime object
- 让 Memory 自动触发 Agent、Build、Flash、Repair 或设备操作

### 8.2 Obsidian Boundary

Obsidian 是 **Knowledge Projection Layer**。

Obsidian 不是：

- Database
- Reasoning Engine
- Memory Source
- Runtime Agent
- 工程事实的唯一来源

固定流程为：

```text
Engineering Memory
  -> Markdown Projection
  -> Obsidian Vault
```

Markdown Projection 只能消费已验证或明确标记可信状态的 Engineering Memory projection。Obsidian Vault 的编辑、文件状态或插件行为不得反向绕过 Memory Approval Flow，也不得直接驱动工程操作。

## 9. RAG Rules

知识库名称为 **Embedded Knowledge Base**。

### 9.1 当前知识范围

- ESP32
- STM32
- ARM Cortex
- FreeRTOS
- Communication Protocol，包括 UART、SPI、I2C、CAN、MQTT 和 TCP/IP
- Embedded Linux，包括 driver、BSP 和 Device Tree

### 9.2 Pipeline 要求

RAG pipeline 必须：

- 将文档解析为技术语义合理的 chunks
- 在 chunking 时保留代码、寄存器名、引脚名、单位和协议术语
- 生成并持久化 embeddings
- 在 ingestion、indexing、retrieval 和 citation 之间保留 metadata
- 返回可追踪的 Retrieval results 与 source citations
- 显式报告解析、索引和检索失败
- 支持可重复 ingestion，并避免重复数据静默污染索引
- 允许在测试中注入 embedding 和 retriever，默认不依赖在线服务

当前 vector store 使用 Chroma 即可。除非出现经过验证的需求并获得设计批准，不得引入 Neo4j、MinIO 或其他增加部署复杂度的组件。

生成的 vector store、未经授权的版权材料和机器相关索引不得提交到 Git。知识来源应保留已知的 source 与 license 信息。

## 10. Security Boundary Rules

### 10.1 Runtime 与外部资源

- LLM 和 Runtime Agent 不得直接读取或修改文件。
- LLM 和 Runtime Agent 不得直接执行 Shell、Git、编译器、EDA 或设备命令。
- Agent 与 Engineering Service 不得直接持有 hardware transport、device handle、provider client 或 credential。
- 所有外部能力必须通过可注入、可验证、可审计的 Port、Tool 或 Adapter 暴露。

### 10.2 Human Approval Boundary

以下高影响操作必须具备显式 Human Approval 或经批准的等价权限边界：

- Workspace write
- Firmware replacement
- Build execution
- Flash
- Hardware action
- HIL validation execution
- Repair application
- Optimization application
- Memory promotion

Approval 必须绑定目标、操作、proposal/action fingerprint、reviewer 和时间，不得被其他请求、项目或已篡改内容复用。

### 10.3 Observation 与 Control

- Observation 不等于 Control。
- Hardware Read 不等于 Hardware Mutation。
- Build、Flash、HIL 和 Device Observation 的 capability 必须分别声明，不得根据一个 capability 推导另一个 capability。
- 图片、日志、Telemetry、Digital Twin 或 Validation 输出必须区分观察、推断和已验证事实。
- 缺少端口、证据、权限或有效 Snapshot 时必须返回结构化 unavailable/rejected/failed 状态，不得 fallback 到隐式执行。

### 10.4 Sensitive Data

日志、API 响应、Projection、Memory 和 Knowledge 不得包含 Secret、Token、密码、credential、私有文档正文、敏感本地路径、raw command、stdout、stderr、provider runtime 或设备秘密。

## 11. Development Rules

### 11.1 技术栈

- Python 3.11
- FastAPI
- Pydantic
- LangGraph
- LangChain
- Chroma
- pytest

Provider、model、embedding、设备和环境配置必须放在配置层或窄接口 adapter 后，不得硬编码凭据、模型名、机器路径或硬件设置。

### 11.1.1 Windows Python Discovery Policy

在 Windows 上发现 Python 版本时，必须优先使用 Windows Python Launcher，不得仅依据
`python3.11`、`where python3.11` 或 `Get-Command python3.11` 的结果判断 Python 3.11
是否可用。发现顺序固定为：

1. 若仓库虚拟环境存在，先检查 `.venv\\Scripts\\python.exe`：

   ```powershell
   .venv\\Scripts\\python.exe --version
   .venv\\Scripts\\python.exe -c "import sys; print(sys.executable); print(sys.version)"
   ```

2. 对明确要求的 Python 版本，使用对应的 Windows Python Launcher selector，例如：

   ```powershell
   py -3.11 --version
   py -3.11 -c "import sys; print(sys.executable); print(sys.version)"
   ```

3. 如仍需确认已安装版本，再使用 `py -0p` 查看 inventory。其他明确版本依次使用
   `py -3.12`、`py -3.13` 等对应 selector。

如果 `py -3.11 --version` 成功，必须报告：

```text
PYTHON_3_11_DISCOVERABLE=true
PYTHON_3_11_DISCOVERY_METHOD=PY_LAUNCHER
```

不得在 Windows Python Launcher 已证明版本可用后再次询问用户是否安装该版本。类似
`WindowsApps\\python.exe` 的 Windows App Execution Alias 既不是仓库 canonical Python
runtime 的充分证据，也不是目标 Python 版本已安装或不可用的充分证据；应优先使用
`.venv\\Scripts\\python.exe` 或 `py -<version> -c "import sys; print(sys.executable)"`
得到的 executable identity。

正常仓库测试、lint、typecheck、build script 和其他项目命令，仓库虚拟环境存在且是项目
预期环境时，必须优先使用 `.venv\\Scripts\\python.exe`。只有任务明确要求某个精确版本的
兼容性、打包、迁移或 interpreter-specific regression verification，或需要为该版本创建
或修复虚拟环境时，才使用 `py -<exact-version>` 执行；不得仅因发现了另一个 Python
版本就静默替换项目虚拟环境。

在报告 `PYTHON_<VERSION>_UNAVAILABLE=true` 之前，必须记录适用的
`.venv\\Scripts\\python.exe`、`py -<version> --version` 和 `py -0p` 检查结果，并区分：

```text
PYTHON_<VERSION>_DISCOVERABLE=
PROJECT_VENV_PRESENT=
PROJECT_VENV_PYTHON_VERSION=
EXACT_VERSION_EXECUTABLE=
DISCOVERY_METHOD=
```

Python discovery failure 属于 environment/preflight 结果，不得误报为 product code defect。

### 11.2 Project Skill Routing

Codex 中存在对应 Project Skill 时，按修改范围使用：

- `agent-architecture`：`agents/`、LangGraph Workflow、Supervisor routing 和 Tool Calling contract
- `rag-development`：`rag/`、文档 ingestion、chunking、embedding、retrieval 和 citation
- `python-backend`：`api/`、FastAPI、Pydantic、dependency injection、service 和 exception mapping
- `testing`：`tests/` 以及所有 Agent、Tool、RAG 或 API 行为变更
- `embedded-c-knowledge`：Firmware、MCU、peripheral、RTOS 和 embedded debug 相关事实
- `git-engineering`：branch、commit、tag、version、changelog 和 release

跨模块修改必须使用所有相关 Project Skills。Skill 用于约束开发过程，不得被 Runtime Agent 当作产品运行能力。

### 11.3 AI Coding Workflow

AI Coding Agent 执行开发任务时必须：

1. 先检查仓库结构、现有实现、测试和未提交修改。
2. 明确任务是否属于当前已批准版本；Roadmap 能力必须先获得范围批准。
3. 大规模或跨模块修改前，先说明影响范围并提交设计与计划供用户批准。
4. 优先进行小而聚焦的修改，不修改无关代码。
5. 修改行为前先补充或调整测试，再实现最小满足需求的代码。
6. 遇到错误先稳定复现、读取完整错误并定位 Root Cause，不进行盲目修复。
7. 先运行 focused tests，再运行相关 regression suite。
8. 完成前进行 review，并报告验证证据、未验证部分和剩余限制。

### 11.4 代码规范

- Python 代码必须使用类型提示。
- 模块必须职责单一，避免在单文件堆积 Agent、Tool、RAG、Service 和 API 逻辑。
- 公共接口和共享 schema 必须清晰、稳定并在边界验证输入。
- 保持 Agent 之间低耦合，通过显式 State、schema 或 service interface 通信。
- Engineering Service 必须通过 Port 或 Protocol 与 Runtime、Tool 和 Adapter 通信。
- 异常必须显式处理，不得静默吞掉。
- 使用结构化日志，跨 API、Workflow、Agent、RAG 和 Tool 传播 trace identifier。
- 日志不得包含 Secret、Token、密码、私有文档正文或敏感本地路径。
- 外部依赖必须可注入，使测试默认不依赖真实硬件和在线服务。
- 不为未经批准的 Roadmap 添加推测性抽象、空壳实现或 Agent。

### 11.5 接口与数据变更

修改以下内容前必须获得用户确认：

- 公共 API
- Workflow State 核心字段
- 共享 Pydantic model
- 持久化数据结构
- Tool 公共 schema
- Approval、Memory 或 Projection 公共 contract

变更时必须说明兼容性影响、迁移方式和测试范围。

### 11.6 Capability Completion Criteria

任何新增或未来能力只有同时满足以下条件，才能被描述为已实现：

1. Source code exists
2. Tests exist and pass
3. Security boundary is verified
4. Documentation is updated

Roadmap、设计文档、目录、类名、DTO、Fake Adapter 或未接入的 API 本身不构成完成证据。无法验证其中任一条件时，必须明确标记为 design、projection、development、unavailable 或 unverified。

### 11.7 Git 规范

提交信息使用以下前缀：

- `feat:` 新功能
- `fix:` 问题修复
- `docs:` 文档或工程规范
- `test:` 测试

版本使用 Semantic Versioning。提交应保持聚焦，不得提交凭据、生成的 vector store、设备 dump、机器相关文件或未经批准的工程资产。

未经用户明确要求，不得执行 `git add`、`commit`、`tag`、`push`、`reset`、`checkout` 或 `stash`。

### 11.8 Pasted Task Execution Policy

Large pasted prompts may be represented by the client as generated pasted-text attachments instead of ordinary inline user-message text.

When the current user turn contains a generated pasted-text attachment and there is no separate typed request outside that attachment:

- Treat the pasted attachment itself as the user's current request.
- Read the full pasted content before deciding whether the user supplied an actionable instruction.
- If the pasted content defines a task and includes execution language such as:
  - "现在开始执行"
  - "开始执行"
  - "请执行"
  - "执行以下任务"
  - "你现在执行"
  - "Now execute"
  - "Start executing"
    then execute that task directly.

Do NOT respond with statements such as:

```text
"The attachment defines a task, but your request is empty."
"Please explicitly ask me to execute it."
"I will not execute the attachment automatically."
```

when the generated pasted-text attachment itself clearly contains the execution request.

A generated pasted-text attachment is part of the current user instruction, not merely reference material.

Only treat an attachment as reference-only when:

- the user explicitly says it is reference material only; or
- it is an ordinary attached document/file rather than generated pasted prompt text; or
- the pasted text contains no actionable request.

If both a separately typed user request and a pasted attachment are present:

- follow the separately typed request;
- use the pasted attachment as supporting task context.

If the pasted task ends with an explicit STOP condition, follow that STOP condition without asking for redundant confirmation.

Do not ask the user to repeat:

"请执行该任务"

when the pasted task already explicitly authorizes execution.

## 12. Testing Rules

所有新增或修改的行为必须使用 pytest 或对应前端测试覆盖：

- 正常路径
- 异常路径
- 输入验证与边界条件
- Workflow 路由、状态转换和终止条件
- Tool schema、错误映射、timeout 与 cancellation
- RAG parsing、chunking、retrieval、metadata 和 citation
- API validation 与 exception mapping
- Projection fingerprint、identity binding 与 tamper rejection
- Approval required、unavailable 与 fail-closed behavior

测试默认必须能在无真实硬件、无串口、无外部模型服务和无公网的环境中运行。使用 dependency injection、fake、stub、temporary directory 和临时 Chroma collection 隔离外部依赖。

禁止在新增行为没有测试的情况下声称任务完成。无法运行测试时，必须说明原因、未验证范围和可执行的后续验证命令。

完成前至少执行：

1. 与修改直接相关的 focused tests。
2. 受影响模块的 regression tests。
3. 条件允许时执行完整 offline test suite。

不得声称通过硬件验证，除非有明确的真实硬件测试证据。

安全边界测试应检查禁止依赖、禁止字段、固定错误映射、单次 Port 调用、输入不可变和输出重新验证。涉及 frontend 时，还应检查无自动刷新、无隐式执行和无未经批准的 mutation control。

## 13. Roadmap

Roadmap 只定义计划顺序，不构成实现声明或执行授权。每个版本开始前必须分别批准 scope、architecture、permission model、test plan 和 completion criteria。

### v2.9 Multimodal Engineering Input Layer

目标：工程视觉理解。

输入：

- PCB screenshot
- Schematic screenshot
- Datasheet image
- Document image

架构：

```text
Safe Input Reference
  -> VisionModelPort
  -> VisionObservation
  -> EngineeringInterpretation
```

必须保持 Observation、Interpretation 与 Verified Engineering Fact 分离。

禁止：

- Vision Agent
- Datasheet Agent
- 自动修改 PCB
- 自动生成硬件
- Build
- Flash

### v3.0 Engineering Memory Platform

目标：建立长期工程经验系统。

包含：

- Conversation Memory Service
- Engineering Memory
- Knowledge Evolution Integration
- Obsidian Projection

固定边界：Conversation 只生成 Memory Candidate；Candidate 必须经过 Existing Memory Approval Flow；Obsidian 只消费 Markdown Projection。

禁止：

- Memory Agent
- Obsidian Agent
- 自动学习模型权重
- 自动修改代码和硬件
- 绕过 Memory Approval Flow

### v3.1 Requirement + System Architecture Layer

该名称仅定义计划阶段。具体 capability、contract、permission 和测试范围必须另行批准。

### v3.2 Engineering Design Generation

该名称仅定义计划阶段。具体 capability、contract、permission 和测试范围必须另行批准。

### v3.3 Execution Platform

该名称仅定义计划阶段。任何执行能力必须继续遵守 Tool Layer、Approval、audit、timeout、cancellation 和 recovery 边界。

### v3.4 Knowledge Governance Enhancement

目标：把显式 Engineering Context evidence 投影为 Knowledge Candidate，经人工审核后生成
`ApprovedKnowledge`。Governance 只通过受控 Sink 发布批准结果，不写入 Memory、Graph 或
Retrieval，不调用 Agent、模型或工程执行能力。

### v3.5 Engineering Knowledge Evolution

目标：把只读 `ApprovedKnowledge` 转换为可审计的 `KnowledgeEvolutionProposal`，支持
`MEMORY`、`GRAPH` 和 `RETRIEVAL` 三类目标，并通过人工 Review 与独立 ApplyRequest 控制
`CREATED -> APPROVED/REJECTED -> APPLIED` 生命周期。

固定边界：

```text
ApprovedKnowledgeSourcePort
  -> KnowledgeEvolutionProposal
  -> Human Review
  -> EvolutionApplyRequest
  -> ControlledEvolutionSinkPort
```

v3.5 只提供 Fake Source、Store 和 Controlled Sink，不连接或修改真实 Memory、Knowledge
Graph 或 Retrieval 系统。`APPLIED` 只表示受控 Sink receipt 已被记录，不表示目标系统已更新。

禁止自动写入知识系统、调用 Agent、调用外部模型或执行任何工程操作。

### v3.6 Validation Intelligence

该名称仅定义计划阶段。验证结果不得在缺少真实证据时宣称硬件已通过。

### v3.7 Optimization Loop

该名称仅定义计划阶段。Optimization Proposal 不得自动修改或执行工程资产。

### v4.0 AI Embedded Engineering Platform

该名称仅定义长期整合目标，不代表现有模块自动获得跨层权限或自治执行能力。

Roadmap 中的条目在对应 Source code、Tests、Security boundary verification 和 Documentation 完成前，均视为未实现。

## 14. Repository Structure

当前架构按职责组织，主要目录关系如下：

```text
embedded-copilot-agent/
|-- AGENTS.md
|-- src/
|   `-- embedded_copilot/
|       |-- agents/
|       |-- api/
|       |-- rag/
|       |-- tools/
|       |-- *_runtime/
|       |-- tool_adapter/
|       |-- engineering_*/
|       |-- hardware*/
|       |-- pcb/
|       |-- firmware*/
|       |-- validation*/
|       |-- digital_twin/
|       |-- optimization/
|       |-- knowledge_evolution/
|       |-- memory_automation/
|       |-- engineering_memory/
|       |-- schemas/
|       `-- services/
|-- frontend/
|-- tests/
|-- docs/
|-- knowledge/
|-- scripts/
|-- Dockerfile
|-- requirements.txt
`-- README.md
```

目录职责：

- `agents/`：只包含 Runtime Agent behavior、routing 和 Workflow node。
- `*_runtime/`：通过公开 Port 提供 framework-independent 能力，不承担 Agent orchestration。
- Engineering Service packages：负责领域分析、Projection、Adapter 和 deterministic capability，不拥有隐式执行权限。
- `rag/`：负责 ingestion、parsing、chunking、embedding、indexing、retrieval 和 citation。
- `tools/`、`tool_runtime/`、`tool_adapter/`：包含外部能力的结构化接口、权限边界和受控 adapter。
- `api/`：只包含 FastAPI 路由、请求响应映射、依赖注入和 API 边界。
- `schemas/`：包含跨模块共享的 typed models 和 validation schemas。
- `services/`：包含不属于 Agent、Tool、RAG 或领域 Service 的应用组合。
- `knowledge_evolution/`、`memory_automation/`、`engineering_memory/`：分别承担知识投影、Memory Candidate flow 和受控工程记忆职责，不得互相绕过 Approval。
- `frontend/`：展示 API 提供的安全 Projection，不直接访问 Runtime 或执行器。
- `tests/`：镜像相关模块结构，并覆盖功能、错误和安全边界。
- `docs/`：保存架构、决策、计划和操作文档。
- `knowledge/`：保存获准的知识来源或 import manifests，不保存生成的 vector store。
- `scripts/`：只保存小型、可重复的开发与维护命令，不承载生产业务逻辑。

禁止混合 Agent、Tool、RAG、Engineering Service 和 API 的模块职责，也禁止把生产业务逻辑放入 `scripts/`、frontend 或测试文件。
