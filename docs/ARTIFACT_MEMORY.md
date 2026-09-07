# Artifact Memory 设计

## 目标

v0.4 用一个窄而完整的记忆层解决重复计算，不把 AurumLab 扩展成另一个知识库项目。记忆对象是 Agent 已验证的结构化任务和 Artifact，不是任意文档片段。

## 决策流程

```text
LLM Intent → typed Spec → data_version → SHA-256 fingerprint
                                           │
                          ┌────────────────┼────────────────┐
                          │                │                │
                      exact_hit    semantic_candidate      miss
                          │                │                │
                    复用 Snapshot     记录候选关系      完整执行 Tool
                    零领域 Tool       完整执行 Tool      保存 Snapshot
```

`semantic_candidate` 不等同于缓存命中。只要均线窗口、日期、成本等结果相关参数不同，Agent 就重新执行；相似度用于解释历史关联，而不是替代计算。

## 两级记忆

1. `ToolGateway` 的 request memory 以 `(tool_name, memory_key)` 为键，只在单次请求中存在。命中不会消耗 Tool 调用预算，但仍检查 Skill Allowlist。
2. `ArtifactCache` 使用 SQLite 持久化最小化 `ArtifactSnapshot`。缓存不保存原始问题、模型路由内容或详细 Tool Audit，只保留可复用领域结果及来源成本聚合。

为限制磁盘消耗，缓存默认最多保留 200 条；每次写入会先清理已过期记录，再按创建时间淘汰超额旧记录。

## 失效模型

| Artifact | 版本边界 | TTL |
|---|---|---:|
| 回测 | 规范化策略规格 + 行情 `data_version` | 无固定 TTL |
| 行情查询 | 查询规格 + 行情 `data_version` | 默认 300 秒 |
| 外部研究 | 规范化查询 + Provider 版本 | 默认 900 秒 |
| 普通回答 / 拒绝 | 不缓存 | — |

合成数据版本是显式常量；本地 SQLite 行情版本由文件大小、纳秒修改时间和 Adapter 配置哈希得到，不向 API 暴露绝对路径。

## 可观测契约

每个 Run 返回：

- `cache_status`：`miss`、`exact_hit`、`semantic_candidate`、`refresh` 或 `bypass`。
- `cache.fingerprint`：只暴露 16 位短指纹。
- `cache.source_run_id` 和 `similarity_score`。
- `saved_tool_calls` 与 `saved_latency_ms`。
- `reason`、`data_version` 和 TTL 到期时间。

缓存异常是优化层故障：读取失败会带原因降级为 `bypass`，写入失败会保留主任务结果并给出不泄漏内部信息的 warning。
