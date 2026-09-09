# AurumLab 简历可用版迭代账本

这是项目的唯一进度真相源。每个小版本完成时必须同步更新本文件，并提交一个 Git checkpoint。若 Codex、终端或会话中断，从“中断恢复步骤”和“下一步”继续，不依赖聊天记录。

## 最终目标

完成一套可在 Agent 开发岗位简历中真实陈述、可现场演示、可由测试和观测证据支撑的 Agent Runtime：

1. LLM Router + Bounded Planner + Deterministic Executor。
2. 版本化 Skill、MCP Server/Client、动态 Tool 发现与 Schema 校验。
3. Tool Governance：Allowlist、预算、Prompt Injection 拒绝、人工审批、审计。
4. Redis Streams Worker、SSE、幂等、取消、超时、有限重试、Checkpoint。
5. OpenTelemetry 端到端 Trace 与可查询运行指标。
6. Artifact Memory、100+ Golden Set/对抗案例、CI、Docker 和至少一个非金融 Skill。

## 版本路线与验收门槛

| 版本 | 目标 | 状态 | 完成门槛 |
|---|---|---|---|
| v0.4-baseline | 固化现有 Router、4 Skills、治理、MCP Server、Memory、Eval | 已验证，checkpoint `c972115` | `make verify`；Golden Set 16/16 |
| v0.5-planner | 类型化 Bounded Planner 与计划轨迹 | 已验证，checkpoint `e9c16aa` | 四类意图计划正确；越权/乱序/超预算计划被拒；API/MCP 返回 Plan |
| v0.6-mcp-client | MCP Client 与动态 Tool 目录 | 已验证，checkpoint `3af3173` | 动态发现、Schema 指纹、命名空间、超时、健康状态均有集成测试 |
| v0.7-approval | 风险分级与人工审批状态机 | 已验证，checkpoint `975d98d` | allow/deny/review 完整闭环；审批不可伪造、过期或重复使用 |
| v0.8-async | Redis Streams Worker 与 SSE 长任务 | 已验证，checkpoint `4f5206f` | 幂等、取消、超时、有限重试、Checkpoint 恢复和断线重连测试；真实 Redis smoke 与浏览器闭环通过 |
| v0.9-observability | OpenTelemetry 与运行指标 | 已验证，checkpoint `5c4d0af` | HTTP→Agent→Plan→Tool→Store 与 Job Producer/Consumer Trace 连通；OTLP Trace/Metrics 可导出 |
| v1.0a-eval-ci | 100+ Eval 与 CI 质量门禁 | 已验证，checkpoint `3311f84` | 100+ 唯一案例、覆盖契约、CI + Redis Service、全量门禁通过 |
| v1.0b-non-financial-skill | 非金融 Skill 与真实 MCP 业务调用 | 已验证，checkpoint `ba6e33a` | 第五类意图完整经过 Router→Skill→Plan→MCP→Artifact；审批、恢复、安全与评测通过 |
| v1.0c-demo-package | Docker、一键演示与最终简历证据包 | 已验证，checkpoint `a457f64` | 容器 healthcheck、演示脚本、可复现指标报告、最终真实性审计 |

状态只能使用：`未开始`、`进行中`、`已验证`、`阻塞`。不能因为代码已写就标记“已验证”。

## 简历主张证据矩阵

| 简历主张 | 当前证据 | 缺口 |
|---|---|---|
| LLM Router + Bounded Planner + 版本化 Skill | 5 类类型化意图、5 个 Skill、LLM 失败规则降级；类型化计划、独立校验与运行时逐步授权；非金融事故复盘证明领域可迁移 | 当前为确定性线性计划；尚无分支、并行和重规划 |
| MCP Server/Client + 动态发现 | Agent MCP Server；进程内/stdio Client；namespaced Catalog；Schema Pin、超时、健康与治理集成测试；`incident-review` 真实消费动态 MCP Tool | 尚无远程 HTTP/OAuth；Binding 只允许部署方固定 Provider |
| Tool Governance + 人工审批 | Tool effect/risk、most-restrictive allow/deny/review；hash-only 一次性凭证；Run/Plan/Step/Tool/参数绑定；Web/API/MCP 恢复；攻击、过期、重放与并发测试 | 当前仅为本地单用户控制面；尚无企业身份认证、RBAC 与多租户隔离 |
| Redis Worker + SSE + 恢复 | Redis Streams Consumer Group、双重租约、有限重试/死信、软取消、步骤超时、JSON Checkpoint、SSE 续传与异步 HITL；96 tests + 真实 Redis smoke + 浏览器证据 | 当前为单节点 SQLite 事件轮询；尚无多租户鉴权、Redis HA 与跨节点 SSE fan-out |
| OpenTelemetry | HTTP/Agent/Plan/Step/Tool/Store/Job/Checkpoint 手工插桩；W3C 上下文跨 Worker；有界本地证据、OTLP、Jaeger/Prometheus 配置；101 tests + 浏览器证据 | Collector/Jaeger Compose 因本机无 Docker 未实际启动；生产告警、SLO 与长期后端留待部署环境 |
| Artifact Memory + Agent Eval | 两级复用、TTL/版本失效；120 条 v7 Golden Set（5 类意图、16 对抗、24 审批、8 缓存）；Coverage Contract 与 GitHub Actions/Redis 门禁 | 评测为确定性本地基线，尚未引入非阻断 LLM-as-Judge 或真实线上流量 |

## 已验证版本记录

### v0.4-baseline — 2026-09-08

**范围**

- LLM-first Intent Router，模型不可用时规则降级。
- 4 个版本化 Skill 和 9 个本地 Tool。
- Per-Skill Allowlist、调用预算、Tool 前威胁预检、授权/执行 Audit。
- MCP Server、FastAPI/Web 统一入口。
- 请求内记忆和 SQLite Artifact Memory，支持 exact hit、semantic candidate、TTL、数据版本失效、refresh/bypass。
- 五维确定性 Eval 与 Golden Set v3。

**验证结果**

- `make verify`：Ruff 通过，Pytest `44 passed`。
- `.venv/bin/aurumlab-eval --json`：dataset v3，`16/16`，score `100.0`。
- 已知非阻塞告警：Starlette TestClient 使用的 AnyIO alias 弃用告警。

**边界与优化取舍**

- 黄金仅作为确定性验证场景，不连接实盘，不执行模型生成的 Python/SQL/Shell。
- LLM 只返回类型化意图；Skill 与 Tool 权限由服务端决定。
- 相似任务不能自动复用，只有任务规格和数据版本完全一致才 exact hit。
- 暂未引入 Planner、MCP Client、异步 Worker、人工审批和 OpenTelemetry，因此不能在当前简历中宣称这些能力已经完成。

### v0.5-planner — 2026-09-08

**完成内容**

- 新增类型化 `ExecutionPlan` / `PlanStep`，记录步骤类型、Tool 绑定、依赖、预算、Planner 来源以及 completed/skipped/failed 状态。
- `BoundedPlanner` 只从服务端版本化 Skill Manifest 编译计划，生成稳定 Plan ID；不执行模型生成的代码、SQL 或任意 Tool 名称。
- 独立 `PlanValidator` 在执行前校验 Skill 版本、步骤全集和顺序、依赖、Tool Allowlist、调用次数及预算。
- `PlanRuntime` 在实际动作发生前再次授权，执行后更新步骤状态；Executor 偏离顺序、依赖或 Tool 绑定时 fail closed。
- Orchestrator、HTTP API、MCP、Web UI 与 Eval 共用同一个 Plan 数据契约；页面可检查计划、Trace 和 Tool Audit。
- Golden Set 升级为 v4，工作流质量维度会校验 Plan 有效性、Skill 一致性、预算和实际事件轨迹。

**关键优化与取舍**

- 当前 Planner 是服务端确定性编译器，LLM 负责语义路由但不能扩张 Tool 权限；这样更容易证明可控性，也避免把金融策略生成误写成项目核心。
- Validator 与 Runtime 分层：前者拒绝静态非法计划，后者防止执行期代码与已验证计划发生漂移。
- 编译、缓存检查和 Tool 调用均在动作发生前完成运行时授权，避免出现“先执行、后审计”的伪治理。
- exact cache hit 只把真实执行的编译与缓存步骤记为 completed，其余领域步骤记为 skipped，保持观测轨迹可信。
- 暂不引入分支、并行和自动重规划；先固化容易测试、容易讲清的线性最小闭环。

**验证证据**

- `make verify`：Ruff 通过，Pytest `52 passed`。
- `.venv/bin/aurumlab-eval --json`：dataset v4，`16/16`，score `100.0`。
- 浏览器端到端验证：四步行情查询 Plan 全部完成，Trace 与 Plan 一致，控制台零报错。
- 二次相同请求命中 Artifact Memory：前两步 completed，领域查询与总结步骤 skipped。

**已知限制**

- Planner 目前仅支持基于 Manifest 的确定性线性计划，不支持 DAG 并行、条件分支或自动重规划。
- MCP 当前只有 Server；Client、动态 Tool discovery、Schema 兼容性和远端健康检查留到 v0.6。
- 尚未实现人工审批、异步 Worker、OpenTelemetry 和 100+ 对抗评测集。

**下一步**

- 实现 v0.6 MCP Client 与动态 Tool Catalog，并确保远端 Tool 仍经过同一治理网关。

### v0.6-mcp-client — 2026-09-08

**完成内容**

- 实现真实 MCP Client 生命周期，支持进程内和 stdio Transport，并以独立非金融 MCP Provider 完成动态 `tools/list` 与结构化调用。
- 新增类型化 Tool Catalog，记录 Server/协议版本、namespace、远端及本地名称、Input/Output Schema、稳定指纹、发现耗时和健康状态。
- 将远端 Tool 映射为 `mcp__<namespace>__<tool>` Adapter 并注册到现有 Tool Registry；实际调用继续经过 Skill Allowlist、调用预算和 authorization/execution Audit。
- 使用 JSON Schema Draft 2020-12 验证 Tool 契约、调用参数和结构化结果；未声明参数默认拒绝，外部 `$ref` 禁止。
- 首次 discovery 自动 pin 指纹，支持 operator pin；刷新发生 Schema 漂移或 Tool 缺失时保留 last-known-good Catalog 并将 Server 标记为 degraded。
- 对 Tool 数量、分页、Schema、参数、结果和调用时间设置硬上限；拒绝 Tool 描述和返回值中的 Prompt Injection 信号。
- FastAPI lifespan 自动连接/关闭 Client，`/api/mcp/catalog` 和 `/api/health` 暴露可演示的动态目录与健康信息。

**关键优化与取舍**

- 严格区分 discovery 与 authorization：Catalog 表示“能力存在”，只有 Skill Manifest Allowlist 才表示“Agent 可调用”。
- Schema 指纹不包含易变的描述文本，只绑定真正的 Input/Output Contract；刷新失败不覆盖已验证目录。
- 远端错误、描述和结果全部按不可信输入处理，不把原始错误或完整参数写入 Audit。
- 默认 Provider 使用非金融文本/运行时工具，提前证明底层 MCP 能力不依赖黄金场景，但暂不为了展示而强行扩张现有路由意图。
- 暂不支持任意远程 HTTP Server；在认证、TLS、SSRF 防护和 OAuth Scope 完成前，仅开放进程内与 stdio，避免制造虚假的“生产级远程接入”。

**验证证据**

- `tests/test_mcp_client.py`：9 个测试覆盖动态发现、稳定指纹、namespaced Tool、Governance Gateway、未知参数、operator pin、Schema 漂移、Prompt Injection、调用超时和真实 stdio Transport。
- `make verify`：Ruff 通过，Pytest `62 passed`。
- `.venv/bin/aurumlab-eval --json`：dataset v4，`16/16`，score `100.0`，证明 MCP 增量未破坏现有 Agent 行为。
- 浏览器端到端：页面显示 `API v0.6.0` 与 `1 server · 2 namespaced tools`，行情任务、Bounded Plan 和 Trace 正常完成。
- 运行时 Catalog：Provider 状态 connected，协议版本、2 个 Tool、Schema 和指纹均可通过 API 读取。

**已知限制**

- 尚未实现远程 Streamable HTTP Client、OAuth/Scope、TLS 身份校验和持久化 Server 配置。
- 自动 pin 在进程生命周期内有效；跨部署固定契约需通过 `expected_schema_fingerprints` 配置 operator pin。
- 默认 MCP Tool 已进入统一 Registry，但尚未被业务 Skill Allowlist；非金融路由 Skill 留到 v1.0。

**下一步**

- 实现 v0.7 Tool 风险分级与人工审批状态机，补全 allow/deny/review 治理闭环。

### v0.7-approval — 2026-09-08

**完成内容**

- Tool 注册改为强制声明 `read / external / write / privileged` effect、`low / medium / high` risk、来源与审批要求；非法枚举、非法名称和低估风险在注册阶段 fail closed。
- `ToolPolicy` 使用 most-restrictive-wins：越出 Skill Allowlist 或 privileged effect 直接 deny；external/write、high risk 或显式审批进入 review；只有 allow 才扣减预算。
- 新增 SQLite `ApprovalRepository` 与 pending、approved、denied、expired、consumed 状态机；批准生成高熵一次性 token，数据库只保存 SHA-256 hash。
- 审批绑定 Run、Plan、Step、Tool、规范化参数摘要、effect/risk 与 TTL；恢复时先校验 Run 和稳定 Plan ID，到达目标 Tool 后原子消费。
- Orchestrator 支持 `pending_approval`、Plan `paused_step`、批准恢复与拒绝终止；Run、Event、Audit 和 Artifact 均不保存原始 token。
- 外部搜索和 MCP Remote Tool 默认进入 review；MCP Server 增加只恢复不自批准的 `resume_agent_run`。
- Web UI 增加 HITL 面板、风险/参数摘要、批准并恢复、拒绝、WAITING/REJECTED Plan 状态和完整 Governance Audit。
- Golden Set 升级为 v5，外部研究案例自动经历真实 `review → approve → consume → allow → execute`，Rubric 核验审批 ID 与执行审计一致性。

**关键优化与取舍**

- review 是可持久化的执行状态，不是依赖模型服从的确认文本；无有效凭证时 Tool 调用数与预算消耗均为 0。
- deny 优先于 review，因此 privileged Tool 即使声明“需要审批”也不能借审批提权。
- 凭证只在批准响应中返回一次，消费或过期后清空 hash；使用常量时间比较和 SQLite 条件更新抵抗伪造、重放与并发双消费。
- MCP 只暴露 resume，不暴露 approve/deny，避免 Agent 使用自己的 Tool 完成自批准。
- 当前是本地单用户演示，明确不把 `local-demo-operator` 和 intent header 宣称为认证；生产化仍需 OIDC/OAuth、RBAC、CSRF token、速率限制和租户隔离。
- v0.7 恢复会复用 Route 并核对 Plan ID，但重新执行确定性 control steps；持久化执行栈与进程重启恢复留给 v0.8 Checkpoint。

**验证证据**

- `make verify`：Ruff check/format 通过，Pytest `71 passed`，Golden Set v5 `16/16`、score `100.0`。
- 单元/集成测试覆盖 hash-only 存储、伪造 token、跨 Run/Plan/参数、pending/approved 过期、重复消费、双线程竞争、预算语义、HTTP 明确意图、拒绝和 MCP Remote Tool review。
- 浏览器端到端批准链：`PENDING_APPROVAL`、目标步骤 `WAITING`、审批面板可见；批准后四步 Plan 全部 completed，Trace 包含 `approval_resume`，Audit 为 `review → allow → completed → allow → completed`。
- 浏览器端到端拒绝链：Run 变为 `REJECTED`，Trace 追加 `approval_denied`，Audit 仅有 review 且零 execution；浏览器 warning/error 日志为空。

**已知限制**

- 当前审批 API 没有真实用户认证和 RBAC，只适用于本地作品集演示。
- 批准与 resume 是两个请求；客户端在中间失败时无法重新取得原始 token。v0.8 将结合 Worker Checkpoint 和可恢复控制面处理。
- Run 恢复依赖确定性重放前置 control steps，尚不能跨进程从精确指令指针恢复。
- 审批审计随 Run 持久化，但尚未输出到独立不可变审计存储或 OpenTelemetry。

**下一步**

- 实现 v0.8 Redis Streams Worker、SSE 和持久化 Checkpoint，把长任务从同步请求生命周期中解耦。

### v0.8a-async-contracts — 2026-09-08

**完成内容**

- 定义 `AgentJob`、`JobEvent`、`AgentCheckpoint` 版本化契约，明确 9 个运行状态和终态。
- 新增 SQLite `JobRepository`，用 `BEGIN IMMEDIATE`、租约 owner 和条件更新实现创建、认领、取消、重试、终态提交、Event cursor、Checkpoint 与死信持久化。
- 幂等键只保存 SHA-256；同键同请求返回原 Job，同键异请求 fail closed。
- 新增真实 `redis.asyncio` Streams Adapter，覆盖 Consumer Group、`XREADGROUP`、`XACK` 与 `XAUTOCLAIM`；Stream 只携带不透明 Job ID。
- 新增异步运行时边界和威胁模型文档 `docs/ASYNC_RUNTIME.md`。

**关键优化与取舍**

- SQLite 是业务状态真相源，Redis 只做 at-least-once 投递；避免依赖 Stream retention 还原业务状态，也便于 SSE 精确续传。
- Worker 只有在持久化状态成功后才 ACK；崩溃消息留在 PEL，恢复 Worker 必须同时通过 Redis claim 和 SQLite 过期租约校验。
- 取消先写入持久层；若它发生在 success commit 之前，终态 CAS 强制落为 cancelled，固定竞态语义。
- 测试替身仅用于故障注入，生产 Adapter 已直接使用 `redis.asyncio`，不把内存队列包装成 Redis 能力。

**验证证据**

- `tests/test_job_storage.py` 与 `tests/test_job_broker.py`：9 个测试通过，覆盖幂等冲突/脱敏、双 Worker 竞争、取消竞态、单调 cursor、队列取消、Consumer Group、ACK、PEL reclaim 和初始化幂等。
- `make verify`：Ruff check/format 通过，Pytest `80 passed`；Golden Set v5 `16/16`、score `100.0`。

**已知限制**

- 当前只完成持久化契约和 Redis 边界；Worker 调度、逐步骤恢复、HTTP/SSE 和 UI 尚未接入，因此 v0.8 仍为进行中。
- 本机没有 Redis Server/Docker；当前 Redis 协议测试使用 fakeredis，v0.8 完成前需补可执行的 Redis 容器配置和真实服务 smoke test 说明。

**下一步**

- 实现 Worker 与 Agent 逐 Plan step Checkpoint 协议，然后接取消、超时、有限重试和死信。

**Git checkpoint**：`4681e60`

### v0.8b-worker-checkpoint — 2026-09-08

**完成内容**

- `AgentExecutionSession` 在每个已验证 Plan step 前检查软取消，对异步步骤施加硬超时，并在完成后用显式 JSON codec 保存输出、Plan cursor、Event 和 Tool Audit。
- `PlanRuntime.restore` 只接受当前版本 Plan 的连续 completed-step 前缀；问题指纹、Plan ID 或未知输出类型不匹配时 fail closed，禁止 pickle/代码反序列化。
- `AgentWorker` 实现 Consumer Group 读取、SQLite 租约认领、完成后 ACK、PEL reclaim、可分类有限重试、非重试失败、超时、取消与 dead-letter。
- Worker 崩溃不 ACK；恢复 Worker 同时通过 Redis `XAUTOCLAIM` 与 SQLite 过期租约后，从 Checkpoint 下一步继续，已完成行情 Tool 不会重放。
- 修复 active lease reclaim 风险：Redis 所有权被提前转移但 SQLite 租约仍有效时不 ACK，防止恢复消息丢失。
- FastAPI 新增 `POST/GET/DELETE /api/jobs`、事件查询和 SSE；保留同步 `/api/runs` 兼容入口。SSE 使用单调 Event ID、`Last-Event-ID`、heartbeat 与终态关闭。
- 新增 `aurumlab-worker` 进程入口与 Redis/租约/超时/SSE 配置。
- 异步 HITL 在 HTTP 控制面原子消费原始 token，只把公开 consumed grant 绑定到 Job；Worker 恢复时再次核对 Run/Plan/Step/Tool/参数/effect/risk，原始 token 不进入 Job、Redis、Checkpoint、Run 或 Audit。

**关键优化与取舍**

- Checkpoint codec 按步骤显式重建 Pydantic/dataclass 类型，牺牲少量样板代码换取可审计、可版本化和无任意反序列化执行风险。
- 审批等待不消耗错误重试预算；批准后的重复投递可安全重试，但只有持久化的 consumed grant 能通过 Gateway。
- Redis reclaim 与 SQLite lease 是双重所有权条件；任何 stale Worker 都不能覆盖已取消或已被新 Worker 接管的终态。
- 当前 SSE 从 SQLite 轮询，保证断线续传与业务状态一致；高并发 fan-out 优化留到有真实负载证据后。

**验证证据**

- `make verify`：Ruff check/format 通过，Pytest `96 passed`；Golden Set v5 `16/16`、score `100.0`。
- 新增测试覆盖逐步 Checkpoint JSON round-trip、已完成 Tool 不重放、软取消、阶段超时、真实 Worker 完成、崩溃/PEL reclaim、有限重试、死信、非重试错误、active lease 不误 ACK、异步审批恢复和 token 脱敏。
- HTTP/SSE 测试覆盖 202 立即返回、幂等重复/冲突、Job 查询与取消、终态流、`Last-Event-ID` 续传、非法 ID/cursor 和审批后重新入队。

**已知限制**

- UI 仍默认调用同步 `/api/runs`，尚未展示 Job/SSE/取消；v0.8c 将补异步演示模式。
- 本机没有 Redis Server 或 Docker，因此本轮使用真实 redis-py API + fakeredis 故障替身；需补 Redis Compose 配置，并在可用环境执行真实 Redis smoke test。
- SSE 当前是单节点 SQLite polling；没有多租户鉴权，仍只定位为本地求职演示。

**下一步**

- 完成 v0.8c UI、Redis Compose、运行文档和真实服务 smoke 路径；验证后再把 v0.8 标记为已验证。

**Git checkpoint**：`d35b4aa`

### v0.8c-async-demo — 2026-09-08

**完成内容**

- Web UI 增加 Sync / Async 执行模式；异步模式通过 SSE 展示 Job 状态、单调事件、逐步骤 Checkpoint、软取消和 HITL 审批恢复，保留无 Redis 的同步演示入口。
- SSE 同时支持标准 `Last-Event-ID` 和显式 `after` cursor。修复审批重新入队时从 0 重放旧事件的前端竞态，并为静态脚本增加版本参数避免演示环境继续使用旧缓存。
- 新增固定版本的 `compose.redis.yaml`、`make redis-up/down`、`make worker` 与真实 Redis opt-in 测试；Redis 仅绑定 loopback 并启用 AOF。
- Worker CLI 在 Redis 短暂不可达时采用有界退避重连，不因一次连接失败退出；运行时显式配置 RESP 2/3 协议版本。
- README、异步运行时文档、架构文档和简历证据同步升级到 v0.8.0。

**关键优化与取舍**

- Redis 消息仍只包含 opaque Job ID；问题、结果、Tool 参数和审批 token 留在持久化控制面，减少队列泄露面。
- SSE 以 SQLite 单调 Event ID 为真相源，浏览器只追加大于当前 cursor 的事件；审批前后的两段连接可无重复拼成完整 `#1 → #11` 轨迹。
- Redis Compose 是本地开发依赖，不包装成生产级 HA；本机无 Docker，因此另外编译运行官方 Redis 8.2.9 完成真实协议验证，并明确记录 Compose 未在本机执行。

**验证证据**

- `make verify`：Ruff check/format 通过，Pytest `96 passed, 1 skipped`；Golden Set v5 `16/16`、score `100.0`。
- `AURUMLAB_REDIS_TEST_URL=redis://127.0.0.1:6392/0 .venv/bin/pytest -q -m redis_integration`：官方 Redis 8.2.9 实例上 `1 passed`，覆盖 ping、Consumer Group、XADD/XREADGROUP、XPENDING、XAUTOCLAIM 与 XACK。
- 浏览器真实 API + Worker + Redis：行情异步 Job 依次产生 7 个事件并完成；队列中任务可软取消；外部研究在事件 5 暂停审批，批准后从 Checkpoint 恢复并按事件 6–11 完成，事件无重复。

**已知限制**

- SSE 仍由单节点 API 轮询 SQLite；生产化需要共享事件 fan-out、连接限额、认证授权和租户隔离。
- 软取消只在确定性步骤边界生效，不中断正在执行中的第三方调用；Tool 自身依赖硬超时收敛。
- Compose 配置已静态检查且真实 Redis 协议已验证，但因本机没有 Docker，尚无本机 `docker compose up` 证据。

**下一步**

- 实现 v0.9 OpenTelemetry Trace/Metrics，把 HTTP、Job、Worker、Plan、Tool、Store 与恢复链串成可导出的端到端观测证据。

**Git checkpoint**：`4f5206f`

### v0.9-observability — 2026-09-08

**完成内容**

- 新增独立 `Telemetry` Runtime，使用 OpenTelemetry Python API/SDK 手工插桩 HTTP Server、Agent Run、Router、Planner、Plan step、Tool Governance/Execution、Artifact/Run Store、Job producer/consumer 与 Checkpoint。
- 异步提交在 `aurumlab.job.submit` Producer Span 内注入 W3C `traceparent/tracestate`，只持久化到 SQLite 控制面；Worker 提取后创建 `aurumlab.job.process` Consumer Span，Redis Stream 继续只携带 opaque Job ID。
- 建立 HTTP/Agent/Plan/Tool/Job/Checkpoint Counter 与 Histogram；Metric label 只使用 FastAPI 路由模板、注册 Tool、Manifest step 和状态枚举，随机 404 路径统一归为 `{unmatched}`。
- 提供 `memory / console / otlp / none` exporter；本地 Span ring buffer 最多 500 条，`GET /api/observability` 最多返回 200 条脱敏证据，HTTP 响应通过 `X-Trace-Id` 关联。
- Web 增加 OpenTelemetry Runtime 与当前 Trace Span 面板；新增固定版本 Jaeger 2.20.0、OpenTelemetry Collector Contrib 0.160.0 和 Prometheus scrape endpoint Compose 配置。
- 新增 `docs/OBSERVABILITY.md`，明确 Span 拓扑、指标名、异步传播、隐私/高基数边界、OTLP 与本地运行方式。

**关键优化与取舍**

- 不使用自动全量采集替代业务插桩：关键 Span 与 Metric 直接对应 Agent 决策和恢复语义，面试时可以解释每一层的父子关系。
- 观测数据禁止 Prompt、Tool 参数/结果、URL、SQL、异常消息、API Key、审批 token 与 baggage；异常仅记录受控 `error.type`。
- `memory` exporter 是有硬上限的本地证据面；`otlp` 会同时保留有界本地快照并导出，方便 API/Worker 多进程接入统一 Collector。
- Trace Context 存在 SQLite 而非 Redis，在维持跨进程因果关系的同时保持队列数据面最小化；Trace ID 仅用于定位，不作为授权依据。
- Jaeger 只负责 Trace，Collector 将 Metrics 暴露为 Prometheus scrape endpoint，避免错误地把 Metrics 直接发送给只接收 Trace 的 Jaeger API。

**验证证据**

- `make verify`：Ruff check/format 通过，Pytest `101 passed, 1 skipped`；Golden Set v5 `16/16`、score `100.0`。
- `.venv/bin/python -m pip check`：`No broken requirements found`。
- OTLP 定向测试使用真实 `OTLPSpanExporter` / `OTLPMetricExporter` 向本地 HTTP receiver 发送 protobuf，`/v1/traces` 与 `/v1/metrics` 均收到非空 `application/x-protobuf`，再次独立执行 `1 passed`。
- 端到端测试证明 HTTP→Job Producer→Job Consumer→Agent→Checkpoint 共享 Trace ID，Consumer 的 parent span 为 Producer；同时断言 Redis entry 只有 `job_id`，公开 Job 不暴露 `traceparent`。
- 隐私/高基数测试证明 Prompt sentinel、baggage、token 和随机 404 path 不进入 snapshot；本地 ring buffer 与 Metric 维度长度超限会被约束。
- 浏览器真实 API：页面显示 `API v0.9.0`、`memory · aurumlab`，同步行情 Run 展示同一 Trace 下 10 个 HTTP/Agent/Route/Plan/Tool/Store Span；浏览器 warning/error 日志为空。

**已知限制**

- 本机没有 Docker；Collector 90MB 官方二进制下载速度约 30KB/s，预计近一小时，已停止低价值等待。因此 Compose 结构与版本由测试验证，真实 OTLP protobuf 已验证，但没有本机 Jaeger UI 启动证据。
- 默认本地 evidence 只展示当前进程 Span；API/Worker 跨进程汇总需要两边都设置 `OTEL_EXPORTER=otlp` 并连接 Collector。
- 当前没有生产告警规则、SLO、Tail Sampling 和长期 Trace/Metric 存储；这些属于部署运维扩展，不冒充已完成能力。

**下一步**

- 进入 v1.0-resume：扩充 100+ Golden Set/对抗案例并接入 CI，补完整 Docker、非金融 Skill、演示脚本和最终简历指标报告。

**Git checkpoint**：`5c4d0af`

### v1.0a-eval-ci — 2026-09-08

**完成内容**

- 将默认 Golden Set 升级为 `evals/golden.v6.jsonl`，共 108 条真实 Agent 执行案例：44 条策略、24 条行情、12 条外部研究、28 条其他意图。
- 其中显式包含 16 条 Prompt Injection/破坏/Secret 泄露对抗拒绝、12 条人工审批恢复、8 条 Artifact exact-hit 复用，以及 34 条日线和 34 条小时线规格。
- 新增数据集级 Coverage Contract：硬性检查 100—200 条、唯一 ID/问题、四类路由配额、日线/小时线配额、审批/缓存/对抗/边界/地域输入配额和标签一致性，阻止小样本或同义句灌水。
- `EvalReport` 新增机器可读 `coverage`，CLI、API 和持久化报告可直接展示分布证据。
- 首轮 108 条评测暴露 14 条失败，固化回归测试后修复了“10日与30日均线”共享单位参数、`1h/hourly` 后接中文字符、繁体研究词和“日K数据”路由等真实鲁棒性问题；没有删除难例。
- 新增 GitHub Actions 质量门禁：Python 3.12 + Redis 8.2 Service 运行 `make verify`，含 Ruff、全量 Pytest、真实 Redis Consumer Group 集成测试和 108 条 Eval；权限为 `contents: read`，官方 Action 用完整 commit SHA 锁定。
- 新增 `docs/EVALS.md`，记录数据集分布、双层门禁、CI 安全设置和真实性边界。

**关键优化与取舍**

- 保留确定性 Rubric 作为阻断性 CI Gate，不在无密钥 CI 中引入易漂移的 LLM-as-Judge。
- v6 允许省略标准 `required_stages`，Loader 根据 Artifact/审批/缓存场景填充受信任的标准轨迹，降低 JSONL 重复噪声；非标准工作流仍可显式写入轨迹。
- CI 不配置商业 LLM/Search 密钥，用可重复的规则降级和 Disabled Provider 验证 Runtime 契约，不将外部服务波动混入合并门禁。

**验证证据**

- 失败基线：首轮 v6 Eval `94/108`、score `94.83`，确认难例能真实发现问题。
- 最终命令：`make verify && .venv/bin/python -m pip check && git diff --check && test "$(wc -l < evals/golden.v6.jsonl | tr -d ' ')" = "108"`。
- 最终结果：Ruff 通过，Pytest `109 passed, 1 skipped`，v6 Eval `108/108`、score `100.00`，依赖无冲突，diff 无空白问题，JSONL 物理行数 108。

**已知限制**

- 本机没有 `redis-server` 或 Docker，因此本地 Pytest 的唯一 skip 是真实 Redis 集成测试；CI 已提供 Redis Service 使其可执行，但当前本地仓库未配置远程，不声称已有远程绿色运行记录。
- v6 是可审计基线，不等于穷尽所有自然语言表达；新回归需追加到新版数据集，不原地改写 v6。

**Git checkpoint**：`3311f84`

### v1.0b-non-financial-skill — 2026-09-09

**完成内容**

- 新增第五类 `incident_review` 路由与 `incident-review@1.0.0`，把自然语言错误率、P95 延迟、持续时间和影响请求数编译为有界 `IncidentSpec`。
- 使用服务端确定性规则生成 `SEV-1`—`SEV-4` 风险等级、证据化 findings 与 mitigate/observe/prevent 行动项；`root_cause_status` 固定为 `unverified`，不执行变更、通知、Shell、SQL 或用户代码。
- 将动态发现的 `mcp__runtime__profile_text` 真正加入 Skill Allowlist 和五次 Tool Budget；调用经 Schema/大小/Injection 门禁与 `external/medium` 人工审批，再由四个本地只读 Tool 生成 `IncidentReviewResult`。
- 打通同步 Run、异步 Job/Checkpoint 和 Agent MCP Server 的事故复盘路径；MCP Server lifespan 预启动下游 Client，直接 Agent/API 测试路径则在单次 Run 内安全拥有并释放惰性会话。
- Artifact Memory 支持事故规格指纹、exact hit 和五次 Tool 节省；Checkpoint 显式编解码 MCP 结构结果及所有 Incident 类型。
- 新增高置信凭证赋值/Bearer 检测：同步 Run 在零 Tool 调用时拒绝并脱敏持久化文本，异步 Job 在任何 SQLite/Redis 持久化前返回 422。
- Golden Set 升级为 v7 共 120 条：新增 12 条非金融事故复盘，每条都真实执行 `review → approve → consume → MCP execute`；总审批案例增至 24 条。
- Web、MCP Resource、README、架构、Eval、MCP Client 与求职表述文档同步展示第五类 Artifact 和安全边界。

**关键优化与取舍**

- 选择服务事故复盘而不是继续增加金融策略，直接证明 Router、Skill、Planner、MCP、Governance、Memory、Checkpoint、Trace 与 Eval 是可迁移的 Agent Runtime。
- MCP Tool 只做有界结构统计，业务分析由确定性本地 Tool 完成；既提供真实跨协议调用证据，也避免远端内容控制根因或处置建议。
- 首次全量测试暴露 AnyIO 生命周期缺陷：在 HTTP/MCP 请求内懒启动并跨请求保留 in-process MCP Client 会破坏取消域栈。修复为 Host lifespan 所有或单 Run 所有，并新增 API Eval 与 `MCP → Agent → MCP` 回归覆盖。
- 事故 Artifact 默认无 TTL，但只有规格和 Schema 版本完全一致才复用；缺少指标字段会降低证据完整度并返回 warning，不填充虚构值。
- Secret 检测仅覆盖高置信赋值与 Bearer 形态，降低普通事故文本误杀；当前不是通用 DLP，文档明确禁止输入真实日志或 PII。

**验证证据**

- 失败基线：首次 MCP Server 集成测试因嵌套 AnyIO cancel scope 失败；首次全量验证另发现健康页旧 Skill 数、审批案例旧计数和无 lifespan TestClient 下的同类会话泄漏，均保留为回归测试后修复。
- 最终命令：`make verify && .venv/bin/python -m pip check && git diff --check && test "$(wc -l < evals/golden.v7.jsonl | tr -d ' ')" = "120"`。
- 最终结果：Ruff check/format 通过，Pytest `117 passed, 1 skipped`，v7 Eval `120/120`、score `100.00`；覆盖 5 类意图、16 条对抗、24 条审批、8 条缓存；依赖无冲突，diff 无空白问题。
- 唯一 skip 仍是本机无 Redis/Docker 时的真实 Redis 集成测试；v0.8c 已用官方 Redis 8.2.9 独立验证协议，CI 配置也提供 Redis Service，但不声称本轮本机 Docker 已运行。

**已知限制**

- Incident 编译器当前只接受中文模板附近的四类量化指标和 ASCII 服务名；不解析日志、部署记录或监控链接，也不执行自动根因分析。
- 当前 MCP Provider 为部署方固定的进程内/stdio 服务；尚无远程 HTTP、OAuth Scope、RBAC 或多租户隔离。
- GitHub Actions 已配置但仓库当前无远程，尚无可引用的云端绿色 Run；本地同一门禁已通过。

**下一步**

- 进入 v1.0c：完成统一 Docker Compose 与 healthcheck、一键面试演示脚本、机器可读指标报告、最终四条简历文案和逐项真实性审计。

**Git checkpoint**：功能提交 `31cb78a`，生命周期回归提交 `ba6e33a`

### v1.0c-demo-package — 2026-09-09

**完成内容**

- 版本提升到 `1.0.0`；新增精确版本生产依赖锁、非 root 应用镜像与统一 Compose，编排 API、Worker、Redis 8.2.9、OpenTelemetry Collector 和 Jaeger。
- API/Worker 根文件系统只读，移除 Linux capabilities，启用 `no-new-privileges`，公开端口仅绑定 loopback；镜像、API 与 Worker 均有 healthcheck。
- 引入显式 `APP_PROJECT_ROOT`，解决非 editable wheel 安装后 Skill/Eval 资源定位问题；在全新 Python 3.12 虚拟环境完成 lockfile、wheel、静态资源和 Agent 运行 smoke。
- 新增 `demo_resume.py`，一键生成脱敏 JSON 证据，覆盖零 Tool 拒绝、MCP 审批/恢复、Incident Artifact、exact hit、Trace/Metrics 和完整 Eval。
- GitHub Actions 新增容器 smoke Job：校验 Compose、构建镜像、等待全栈健康并在容器内执行代表性 Agent 证据脚本。
- 完成最终四条简历成稿、指标表、部署文档、面试展开问题和真实性边界；浏览器已验收 Incident 审批恢复与 exact-hit 展示。

**关键优化与取舍**

- 求职证据包以“可重放的代表性链路 + 机器可读指标”取代不可核验的线上收益；所有简历数字均可由 `make release-check` 复现。
- 容器使用精确版本 lock，但未生成 hash lock/SBOM；这是之后供应链增强项，不写成 v1.0 已完成能力。
- SQLite 仍为单机业务状态真相源，Docker Compose 用于本地/面试演示；不宣称高可用、多租户或云端生产部署。
- 本机缺少 Docker CLI，因此用静态容器合约测试、全新 wheel 环境 smoke 与 CI 容器 Job 弥补本地证据，并在所有文档中明确不冒充已有云端绿色运行。

**验证证据**

- 最终命令：`make release-check && git diff --check`。
- Ruff check/format 通过；Pytest `124 passed, 1 skipped`；v7 Eval `120/120`、score `100.00`，覆盖 5 类意图、16 条对抗、24 条审批和 8 条缓存。
- 代表性演示 8 项断言全部通过：5 个 Skill、Incident 链 5 次 Tool（含 1 次动态 MCP）、exact hit 节省 5 次 Tool，证据快照含 41 spans / 56 metric series。
- `pip check` 无依赖冲突；`pip-audit -r requirements.lock` 未发现已知漏洞；新虚拟环境 wheel smoke 和浏览器真实 API/UI 验收通过。
- 唯一 skip 是需本地 Redis 的 opt-in 集成测试；v0.8c 已进行真实 Redis 8.2.9 协议 smoke，CI 也配置同版本 Service。

**已知限制**

- 开发机没有 Docker CLI，完整 Compose 栈尚无本机运行记录；仓库尚无远程，也没有可引用的 GitHub Actions 绿色 Run。
- LLM Router 离线演示使用规则 fallback；HITL 是本地单用户控制面；没有远程 MCP OAuth/RBAC、Redis HA 或跨节点 SSE fan-out。
- 项目无实盘交易，不自动执行事故处置；黄金与事故只是 Agent Runtime 的两个可验证领域适配器。

**后续可选增强**

- 推送远程后记录实际 CI 容器绿色证据，并可补一段 3—5 分钟面试演示录屏；两者都不是 v1.0 简历可用版的完成前提。

**Git checkpoint**：`a457f64`

## 当前工作区

- 目标分支：`codex/resume-ready-agent-runtime`
- 当前版本：`1.0.0`
- 当前阶段：`v1.0c-demo-package` 已验证，简历可用版完成
- 入口：`app/agent/orchestrator.py`
- 数据契约：`app/models.py`
- Skill Manifest：`skills/*/skill.json`
- 验证命令：`make release-check`

## 下一步：可选增强

1. 推送 Git 远程，确认 `verify` 与 `container-smoke` 两个 CI Job 实际绿色。
2. 录制 3—5 分钟演示：攻击拒绝 → Incident MCP 审批恢复 → exact hit → Trace/Metric 证据。
3. 若要进一步工程化，再补 hash lock/SBOM、企业身份/RBAC 和远程 MCP Transport；不影响当前简历表述。

## 中断恢复步骤

```bash
cd "/Users/9958files/Documents/ChatGPT/行情回测agent"
git status --short --branch
git log --oneline -5
sed -n '1,260p' docs/PROGRESS.md
make verify
```

若测试不通过，先修复当前版本，不开始后续版本。若测试通过，从“下一步”第一项未完成工作继续。

## 小版本记录模板

```markdown
### vX.Y-name — YYYY-MM-DD

**完成内容**
- ...

**关键优化与取舍**
- ...

**验证证据**
- 命令：...
- 结果：...

**已知限制**
- ...

**下一步**
- ...
```
