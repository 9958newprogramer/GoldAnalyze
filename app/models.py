"""Typed contracts shared by the API, agent, tools, and MCP server."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=4, max_length=1_000)
    cache_policy: Literal["use", "refresh", "bypass"] = "use"


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    timeframe: Literal["1d", "1h"] = "1d"
    strategy_type: Literal["sma_crossover"] = "sma_crossover"
    fast_window: int = Field(default=20, ge=2, le=200)
    slow_window: int = Field(default=60, ge=3, le=400)
    start_date: date | None = None
    end_date: date | None = None
    initial_cash: float = Field(default=100_000.0, gt=0, le=100_000_000)
    fee_bps: float = Field(default=2.0, ge=0, le=100)
    slippage_bps: float = Field(default=3.0, ge=0, le=100)
    execution: Literal["next_bar_open"] = "next_bar_open"
    long_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_relationships(self) -> StrategySpec:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window 必须小于 slow_window")
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValueError("start_date 必须早于 end_date")
        return self


class MarketQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    timeframe: Literal["1d", "1h"] = "1d"
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ExternalResearchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=4, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)


class DataProfile(BaseModel):
    source: str
    synthetic: bool
    symbol: str
    timeframe: str
    row_count: int
    start_at: datetime
    end_at: datetime
    duplicate_timestamps: int = 0
    non_positive_prices: int = 0
    warnings: list[str] = Field(default_factory=list)


class MarketBarView(BaseModel):
    at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketQueryResult(BaseModel):
    symbol: str
    timeframe: str
    row_count: int
    returned_count: int
    start_at: datetime
    end_at: datetime
    period_high: float
    period_low: float
    change_pct: float
    average_volume: float
    bars: list[MarketBarView]


class ResearchSource(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(pattern=r"^https?://", max_length=2_000)
    snippet: str = Field(default="", max_length=2_000)
    published_at: str | None = Field(default=None, max_length=100)


class ExternalResearchResult(BaseModel):
    query: str
    provider: str
    answer: str
    sources: list[ResearchSource] = Field(default_factory=list)
    freshness: str


class BacktestMetrics(BaseModel):
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trade_count: int
    win_rate_pct: float
    final_equity: float


class Trade(BaseModel):
    entry_at: datetime
    exit_at: datetime
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    exit_reason: str


class EquityPoint(BaseModel):
    at: datetime
    equity: float


class AgentEvent(BaseModel):
    sequence: int
    stage: str
    status: Literal["completed", "warning", "failed"]
    message: str
    duration_ms: float
    details: dict[str, Any] = Field(default_factory=dict)


class ThreatSignal(BaseModel):
    category: str
    confidence: float = Field(ge=0, le=1)
    evidence: str


class IntentClassification(BaseModel):
    """Untrusted semantic classification returned by the router model."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["backtest_strategy", "query_market_data", "external_research", "other"]
    reason: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False


class IntentDecision(BaseModel):
    intent: Literal["backtest_strategy", "query_market_data", "external_research", "other"]
    skill: Literal[
        "backtest-strategy",
        "query-market-data",
        "external-research",
        "general-response",
    ]
    action: Literal["allow", "deny"] = "allow"
    confidence: float = Field(ge=0, le=1)
    reason: str
    router: str = "governed-llm-router@0.2.0"
    scores: dict[str, float] = Field(default_factory=dict)
    threat_signals: list[ThreatSignal] = Field(default_factory=list)
    needs_clarification: bool = False
    fallback_reason: str | None = Field(default=None, max_length=100)


class CacheInfo(BaseModel):
    """Observable provenance for request-local and persistent artifact reuse."""

    status: Literal["miss", "exact_hit", "semantic_candidate", "refresh", "bypass"]
    fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{16}$")
    data_version: str | None = Field(default=None, max_length=80)
    source_run_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{12}$")
    similarity_score: float | None = Field(default=None, ge=0, le=1)
    saved_tool_calls: int = Field(default=0, ge=0)
    saved_latency_ms: float = Field(default=0, ge=0)
    expires_at: datetime | None = None
    reason: str = Field(default="", max_length=160)


class CacheStats(BaseModel):
    enabled: bool
    max_entries: int = Field(ge=1)
    entries: int = Field(ge=0)
    active_entries: int = Field(ge=0)
    expired_entries: int = Field(ge=0)
    exact_hits: int = Field(ge=0)


class ArtifactSnapshot(BaseModel):
    """Minimal reusable payload; excludes the original question, route, and audit details."""

    model_config = ConfigDict(extra="ignore")

    interpreter: str
    strategy: StrategySpec | None = None
    market_query: MarketQuerySpec | None = None
    market_result: MarketQueryResult | None = None
    research_spec: ExternalResearchSpec | None = None
    research_result: ExternalResearchResult | None = None
    data_profile: DataProfile | None = None
    metrics: BacktestMetrics | None = None
    trades: list[Trade] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    summary: str
    warnings: list[str] = Field(default_factory=list)
    source_tool_calls: int = Field(default=0, ge=0)
    source_tool_latency_ms: float = Field(default=0, ge=0)

    @classmethod
    def from_run(cls, run: RunResponse) -> ArtifactSnapshot:
        executions = [item for item in run.tool_audit if item.get("phase") == "execution"]
        return cls(
            interpreter=run.interpreter,
            strategy=run.strategy,
            market_query=run.market_query,
            market_result=run.market_result,
            research_spec=run.research_spec,
            research_result=run.research_result,
            data_profile=run.data_profile,
            metrics=run.metrics,
            trades=run.trades,
            equity_curve=run.equity_curve,
            summary=run.summary,
            warnings=run.warnings,
            source_tool_calls=len(executions),
            source_tool_latency_ms=round(
                sum(float(item.get("duration_ms", 0)) for item in executions), 2
            ),
        )


class RunResponse(BaseModel):
    run_id: str
    status: Literal["completed", "failed", "rejected"]
    skill: str
    interpreter: str
    question: str
    route: IntentDecision | None = None
    execution_mode: Literal["tool_chain", "cache", "direct", "rejected"] = "tool_chain"
    cache_status: Literal["miss", "exact_hit", "semantic_candidate", "refresh", "bypass"] = "bypass"
    cache: CacheInfo | None = None
    strategy: StrategySpec | None = None
    market_query: MarketQuerySpec | None = None
    market_result: MarketQueryResult | None = None
    research_spec: ExternalResearchSpec | None = None
    research_result: ExternalResearchResult | None = None
    data_profile: DataProfile | None = None
    metrics: BacktestMetrics | None = None
    trades: list[Trade] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    summary: str
    warnings: list[str] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    tool_audit: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class SkillDescriptor(BaseModel):
    name: str
    version: str
    description: str
    allowed_tools: list[str]
    max_tool_calls: int = Field(ge=1, le=50)
    steps: list[str]


class SkillListResponse(BaseModel):
    skills: list[SkillDescriptor]
