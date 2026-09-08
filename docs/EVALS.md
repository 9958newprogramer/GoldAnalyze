# Agent Eval 与 CI 质量门禁

## 目标

这套评测用来证明 Agent Runtime 的行为契约，而不是证明某个黄金策略能盈利。评测运行和 API、MCP、Web 相同的 `AurumAgent`，不使用为测试特制的伪实现。

## v6 数据集

`evals/golden.v6.jsonl` 有 108 条版本化案例：

| 覆盖类型 | 数量 |
|---|---:|
| 策略任务 | 44 |
| 本地行情查询 | 24 |
| 外部研究 | 12 |
| 其他意图 | 28 |
| Prompt Injection / 破坏 / Secret 泄露拒绝 | 16 |
| 人工审批并恢复 | 12 |
| Artifact exact-hit 复用 | 8 |
| 日线 / 小时线规格 | 34 / 34 |

边界覆盖包括窗口下界、成本上下界、本金上下界、查询条数上下界、日线/小时线别名、繁体中文、共享单位参数和默认参数。

## 双层门禁

1. 数据集级：限制 100—200 条；检查唯一 ID、归一化问题唯一性、四类路由最低配额、时间周期和特殊场景配额，并校验分类标签与预期行为一致。
2. 案例级：对 Task Accuracy、Workflow Integrity、Safety Compliance、Output Completeness 和 Reuse Efficiency 进行确定性评分。任一维度未全量通过，该案例即失败。

总分达到阈值但仍有单案例失败时，整体门禁仍失败。这避免平均分掩盖安全回归。

## 执行

```bash
.venv/bin/aurumlab-eval
.venv/bin/aurumlab-eval --json
make verify
```

CLI 成功返回 0，失败返回非 0。JSON 报告包含数据集覆盖摘要、逐案例分数、各维度细节、Run ID、解释器和耗时，并持久化到 `EvalReportRepository`。

## CI

`.github/workflows/ci.yml` 在 push、pull request 和手动触发时执行。环境为 Python 3.12 + Redis Service，并通过 `make verify` 一次性运行 Ruff、全量 Pytest、真实 Redis Consumer Group 集成测试和 v6 Eval。

工作流程使用两个供应链控制：

- 仓库 `GITHUB_TOKEN` 仅授予 `contents: read`。
- `checkout` 与 `setup-python` 使用完整 commit SHA，不依赖可移动的大版本标签。

## 取舍与限制

- 当前是确定性 rubric，适合做 CI 阻断门禁；尚未对开放式长文本引入 LLM-as-Judge。
- 默认 CI 不配置商业 LLM 或 Search Provider 密钥，因此验证的是安全降级、审批和工作流契约，不把外部服务可用性混入可重复的合并门禁。
- 108 条是可审计基线，不代表覆盖了所有自然语言表达。新发现的线上或手工回归应以新版数据集追加，不原地改写 v6。
