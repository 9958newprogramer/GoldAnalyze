# Human-in-the-loop Tool 审批

v0.7 在 Tool Gateway 增加 `allow / deny / review` 三态治理。审批是 Agent 执行控制面的一部分，不是聊天中的“是否继续”提示：需要 review 的 Tool 在获得有效凭证前不会执行，也不会消耗 Tool 调用预算。

## 决策顺序

最严格规则优先：

1. Tool 未进入当前 Skill Allowlist：`deny`。
2. Tool effect 为 `privileged`：`deny`，即使声明需要审批也不能绕过。
3. effect 为 `external / write`、risk 为 `high`，或 Tool 显式声明 `requires_approval`：`review`。
4. 其余受允许的只读低风险 Tool：`allow`。

Tool 注册时必须提供 `effect`、`risk` 和来源元数据。非法枚举、低风险的外部/写入/特权 Tool 以及非法 Tool 名称均在注册阶段拒绝。MCP 动态发现的 Tool 默认标记为 `external / medium / requires_approval`，发现成功不代表取得执行权限。

## 状态机

```text
                  approve                  consume at exact Tool call
pending ----------------------------> approved ------------------------> consumed
   |                                      |
   | deny                                 | TTL
   v                                      v
denied                                 expired
   |
   └──────── terminal

pending -- TTL --> expired
```

- `pending`：Run 已暂停，Tool 未执行，预算未扣减。
- `approved`：签发一次性 bearer token；数据库只保存 SHA-256 hash。
- `consumed`：Gateway 在目标 Tool 调用前原子消费 token，然后才计入预算并执行。
- `denied / expired / consumed`：终态，不能再次批准或恢复。

批准 API 只返回一次原始 token。Run、Approval、Event、Audit 和 Artifact 都不保存它；消费或过期时数据库中的 token hash 也会清空。

## 凭证绑定

每个审批凭证同时绑定：

- `run_id`
- `plan_id`
- `step_id`
- `tool_name`
- 规范化 Tool 参数的 SHA-256 摘要
- `effect / risk`
- 过期时间

恢复时先校验 Run 与 Approval ID，再重新从当前 Skill Manifest 构建 Plan 并比对稳定 Plan ID；Gateway 到达目标 Tool 时再次校验全部绑定字段并执行 SQLite `BEGIN IMMEDIATE` 原子状态转换。因此，伪造 token、跨 Run/Plan/参数使用、过期、重复消费和并发双消费均会 fail closed。

## HTTP 与 MCP 控制面

```text
POST /api/runs
  └─ pending_approval + token-free ApprovalRequest

POST /api/runs/{run_id}/approvals/{approval_id}/approve
  └─ ApprovalGrant + one-time token

POST /api/runs/{run_id}/resume
  └─ validate → replay deterministic control steps → atomic consume → execute
```

批准和拒绝请求还要求 `X-AurumLab-Approval-Intent` 明确表达动作，减少浏览器中的意外跨站提交。MCP Server 只暴露 `resume_agent_run`，不暴露自批准 Tool，避免 Agent 给自己提权。

Web UI 将 token 保留在批准事件处理函数的局部变量中，收到后立即调用恢复接口；不会放入 DOM、URL、日志、`localStorage` 或 `sessionStorage`。

## 威胁模型与诚实边界

| 威胁 | 控制 |
|---|---|
| Prompt Injection 要求越权调用 | 预检 + 服务端 Skill Allowlist；模型不能扩张权限 |
| 将 review 当作 allow | review 直接暂停；无凭证时 Tool 调用数保持 0 |
| 伪造或猜测 token | 高熵随机 secret；只保存 hash；常量时间比较 |
| 修改参数后复用批准 | 参数规范化摘要与完整调用绑定 |
| 跨 Run/Plan/Tool 使用 | 多字段绑定并在消费点复核 |
| 重放或并发双消费 | SQLite 原子条件更新；成功一次后清除 hash |
| 凭证泄露到观测数据 | Public Model 不包含 token；Audit 只记录 approval_id |
| MCP Agent 自批准 | MCP 不提供 approve/deny Tool |

这是本地单用户作品集控制面，`local-demo-operator` 是演示身份，不是经过认证的企业用户。`X-AurumLab-Approval-Intent` 不是认证机制。公网或多租户部署仍需 OIDC/OAuth、RBAC、租户隔离、CSRF token、速率限制、TLS 和集中式审计。

v0.7 的恢复会使用已保存的 Route 和 Plan ID，重新执行确定性的前置 control steps，到达目标 Tool 后消费凭证；它不是持久化执行栈。v0.8 将以 Worker Checkpoint 替代此前置重放，并覆盖进程重启和 SSE 断线恢复。

## 验证范围

`tests/test_approval.py`、`tests/test_api.py`、`tests/test_agent.py` 和 `tests/test_mcp_client.py` 覆盖状态转换、hash-only 存储、伪造、过期、跨绑定、重放、并发消费、预算语义、HTTP 明确意图、Plan 暂停/恢复以及 MCP Remote Tool review。

Golden Set v5 的外部研究案例会真实经历 `review → approve → consume → allow → execute`，Safety Rubric 同时校验审批 ID 的审计链一致性。
