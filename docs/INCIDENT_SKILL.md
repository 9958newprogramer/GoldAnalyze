# 非金融 Incident Review Skill

## 为什么加入这条工作流

`incident-review@1.0.0` 用服务事故复盘证明 AurumLab 的核心能力不依赖黄金或金融领域。它与回测 Skill 复用 Router、Planner、Governance、MCP、Artifact Memory、Checkpoint、SSE、OpenTelemetry 和 Eval Runtime，但拥有独立的数据契约、Tool Allowlist、预算和输出语义。

这不是一个 SRE 自动处置系统。它只把用户给出的可量化事实整理为结构化复盘，不读取日志、不推断根因、不修改服务、不发送通知。

## 受约束执行链

```text
自然语言事故描述
  → Router: incident_review
  → incident-review@1.0.0
  → Bounded Plan（2 control steps + 5 Tool steps）
  → IncidentSpec 编译
  → Artifact exact-hit 检查
  → mcp__runtime__profile_text（external/medium，必须人工审批）
  → validate_incident_spec（read/low）
  → assess_incident_impact（read/low）
  → build_incident_action_plan（read/low）
  → summarize_incident_review（read/low）
  → IncidentReviewResult + Event + Tool Audit + Trace
```

MCP Tool 通过运行时 `tools/list` 动态发现，Input/Output Schema 经 Draft 2020-12 校验并生成稳定指纹。它只返回字符数、词数、行数和是否含代码围栏；Gateway 仍执行 Skill Allowlist、五次调用预算、人工审批和 authorization/execution Audit。

## 输入与输出契约

当前编译器识别以下事实，所有字段均由 Pydantic 约束：

| 字段 | 约束 | 用途 |
|---|---:|---|
| `service` | 安全 ASCII 服务标识；缺失时为 `unknown-service` | Artifact 分区与说明 |
| `error_rate_pct` | 0—100 | 风险分级 |
| `p95_latency_ms` | 非负、有界 | 风险分级 |
| `duration_minutes` | 非负、有界 | 风险分级 |
| `affected_requests` | 非负整数、有界 | 风险分级 |

至少需要一个可量化信号，否则工作流 fail closed。风险分数由服务端固定规则计算，输出 `SEV-1` 至 `SEV-4`、证据化 findings 和三类行动项：止损、观测、预防。`root_cause_status` 始终为 `unverified`，除非未来引入有来源的日志/变更证据链。

Artifact 指纹由规范化事故规格与 `incident-review-v1` 数据版本生成。只有完全一致的规格才能自动 exact-hit；复用时跳过全部五个 Tool，不再次请求 MCP 审批，并在 Provenance 中记录来源 Run 和节省调用数。

## 安全与隐私边界

- HTTP/MCP 请求最多 1,000 字符；MCP Provider 自身还有 2,000 字符上限与 32 KiB 参数上限。
- `api_key=...`、`token=...`、`password=...`、`secret=...`、`密码=...`、`密钥=...` 以及 Bearer Token 会被识别。同步 Run 在任何 Tool 前拒绝，并将持久化问题替换为 `[REDACTED]`；异步 Job 在写入 SQLite/Redis 前返回 422。
- Tool 描述、Schema 和结构化结果都按不可信输入处理；外部 `$ref`、未知字段、过大载荷、Schema 漂移和 Prompt Injection 信号均 fail closed。
- 审批凭证绑定 Run、Plan、Step、Tool、参数摘要、effect 和 risk；数据库只保存 hash，原始 token 不进入 Run、Audit、Checkpoint 或 Redis。
- Incident Tool 不执行 Shell、SQL、Python、文件写入、服务变更或通知。唯一外部 effect 是已审批的 MCP 结构统计调用。
- Trace/Metrics 不记录原始 Prompt、Tool 参数/结果、凭证或自由文本错误。

当前项目是本地单用户作品集，不具备生产 RBAC、租户隔离、日志脱敏管道或 PII 数据治理。演示时只应输入合成事故指标，不应粘贴真实日志、用户数据或密钥。

## 可复现验证

```bash
.venv/bin/pytest -q tests/test_incident.py tests/test_mcp_server.py
.venv/bin/aurumlab-eval
```

测试覆盖类型化编译、证据不足拒绝、MCP 动态 Tool 来源、审批恢复、五次调用预算、Artifact exact-hit、Checkpoint 断点恢复、Secret 拒绝与脱敏，以及 `MCP Server → Agent → MCP Provider` 的嵌套生命周期。
