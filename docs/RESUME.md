# 求职表达草稿

## 项目一句话

设计并实现一个多 Skill Agent 执行平台：将自然语言请求路由到策略回测、只读行情查询、外部研究或直接回答工作流，并通过 Tool Policy、MCP、审计轨迹和自动评测保障可控执行。

## v0.4 可写入简历的要点

- 从零实现 LLM-first Intent Router 与四个版本化 Skill，模型输出经 Pydantic 校验，Skill 映射由服务端约束，并支持超时、非法输出和未配置场景的规则降级。
- 设计 Per-Skill Tool Allowlist、调用预算、执行前 Prompt Injection 拒绝和授权/执行 Audit，使危险请求在零 Tool 调用时 fail closed。
- 以 Provider/Adapter 模式接入 Tavily 与只读 SQLite 行情库，支持无 API Key、无私人数据时确定性降级，保证项目可克隆运行。
- 建立 16 条多意图 Golden Set 和五维确定性 Rubric，将路由、工作流、安全、输出完整性与复用效率接入 CLI/API 回归门禁。
- 实现基于规范化 Intent、类型化 Spec 和行情数据版本的两级 Artifact Memory；等价任务零领域 Tool 调用复用，相似但不同任务仅提示候选并安全重算。
- 为行情与外部研究实现 TTL、数据版本失效、强制刷新和缓存旁路，返回来源 Run、相似度及节省的 Tool 调用/耗时。
- 将评测升级为 Golden Set v3 和五维 Rubric，直接验证 miss → exact hit 的执行模式、Provenance 与 Tool 调用下降。

测试数量、质量门禁分数和耗时应以最新 `make verify` 输出为准，不在简历里写无法复现的固定数字。

## 面试时主动强调

- 黄金仅用作有确定性输入输出的工具场景，工程重点是 Agent 的路由、能力边界、协议暴露、治理、可观测与评测。
- 与 AgentForge 的区别：AgentForge 证明 Agentic RAG；AurumLab 证明“识别意图—选择 Skill—受控调用 Tool—生成 Artifact—评测与复用”的执行系统。
- LLM 负责语义理解，代码负责安全预检、类型校验、Skill 映射和 Tool 权限；规则 Router 仅承担可观测的故障降级。

## 下一版本增量表述

v0.5 可增加：基于 Worker + SSE 的长任务执行，支持幂等、取消、超时、有限重试和断点恢复，并以 OpenTelemetry 串联 HTTP → Agent → Tool → Store。
