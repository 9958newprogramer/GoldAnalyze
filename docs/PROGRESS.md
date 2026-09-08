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
| v0.6-mcp-client | MCP Client 与动态 Tool 目录 | 已验证 | 动态发现、Schema 指纹、命名空间、超时、健康状态均有集成测试 |
| v0.7-approval | 风险分级与人工审批状态机 | 已验证 | allow/deny/review 完整闭环；审批不可伪造、过期或重复使用 |
| v0.8-async | Redis Streams Worker 与 SSE 长任务 | 未开始 | 幂等、取消、超时、有限重试、Checkpoint 恢复和断线重连测试 |
| v0.9-observability | OpenTelemetry 与运行指标 | 未开始 | HTTP→Agent→Tool→Store/Worker Trace 连通；关键指标可导出 |
| v1.0-resume | 招聘展示与质量证据包 | 未开始 | 100+ Eval、CI、Docker、非金融 Skill、演示脚本、简历指标报告 |

状态只能使用：`未开始`、`进行中`、`已验证`、`阻塞`。不能因为代码已写就标记“已验证”。

## 简历主张证据矩阵

| 简历主张 | 当前证据 | 缺口 |
|---|---|---|
| LLM Router + Bounded Planner + 版本化 Skill | 4 类类型化意图、4 个 Skill、LLM 失败规则降级；类型化计划、独立校验与运行时逐步授权 | 当前为确定性线性计划；尚无分支、并行和重规划 |
| MCP Server/Client + 动态发现 | Agent MCP Server；进程内/stdio Client；namespaced Catalog；Schema Pin、超时、健康与治理集成测试 | 尚无远程 HTTP/OAuth；默认远端 Tool 尚未进入业务 Skill |
| Tool Governance + 人工审批 | Tool effect/risk、most-restrictive allow/deny/review；hash-only 一次性凭证；Run/Plan/Step/Tool/参数绑定；Web/API/MCP 恢复；攻击、过期、重放与并发测试 | 当前仅为本地单用户控制面；尚无企业身份认证、RBAC 与多租户隔离 |
| Redis Worker + SSE + 恢复 | 无 | 整项待实现 |
| OpenTelemetry | 无 | 整项待实现 |
| Artifact Memory + Agent Eval | 两级复用、TTL/版本失效、16 条 v5 Golden Set，Eval 校验 Plan/Event 及审批审计链一致性 | 需扩充至 100+、接入 CI 并补对抗覆盖 |

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

## 当前工作区

- 目标分支：`codex/resume-ready-agent-runtime`
- 当前版本：`0.7.0`
- 当前阶段：准备实现 `v0.8-async`
- 入口：`app/agent/orchestrator.py`
- 数据契约：`app/models.py`
- Skill Manifest：`skills/*/skill.json`
- 验证命令：`make verify && .venv/bin/aurumlab-eval --json`

## 下一步：v0.8-async

1. 定义 `AgentJob`、`JobEvent`、`Checkpoint` 与明确的 queued/running/waiting/cancelling/completed/failed 状态机，先固化存储契约。
2. 使用 Redis Streams Consumer Group 投递和认领任务，HTTP 只创建 Job 并立即返回；同步 `/api/runs` 保留为兼容入口。
3. Worker 以幂等键和原子状态转换执行任务，支持软取消、每阶段超时、可分类的有限重试和死信记录。
4. 在每个确定性 Plan step 后保存 Checkpoint；进程重启或 pending message 回收时，从已完成步骤之后恢复而不是重放整个工作流。
5. 提供带单调 event ID 的 SSE，支持 `Last-Event-ID` 断线续传、心跳和终态自动关闭。
6. 覆盖重复提交、双 Worker 竞争、Worker 崩溃、取消竞态、超时、可重试/不可重试错误、Checkpoint 恢复与 SSE 重连测试。
7. 更新演示 UI、架构文档和求职表达，完成验证后提交独立 Git checkpoint。

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
