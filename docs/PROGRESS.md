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
| v0.4-baseline | 固化现有 Router、4 Skills、治理、MCP Server、Memory、Eval | 已验证，待 checkpoint | `make verify`；Golden Set 16/16 |
| v0.5-planner | 类型化 Bounded Planner 与计划轨迹 | 进行中 | 四类意图计划正确；越权/乱序/超预算计划被拒；API/MCP 返回 Plan |
| v0.6-mcp-client | MCP Client 与动态 Tool 目录 | 未开始 | 动态发现、Schema 指纹、命名空间、超时、健康状态均有集成测试 |
| v0.7-approval | 风险分级与人工审批状态机 | 未开始 | allow/deny/review 完整闭环；审批不可伪造、过期或重复使用 |
| v0.8-async | Redis Streams Worker 与 SSE 长任务 | 未开始 | 幂等、取消、超时、有限重试、Checkpoint 恢复和断线重连测试 |
| v0.9-observability | OpenTelemetry 与运行指标 | 未开始 | HTTP→Agent→Tool→Store/Worker Trace 连通；关键指标可导出 |
| v1.0-resume | 招聘展示与质量证据包 | 未开始 | 100+ Eval、CI、Docker、非金融 Skill、演示脚本、简历指标报告 |

状态只能使用：`未开始`、`进行中`、`已验证`、`阻塞`。不能因为代码已写就标记“已验证”。

## 简历主张证据矩阵

| 简历主张 | 当前证据 | 缺口 |
|---|---|---|
| LLM Router + 版本化 Skill | 4 类类型化意图、4 个 Skill、LLM 失败规则降级 | 尚无 Bounded Planner |
| MCP Server/Client + 动态发现 | 3 Tools、固定 Resources/Template 的 MCP Server 测试 | 尚无 MCP Client、动态发现与 Schema 兼容检查 |
| Tool Governance + 人工审批 | Per-Skill Allowlist、预算、预检拒绝、Audit | 尚无 `review` 状态与审批凭证 |
| Redis Worker + SSE + 恢复 | 无 | 整项待实现 |
| OpenTelemetry | 无 | 整项待实现 |
| Artifact Memory + Agent Eval | 两级复用、TTL/版本失效、16 条 v3 Golden Set | 需扩充至 100+、接入 CI 并补对抗覆盖 |

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

## 当前工作区

- 目标分支：`codex/resume-ready-agent-runtime`
- 当前版本：`0.4.0`
- 当前阶段：准备实现 `v0.5-planner`
- 入口：`app/agent/orchestrator.py`
- 数据契约：`app/models.py`
- Skill Manifest：`skills/*/skill.json`
- 验证命令：`make verify && .venv/bin/aurumlab-eval --json`

## 下一步：v0.5-planner

1. 新增 `ExecutionPlan`、`PlanStep`、依赖和预算约束模型。
2. 从已选 Skill 生成确定性基线 Plan；可选 LLM Planner 只能在 Skill 允许的步骤/Tool 中排序和选择。
3. 增加独立 Plan Validator，拒绝未知 Tool、重复 Step、依赖乱序、循环和超预算。
4. Orchestrator 在领域执行前生成并记录 Plan，`RunResponse` 返回计划与 Planner 来源。
5. 增加单元、Agent、API、MCP 和 Eval 回归测试。
6. 更新 README、ARCHITECTURE、RESUME 和本账本，运行完整验证后提交 checkpoint。

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
