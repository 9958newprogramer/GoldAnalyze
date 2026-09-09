# Java / Python 契约 v1

本目录是 GoldAnalyze 控制平面与 Python 计算服务之间的可生成契约，不是手工复制的示例响应。

- `backtest-service.openapi.json`：Java 调用 Python Backtest Engine 的内部 HTTP API。
- `asyncapi.v1.json`：固定 Kafka Topic、收发方向和消息类型。
- `schemas/*.schema.json`：命令、事件、错误和计算结果的 Draft 2020-12 JSON Schema。
- `manifest.json`：每个产物的 SHA-256，用于 CI 检测契约漂移。

重新生成：

```bash
.venv/bin/python -m app.contracts.exporter
git diff --exit-code docs/contracts
```

Java 侧应从这些文件生成或校验 DTO，不应复制 Python 内部 ORM/SQLite 模型。兼容规则为：同一 `*.v1` Topic 只允许向后兼容地增加可选字段；删除、重命名、缩窄枚举或改变语义必须发布 `v2` Topic/Schema。

HTTP 调用必须携带 `Authorization: Bearer <service-token>`；线上环境还应由网关或 Service Mesh 提供 TLS/mTLS。Topic 名和响应目的地均为部署配置，消息体不接受 `reply_topic` 或回调 URL。
