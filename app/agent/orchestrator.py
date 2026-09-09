"""Multi-Skill Agent orchestrator with routing, governance, and typed artifacts."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from opentelemetry.trace import SpanKind

from app.agent.checkpoint import (
    AgentExecutionCancelled,
    AgentExecutionSession,
    AgentStageTimeout,
    CancelProbe,
    CheckpointSink,
)
from app.agent.incident import (
    INCIDENT_SCHEMA_VERSION,
    assess_incident_impact,
    build_incident_action_plan,
    compile_incident_spec,
    summarize_incident_review,
    validate_incident_spec,
)
from app.agent.interpreter import OpenAICompatibleStrategyInterpreter
from app.agent.planner import BoundedPlanner, PlanRuntime
from app.agent.router import IntentRouter
from app.agent.task_compiler import compile_external_research, compile_market_query
from app.approval import (
    ApprovalBindingError,
    ApprovalRepository,
    ApprovalRequired,
    ApprovalStateError,
    ApprovalTokenError,
)
from app.backtest_service.port import (
    BacktestCallContext,
    BacktestExecution,
    BacktestExecutionPort,
    LocalBacktestExecutor,
)
from app.domain.backtest import BacktestResult
from app.domain.market_data import MarketDataRepository, profile_bars
from app.mcp_client import MCPClientManager
from app.memory import ArtifactCache, CacheLookup, TaskFingerprint, build_task_fingerprint
from app.models import (
    AgentCheckpoint,
    AgentEvent,
    ApprovalRequest,
    ArtifactSnapshot,
    CacheInfo,
    DataProfile,
    ExecutionPlan,
    ExternalResearchResult,
    ExternalResearchSpec,
    IncidentActionItem,
    IncidentAssessment,
    IncidentInputProfile,
    IncidentReviewResult,
    IncidentSpec,
    IntentDecision,
    MarketBarView,
    MarketQueryResult,
    MarketQuerySpec,
    ResearchSource,
    RunResponse,
    StrategySpec,
)
from app.observability import Telemetry
from app.security import redact_sensitive_text
from app.skills.registry import SkillRegistry
from app.storage import RunRepository
from app.tools.registry import ToolGateway, ToolMetadata, ToolPolicy, ToolRegistry
from app.tools.search import SearchProvider


def _backtest_summary(result: BacktestResult, profile: DataProfile) -> str:
    metrics = result.metrics
    direction = "正收益" if metrics.total_return_pct >= 0 else "负收益"
    return (
        f"本次实验使用 {profile.row_count} 条 {profile.timeframe} K 线，产生 "
        f"{metrics.trade_count} 笔完整交易。策略为{direction}，总收益率 "
        f"{metrics.total_return_pct:.2f}%，最大回撤 {metrics.max_drawdown_pct:.2f}%。"
        "结果来自确定性回测工具，仅用于演示 Agent 工程闭环，不构成投资建议。"
    )


def _bounded_backtest_spec(spec: StrategySpec) -> StrategySpec:
    """Fill omitted dates before crossing the Backtest Service trust boundary."""
    if spec.start_date is not None and spec.end_date is not None:
        return spec
    if spec.start_date is not None:
        return spec.model_copy(update={"end_date": spec.start_date.replace(month=12, day=31)})
    if spec.end_date is not None:
        return spec.model_copy(update={"start_date": spec.end_date.replace(month=1, day=1)})
    end_date = datetime(2025, 12, 31, tzinfo=UTC).date()
    start_year = 2021 if spec.timeframe == "1h" else 2018
    return spec.model_copy(
        update={"start_date": end_date.replace(year=start_year), "end_date": end_date}
    )


class AurumAgent:
    def __init__(
        self,
        interpreter: OpenAICompatibleStrategyInterpreter,
        market_repository: MarketDataRepository,
        search_provider: SearchProvider,
        skills: SkillRegistry,
        runs: RunRepository,
        artifacts: ArtifactCache,
        approvals: ApprovalRepository,
        router: IntentRouter | None = None,
        planner: BoundedPlanner | None = None,
        market_cache_ttl_seconds: int = 300,
        research_cache_ttl_seconds: int = 900,
        telemetry: Telemetry | None = None,
        mcp_clients: MCPClientManager | None = None,
        backtest_executor: BacktestExecutionPort | None = None,
    ):
        self.interpreter = interpreter
        self.market_repository = market_repository
        self.search_provider = search_provider
        self.skills = skills
        self.runs = runs
        self.artifacts = artifacts
        self.approvals = approvals
        self.router = router or IntentRouter()
        self.planner = planner or BoundedPlanner()
        self.market_cache_ttl_seconds = market_cache_ttl_seconds
        self.research_cache_ttl_seconds = research_cache_ttl_seconds
        self.telemetry = telemetry
        self.mcp_clients = mcp_clients
        self.backtest_executor = backtest_executor or LocalBacktestExecutor(market_repository)
        self.tools = ToolRegistry()
        read_low = ToolMetadata(effect="read", risk="low")
        self.tools.register(
            "resolve_backtest_data_version",
            self._resolve_backtest_data_version,
            read_low,
        )
        self.tools.register("execute_backtest", self._execute_backtest, read_low)
        self.tools.register("summarize_result", self._summarize_result, read_low)
        self.tools.register("query_market_data", self._query_market_data, read_low)
        self.tools.register("summarize_market_query", self._summarize_market_query, read_low)
        self.tools.register(
            "search_external_knowledge",
            self._search_external_knowledge,
            ToolMetadata(
                effect="external",
                risk="medium",
                requires_approval=True,
            ),
        )
        self.tools.register(
            "summarize_external_research", self._summarize_external_research, read_low
        )
        self.tools.register("validate_incident_spec", self._validate_incident_spec, read_low)
        self.tools.register("assess_incident_impact", self._assess_incident_impact, read_low)
        self.tools.register(
            "build_incident_action_plan", self._build_incident_action_plan, read_low
        )
        self.tools.register("summarize_incident_review", self._summarize_incident_review, read_low)
        self.tools.register("compose_general_response", self._compose_general_response, read_low)

    async def _resolve_backtest_data_version(
        self,
        spec: StrategySpec,
        run_id: str,
        call_context: BacktestCallContext | None = None,
    ) -> str:
        material = json.dumps(spec.model_dump(mode="json"), sort_keys=True).encode()
        context = call_context or BacktestCallContext.for_agent_run(
            run_id=run_id, idempotency_key=f"preflight-{hashlib.sha256(material).hexdigest()}"
        )
        return await self.backtest_executor.data_version(spec, context)

    async def _execute_backtest(
        self,
        spec: StrategySpec,
        run_id: str,
        task_fingerprint: str,
        expected_data_version: str,
        call_context: BacktestCallContext | None = None,
    ) -> BacktestExecution:
        context = call_context or BacktestCallContext.for_agent_run(
            run_id=run_id,
            idempotency_key=task_fingerprint,
        )
        return await self.backtest_executor.execute(
            spec,
            context,
            expected_data_version=expected_data_version,
        )

    async def _summarize_result(self, result: BacktestResult, profile: DataProfile) -> str:
        return _backtest_summary(result, profile)

    async def _query_market_data(self, query: MarketQuerySpec) -> dict[str, Any]:
        repository_spec = StrategySpec(
            symbol=query.symbol,
            timeframe=query.timeframe,
            start_date=query.start_date,
            end_date=query.end_date,
        )
        bars = self.market_repository.load(repository_spec)
        profile = profile_bars(self.market_repository, repository_spec, bars)
        selected = bars[-query.limit :]
        first_close = selected[0].close
        result = MarketQueryResult(
            symbol=query.symbol,
            timeframe=query.timeframe,
            row_count=len(bars),
            returned_count=len(selected),
            start_at=selected[0].at,
            end_at=selected[-1].at,
            period_high=round(max(bar.high for bar in selected), 4),
            period_low=round(min(bar.low for bar in selected), 4),
            change_pct=round((selected[-1].close / first_close - 1) * 100, 4),
            average_volume=round(sum(bar.volume for bar in selected) / len(selected), 2),
            bars=[MarketBarView.model_validate(bar.__dict__) for bar in selected],
        )
        return {"result": result, "profile": profile}

    async def _summarize_market_query(
        self,
        query: MarketQuerySpec,
        result: MarketQueryResult,
        profile: DataProfile,
    ) -> str:
        return (
            f"已从 {profile.source} 读取 {profile.row_count} 条 {query.timeframe} 数据，"
            f"返回最近 {result.returned_count} 条。区间最高价 {result.period_high:.2f}，"
            f"最低价 {result.period_low:.2f}，收盘价变化 {result.change_pct:.2f}%。"
        )

    async def _search_external_knowledge(
        self,
        spec: ExternalResearchSpec,
    ) -> dict[str, Any]:
        try:
            sources = await self.search_provider.search(spec.query, spec.max_results)
            return {"sources": sources, "warning": None}
        except Exception as exc:  # noqa: BLE001 -- provider errors degrade without leaking details
            return {
                "sources": [],
                "warning": f"外部 Search Provider 暂不可用：{type(exc).__name__}",
            }

    async def _summarize_external_research(
        self,
        spec: ExternalResearchSpec,
        sources: list[ResearchSource],
    ) -> ExternalResearchResult:
        if not sources:
            answer = (
                "当前没有获得外部来源。请配置 TAVILY_API_KEY 后重试；"
                "Agent 没有使用内部知识冒充实时搜索结果。"
            )
        else:
            evidence = "；".join(
                f"{source.title}：{source.snippet[:220]}" for source in sources[:3]
            )
            answer = f"外部检索返回 {len(sources)} 个来源。可核验的摘要片段：{evidence}"
        return ExternalResearchResult(
            query=spec.query,
            provider=self.search_provider.name,
            answer=answer,
            sources=sources,
            freshness=datetime.now(UTC).isoformat(),
        )

    async def _compose_general_response(self, question: str) -> str:
        return (
            "当前 Agent 支持四类可执行任务：策略回测、本地黄金行情查询、"
            "外部知识检索和服务事故复盘。"
            "这个问题未触发领域工具；你可以直接描述目标、周期和参数。"
        )

    async def _validate_incident_spec(self, spec: IncidentSpec) -> list[str]:
        return validate_incident_spec(spec)

    async def _assess_incident_impact(self, spec: IncidentSpec) -> IncidentAssessment:
        return assess_incident_impact(spec)

    async def _build_incident_action_plan(
        self, assessment: IncidentAssessment
    ) -> list[IncidentActionItem]:
        return build_incident_action_plan(assessment)

    async def _summarize_incident_review(
        self,
        spec: IncidentSpec,
        assessment: IncidentAssessment,
        input_profile: IncidentInputProfile,
        action_items: list[IncidentActionItem],
    ) -> IncidentReviewResult:
        return summarize_incident_review(spec, assessment, input_profile, action_items)

    async def _ensure_skill_tools(self, skill) -> None:
        remote_tools = [name for name in skill.allowed_tools if name.startswith("mcp__")]
        if not remote_tools:
            return
        if self.mcp_clients is None:
            raise RuntimeError("Skill 依赖 MCP Tool，但运行时未配置 MCP Client")
        await self.mcp_clients.start()
        self.mcp_clients.register_tools(self.tools)
        missing = [name for name in remote_tools if not self.tools.contains(name)]
        if missing:
            raise RuntimeError("Skill 依赖的 MCP Tool 未通过动态发现与 Schema 门禁")

    async def _timed_event(
        self,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        stage: str,
        message: str,
        action: Callable[[], Awaitable[Any]],
        tool_name: str | None = None,
        session: AgentExecutionSession | None = None,
    ) -> Any:
        if session is not None:
            restored, output = session.restore(stage)
            if restored:
                if self.telemetry is not None:
                    with self.telemetry.span(
                        "aurumlab.plan.step",
                        attributes={
                            "aurumlab.plan.step": stage,
                            "aurumlab.job.status": "restored",
                        },
                    ):
                        self.telemetry.count(
                            "aurumlab.agent.plan.steps",
                            attributes={"step": stage, "result": "restored"},
                        )
                return output
        runtime.begin(stage, tool_name)
        started = perf_counter()
        scope = (
            self.telemetry.span(
                "aurumlab.plan.step",
                attributes={
                    "aurumlab.plan.step": stage,
                    **({"aurumlab.tool.name": tool_name} if tool_name else {}),
                },
            )
            if self.telemetry is not None
            else nullcontext()
        )
        try:
            with scope:
                output = (
                    await session.bounded(stage, action) if session is not None else await action()
                )
                runtime.complete(stage)
                duration_ms = round((perf_counter() - started) * 1_000, 2)
                events.append(
                    AgentEvent(
                        sequence=len(events) + 1,
                        stage=stage,
                        status="completed",
                        message=message,
                        duration_ms=duration_ms,
                    )
                )
                if self.telemetry is not None:
                    self.telemetry.count(
                        "aurumlab.agent.plan.steps",
                        attributes={"step": stage, "result": "completed"},
                    )
                    self.telemetry.record(
                        "aurumlab.agent.plan.step.duration",
                        duration_ms,
                        attributes={"step": stage, "result": "completed"},
                    )
            if session is not None:
                checkpoint_scope = (
                    self.telemetry.span(
                        "aurumlab.store.checkpoint.save",
                        attributes={"aurumlab.plan.step": stage},
                    )
                    if self.telemetry is not None
                    else nullcontext()
                )
                with checkpoint_scope:
                    session.persist(stage, output, runtime, events)
            return output
        except ApprovalRequired as exc:
            runtime.pause(stage)
            duration_ms = round((perf_counter() - started) * 1_000, 2)
            events.append(
                AgentEvent(
                    sequence=len(events) + 1,
                    stage="approval_required",
                    status="waiting_approval",
                    message=f"Tool {tool_name or stage} 等待人工审批，尚未执行",
                    duration_ms=duration_ms,
                    details={
                        "approval_id": exc.approval.approval_id,
                        "tool": exc.approval.tool_name,
                        "effect": exc.approval.effect,
                        "risk": exc.approval.risk,
                        "expires_at": exc.approval.expires_at.isoformat(),
                    },
                )
            )
            if self.telemetry is not None:
                self.telemetry.count(
                    "aurumlab.agent.plan.steps",
                    attributes={"step": stage, "result": "waiting_approval"},
                )
            raise
        except Exception as exc:
            runtime.fail(stage)
            events.append(
                AgentEvent(
                    sequence=len(events) + 1,
                    stage=stage,
                    status="failed",
                    message=f"{message}失败：{exc}",
                    duration_ms=round((perf_counter() - started) * 1_000, 2),
                    details={"error_type": type(exc).__name__},
                )
            )
            if self.telemetry is not None:
                self.telemetry.count(
                    "aurumlab.agent.plan.steps",
                    attributes={"step": stage, "result": "failed"},
                )
            raise

    def _timed_sync_event(
        self,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        stage: str,
        message: str,
        action: Callable[[], Any],
        session: AgentExecutionSession | None = None,
    ) -> Any:
        if session is not None:
            restored, output = session.restore(stage)
            if restored:
                if self.telemetry is not None:
                    with self.telemetry.span(
                        "aurumlab.plan.step",
                        attributes={
                            "aurumlab.plan.step": stage,
                            "aurumlab.job.status": "restored",
                        },
                    ):
                        self.telemetry.count(
                            "aurumlab.agent.plan.steps",
                            attributes={"step": stage, "result": "restored"},
                        )
                return output
            session.before_step()
        runtime.begin(stage)
        started = perf_counter()
        scope = (
            self.telemetry.span(
                "aurumlab.plan.step",
                attributes={"aurumlab.plan.step": stage},
            )
            if self.telemetry is not None
            else nullcontext()
        )
        try:
            with scope:
                output = action()
                runtime.complete(stage)
                duration_ms = round((perf_counter() - started) * 1_000, 2)
                events.append(
                    AgentEvent(
                        sequence=len(events) + 1,
                        stage=stage,
                        status="completed",
                        message=message,
                        duration_ms=duration_ms,
                    )
                )
                if self.telemetry is not None:
                    self.telemetry.count(
                        "aurumlab.agent.plan.steps",
                        attributes={"step": stage, "result": "completed"},
                    )
                    self.telemetry.record(
                        "aurumlab.agent.plan.step.duration",
                        duration_ms,
                        attributes={"step": stage, "result": "completed"},
                    )
            if session is not None:
                checkpoint_scope = (
                    self.telemetry.span(
                        "aurumlab.store.checkpoint.save",
                        attributes={"aurumlab.plan.step": stage},
                    )
                    if self.telemetry is not None
                    else nullcontext()
                )
                with checkpoint_scope:
                    session.persist(stage, output, runtime, events)
            return output
        except Exception as exc:
            runtime.fail(stage)
            events.append(
                AgentEvent(
                    sequence=len(events) + 1,
                    stage=stage,
                    status="failed",
                    message=f"{message}失败：{exc}",
                    duration_ms=round((perf_counter() - started) * 1_000, 2),
                    details={"error_type": type(exc).__name__},
                )
            )
            if self.telemetry is not None:
                self.telemetry.count(
                    "aurumlab.agent.plan.steps",
                    attributes={"step": stage, "result": "failed"},
                )
            raise

    def _lookup_cache(
        self,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        task: TaskFingerprint,
        cache_policy: Literal["use", "refresh", "bypass"],
        session: AgentExecutionSession | None = None,
    ) -> CacheLookup:
        if session is not None:
            restored, output = session.restore("cache_lookup")
            if restored:
                return output
            session.before_step()
        runtime.begin("cache_lookup")
        started = perf_counter()
        try:
            if cache_policy == "bypass":
                lookup = CacheLookup(
                    CacheInfo(
                        status="bypass",
                        fingerprint=task.public_fingerprint,
                        data_version=task.data_version,
                        reason="caller_bypassed_cache",
                    )
                )
            elif cache_policy == "refresh":
                lookup = CacheLookup(
                    CacheInfo(
                        status="refresh",
                        fingerprint=task.public_fingerprint,
                        data_version=task.data_version,
                        reason="caller_forced_refresh",
                    )
                )
            else:
                try:
                    lookup_scope = (
                        self.telemetry.span("aurumlab.store.artifact.lookup")
                        if self.telemetry is not None
                        else nullcontext()
                    )
                    with lookup_scope:
                        lookup = self.artifacts.lookup(task)
                except Exception as exc:  # noqa: BLE001 -- cache cannot break primary execution
                    lookup = CacheLookup(
                        CacheInfo(
                            status="miss",
                            fingerprint=task.public_fingerprint,
                            data_version=task.data_version,
                            reason=f"cache_lookup_failed:{type(exc).__name__}",
                        )
                    )
            messages = {
                "exact_hit": "命中长期 Artifact Cache，跳过领域 Tool 链",
                "semantic_candidate": "发现相似任务，但规格不同，将重新执行以保证结果正确",
                "miss": "未找到可复用 Artifact，将执行完整 Tool 链",
                "refresh": "调用方要求刷新，将执行完整 Tool 链并更新 Artifact",
                "bypass": "本次请求绕过 Artifact Cache",
            }
            duration_ms = round((perf_counter() - started) * 1_000, 2)
            events.append(
                AgentEvent(
                    sequence=len(events) + 1,
                    stage="cache_lookup",
                    status="completed",
                    message=messages[lookup.info.status],
                    duration_ms=duration_ms,
                    details=lookup.info.model_dump(mode="json", exclude_none=True),
                )
            )
            runtime.complete("cache_lookup")
            if self.telemetry is not None:
                metric_attributes = {
                    "step": "cache_lookup",
                    "result": "completed",
                    "cache_status": lookup.info.status,
                }
                self.telemetry.count("aurumlab.agent.plan.steps", attributes=metric_attributes)
                self.telemetry.record(
                    "aurumlab.agent.plan.step.duration",
                    duration_ms,
                    attributes=metric_attributes,
                )
            if session is not None:
                checkpoint_scope = (
                    self.telemetry.span(
                        "aurumlab.store.checkpoint.save",
                        attributes={"aurumlab.plan.step": "cache_lookup"},
                    )
                    if self.telemetry is not None
                    else nullcontext()
                )
                with checkpoint_scope:
                    session.persist("cache_lookup", lookup, runtime, events)
            return lookup
        except Exception:
            runtime.fail("cache_lookup")
            raise

    @staticmethod
    def _cached_payload(source: ArtifactSnapshot, cache: CacheInfo) -> dict[str, Any]:
        return {
            "interpreter": source.interpreter,
            "strategy": source.strategy,
            "market_query": source.market_query,
            "market_result": source.market_result,
            "research_spec": source.research_spec,
            "research_result": source.research_result,
            "incident_spec": source.incident_spec,
            "incident_result": source.incident_result,
            "data_profile": source.data_profile,
            "metrics": source.metrics,
            "trades": source.trades,
            "equity_curve": source.equity_curve,
            "summary": source.summary,
            "warnings": [
                *source.warnings,
                f"已复用 Run {cache.source_run_id} 的结构化 Artifact，未重复调用领域 Tool。",
            ],
            "cache_status": cache.status,
            "cache": cache,
        }

    async def _run_backtest_workflow(
        self,
        question: str,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        gateway: ToolGateway,
        cache_policy: Literal["use", "refresh", "bypass"],
        session: AgentExecutionSession | None = None,
        backtest_call_context: BacktestCallContext | None = None,
    ) -> dict[str, Any]:
        interpretation = await self._timed_event(
            events,
            runtime,
            "interpret_strategy",
            "已将用户问题编译为受约束的 StrategySpec",
            lambda: self.interpreter.interpret(question),
            session=session,
        )
        bounded_spec = _bounded_backtest_spec(interpretation.spec)
        if bounded_spec != interpretation.spec:
            interpretation = interpretation.__class__(
                spec=bounded_spec,
                interpreter=interpretation.interpreter,
                warnings=[
                    *interpretation.warnings,
                    "未给出完整日期范围，已在跨服务边界补入有界默认日期。",
                ],
            )
        run_id = gateway.run_id or "standalone-run"
        data_version = await self._timed_event(
            events,
            runtime,
            "resolve_backtest_data_version",
            "已通过受治理 Tool 锁定回测数据版本",
            lambda: gateway.call(
                "resolve_backtest_data_version",
                spec=bounded_spec,
                run_id=run_id,
                call_context=backtest_call_context,
            ),
            tool_name="resolve_backtest_data_version",
            session=session,
        )
        task = build_task_fingerprint(
            "backtest_strategy",
            bounded_spec,
            data_version,
        )
        lookup = self._lookup_cache(events, runtime, task, cache_policy, session)
        if lookup.info.status == "exact_hit" and lookup.source is not None:
            return self._cached_payload(lookup.source, lookup.info)
        execution: BacktestExecution = await self._timed_event(
            events,
            runtime,
            "execute_backtest",
            "独立确定性 Backtest Engine 执行完成",
            lambda: gateway.call(
                "execute_backtest",
                memory_key=f"{task.fingerprint}:execute_backtest",
                spec=bounded_spec,
                run_id=run_id,
                task_fingerprint=task.fingerprint,
                expected_data_version=data_version,
                call_context=backtest_call_context,
            ),
            tool_name="execute_backtest",
            session=session,
        )
        summary = await self._timed_event(
            events,
            runtime,
            "summarize_result",
            "已根据结构化实验产物生成反馈",
            lambda: gateway.call(
                "summarize_result",
                result=execution.result,
                profile=execution.profile,
            ),
            tool_name="summarize_result",
            session=session,
        )
        return {
            "interpreter": interpretation.interpreter,
            "strategy": bounded_spec,
            "data_profile": execution.profile,
            "metrics": execution.result.metrics,
            "trades": execution.result.trades,
            "equity_curve": execution.result.equity_curve,
            "summary": summary,
            "warnings": [*interpretation.warnings, *execution.warnings],
            "cache_status": lookup.info.status,
            "cache": lookup.info,
            "_cache_task": task,
            "_cache_ttl": None,
        }

    async def _run_market_query_workflow(
        self,
        question: str,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        gateway: ToolGateway,
        cache_policy: Literal["use", "refresh", "bypass"],
        session: AgentExecutionSession | None = None,
    ) -> dict[str, Any]:
        query = self._timed_sync_event(
            events,
            runtime,
            "compile_market_query",
            "已将问题编译为受约束的 MarketQuerySpec",
            lambda: compile_market_query(question),
            session=session,
        )
        repository_spec = StrategySpec(
            symbol=query.symbol,
            timeframe=query.timeframe,
            start_date=query.start_date,
            end_date=query.end_date,
        )
        task = build_task_fingerprint(
            "query_market_data",
            query,
            self.market_repository.data_version(repository_spec),
        )
        lookup = self._lookup_cache(events, runtime, task, cache_policy, session)
        if lookup.info.status == "exact_hit" and lookup.source is not None:
            return self._cached_payload(lookup.source, lookup.info)
        queried = await self._timed_event(
            events,
            runtime,
            "query_market_data",
            "已通过只读行情 Tool 获取结构化数据快照",
            lambda: gateway.call(
                "query_market_data",
                memory_key=f"{task.fingerprint}:query_market_data",
                query=query,
            ),
            tool_name="query_market_data",
            session=session,
        )
        summary = await self._timed_event(
            events,
            runtime,
            "summarize_market_query",
            "已根据行情快照生成可核验摘要",
            lambda: gateway.call(
                "summarize_market_query",
                query=query,
                result=queried["result"],
                profile=queried["profile"],
            ),
            tool_name="summarize_market_query",
            session=session,
        )
        return {
            "interpreter": "deterministic-market-compiler",
            "market_query": query,
            "market_result": queried["result"],
            "data_profile": queried["profile"],
            "summary": summary,
            "warnings": queried["profile"].warnings,
            "cache_status": lookup.info.status,
            "cache": lookup.info,
            "_cache_task": task,
            "_cache_ttl": self.market_cache_ttl_seconds,
        }

    async def _run_external_research_workflow(
        self,
        question: str,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        gateway: ToolGateway,
        cache_policy: Literal["use", "refresh", "bypass"],
        session: AgentExecutionSession | None = None,
    ) -> dict[str, Any]:
        spec = self._timed_sync_event(
            events,
            runtime,
            "compile_research_query",
            "已生成有界的 ExternalResearchSpec",
            lambda: compile_external_research(question),
            session=session,
        )
        task = build_task_fingerprint(
            "external_research",
            spec,
            f"provider-v1:{self.search_provider.name}",
        )
        lookup = self._lookup_cache(events, runtime, task, cache_policy, session)
        if lookup.info.status == "exact_hit" and lookup.source is not None:
            return self._cached_payload(lookup.source, lookup.info)
        searched = await self._timed_event(
            events,
            runtime,
            "search_external_knowledge",
            "已调用受治理的外部 Search Provider",
            lambda: gateway.call("search_external_knowledge", spec=spec),
            tool_name="search_external_knowledge",
            session=session,
        )
        result = await self._timed_event(
            events,
            runtime,
            "summarize_external_research",
            "已基于来源片段生成带出处的反馈",
            lambda: gateway.call(
                "summarize_external_research",
                spec=spec,
                sources=searched["sources"],
            ),
            tool_name="summarize_external_research",
            session=session,
        )
        warnings = [searched["warning"]] if searched["warning"] else []
        if self.search_provider.name == "unconfigured":
            warnings.append("未配置 TAVILY_API_KEY，未执行真实外部检索。")
        return {
            "interpreter": "deterministic-research-compiler",
            "research_spec": spec,
            "research_result": result,
            "summary": result.answer,
            "warnings": warnings,
            "cache_status": lookup.info.status,
            "cache": lookup.info,
            "_cache_task": task,
            "_cache_ttl": self.research_cache_ttl_seconds,
        }

    async def _run_general_workflow(
        self,
        question: str,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        gateway: ToolGateway,
        cache_policy: Literal["use", "refresh", "bypass"],
        session: AgentExecutionSession | None = None,
    ) -> dict[str, Any]:
        summary = await self._timed_event(
            events,
            runtime,
            "compose_general_response",
            "已生成能力边界内的直接反馈",
            lambda: gateway.call("compose_general_response", question=question),
            tool_name="compose_general_response",
            session=session,
        )
        return {
            "interpreter": "direct",
            "summary": summary,
            "warnings": [],
            "cache_status": "bypass",
            "cache": CacheInfo(status="bypass", reason="general_response_is_not_cached"),
        }

    async def _run_incident_review_workflow(
        self,
        question: str,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        gateway: ToolGateway,
        cache_policy: Literal["use", "refresh", "bypass"],
        session: AgentExecutionSession | None = None,
    ) -> dict[str, Any]:
        spec = self._timed_sync_event(
            events,
            runtime,
            "compile_incident_spec",
            "已将事故描述编译为受约束的 IncidentSpec",
            lambda: compile_incident_spec(question),
            session=session,
        )
        task = build_task_fingerprint("incident_review", spec, INCIDENT_SCHEMA_VERSION)
        lookup = self._lookup_cache(events, runtime, task, cache_policy, session)
        if lookup.info.status == "exact_hit" and lookup.source is not None:
            return self._cached_payload(lookup.source, lookup.info)
        profile_payload = await self._timed_event(
            events,
            runtime,
            "mcp__runtime__profile_text",
            "已通过动态发现的 MCP Tool 生成有界输入轮廓",
            lambda: gateway.call(
                "mcp__runtime__profile_text",
                step_id="mcp__runtime__profile_text",
                text=question,
            ),
            tool_name="mcp__runtime__profile_text",
            session=session,
        )
        input_profile = IncidentInputProfile.model_validate(profile_payload)
        validation_warnings = await self._timed_event(
            events,
            runtime,
            "validate_incident_spec",
            "已验证事故指标边界与证据完整性",
            lambda: gateway.call("validate_incident_spec", spec=spec),
            tool_name="validate_incident_spec",
            session=session,
        )
        assessment = await self._timed_event(
            events,
            runtime,
            "assess_incident_impact",
            "已基于结构化事实生成确定性风险分级",
            lambda: gateway.call("assess_incident_impact", spec=spec),
            tool_name="assess_incident_impact",
            session=session,
        )
        action_items = await self._timed_event(
            events,
            runtime,
            "build_incident_action_plan",
            "已生成只读的分级行动项",
            lambda: gateway.call("build_incident_action_plan", assessment=assessment),
            tool_name="build_incident_action_plan",
            session=session,
        )
        result = await self._timed_event(
            events,
            runtime,
            "summarize_incident_review",
            "已生成不虚构根因的 Incident Artifact",
            lambda: gateway.call(
                "summarize_incident_review",
                spec=spec,
                assessment=assessment,
                input_profile=input_profile,
                action_items=action_items,
            ),
            tool_name="summarize_incident_review",
            session=session,
        )
        return {
            "interpreter": "deterministic-incident-compiler",
            "incident_spec": spec,
            "incident_result": result,
            "summary": result.summary,
            "warnings": validation_warnings,
            "cache_status": lookup.info.status,
            "cache": lookup.info,
            "_cache_task": task,
            "_cache_ttl": None,
        }

    async def run(
        self,
        question: str,
        *,
        cache_policy: Literal["use", "refresh", "bypass"] = "use",
        backtest_call_context: BacktestCallContext | None = None,
    ) -> RunResponse:
        return await self._observed_execute(
            question,
            cache_policy=cache_policy,
            backtest_call_context=backtest_call_context,
        )

    async def run_checkpointed(
        self,
        question: str,
        *,
        job_id: str,
        cache_policy: Literal["use", "refresh", "bypass"],
        checkpoint_sink: CheckpointSink,
        cancel_probe: CancelProbe,
        stage_timeout_seconds: float,
        checkpoint: AgentCheckpoint | None = None,
        trusted_consumed_approval: ApprovalRequest | None = None,
        backtest_call_context: BacktestCallContext | None = None,
    ) -> RunResponse:
        """Execute for a Worker, persisting after every completed Plan step."""
        return await self._observed_execute(
            question,
            cache_policy=cache_policy,
            checkpoint=checkpoint,
            checkpoint_job_id=job_id,
            checkpoint_sink=checkpoint_sink,
            cancel_probe=cancel_probe,
            stage_timeout_seconds=stage_timeout_seconds,
            trusted_consumed_approval=trusted_consumed_approval,
            backtest_call_context=backtest_call_context,
        )

    async def resume(self, run_id: str, approval_token: str) -> RunResponse:
        pending = self.runs.get(run_id)
        if pending is None:
            raise ApprovalBindingError("run not found")
        if pending.status != "pending_approval" or pending.approval is None:
            raise ApprovalStateError("run is not waiting for approval")
        if pending.route is None or pending.plan is None:
            raise ApprovalBindingError("pending run is missing its route or plan")
        self.approvals.validate_token(
            approval_token,
            approval_id=pending.approval.approval_id,
            run_id=run_id,
        )
        current_plan = self.planner.build(self.skills.get(pending.route.skill))
        if current_plan.plan_id != pending.plan.plan_id:
            raise ApprovalBindingError("plan changed after approval was requested")
        return await self._observed_execute(
            pending.question,
            cache_policy=pending.requested_cache_policy,
            run_id=pending.run_id,
            created_at=pending.created_at,
            route_override=pending.route,
            approval_token=approval_token,
            prior_audit=pending.tool_audit,
            expected_plan_id=pending.plan.plan_id,
        )

    async def _observed_execute(self, question: str, **kwargs: Any) -> RunResponse:
        started = perf_counter()
        job_id = kwargs.get("checkpoint_job_id")
        owns_mcp_session = self.mcp_clients is not None and not self.mcp_clients.started
        scope = (
            self.telemetry.span(
                "aurumlab.agent.run",
                attributes={
                    **({"aurumlab.job.id": job_id} if job_id else {}),
                },
                kind=SpanKind.INTERNAL,
            )
            if self.telemetry is not None
            else nullcontext()
        )
        try:
            with scope as span:
                try:
                    response = await self._execute(question, **kwargs)
                except Exception as exc:
                    if span is not None:
                        self.telemetry.mark_error(span, type(exc).__name__)
                    if self.telemetry is not None:
                        self.telemetry.count(
                            "aurumlab.agent.runs",
                            attributes={"intent": "unknown", "status": "error"},
                        )
                    raise
                duration_ms = round((perf_counter() - started) * 1_000, 2)
                if span is not None:
                    span.set_attribute("aurumlab.run.id", response.run_id)
                    span.set_attribute(
                        "aurumlab.intent", response.route.intent if response.route else "unknown"
                    )
                    span.set_attribute("aurumlab.skill", response.skill)
                    span.set_attribute("aurumlab.cache.status", response.cache_status)
                if self.telemetry is not None:
                    attributes = {
                        "intent": response.route.intent if response.route else "unknown",
                        "status": response.status,
                        "cache_status": response.cache_status,
                    }
                    self.telemetry.count("aurumlab.agent.runs", attributes=attributes)
                    self.telemetry.record(
                        "aurumlab.agent.run.duration", duration_ms, attributes=attributes
                    )
                return response
        finally:
            if owns_mcp_session and self.mcp_clients is not None and self.mcp_clients.started:
                await self.mcp_clients.stop()

    def deny_approval(self, run_id: str, approval_id: str) -> RunResponse:
        pending = self.runs.get(run_id)
        if pending is None:
            raise ApprovalBindingError("run not found")
        if pending.status != "pending_approval" or pending.approval is None:
            raise ApprovalStateError("run is not waiting for approval")
        if not hmac.compare_digest(pending.approval.approval_id, approval_id):
            raise ApprovalBindingError("approval is bound to a different run")
        step_ids = [step.step_id for step in pending.plan.steps]
        if pending.approval.step_id not in step_ids:
            raise ApprovalBindingError("approval step is missing from the persisted plan")
        approval = self.approvals.deny(approval_id, decided_by="local-demo-operator")
        rejected_index = step_ids.index(approval.step_id)
        terminal_plan = pending.plan.model_copy(
            update={
                "paused_step": None,
                "rejected_step": approval.step_id,
                "skipped_steps": step_ids[rejected_index + 1 :],
            }
        )
        events = [
            *pending.events,
            AgentEvent(
                sequence=len(pending.events) + 1,
                stage="approval_denied",
                status="warning",
                message="人工审批已拒绝，受控 Tool 未执行",
                duration_ms=0,
                details={"approval_id": approval_id, "tool": approval.tool_name},
            ),
        ]
        response = pending.model_copy(
            update={
                "status": "rejected",
                "execution_mode": "rejected",
                "approval": approval,
                "plan": terminal_plan,
                "summary": "人工审批已拒绝，Agent 未执行等待审批的 Tool。",
                "warnings": [*pending.warnings, "审批拒绝后不能恢复本次 Run。"],
                "events": events,
            }
        )
        self._save_run(response)
        return response

    async def _execute(
        self,
        question: str,
        *,
        cache_policy: Literal["use", "refresh", "bypass"],
        run_id: str | None = None,
        created_at: datetime | None = None,
        route_override: IntentDecision | None = None,
        approval_token: str | None = None,
        prior_audit: list[dict[str, Any]] | None = None,
        expected_plan_id: str | None = None,
        checkpoint: AgentCheckpoint | None = None,
        checkpoint_job_id: str | None = None,
        checkpoint_sink: CheckpointSink | None = None,
        cancel_probe: CancelProbe | None = None,
        stage_timeout_seconds: float = 30.0,
        trusted_consumed_approval: ApprovalRequest | None = None,
        backtest_call_context: BacktestCallContext | None = None,
    ) -> RunResponse:
        stored_question = redact_sensitive_text(question)
        if checkpoint is not None:
            run_id = checkpoint.run_id
            created_at = checkpoint.created_at
            route_override = checkpoint.route
            prior_audit = checkpoint.tool_audit
            expected_plan_id = checkpoint.plan_id
        run_id = run_id or uuid4().hex[:12]
        created_at = created_at or datetime.now(UTC)
        events: list[AgentEvent] = list(checkpoint.events) if checkpoint else []
        cache_task: TaskFingerprint | None = None
        cache_ttl: int | None = None
        plan: ExecutionPlan | None = None
        runtime: PlanRuntime | None = None
        route_started = perf_counter()
        route_scope = (
            self.telemetry.span("aurumlab.agent.route")
            if self.telemetry is not None
            else nullcontext()
        )
        with route_scope as route_span:
            route = route_override or await self.router.route(question)
            if route_span is not None:
                route_span.set_attribute("aurumlab.intent", route.intent)
                route_span.set_attribute("aurumlab.skill", route.skill)
        if checkpoint is None:
            events.append(
                AgentEvent(
                    sequence=1,
                    stage="route_intent",
                    status="completed",
                    message=f"路由到 {route.intent}，置信度 {route.confidence:.2f}",
                    duration_ms=round((perf_counter() - route_started) * 1_000, 2),
                    details={
                        "skill": route.skill,
                        "action": route.action,
                        "reason": route.reason,
                        "scores": route.scores,
                        "threat_categories": [item.category for item in route.threat_signals],
                        "router": route.router,
                        "needs_clarification": route.needs_clarification,
                        "fallback_reason": route.fallback_reason,
                        "resumed": route_override is not None,
                    },
                )
            )

        if route.action == "deny":
            events.append(
                AgentEvent(
                    sequence=2,
                    stage="policy_reject",
                    status="warning",
                    message="治理策略已在 Tool 执行前拒绝请求",
                    duration_ms=0,
                )
            )
            response = RunResponse(
                run_id=run_id,
                status="rejected",
                skill=f"{route.skill}@0.1.0",
                interpreter=route.router,
                question=stored_question,
                route=route,
                execution_mode="rejected",
                requested_cache_policy=cache_policy,
                cache_status="bypass",
                cache=CacheInfo(status="bypass", reason="policy_rejected_before_cache_lookup"),
                summary="请求包含高风险指令，Agent 已在调用任何 Tool 前安全拒绝。",
                warnings=["未执行任何代码、SQL、Shell、外部请求或行情操作。"],
                events=events,
                created_at=created_at,
            )
            self._save_run(response)
            return response

        skill = self.skills.get(route.skill)
        try:
            await self._ensure_skill_tools(skill)
        except Exception as exc:  # noqa: BLE001 -- dependency discovery must fail closed
            events.append(
                AgentEvent(
                    sequence=len(events) + 1,
                    stage="select_skill",
                    status="failed",
                    message="Skill 依赖的 MCP Tool 未通过动态发现与兼容性门禁",
                    duration_ms=0,
                    details={"error_type": type(exc).__name__},
                )
            )
            response = RunResponse(
                run_id=run_id,
                status="failed",
                skill=f"{skill.name}@{skill.version}",
                interpreter="mcp-discovery-failed",
                question=stored_question,
                route=route,
                requested_cache_policy=cache_policy,
                summary="Agent 已安全停止：Skill 依赖的 MCP Tool 不可用。",
                warnings=["未执行任何领域 Tool，也未降级绕过 MCP Schema 门禁。"],
                events=events,
                created_at=created_at,
            )
            self._save_run(response)
            return response
        policy = ToolPolicy(
            policy_name=f"{skill.name}@{skill.version}",
            allowed_tools=set(skill.allowed_tools),
            max_calls=skill.max_tool_calls,
            audit_log=list(prior_audit or []),
            calls=sum(
                item.get("phase") == "authorization" and item.get("decision") == "allow"
                for item in prior_audit or []
            ),
        )
        if checkpoint is None:
            events.append(
                AgentEvent(
                    sequence=2,
                    stage="select_skill",
                    status="completed",
                    message=f"已加载 {skill.name}@{skill.version} Tool Policy",
                    duration_ms=0,
                    details={
                        "allowed_tools": skill.allowed_tools,
                        "max_tool_calls": skill.max_tool_calls,
                        "tool_risks": {
                            name: {
                                "effect": self.tools.get_definition(name).metadata.effect,
                                "risk": self.tools.get_definition(name).metadata.risk,
                                "requires_approval": self.tools.get_definition(
                                    name
                                ).metadata.requires_approval,
                            }
                            for name in skill.allowed_tools
                        },
                    },
                )
            )

        gateway: ToolGateway | None = None
        session: AgentExecutionSession | None = None
        try:
            plan_started = perf_counter()
            try:
                plan_scope = (
                    self.telemetry.span(
                        "aurumlab.agent.plan",
                        attributes={"aurumlab.skill": f"{skill.name}@{skill.version}"},
                    )
                    if self.telemetry is not None
                    else nullcontext()
                )
                with plan_scope as plan_span:
                    plan = self.planner.build(skill)
                    if plan_span is not None:
                        plan_span.set_attribute("aurumlab.plan.id", plan.plan_id)
                if expected_plan_id is not None and plan.plan_id != expected_plan_id:
                    raise ApprovalBindingError("plan changed after approval was requested")
                runtime = (
                    PlanRuntime.restore(plan, checkpoint.completed_steps)
                    if checkpoint is not None
                    else PlanRuntime(plan)
                )
                if checkpoint is None:
                    events.append(
                        AgentEvent(
                            sequence=len(events) + 1,
                            stage="build_plan",
                            status="completed",
                            message=(
                                f"已生成并校验 {len(plan.steps)} 步 Bounded Plan，"
                                f"计划调用 {plan.planned_tool_calls}/{plan.max_tool_calls} 个 Tool"
                            ),
                            duration_ms=round((perf_counter() - plan_started) * 1_000, 2),
                            details={
                                "plan_id": plan.plan_id,
                                "planner": plan.planner,
                                "steps": [step.step_id for step in plan.steps],
                                "planned_tool_calls": plan.planned_tool_calls,
                            },
                        )
                    )
            except Exception as exc:
                events.append(
                    AgentEvent(
                        sequence=len(events) + 1,
                        stage="build_plan",
                        status="failed",
                        message="Bounded Plan 生成或校验失败，拒绝进入 Executor",
                        duration_ms=round((perf_counter() - plan_started) * 1_000, 2),
                        details={"error_type": type(exc).__name__},
                    )
                )
                raise

            def approval_consumed(approval: ApprovalRequest) -> None:
                events.append(
                    AgentEvent(
                        sequence=len(events) + 1,
                        stage="approval_resume",
                        status="completed",
                        message="一次性人工审批已校验并原子消费，恢复 Tool 执行",
                        duration_ms=0,
                        details={
                            "approval_id": approval.approval_id,
                            "tool": approval.tool_name,
                            "risk": approval.risk,
                        },
                    )
                )

            gateway = ToolGateway(
                self.tools,
                policy,
                approvals=self.approvals,
                run_id=run_id,
                plan_id=plan.plan_id,
                approval_token=approval_token,
                trusted_consumed_approval=trusted_consumed_approval,
                on_approval_consumed=approval_consumed,
                telemetry=self.telemetry,
            )

            if checkpoint_job_id is not None:
                if checkpoint_sink is None or cancel_probe is None:
                    raise ValueError("Checkpoint execution requires sink and cancel probe")
                if checkpoint is not None:
                    session = AgentExecutionSession.from_checkpoint(
                        checkpoint,
                        question=question,
                        plan=plan,
                        sink=checkpoint_sink,
                        cancel_probe=cancel_probe,
                        audit_provider=lambda: policy.audit_log,
                        stage_timeout_seconds=stage_timeout_seconds,
                    )
                else:
                    session = AgentExecutionSession(
                        job_id=checkpoint_job_id,
                        run_id=run_id,
                        question=question,
                        route=route,
                        plan=plan,
                        created_at=created_at,
                        sink=checkpoint_sink,
                        cancel_probe=cancel_probe,
                        audit_provider=lambda: policy.audit_log,
                        stage_timeout_seconds=stage_timeout_seconds,
                    )

            workflows = {
                "backtest_strategy": self._run_backtest_workflow,
                "query_market_data": self._run_market_query_workflow,
                "external_research": self._run_external_research_workflow,
                "incident_review": self._run_incident_review_workflow,
                "other": self._run_general_workflow,
            }
            if route.intent == "backtest_strategy":
                payload = await self._run_backtest_workflow(
                    question,
                    events,
                    runtime,
                    gateway,
                    cache_policy,
                    session,
                    backtest_call_context,
                )
            else:
                payload = await workflows[route.intent](
                    question, events, runtime, gateway, cache_policy, session
                )
            cache_task = payload.pop("_cache_task", None)
            cache_ttl = payload.pop("_cache_ttl", None)
            cache_status = payload.get("cache_status", "bypass")
            plan = runtime.snapshot()
            response = RunResponse(
                run_id=run_id,
                status="completed",
                skill=f"{skill.name}@{skill.version}",
                question=stored_question,
                route=route,
                plan=plan,
                execution_mode=(
                    "direct"
                    if route.intent == "other"
                    else "cache"
                    if cache_status == "exact_hit"
                    else "tool_chain"
                ),
                requested_cache_policy=cache_policy,
                events=events,
                tool_audit=policy.audit_log,
                approval=gateway.consumed_approval,
                created_at=created_at,
                **payload,
            )
        except ApprovalRequired as exc:
            if runtime is not None:
                plan = runtime.snapshot()
            response = RunResponse(
                run_id=run_id,
                status="pending_approval",
                skill=f"{skill.name}@{skill.version}",
                interpreter="pending-approval",
                question=stored_question,
                route=route,
                plan=plan,
                execution_mode="approval",
                requested_cache_policy=cache_policy,
                cache_status="bypass",
                cache=CacheInfo(
                    status="bypass",
                    reason="approval_required_before_tool_execution",
                ),
                summary=(
                    f"Tool {exc.approval.tool_name} 需要人工审批；"
                    "当前 Run 已安全暂停，Tool 尚未执行且预算未扣减。"
                ),
                warnings=["审批凭证仅在批准时返回一次，且不能跨 Run、Plan 或参数使用。"],
                events=events,
                tool_audit=policy.audit_log,
                approval=exc.approval,
                created_at=created_at,
            )
        except (
            ApprovalTokenError,
            ApprovalStateError,
            ApprovalBindingError,
            AgentExecutionCancelled,
            AgentStageTimeout,
        ):
            raise
        except Exception as exc:  # noqa: BLE001 -- outer Agent boundary must fail closed
            if runtime is not None:
                plan = runtime.snapshot()
            response = RunResponse(
                run_id=run_id,
                status="failed",
                skill=f"{skill.name}@{skill.version}",
                interpreter="failed-before-artifact",
                question=stored_question,
                route=route,
                plan=plan,
                requested_cache_policy=cache_policy,
                summary=f"Agent 已安全停止：{exc}",
                warnings=["未运行用户提供的代码、SQL 或 Shell。"],
                events=events,
                tool_audit=policy.audit_log,
                created_at=created_at,
            )

        self._save_run(response)
        if cache_task is not None and response.cache_status in {
            "miss",
            "semantic_candidate",
            "refresh",
        }:
            try:
                self._store_artifact(cache_task, response, cache_ttl)
            except Exception as exc:  # noqa: BLE001 -- result remains valid when cache write fails
                response.warnings.append(f"Artifact Cache 写入失败：{type(exc).__name__}")
                self._save_run(response)
        return response

    def _save_run(self, response: RunResponse) -> None:
        scope = (
            self.telemetry.span(
                "aurumlab.store.run.save",
                attributes={"aurumlab.run.id": response.run_id},
            )
            if self.telemetry is not None
            else nullcontext()
        )
        with scope:
            self.runs.save(response)

    def _store_artifact(
        self,
        task: TaskFingerprint,
        response: RunResponse,
        ttl_seconds: int | None,
    ) -> None:
        scope = (
            self.telemetry.span(
                "aurumlab.store.artifact.save",
                attributes={"aurumlab.run.id": response.run_id},
            )
            if self.telemetry is not None
            else nullcontext()
        )
        with scope:
            self.artifacts.store(task, response, ttl_seconds=ttl_seconds)
