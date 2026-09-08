# OpenTelemetry 可观测性契约

## 目标与边界

v0.9 使用 OpenTelemetry Python API/SDK 手工插桩，把同步请求和异步 Job 的关键 Agent
决策串成同一条因果链。默认 `memory` exporter 只保留最近 500 个脱敏 Span，便于本地页面和
`GET /api/observability` 直接展示；`otlp` exporter 额外把 Trace/Metrics 发送到 Collector。

观测数据禁止包含：Prompt/问题正文、Tool 参数或结果、URL、SQL、API Key、审批 token、
异常消息和用户可控 baggage。Trace 可以记录内部 Run/Job ID、稳定 Plan ID、版本化 Skill、
受控 Tool 名称、决策枚举、状态和耗时。Metric 维度只能使用服务端枚举；HTTP 使用路由模板，
未知路径统一为 `{unmatched}`，避免 ID、随机路径或问题文本造成高基数。

## Trace 拓扑

同步请求：

```text
HTTP SERVER
└─ aurumlab.agent.run
   ├─ aurumlab.agent.route
   ├─ aurumlab.agent.plan
   ├─ aurumlab.plan.step
   │  └─ aurumlab.tool.call
   ├─ aurumlab.store.artifact.lookup/save
   └─ aurumlab.store.run.save
```

异步请求：

```text
POST /api/jobs (SERVER)
└─ aurumlab.job.submit (PRODUCER)
   ┄ traceparent 持久化到 SQLite；Redis Stream 仍只含 opaque job_id ┄
   └─ aurumlab.job.process (CONSUMER)
      └─ aurumlab.agent.run
         ├─ route / plan / plan.step / tool.call
         └─ aurumlab.store.checkpoint.save
```

`TraceContextTextMapPropagator` 只注入 W3C `traceparent` / `tracestate`。Worker 从可信 SQLite
控制面读取并提取父上下文；重试与 Checkpoint 恢复仍属于原始 Job Trace。HTTP 响应返回
`X-Trace-Id`，用于在本地证据 API 或 Jaeger 中定位，不把 Trace ID 当作授权凭证。

## 指标

| Instrument | 类型 | 低基数维度 |
|---|---|---|
| `aurumlab.http.server.requests` | Counter | method、route template、status class |
| `aurumlab.http.server.duration` | Histogram(ms) | method、route template、status class |
| `aurumlab.agent.runs` | Counter | intent、status、cache status |
| `aurumlab.agent.run.duration` | Histogram(ms) | intent、status、cache status |
| `aurumlab.agent.plan.steps` | Counter | manifest step、result、可选 cache status |
| `aurumlab.agent.plan.step.duration` | Histogram(ms) | manifest step、result |
| `aurumlab.agent.tool.decisions` | Counter | registered tool、effect、risk、decision |
| `aurumlab.agent.tool.calls` | Counter | registered tool、outcome |
| `aurumlab.agent.tool.duration` | Histogram(ms) | registered tool、outcome |
| `aurumlab.job.submissions` | Counter | created、status |
| `aurumlab.job.attempts` | Counter | bounded terminal/intermediate result |
| `aurumlab.job.checkpoint.restores` | Counter | result |

本地 evidence API 返回这些指标的有界聚合快照，不代替监控后端。生产导出使用 OTLP；
Collector 的 batch 与 memory limiter 负责背压，Prometheus endpoint 用于查询指标，Jaeger 用于
查询分布式 Trace。

## 本地运行

零依赖演示（默认）：

```bash
OTEL_EXPORTER=memory make run
curl -s http://127.0.0.1:8010/api/observability
```

Collector + Jaeger + Prometheus scrape endpoint：

```bash
make observability-up
OTEL_EXPORTER=otlp make run
# 另一个终端；异步链路需要相同 OTLP 配置
OTEL_EXPORTER=otlp make worker

open http://127.0.0.1:16686
curl -s http://127.0.0.1:9464/metrics | grep aurumlab
```

容器版本固定为 Jaeger 2.20.0 和 OpenTelemetry Collector Contrib 0.160.0；端口仅绑定
loopback。该 Compose 使用临时内存 Trace 存储，适合作品集演示，不是生产高可用部署。

## 验证要求

- 同步 Run 的 HTTP/Agent/Plan/Tool/Store Span 必须共享 Trace ID，并形成父子关系。
- 异步提交的 `job.process` 必须以 `job.submit` 为父 Span；Redis entry 只能包含 `job_id`。
- Snapshot 与序列化 Span 中不得出现测试 Prompt sentinel、参数、token 或原始 URL。
- 本地 Span buffer、API 查询条数和 Metric 单维度长度都有硬上限。
- 100+ Eval 阶段只汇总低基数结果；不能为每个自由文本案例创建 Metric label。
