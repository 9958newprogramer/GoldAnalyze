# AurumLab 架构决策与迭代边界

## 项目叙事

AurumLab 的主语是 Agent 工程，不是黄金策略：

> Agent 先进行确定性安全预检，再由 LLM 识别语义意图，加载版本化 Skill，生成并校验 Bounded Plan，最后在独立 Tool Policy 下执行可审计的结构化任务。

这与 AgentForge 形成互补：AgentForge 重点展示 Agentic RAG 和知识库工作流；AurumLab 重点展示意图路由、Skill/MCP、工具治理、确定性任务执行、降级策略和自动评测。

## v0.9 运行架构

```text
Web / HTTP / MCP ── sync ───────────────────────────────┐
        │                                               ↓
        └─ async Job API → SQLite Job/Event → Redis Stream → Worker
                                               │            ├─ lease/CAS
                                               │            ├─ timeout/retry/cancel
                                               └─ PEL claim  └─ step Checkpoint → SSE
                                                            ↓
    AurumAgent
        ├── Preflight Threat Detection ── deny → zero tool call
        ├── LLM Intent Classifier
        │     └── invalid / timeout / unconfigured → RuleBasedIntentRouter
        │     ├── backtest_strategy  → backtest-strategy
        │     ├── query_market_data  → query-market-data
        │     ├── external_research  → external-research
        │     └── other              → general-response
        ├── SkillRegistry: version + allowed_tools + max_tool_calls
        ├── BoundedPlanner: Skill Manifest → typed ExecutionPlan
        │       ├── PlanValidator → step/dependency/tool/budget validation
        │       └── PlanRuntime → authorize each step before execution
        ├── Artifact Memory: fingerprint → exact / candidate / miss
        │       ├── exact hit → reuse snapshot + provenance
        │       └── candidate / miss → execute governed workflow
        ├── MCP Client Manager
        │       ├── tools/list → namespaced Tool Catalog
        │       ├── JSON Schema validation + fingerprint pin
        │       └── health / timeout / last-known-good isolation
        ├── ApprovalRepository: pending → approved/denied/expired → consumed
        │       └── one-time token hash + run/plan/step/tool/args binding
        └── ToolGateway: evaluate → allow/deny/review → execute → audit
                ├── deterministic backtest tools
                ├── read-only market query tools
                ├── bounded SearchProvider
                ├── governed MCP Tool adapter
                └── direct-response tool
                          ↓
              RunResponse → SQLite RunRepository + ArtifactCache

OpenTelemetry
    ├── HTTP Server → Agent → Route → Plan → Step → Tool → Store
    ├── Job Producer ┄ W3C traceparent in SQLite ┄ Job Consumer → Checkpoint
    ├── bounded local evidence API / Web panel
    └── OTLP → Collector → Jaeger Trace / Prometheus scrape metrics
```

所有入口共享同一个 Orchestrator。MCP、Web 和 API 不复制领域逻辑，便于验证不同协议下的行为一致性。

## 核心决策

### 1. LLM 是主路由，规则只是降级基线

v0.3.1 的 Router 返回强类型 `IntentDecision`：意图、Skill、置信度、理由、路由实现和 fallback 原因。LLM 只返回意图、理由与是否需要澄清；Skill 映射、权限和执行由代码决定。规则路由只在模型未配置、超时、调用失败或输出未通过 Pydantic 时启用，使项目既展示真实语义路由，也能离线运行。

### 2. Skill 是能力与权限的部署单元

每个 Skill 拥有独立版本、步骤、Allowlist 和 Tool 调用预算。Orchestrator 不根据模型自由生成 Tool 名称，而是先加载 Skill，再由 `ToolGateway` 逐次授权。Audit 仅记录 policy、tool、decision、outcome 和 duration，不记录 Secret 或完整参数。

### 3. Planner 是 Executor 的前置安全边界

`BoundedPlanner` 从服务端 Skill Manifest 生成类型化 `ExecutionPlan`，而不是让模型自由发明步骤或 Tool。`PlanValidator` 要求步骤与 Manifest 完整一致、依赖只指向已出现步骤、Tool 绑定属于 Allowlist 且计划调用数不超过预算；`PlanRuntime` 在编译、缓存和 Tool 动作真正发生前逐步授权，乱序或绑定漂移会 fail closed。Artifact exact hit 只完成前置步骤，其余步骤明确标记为 skipped。

### 4. 外部检索是 Provider，不绑定 Tavily

`SearchProvider` 协议隔离外部服务。Tavily 是首个 Adapter；无 Key 时返回明确的无来源结果，网络错误则降级为 warning。搜索结果视为不可信输入，经 Pydantic、数量上限和字段长度约束后才能进入结果对象。

### 5. MCP 发现与授权严格分离

MCP Client 通过进程内或 stdio transport 动态执行 `tools/list`，但发现成功不授予调用权。远端 Tool 先经过命名空间、Schema、大小、Prompt Injection 与指纹校验，再注册为本地 Adapter；只有 Skill Manifest 明确 Allowlist 后，才能通过原有 Tool Budget 和 Audit。首次发现自动 pin Schema，刷新发生漂移时保留 last-known-good Catalog 并降级 Server 健康状态。

当前 Server Binding 由部署方提供，不允许用户 Prompt 指定命令或 URL；暂不开放远程 HTTP，避免在尚无认证、SSRF 防护与 OAuth Scope 时扩大攻击面。详细约束见 [`MCP_CLIENT.md`](MCP_CLIENT.md)。

### 6. review 是执行状态，不是提示文本

Tool 注册时必须声明 effect、risk、来源和审批要求。Policy 使用 most-restrictive-wins：越出 Allowlist 或 privileged effect 直接 deny；external/write、high risk 或显式标记进入 review。review 会持久化 token-free `ApprovalRequest`、暂停 Plan，并在任何副作用或预算扣减前结束当前执行。

批准只签发一次原始 token，数据库保存 hash；凭证绑定 Run、Plan、Step、Tool、参数摘要、effect/risk 和 TTL。恢复时校验稳定 Plan ID，到达同一个 Tool 调用后原子消费，防止伪造、篡改、跨任务使用、重放和并发双消费。MCP 可以使用外部控制面签发的凭证恢复，但不能自批准。详见 [`APPROVALS.md`](APPROVALS.md)。

### 7. 不执行模型生成代码

用户输入和模型输出都不可信。AurumLab 只接受 Pydantic 任务规格，领域层执行固定函数；SQLite 行情库只读、参数化查询，标识符经过校验。这样每次 Run 都可测试、比较和持久化。

### 8. Redis 负责投递，SQLite 负责业务真相

异步 API 先持久化 Job，再向 Redis Stream 投递只含 Job ID 的消息。Consumer Group 提供 at-least-once 分发，SQLite lease owner 和条件更新决定唯一业务执行权；只有终态、重试或审批等待写入成功后才 ACK。Worker 崩溃时消息留在 PEL，恢复 Worker 通过 `XAUTOCLAIM` 后还必须等待旧 SQLite 租约过期。Redis 暂时不可用不会丢 Job，同幂等键重试可重新投递。

每个 Plan step 完成后，Checkpoint 用显式 JSON codec 保存输出和连续 cursor。恢复同时核对问题指纹、Plan ID 与步骤前缀；未知类型、版本漂移或非连续步骤 fail closed。SSE 直接读取 SQLite append-only JobEvent，使浏览器 cursor 与业务状态一致。

### 9. 暂不使用 LangGraph

AgentForge 已展示 LangGraph。当前显式 Orchestrator 更能突出路由、Skill、Policy 与执行语义；异步 Worker 已使用持久化 Checkpoint 解决长任务和进程重启恢复，不为了技术栈重叠引入图框架。

### 10. 相似不等于可复用

只有规范化任务规格和数据版本完全一致时才自动复用。结构化相似度超过阈值只产生 `semantic_candidate`，新策略仍调用领域 Tool；这避免了用 10/30 均线的结果回答 11/31 均线。回测依赖行情版本，行情和外部检索还受 TTL 约束。详见 [`ARTIFACT_MEMORY.md`](ARTIFACT_MEMORY.md)。

### 11. 可观测上下文不扩大数据暴露面

同步入口以 HTTP Server Span 为根，异步入口以 Job Producer/Consumer 连接 API 与 Worker；W3C
Trace Context 保存在 SQLite，而不是放入 Redis Stream。Span 只采集 Run/Job/Plan ID、版本化
Skill、注册 Tool、治理枚举、状态与耗时；Metric label 只用路由模板和服务端枚举。Prompt、参数、
结果、URL、SQL、异常消息、Secret、审批 token 和 baggage 均禁止进入观测数据。默认本地 buffer
有硬上限，OTLP 可导出到 Collector。详见 [`OBSERVABILITY.md`](OBSERVABILITY.md)。

## 评测架构

```text
golden.v6.jsonl (108 cases) → dataset coverage contract
        ↓
EvalRunner → real AurumAgent → Run + Trace + Tool Audit
        ↓
Deterministic Rubric
        ├── Task Accuracy 40%
        ├── Workflow Integrity 20%
        ├── Safety Compliance 20%
        ├── Output Completeness 10%
        └── Reuse Efficiency 10%
        ↓
EvalReportRepository → CLI exit code / API / Web panel
```

数据集级先检查规模、唯一性、路由分布和审批/对抗/缓存/边界/地域输入配额，然后才执行案例。开放式报告暂不使用 LLM-as-Judge，避免离线和 CI 分数漂移。后续可将 Judge 作为非阻塞维度，不替代可确定断言。

## 威胁模型

| 边界 | 风险 | v0.9 控制 |
|---|---|---|
| HTTP/MCP 输入 | 超长输入、Prompt Injection、破坏指令 | 长度校验；Tool 前威胁预检；fail closed |
| 路由、Plan 与 Skill | 误路由、乱序或越权计划 | Pydantic 枚举；服务端 Skill 映射；Plan Validator/Runtime；Per-Skill Allowlist 与预算 |
| LLM 输出 | 非法字段、代码或 SQL | `extra=forbid`；枚举/范围；确定性 fallback |
| 外部搜索 | 超时、重定向、不可信内容 | HTTPS；8 秒超时；不跟随重定向；有界验证 |
| MCP Server → Client | 恶意 Tool 描述、Schema 漂移、参数/结果注入、DoS | Namespace；Draft 2020-12；Schema Pin；大小/分页/超时限制；Injection 拒绝；故障隔离 |
| Tool review → resume | 凭证伪造、参数篡改、跨 Run 使用、重放、并发双消费 | 高熵一次性 token；hash-only 存储；多字段绑定；TTL；SQLite 原子消费 |
| Redis / Worker | 重复投递、租约窃取、崩溃丢消息、无限重试 | Consumer Group + PEL；Redis claim 与 SQLite lease 双校验；完成后 ACK；最多 3 次；dead-letter |
| Checkpoint / SSE | 任意反序列化、步骤重放、跨 Job 越界读取 | 版本化显式 JSON codec；问题/Plan/前缀校验；每 Job 单调 cursor；路径绑定查询 |
| 行情 SQLite | SQL 注入、意外写入 | `mode=ro`；参数化值；标识符校验 |
| 持久化 | Secret 泄露、缓存污染、陈旧结果 | 参数化 SQL；最小化 Artifact Snapshot；数据版本 + TTL；Run ID 校验 |
| OpenTelemetry | Prompt/Token 泄露、高基数标签、伪造上下文 | 字段白名单；无 baggage；路由模板；有界 buffer；Metric 维度长度限制；OTLP operator config |
| Eval API | 计算资源滥用 | 固定本地数据集；最多 50 案例 |

当前是本地单用户作品集，不宣称具备多租户生产安全。公网部署前需要身份认证、租户隔离、速率限制、CSRF 策略和 MCP OAuth/Scope。

## 版本进度与目标

### v0.1—v0.2（已完成）

- 可运行的 SMA 回测闭环、数据 Repository 与合成数据降级。
- Skill Registry、Tool Policy、MCP、Trace、SQLite Run Store。
- Golden Set v1 和自动质量门禁。

### v0.3.1（已完成）

- 四类顶层意图和结构化 `IntentDecision`。
- 四个版本化 Skill 与独立 Tool Budget。
- 本地行情查询、Tavily Provider、能力边界回答。
- Tool 前拒绝、授权/执行审计、Golden Set v2。
- Web/API/MCP 统一多意图入口与展示。
- LLM 主路由、规则故障降级、路由模式与 fallback 原因可观测。
- 共享周期解析器，修复“1小时K”被误判为日线的问题。

### v0.4（已完成：结果复用与记忆）

- 对规范化 Intent + Spec + 数据版本生成稳定任务指纹。
- ToolGateway 请求内短期记忆，避免同一次工作流重复调用 Tool。
- SQLite 长期 Artifact Cache，区分 exact hit、semantic candidate、miss、refresh 和 bypass。
- 行情/外部知识 TTL 与数据版本失效；回测按行情版本确定性复用。
- 返回 `cache_status`、来源 Run ID、相似度、节省 Tool 调用和 Tool 耗时，并纳入 Golden Set v3。
- Artifact Snapshot 遵循数据最小化，不复制原始问题、Route 和详细 Audit。

这里实现“相同问题快速返回”的工程能力，但不扩展成另一个知识库项目。

### v0.5（已完成：受约束计划与执行）

- 类型化 `ExecutionPlan`、control/tool step、显式依赖与稳定 Plan ID。
- Plan Validator 拒绝未知/重复步骤、前向或循环依赖、越权 Tool、Tool 伪装和超预算计划。
- Plan Runtime 在实际动作前强制校验顺序和 Tool 绑定，并记录 completed/skipped/failed step。
- Web/API/MCP 统一返回 Plan；Web UI 展示执行计划及最终状态。
- Golden Set v4 将 Plan 有效性及 Plan/Event 轨迹一致性纳入 Workflow Integrity。

### v0.6（已完成：MCP Client 与动态工具目录）

- MCP Client 支持进程内与真实 stdio 生命周期，动态发现独立非金融 Provider。
- Tool 命名空间、JSON Schema Draft 2020-12 校验、稳定指纹和首次/刷新 Pin。
- 未知参数、Schema 漂移、命名冲突、Prompt Injection、分页/大小/调用超时 fail closed。
- 远端 Tool Adapter 复用现有 Skill Allowlist、调用预算和 Tool Audit。
- HTTP Catalog/Health 暴露 Server 状态、协议/实现版本、Tool 契约和发现耗时。

### v0.7（已完成：治理审批）

- Tool effect/risk 元数据与 allow/deny/review 三态策略，most-restrictive-wins。
- pending/approved/denied/expired/consumed 状态机和一次性 hash-only 凭证。
- Run/Plan/Step/Tool/参数摘要绑定，过期、伪造、重放及并发双消费 fail closed。
- Web 审批面板、HTTP 控制面和只恢复不自批准的 MCP Tool。
- Golden Set v5 对外部研究执行真实审批链，并核验审批与 Tool Audit 一致性。

### v0.8（长任务执行）

- Redis Streams Consumer Group Worker、SQLite lease/CAS、PEL reclaim 和完成后 ACK。
- 每个 Plan step 的版本化 JSON Checkpoint 与精确续跑。
- 幂等、软取消、阶段超时、错误分类、有限重试和 dead-letter。
- SSE 单调事件、`Last-Event-ID`、heartbeat、异步 HITL 与 Web 异步模式。

### v0.9（可观测）

- OpenTelemetry 串联 HTTP → Agent → Plan → Tool → Store 与异步 Job producer/consumer。
- W3C Trace Context 跨 Worker 传播；Redis payload 仍只含 Job ID。
- 请求/Run/Plan/Tool/Job/Checkpoint Counter 与 Histogram，低基数和隐私边界。
- 有界本地 evidence、OTLP Collector、Jaeger Trace 和 Prometheus scrape endpoint。

### v1.0-resume（招聘展示增强）

- Router 离线混淆矩阵与对抗集扩容。
- 增加一个非金融工作流，证明架构可迁移，避免项目被理解为量化策略仓库。

## 面试演示主线

1. 连续输入四类问题，展示路由解释和不同 Skill/Policy。
2. 展示行情查询不触发回测、普通问题不触发外部服务。
3. 输入 Prompt Injection，展示在零 Tool 调用时拒绝。
4. 通过 MCP 调用同一请求，再用 Run Resource 读取结果。
5. 查看 MCP Catalog，展示 stdio 动态发现、namespaced Tool、Schema 指纹和健康状态。
6. 提交外部研究请求，展示 Plan 暂停、风险说明、人工批准、一次性消费和恢复轨迹。
7. 运行 Golden Set v5，展示 16 条案例、Plan/Event/Approval 一致性和复用效率门禁。
8. 连续提问等价策略，展示 miss 与 exact hit 的耗时、Tool 调用和来源 Run 差异。
9. 将参数改为相近值，展示 semantic candidate 仍重新执行的正确性边界。
