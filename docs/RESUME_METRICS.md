# AurumLab v1.0 求职证据与简历成稿

## 简历直接使用版

**AurumLab｜可治理多 Skill Agent Runtime（独立项目）**

`Python / FastAPI / Pydantic / MCP / Redis Streams / SSE / SQLite / OpenTelemetry / Docker`

项目定位：面向工具型 Agent 的可控执行与工程化平台，以黄金研究和非金融服务事故复盘作为两类可验证业务载体，重点展示路由、规划、工具治理、异步恢复、可观测和评测能力，不涉及模型训练或实盘交易。

- 设计 LLM-first Router、Bounded Planner 与确定性 Executor 混合架构，将请求路由为 5 类强类型意图，并由 5 个版本化 Skill 编译受约束 `ExecutionPlan`；通过 Pydantic、PlanValidator 与 PlanRuntime 逐层校验步骤依赖、Tool 绑定和调用预算，模型超时或非法输出时可观测降级，避免 LLM 直接扩张执行权限。
- 实现 MCP Server/Client 与动态 Tool Catalog，支持 in-process/stdio `tools/list`、命名空间、Draft 2020-12 Schema 校验、指纹 Pin 和漂移隔离；统一 Governance Gateway 执行 Allowlist、预算、Prompt Injection 拒绝、effect/risk 分级、一次性人工审批与完整 Audit，非金融事故复盘 Skill 已真实跑通 `MCP → review → resume → Artifact` 链路。
- 基于 Redis Streams Consumer Group 与 SQLite source-of-truth 构建异步 Agent Runtime，消息仅携带 Job ID，支持 SHA-256 幂等、lease/CAS 认领、PEL reclaim、SSE 断线续传、软取消、步骤超时、1—3 次有限重试/dead-letter 和版本化 Checkpoint；使用 OpenTelemetry + W3C Trace Context 串联 HTTP、Job、Worker、Plan、Tool 与 Store。
- 构建 Artifact Memory 与 Agent Eval 体系，以 Intent、类型化 Spec、数据/Schema 版本和 TTL 生成任务指纹，exact hit 可跳过事故 Skill 的 5 次 Tool 调用；建立 120 条 Golden Set（5 类意图、16 条安全对抗、24 条审批、8 条缓存）与五维 Rubric，本地达到 `120/120、100.00`，并接入 GitHub Actions Redis 门禁和全栈 Docker smoke 配置。

## 为什么这样写

牛客的 Agent 简历经验反复强调三点：不要只堆技术名词，要说明“模型如何决策调用工具、工作流如何受控”；突出个人主导的难点与行动；量化结果必须能被追问和复现，而不是虚构线上收益。参考：[Agent 项目经历该怎么写](https://www.nowcoder.com/discuss/916809937089462272)、[面试官视角拆 Agent 简历](https://www.nowcoder.com/discuss/898495069127200768)、[后端大模型应用开发突围指南](https://www.nowcoder.com/discuss/861015986122600448)。

因此四条分别只承担一个主题：决策/规划、MCP/治理、异步/观测、Memory/Eval；量化数据来自本项目的自动化报告，不写用户数、QPS、收益率或“准确率提升”等不存在的生产指标。

## 可复现指标

| 指标 | v1.0 证据 | 复现入口 |
|---|---:|---|
| 顶层意图 / Skill | 5 / 5 | `GET /api/health`、`GET /api/skills` |
| Golden Set | 120 条唯一问题 | `evals/golden.v7.jsonl` |
| Eval 结果 | 120/120，100.00 | `make eval` 或 `make demo-resume` |
| 对抗 / 审批 / 缓存案例 | 16 / 24 / 8 | Eval `coverage` |
| 非金融 Incident Tool 链 | 5 次，其中 1 次动态 MCP | `tests/test_incident.py`、证据 JSON |
| Incident exact-hit | 0 次实际 Tool，节省 5 次 | `make demo-resume-fast` |
| OTel 本次代表性演示 | 41 spans / 56 metric series | `docs/evidence/resume-evidence.v1.json` |
| 自动化测试 | 124 passed / 1 opt-in Redis skipped | `make verify` |

机器可读快照位于 [`docs/evidence/resume-evidence.v1.json`](evidence/resume-evidence.v1.json)。`generated_at`、Span 数和指标序列数是一次本地执行样本，不应被解释为性能基准；Golden Set 分布和通过结果才是稳定质量证据。

## 面试时可展开的四条技术主线

1. 为什么 LLM 只做语义分类，Planner 却由服务端 Skill Manifest 确定性编译；如何证明模型不能发明 Tool 或扩大预算。
2. MCP discovery 为什么不等于 authorization；Schema Pin、`additionalProperties=false`、大小/超时限制和结果 Injection 拒绝分别阻止什么。
3. Redis at-least-once 与 SQLite source-of-truth 如何组合；为什么 ACK 在业务终态之后，Checkpoint 如何避免重放已完成 Tool。
4. Artifact exact hit 与 semantic candidate 为什么分开；120 条数据集如何通过唯一性、路由配额和对抗/审批/缓存标签防止“同义句灌水”。

## 真实性边界

- LLM Router 已实现并可接 OpenAI-compatible 模型，但离线演示和 CI 默认使用规则 fallback，不宣称训练或微调模型。
- Docker/CI 配置包含真实全栈 smoke 步骤；当前开发机没有 Docker CLI、仓库没有远程运行记录，因此不能声称已有云端绿色构建。
- HITL 是本地单用户控制面，不是企业级 RBAC；远程 MCP HTTP/OAuth、多租户、Redis HA、跨节点 SSE fan-out 不在 v1.0 范围。
- 项目没有实盘交易，也不会自动执行事故处置。黄金和事故只是验证 Agent Runtime 的两个领域适配器。
