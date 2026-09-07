"""Multi-Skill Agent orchestrator with routing, governance, and typed artifacts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from app.agent.interpreter import OpenAICompatibleStrategyInterpreter
from app.agent.planner import BoundedPlanner, PlanRuntime
from app.agent.router import IntentRouter
from app.agent.task_compiler import compile_external_research, compile_market_query
from app.domain.backtest import BacktestResult, run_sma_crossover
from app.domain.market_data import Bar, MarketDataRepository, profile_bars
from app.memory import ArtifactCache, CacheLookup, TaskFingerprint, build_task_fingerprint
from app.models import (
    AgentEvent,
    ArtifactSnapshot,
    CacheInfo,
    DataProfile,
    ExecutionPlan,
    ExternalResearchResult,
    ExternalResearchSpec,
    MarketBarView,
    MarketQueryResult,
    MarketQuerySpec,
    ResearchSource,
    RunResponse,
    StrategySpec,
)
from app.skills.registry import SkillRegistry
from app.storage import RunRepository
from app.tools.registry import ToolGateway, ToolPolicy, ToolRegistry
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


class AurumAgent:
    def __init__(
        self,
        interpreter: OpenAICompatibleStrategyInterpreter,
        market_repository: MarketDataRepository,
        search_provider: SearchProvider,
        skills: SkillRegistry,
        runs: RunRepository,
        artifacts: ArtifactCache,
        router: IntentRouter | None = None,
        planner: BoundedPlanner | None = None,
        market_cache_ttl_seconds: int = 300,
        research_cache_ttl_seconds: int = 900,
    ):
        self.interpreter = interpreter
        self.market_repository = market_repository
        self.search_provider = search_provider
        self.skills = skills
        self.runs = runs
        self.artifacts = artifacts
        self.router = router or IntentRouter()
        self.planner = planner or BoundedPlanner()
        self.market_cache_ttl_seconds = market_cache_ttl_seconds
        self.research_cache_ttl_seconds = research_cache_ttl_seconds
        self.tools = ToolRegistry()
        self.tools.register("inspect_market_data", self._inspect_market_data)
        self.tools.register("validate_strategy_spec", self._validate_strategy_spec)
        self.tools.register("run_backtest", self._run_backtest)
        self.tools.register("summarize_result", self._summarize_result)
        self.tools.register("query_market_data", self._query_market_data)
        self.tools.register("summarize_market_query", self._summarize_market_query)
        self.tools.register("search_external_knowledge", self._search_external_knowledge)
        self.tools.register("summarize_external_research", self._summarize_external_research)
        self.tools.register("compose_general_response", self._compose_general_response)

    async def _inspect_market_data(self, spec: StrategySpec) -> dict[str, Any]:
        bars = self.market_repository.load(spec)
        profile = profile_bars(self.market_repository, spec, bars)
        return {"bars": bars, "profile": profile}

    async def _validate_strategy_spec(
        self,
        spec: StrategySpec,
        bars: list[Bar],
        profile: DataProfile,
    ) -> list[str]:
        if profile.duplicate_timestamps or profile.non_positive_prices:
            raise ValueError("行情数据未通过硬性质量检查")
        if len(bars) < spec.slow_window + 5:
            raise ValueError("行情数据不足以完成指标预热")
        return [
            "信号在当前 K 线收盘后确认，统一在下一根 K 线开盘成交，避免同 K 线未来函数。",
            *profile.warnings,
        ]

    async def _run_backtest(self, spec: StrategySpec, bars: list[Bar]) -> BacktestResult:
        return run_sma_crossover(spec, bars)

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
            "当前 Agent 支持三类可执行任务：策略回测、本地黄金行情查询和外部知识检索。"
            "这个问题未触发领域工具；你可以直接描述目标、周期和参数。"
        )

    @staticmethod
    async def _timed_event(
        events: list[AgentEvent],
        runtime: PlanRuntime,
        stage: str,
        message: str,
        action: Callable[[], Awaitable[Any]],
        tool_name: str | None = None,
    ) -> Any:
        runtime.begin(stage, tool_name)
        started = perf_counter()
        try:
            output = await action()
            runtime.complete(stage)
            events.append(
                AgentEvent(
                    sequence=len(events) + 1,
                    stage=stage,
                    status="completed",
                    message=message,
                    duration_ms=round((perf_counter() - started) * 1_000, 2),
                )
            )
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
            raise

    @staticmethod
    def _timed_sync_event(
        events: list[AgentEvent],
        runtime: PlanRuntime,
        stage: str,
        message: str,
        action: Callable[[], Any],
    ) -> Any:
        runtime.begin(stage)
        started = perf_counter()
        try:
            output = action()
            runtime.complete(stage)
            events.append(
                AgentEvent(
                    sequence=len(events) + 1,
                    stage=stage,
                    status="completed",
                    message=message,
                    duration_ms=round((perf_counter() - started) * 1_000, 2),
                )
            )
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
            raise

    def _lookup_cache(
        self,
        events: list[AgentEvent],
        runtime: PlanRuntime,
        task: TaskFingerprint,
        cache_policy: Literal["use", "refresh", "bypass"],
    ) -> CacheLookup:
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
            events.append(
                AgentEvent(
                    sequence=len(events) + 1,
                    stage="cache_lookup",
                    status="completed",
                    message=messages[lookup.info.status],
                    duration_ms=round((perf_counter() - started) * 1_000, 2),
                    details=lookup.info.model_dump(mode="json", exclude_none=True),
                )
            )
            runtime.complete("cache_lookup")
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
    ) -> dict[str, Any]:
        interpretation = await self._timed_event(
            events,
            runtime,
            "interpret_strategy",
            "已将用户问题编译为受约束的 StrategySpec",
            lambda: self.interpreter.interpret(question),
        )
        task = build_task_fingerprint(
            "backtest_strategy",
            interpretation.spec,
            self.market_repository.data_version(interpretation.spec),
        )
        lookup = self._lookup_cache(events, runtime, task, cache_policy)
        if lookup.info.status == "exact_hit" and lookup.source is not None:
            return self._cached_payload(lookup.source, lookup.info)
        inspected = await self._timed_event(
            events,
            runtime,
            "inspect_market_data",
            "已通过受治理 Tool 读取并检查行情数据",
            lambda: gateway.call(
                "inspect_market_data",
                memory_key=f"{task.fingerprint}:inspect_market_data",
                spec=interpretation.spec,
            ),
            tool_name="inspect_market_data",
        )
        bars: list[Bar] = inspected["bars"]
        profile: DataProfile = inspected["profile"]
        validation_warnings = await self._timed_event(
            events,
            runtime,
            "validate_strategy_spec",
            "已验证参数边界、数据充分性和下一根 K 线成交约束",
            lambda: gateway.call(
                "validate_strategy_spec",
                spec=interpretation.spec,
                bars=bars,
                profile=profile,
            ),
            tool_name="validate_strategy_spec",
        )
        result: BacktestResult = await self._timed_event(
            events,
            runtime,
            "run_backtest",
            "确定性回测工具执行完成",
            lambda: gateway.call(
                "run_backtest",
                memory_key=f"{task.fingerprint}:run_backtest",
                spec=interpretation.spec,
                bars=bars,
            ),
            tool_name="run_backtest",
        )
        summary = await self._timed_event(
            events,
            runtime,
            "summarize_result",
            "已根据结构化实验产物生成反馈",
            lambda: gateway.call("summarize_result", result=result, profile=profile),
            tool_name="summarize_result",
        )
        return {
            "interpreter": interpretation.interpreter,
            "strategy": interpretation.spec,
            "data_profile": profile,
            "metrics": result.metrics,
            "trades": result.trades,
            "equity_curve": result.equity_curve,
            "summary": summary,
            "warnings": [*interpretation.warnings, *validation_warnings],
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
    ) -> dict[str, Any]:
        query = self._timed_sync_event(
            events,
            runtime,
            "compile_market_query",
            "已将问题编译为受约束的 MarketQuerySpec",
            lambda: compile_market_query(question),
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
        lookup = self._lookup_cache(events, runtime, task, cache_policy)
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
    ) -> dict[str, Any]:
        spec = self._timed_sync_event(
            events,
            runtime,
            "compile_research_query",
            "已生成有界的 ExternalResearchSpec",
            lambda: compile_external_research(question),
        )
        task = build_task_fingerprint(
            "external_research",
            spec,
            f"provider-v1:{self.search_provider.name}",
        )
        lookup = self._lookup_cache(events, runtime, task, cache_policy)
        if lookup.info.status == "exact_hit" and lookup.source is not None:
            return self._cached_payload(lookup.source, lookup.info)
        searched = await self._timed_event(
            events,
            runtime,
            "search_external_knowledge",
            "已调用受治理的外部 Search Provider",
            lambda: gateway.call("search_external_knowledge", spec=spec),
            tool_name="search_external_knowledge",
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
    ) -> dict[str, Any]:
        summary = await self._timed_event(
            events,
            runtime,
            "compose_general_response",
            "已生成能力边界内的直接反馈",
            lambda: gateway.call("compose_general_response", question=question),
            tool_name="compose_general_response",
        )
        return {
            "interpreter": "direct",
            "summary": summary,
            "warnings": [],
            "cache_status": "bypass",
            "cache": CacheInfo(status="bypass", reason="general_response_is_not_cached"),
        }

    async def run(
        self,
        question: str,
        *,
        cache_policy: Literal["use", "refresh", "bypass"] = "use",
    ) -> RunResponse:
        run_id = uuid4().hex[:12]
        created_at = datetime.now(UTC)
        events: list[AgentEvent] = []
        cache_task: TaskFingerprint | None = None
        cache_ttl: int | None = None
        plan: ExecutionPlan | None = None
        runtime: PlanRuntime | None = None
        route_started = perf_counter()
        route = await self.router.route(question)
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
                question=question,
                route=route,
                execution_mode="rejected",
                cache_status="bypass",
                cache=CacheInfo(status="bypass", reason="policy_rejected_before_cache_lookup"),
                summary="请求包含高风险指令，Agent 已在调用任何 Tool 前安全拒绝。",
                warnings=["未执行任何代码、SQL、Shell、外部请求或行情操作。"],
                events=events,
                created_at=created_at,
            )
            self.runs.save(response)
            return response

        skill = self.skills.get(route.skill)
        policy = ToolPolicy(
            policy_name=f"{skill.name}@{skill.version}",
            allowed_tools=set(skill.allowed_tools),
            max_calls=skill.max_tool_calls,
        )
        gateway = ToolGateway(self.tools, policy)
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
                },
            )
        )

        try:
            plan_started = perf_counter()
            try:
                plan = self.planner.build(skill)
                runtime = PlanRuntime(plan)
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

            workflows = {
                "backtest_strategy": self._run_backtest_workflow,
                "query_market_data": self._run_market_query_workflow,
                "external_research": self._run_external_research_workflow,
                "other": self._run_general_workflow,
            }
            payload = await workflows[route.intent](
                question, events, runtime, gateway, cache_policy
            )
            cache_task = payload.pop("_cache_task", None)
            cache_ttl = payload.pop("_cache_ttl", None)
            cache_status = payload.get("cache_status", "bypass")
            plan = runtime.snapshot()
            response = RunResponse(
                run_id=run_id,
                status="completed",
                skill=f"{skill.name}@{skill.version}",
                question=question,
                route=route,
                plan=plan,
                execution_mode=(
                    "direct"
                    if route.intent == "other"
                    else "cache"
                    if cache_status == "exact_hit"
                    else "tool_chain"
                ),
                events=events,
                tool_audit=policy.audit_log,
                created_at=created_at,
                **payload,
            )
        except Exception as exc:  # noqa: BLE001 -- outer Agent boundary must fail closed
            if runtime is not None:
                plan = runtime.snapshot()
            response = RunResponse(
                run_id=run_id,
                status="failed",
                skill=f"{skill.name}@{skill.version}",
                interpreter="failed-before-artifact",
                question=question,
                route=route,
                plan=plan,
                summary=f"Agent 已安全停止：{exc}",
                warnings=["未运行用户提供的代码、SQL 或 Shell。"],
                events=events,
                tool_audit=policy.audit_log,
                created_at=created_at,
            )

        self.runs.save(response)
        if cache_task is not None and response.cache_status in {
            "miss",
            "semantic_candidate",
            "refresh",
        }:
            try:
                self.artifacts.store(cache_task, response, ttl_seconds=cache_ttl)
            except Exception as exc:  # noqa: BLE001 -- result remains valid when cache write fails
                response.warnings.append(f"Artifact Cache 写入失败：{type(exc).__name__}")
                self.runs.save(response)
        return response
