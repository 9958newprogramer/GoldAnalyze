# AurumLab 架构决策与迭代边界

## 项目叙事

AurumLab 的主语是 Agent 工程，不是黄金策略：

> Agent 先进行确定性安全预检，再由 LLM 识别语义意图，加载版本化 Skill，并在独立 Tool Policy 下生成可审计的结构化产物。

这与 AgentForge 形成互补：AgentForge 重点展示 Agentic RAG 和知识库工作流；AurumLab 重点展示意图路由、Skill/MCP、工具治理、确定性任务执行、降级策略和自动评测。

## v0.4 运行架构

```text
Web / HTTP / MCP
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
        ├── Artifact Memory: fingerprint → exact / candidate / miss
        │       ├── exact hit → reuse snapshot + provenance
        │       └── candidate / miss → execute governed workflow
        └── ToolGateway: request memory → authorize → execute → audit
                ├── deterministic backtest tools
                ├── read-only market query tools
                ├── bounded SearchProvider
                └── direct-response tool
                          ↓
              RunResponse → SQLite RunRepository + ArtifactCache
```

所有入口共享同一个 Orchestrator。MCP、Web 和 API 不复制领域逻辑，便于验证不同协议下的行为一致性。

## 核心决策

### 1. LLM 是主路由，规则只是降级基线

v0.3.1 的 Router 返回强类型 `IntentDecision`：意图、Skill、置信度、理由、路由实现和 fallback 原因。LLM 只返回意图、理由与是否需要澄清；Skill 映射、权限和执行由代码决定。规则路由只在模型未配置、超时、调用失败或输出未通过 Pydantic 时启用，使项目既展示真实语义路由，也能离线运行。

### 2. Skill 是能力与权限的部署单元

每个 Skill 拥有独立版本、步骤、Allowlist 和 Tool 调用预算。Orchestrator 不根据模型自由生成 Tool 名称，而是先加载 Skill，再由 `ToolGateway` 逐次授权。Audit 仅记录 policy、tool、decision、outcome 和 duration，不记录 Secret 或完整参数。

### 3. 外部检索是 Provider，不绑定 Tavily

`SearchProvider` 协议隔离外部服务。Tavily 是首个 Adapter；无 Key 时返回明确的无来源结果，网络错误则降级为 warning。搜索结果视为不可信输入，经 Pydantic、数量上限和字段长度约束后才能进入结果对象。

### 4. 不执行模型生成代码

用户输入和模型输出都不可信。AurumLab 只接受 Pydantic 任务规格，领域层执行固定函数；SQLite 行情库只读、参数化查询，标识符经过校验。这样每次 Run 都可测试、比较和持久化。

### 5. 暂不使用 LangGraph

AgentForge 已展示 LangGraph。当前显式 Orchestrator 更能突出路由、Skill、Policy 与执行语义；等异步任务、断点恢复或人工审批成为真实需求后，再引入 checkpoint 图编排。

### 6. 相似不等于可复用

只有规范化任务规格和数据版本完全一致时才自动复用。结构化相似度超过阈值只产生 `semantic_candidate`，新策略仍调用领域 Tool；这避免了用 10/30 均线的结果回答 11/31 均线。回测依赖行情版本，行情和外部检索还受 TTL 约束。详见 [`ARTIFACT_MEMORY.md`](ARTIFACT_MEMORY.md)。

## 评测架构

```text
golden.v3.jsonl (16 cases)
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

开放式报告暂不使用 LLM-as-Judge，避免离线和 CI 分数漂移。后续可将 Judge 作为非阻塞维度，不替代可确定断言。

## 威胁模型

| 边界 | 风险 | v0.4 控制 |
|---|---|---|
| HTTP/MCP 输入 | 超长输入、Prompt Injection、破坏指令 | 长度校验；Tool 前威胁预检；fail closed |
| 路由与 Skill | 误路由、模型输出越权 | Pydantic 枚举；服务端 Skill 映射；Golden Set；Per-Skill Allowlist 与预算 |
| LLM 输出 | 非法字段、代码或 SQL | `extra=forbid`；枚举/范围；确定性 fallback |
| 外部搜索 | 超时、重定向、不可信内容 | HTTPS；8 秒超时；不跟随重定向；有界验证 |
| 行情 SQLite | SQL 注入、意外写入 | `mode=ro`；参数化值；标识符校验 |
| 持久化 | Secret 泄露、缓存污染、陈旧结果 | 参数化 SQL；最小化 Artifact Snapshot；数据版本 + TTL；Run ID 校验 |
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

### v0.5（长任务与可观测）

- Worker 执行新回测，SSE 推送阶段进度。
- 幂等、取消、超时、有限重试与恢复。
- OpenTelemetry 串联 HTTP → Agent → Tool → Store。
- 延迟、失败率、缓存命中率和 Tool 调用量仪表盘。

### v0.6（招聘展示增强）

- Router 离线混淆矩阵与对抗集扩容。
- MCP Client 动态发现和 Tool schema 兼容检查。
- 增加一个非金融工作流，证明架构可迁移，避免项目被理解为量化策略仓库。

## 面试演示主线

1. 连续输入四类问题，展示路由解释和不同 Skill/Policy。
2. 展示行情查询不触发回测、普通问题不触发外部服务。
3. 输入 Prompt Injection，展示在零 Tool 调用时拒绝。
4. 通过 MCP 调用同一请求，再用 Run Resource 读取结果。
5. 运行 Golden Set v3，展示 16 条案例和包含复用效率的确定性 CI 门禁。
6. 连续提问等价策略，展示 miss 与 exact hit 的耗时、Tool 调用和来源 Run 差异。
7. 将参数改为相近值，展示 semantic candidate 仍重新执行的正确性边界。
