# 求职表达草稿

## 项目一句话

设计并实现一个多 Skill Agent 执行平台：通过 LLM Router 理解语义意图，将版本化 Skill 编译为受约束执行计划，并由确定性 Executor 在 Tool Policy、MCP、审计轨迹和自动评测保护下生成结构化产物。

## v0.4 可写入简历的要点

- 从零实现 LLM-first Intent Router 与四个版本化 Skill，模型输出经 Pydantic 校验，Skill 映射由服务端约束，并支持超时、非法输出和未配置场景的规则降级。
- 设计 Per-Skill Tool Allowlist、调用预算、执行前 Prompt Injection 拒绝和授权/执行 Audit，使危险请求在零 Tool 调用时 fail closed。
- 以 Provider/Adapter 模式接入 Tavily 与只读 SQLite 行情库，支持无 API Key、无私人数据时确定性降级，保证项目可克隆运行。
- 建立 16 条多意图 Golden Set 和五维确定性 Rubric，将路由、工作流、安全、输出完整性与复用效率接入 CLI/API 回归门禁。
- 实现基于规范化 Intent、类型化 Spec 和行情数据版本的两级 Artifact Memory；等价任务零领域 Tool 调用复用，相似但不同任务仅提示候选并安全重算。
- 为行情与外部研究实现 TTL、数据版本失效、强制刷新和缓存旁路，返回来源 Run、相似度及节省的 Tool 调用/耗时。
- 将评测升级为 Golden Set v3 和五维 Rubric，直接验证 miss → exact hit 的执行模式、Provenance 与 Tool 调用下降。

测试数量、质量门禁分数和耗时应以最新 `make verify` 输出为准，不在简历里写无法复现的固定数字。

## v0.5 新增的可写要点

- 设计 `BoundedPlanner → PlanValidator → PlanRuntime` 三层计划链路，从 Skill Manifest 生成类型化 ExecutionPlan，并在每个 control/tool 动作发生前校验顺序、依赖、Tool 绑定和调用预算。
- 对未知/重复步骤、前向或循环依赖、越权 Tool、Tool 伪装与超预算计划 fail closed；Artifact exact hit 显式标记未执行领域步骤为 skipped，避免伪造执行轨迹。
- 将 Plan 统一暴露给 Web、HTTP API 与 MCP，并在 Golden Set v4 中验证 Plan 有效性及 Plan/Event 执行轨迹一致性。

## v0.6 新增的可写要点

- 实现 MCP Server/Client 双向能力与动态 Tool Catalog，支持进程内及 stdio Transport；将远端工具统一映射为 namespaced Tool，并暴露 Server、协议版本、发现耗时、健康状态和 Schema 指纹。
- 设计 MCP Schema Compatibility Gate，基于 JSON Schema Draft 2020-12 校验 Input/Output Contract，首次发现自动 Pin；刷新发生 Schema 漂移、Tool 缺失、名称冲突或外部 `$ref` 时 fail closed，并保留 last-known-good Catalog。
- 将 MCP Remote Tool Adapter 接入既有 Governance Gateway，使动态发现与动态授权解耦；远端调用继续执行 Per-Skill Allowlist、调用预算、参数白名单、超时控制和 authorization/execution Audit。
- 将 MCP Tool 描述和结构化结果视为不可信输入，对 Prompt Injection、未知参数及过大 Schema/参数/结果进行调用前后拦截；以进程内和真实 stdio 集成测试覆盖发现、调用、漂移、超时及故障隔离。

## v0.7 新增的可写要点

- 为本地与 MCP Tool 建立 `read / external / write / privileged` effect、`low / medium / high` risk 元数据，在统一 Governance Gateway 中执行 most-restrictive-wins 的 allow/deny/review 决策；review 前不执行 Tool、不扣减预算。
- 实现 pending、approved、denied、expired、consumed 人工审批状态机；一次性凭证绑定 Run、Plan、Step、Tool、参数 SHA-256 摘要与 TTL，数据库仅保存 token hash，并通过 SQLite 原子条件更新阻止重放和并发双消费。
- 打通 HTTP、Web 和 MCP 审批恢复链：Web 可展示暂停步骤、风险和参数摘要并批准/拒绝；MCP 只接受控制面签发的凭证恢复，不向 Agent 暴露自批准能力。
- 将外部研究和动态 MCP Tool 默认纳入审批范围，补充伪造、过期、跨任务、参数篡改、重放、并发竞争、明确审批意图及审计一致性测试；Golden Set v5 验证真实 `review → consume → execute` 链路。

当前实现是本地单用户 HITL 演示，不能在简历或面试中表述为“企业级 RBAC/身份认证”。同步 `/api/runs` 的 resume 仍会重放确定性 control steps；只有 Redis Worker 的异步 Job 路径实现逐 Plan step 持久化 Checkpoint 恢复，面试时必须准确区分。

## v0.8 新增的可写要点

- 设计 Redis Streams Consumer Group + SQLite source-of-truth 的异步控制面：HTTP 202 创建 Job，消息仅携带不透明 Job ID；Worker 以租约 CAS 认领，业务状态持久化成功后才 ACK，崩溃消息通过 PEL `XAUTOCLAIM` 恢复。
- 实现版本化 JSON Checkpoint，在每个确定性 Plan step 后保存连续执行指针、显式类型化输出、Event 与 Tool Audit；恢复时校验问题指纹和稳定 Plan ID，从下一步继续并避免重复调用已完成 Tool。
- 支持 SHA-256 幂等键、步骤边界软取消、步骤级硬超时、1–3 次有限重试、错误分类和 dead-letter；固定取消与完成、Redis claim 与 SQLite lease 等并发竞态语义。
- 基于 SQLite 单调 Event ID 实现 SSE，支持 `Last-Event-ID` 断线续传、heartbeat 和终态自动关闭；Web 可切换同步/异步执行并展示排队、步骤、恢复、重试与取消状态。
- 将 HITL 扩展到异步 Job：控制面先原子消费一次性 token，Worker 再校验持久化 grant 与 Run/Plan/Step/Tool/参数/effect/risk 绑定；原始凭证不进入 Redis、Job、Checkpoint、Run 或 Audit。

可量化表述必须引用最新验证记录。当前 v0.8 定向与全量测试覆盖重复提交、双 Worker 竞争、Worker 崩溃、active lease reclaim、取消竞态、超时、有限重试/死信、Checkpoint 恢复、SSE 重连与异步审批；最终数字以 `docs/PROGRESS.md` 为准。

## 面试时主动强调

- 黄金仅用作有确定性输入输出的工具场景，工程重点是 Agent 的路由、能力边界、协议暴露、治理、可观测与评测。
- 与 AgentForge 的区别：AgentForge 证明 Agentic RAG；AurumLab 证明“识别意图—选择 Skill—受控调用 Tool—生成 Artifact—评测与复用”的执行系统。
- LLM 负责语义理解，代码负责安全预检、类型校验、Skill 映射和 Tool 权限；规则 Router 仅承担可观测的故障降级。

## 下一版本增量表述（尚不可作为已完成能力）

v0.9 将增加 OpenTelemetry Trace 与运行指标；v1.0 才会完成 100+ Golden Set、CI、完整 Docker、非金融 Skill 和演示证据包。在对应版本验证前不能写成已完成。
