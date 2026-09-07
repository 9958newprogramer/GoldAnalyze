# MCP Client、动态 Tool Catalog 与治理边界

## 目标

v0.6 证明 AurumLab 不只是“暴露了一个 MCP Server”，也能作为 MCP Host 动态发现和调用外部能力。黄金不是该层的特殊条件；默认连接的是独立非金融 Tool Provider。

```text
Operator-owned Binding
        ↓
MCP Client session (in-process / stdio)
        ↓ tools/list
Untrusted Tool metadata
        ↓ schema / size / injection validation
Namespaced Tool Catalog + fingerprint pin
        ↓ adapter registration
Tool Registry → Skill Allowlist → Budget → Execution Audit
```

## 发现与兼容策略

- 远端名称保留在描述符中，运行时名称固定为 `mcp__<namespace>__<safe_name>`；Server ID 和 namespace 必须唯一，归一化碰撞会拒绝整个 Server。
- Input/Output Schema 使用 JSON Schema Draft 2020-12 校验；禁止远程 `$ref`，避免验证阶段产生隐式网络访问。
- Schema、参数和结果分别限制为 64 KiB、32 KiB 和 256 KiB；单 Server 最多发现 50 个 Tool，最多读取 10 页。
- 指纹只包含规范化 Input/Output Schema，不把描述文本混入契约。首次发现自动 pin；刷新时同名 Tool 指纹变化即 `MCPSchemaDriftError`。
- 可选 operator pin 用于首次连接校验；缺少已 pin Tool 或指纹不匹配时拒绝上线。
- 刷新失败保留 last-known-good Catalog，同时健康状态改为 `degraded`，避免未经审核的新 Schema 替换已验证契约。

## 调用治理

Catalog 只代表“已发现”，不代表“允许调用”。`register_tools()` 只创建 Adapter，实际调用仍必须经过原有 `ToolPolicy`：

1. Skill Allowlist 检查 namespaced Tool。
2. 递增本次请求的 Tool Budget。
3. 使用关闭 `additionalProperties` 的 Schema 校验参数。
4. 在有界超时内通过 MCP session 调用。
5. 校验声明的结构化输出、大小和 Prompt Injection 信号。
6. 由 `ToolGateway` 记录 authorization / execution / duration / outcome Audit。

Tool 的描述、错误和结果均视为外部不可信数据。错误响应不会把远端原文或参数写入 Audit；Tool 描述和结果中的指令注入信号会在进入 Planner/下游处理前拒绝。

## 生命周期与故障隔离

- FastAPI lifespan 启动 MCP Client、完成 discovery、再把合格 Adapter 注册到 Tool Registry；退出时关闭会话。
- 每个 Server 独立维护 `connected / degraded / unavailable / disconnected` 状态、发现耗时和安全的错误类型。
- Server 连接或发现失败不会阻断本地 Router、Planner 和领域 Tool；调用超时会将 Server 降级，后续调用需先通过刷新恢复健康。
- `GET /api/mcp/catalog` 返回可演示的 Catalog，`GET /api/health` 返回连接 Server 数与 Tool 数。

## 可复现演示

```bash
# MCP Tool Provider 的独立 stdio 入口
.venv/bin/aurumlab-mcp-tools

# 启动 Web/API 后查看动态目录
curl -s http://127.0.0.1:8010/api/mcp/catalog

# 运行进程内 + 真实 stdio 集成测试
.venv/bin/pytest -q tests/test_mcp_client.py
```

## 当前边界

- Server Binding 由应用部署方在代码/配置层提供，不接受用户 Prompt 传入任意命令或 URL。
- 当前只实现进程内和 stdio；远程 Streamable HTTP 留到有认证、TLS、SSRF 防护和 OAuth Scope 后再开放。
- 默认远端 Tool 已进入统一 Registry，但尚未被现有黄金 Skills Allowlist；v1.0 的非金融 Skill 会以真实业务工作流使用它。
