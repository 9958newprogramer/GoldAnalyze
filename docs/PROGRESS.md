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
| v0.7-approval | 风险分级与人工审批状态机 | 未开始 | allow/deny/review 完整闭环；审批不可伪造、过期或重复使用 |
| v0.8-async | Redis Streams Worker 与 SSE 长任务 | 未开始 | 幂等、取消、超时、有限重试、Checkpoint 恢复和断线重连测试 |
| v0.9-observability | OpenTelemetry 与运行指标 | 未开始 | HTTP→Agent→Tool→Store/Worker Trace 连通；关键指标可导出 |
| v1.0-resume | 招聘展示与质量证据包 | 未开始 | 100+ Eval、CI、Docker、非金融 Skill、演示脚本、简历指标报告 |

状态只能使用：`未开始`、`进行中`、`已验证`、`阻塞`。不能因为代码已写就标记“已验证”。

## 简历主张证据矩阵

| 简历主张 | 当前证据 | 缺口 |
|---|---|---|
| LLM Router + Bounded Planner + 版本化 Skill | 4 类类型化意图、4 个 Skill、LLM 失败规则降级；类型化计划、独立校验与运行时逐步授权 | 当前为确定性线性计划；尚无分支、并行和重规划 |
| MCP Server/Client + 动态发现 | Agent MCP Server；进程内/stdio Client；namespaced Catalog；Schema Pin、超时、健康与治理集成测试 | 尚无远程 HTTP/OAuth；默认远端 Tool 尚未进入业务 Skill |
| Tool Governance + 人工审批 | Per-Skill Allowlist、预算、预检拒绝、Audit | 尚无 `review` 状态与审批凭证 |
| Redis Worker + SSE + 恢复 | 无 | 整项待实现 |
| OpenTelemetry | 无 | 整项待实现 |
| Artifact Memory + Agent Eval | 两级复用、TTL/版本失效、16 条 v4 Golden Set，Eval 校验 Plan 与实际轨迹一致性 | 需扩充至 100+、接入 CI 并补对抗覆盖 |

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

## 当前工作区

- 目标分支：`codex/resume-ready-agent-runtime`
- 当前版本：`0.6.0`
- 当前阶段：准备实现 `v0.7-approval`
- 入口：`app/agent/orchestrator.py`
- 数据契约：`app/models.py`
- Skill Manifest：`skills/*/skill.json`
- 验证命令：`make verify && .venv/bin/aurumlab-eval --json`

## 下一步：v0.7-approval

1. 为 Tool 定义 `read / external / write / privileged` effect 与 `low / medium / high` 风险元数据，并在注册时 fail closed 校验。
2. 将 `ToolPolicy.authorize()` 升级为类型化 allow/deny/review 决策，保持预算只在真正进入执行时扣减。
3. 设计 `ApprovalRequest` 状态机：pending、approved、denied、expired、consumed；审批绑定 run、plan、step、tool、参数摘要和过期时间。
4. 使用高熵 nonce、服务端完整性校验及 SQLite 原子状态转换，拒绝伪造、过期、跨任务和重复使用的审批凭证。
5. 增加本地演示用审批 API 与 Plan paused/resumed 轨迹，不宣称当前单用户 UI 具备企业身份系统。
6. 覆盖 allow/deny/review、篡改、超时、重放、并发消费与审计测试，更新文档并提交独立 checkpoint。

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
