# Docker 部署与演示契约

## 一键本地栈

```bash
docker compose config --quiet
make stack-up
curl --fail http://127.0.0.1:8010/api/health
make demo-resume-fast
```

`compose.yaml` 包含五个服务：

| 服务 | 角色 | 健康条件 |
|---|---|---|
| `api` | FastAPI、Web、同步/异步控制面 | `/api/health` 返回成功 |
| `worker` | Redis Streams Consumer、Checkpoint Executor | Redis ping 成功且主进程存活 |
| `redis` | 只传递 opaque Job ID 的队列数据面 | `redis-cli ping` |
| `otel-collector` | OTLP Trace/Metrics 接收与转发 | 容器启动；内部扩展监听 13133 |
| `jaeger` | Trace 查询 UI 与 OTLP 后端 | 容器启动 |

API 位于 `127.0.0.1:8010`，Jaeger 位于 `127.0.0.1:16686`，Prometheus scrape endpoint 位于 `127.0.0.1:9464/metrics`。Redis 的调试端口也只绑定 loopback。`make stack-down` 保留 named volume；如明确要删除本地运行状态，可执行 `docker compose down --volumes`。

## 镜像与状态边界

- API 和 Worker 复用 `aurumlab:1.0.0`，避免两个执行进程出现代码或依赖漂移。
- 基础镜像固定为 Python `3.12.14-slim-bookworm`，生产依赖由 `requirements.lock` 精确固定；项目自身使用 `--no-deps` 安装，避免构建时重新求解版本。
- 最终进程以 UID/GID 10001 运行。应用容器使用只读根文件系统、64 MiB `/tmp`、`cap_drop: ALL` 和 `no-new-privileges`。
- `/app/var` 是 API/Worker 共享的 SQLite named volume；Redis AOF 使用独立 volume。代码、Eval 和 Skill Manifest 不可写。
- `.dockerignore` 排除 `.env`、Git 元数据、虚拟环境、测试缓存、本地 `var` 和测试文件，避免把凭证或运行数据送入构建上下文。
- 外部 LLM/Search 密钥默认不进入 Compose。无密钥时使用受测的规则 Router、固定种子行情和 Disabled Search Provider，仍能完成演示。

当前 Compose 面向单机作品集：SQLite 共享 volume 不是多节点数据库，Redis 没有认证/TLS/HA，HTTP API 没有企业身份认证。它不应直接暴露公网。

## CI 容器 smoke

质量门禁先运行 Ruff、全量 Pytest、真实 Redis Consumer Group 测试和 120 条 Eval。通过后，独立 `container-smoke` Job 执行：

```text
docker compose config --quiet
→ docker build aurumlab:1.0.0
→ docker compose up --wait
→ GET /api/health
→ 容器内 demo_resume.py --skip-eval
→ docker compose down --volumes
```

官方 GitHub Action 使用完整 commit SHA，仓库 token 只有 `contents: read`。当前本地开发机没有 Docker CLI，因此本轮本地证据是 YAML/Dockerfile 契约测试、依赖锁安装检查和离线运行脚本；不能把尚未发生的远程 CI 配置描述为“云端已绿”。

## 面试演示顺序

1. `make stack-up`，查看 API、Worker 和 OTel 服务健康。
2. 在 Web 中运行本地行情查询，展示 Router、Plan、Tool Audit 和 Trace。
3. 输入危险指令，展示零 Tool 拒绝。
4. 运行事故复盘，展示 MCP Tool 的 `review`、批准、原子消费与恢复执行。
5. 重复同一事故规格，展示 exact hit 跳过五个 Tool。
6. 切换 Async 模式，展示 Redis Job、SSE、Checkpoint 与取消。
7. 打开 Jaeger 查询 API→Job→Worker→Tool Trace；打开 9464 查看指标。
8. 运行 `make demo-resume`，展示机器可读证据和 120/120 Eval。
