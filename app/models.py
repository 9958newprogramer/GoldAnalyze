"""Typed contracts shared by the API, agent, tools, and MCP server."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=4, max_length=1_000)
    cache_policy: Literal["use", "refresh", "bypass"] = "use"


JobStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "dead_letter",
]


class JobCreateRequest(RunRequest):
    """Bounded payload accepted by the asynchronous control plane."""

    max_attempts: int = Field(default=2, ge=1, le=3)


class AgentJob(BaseModel):
    """Durable job state; Redis messages contain only ``job_id``."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    status: JobStatus
    question: str = Field(min_length=4, max_length=1_000)
    cache_policy: Literal["use", "refresh", "bypass"] = "use"
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    attempts: int = Field(default=0, ge=0, le=3)
    max_attempts: int = Field(default=2, ge=1, le=3)
    run_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{12}$")
    last_event_id: int = Field(default=0, ge=0)
    cancel_requested: bool = False
    error_code: str | None = Field(default=None, max_length=80)
    error_message: str | None = Field(default=None, max_length=300)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobEvent(BaseModel):
    """Append-only event whose per-job id is also the SSE cursor."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    event_id: int = Field(ge=1)
    event_type: Literal[
        "job_queued",
        "job_started",
        "step_completed",
        "approval_required",
        "approval_resumed",
        "retry_scheduled",
        "cancel_requested",
        "job_completed",
        "job_failed",
        "job_cancelled",
        "job_timed_out",
        "dead_lettered",
        "checkpoint_restored",
    ]
    status: JobStatus
    message: str = Field(min_length=1, max_length=300)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentCheckpoint(BaseModel):
    """Trusted, versioned execution snapshot written after each completed Plan step."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent-checkpoint-v1"] = "agent-checkpoint-v1"
    job_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    run_id: str = Field(pattern=r"^[a-f0-9]{12}$")
    question_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    route: IntentDecision
    plan_id: str = Field(pattern=r"^[a-f0-9]{12}$")
    completed_steps: list[str] = Field(default_factory=list, max_length=32)
    step_outputs: dict[str, Any] = Field(default_factory=dict)
    events: list[AgentEvent] = Field(default_factory=list, max_length=64)
    tool_audit: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    created_at: datetime
    updated_at: datetime


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


class IncidentSpec(BaseModel):
    """Bounded facts compiled from an incident-review request."""

    model_config = ConfigDict(extra="forbid")

    service: str = Field(default="unknown-service", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    error_rate_pct: float | None = Field(default=None, ge=0, le=100)
    p95_latency_ms: float | None = Field(default=None, ge=0, le=120_000)
    duration_minutes: int | None = Field(default=None, ge=1, le=10_080)
    affected_requests: int | None = Field(default=None, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def require_measurable_evidence(self) -> IncidentSpec:
        signals = (
            self.error_rate_pct,
            self.p95_latency_ms,
            self.duration_minutes,
            self.affected_requests,
        )
        if all(value is None for value in signals):
            raise ValueError("事故复盘至少需要一项可量化证据")
        return self


class IncidentAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["SEV-1", "SEV-2", "SEV-3", "SEV-4"]
    risk_score: int = Field(ge=0, le=100)
    root_cause_status: Literal["unverified"] = "unverified"
    findings: list[str] = Field(min_length=1, max_length=8)


class IncidentInputProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: int = Field(ge=0, le=2_000)
    words: int = Field(ge=0, le=1_000)
    lines: int = Field(ge=1, le=1_000)
    contains_code_fence: bool


class IncidentActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(pattern=r"^ACT-[1-9][0-9]?$", max_length=6)
    priority: Literal["P0", "P1", "P2"]
    owner_role: Literal["on-call", "service-owner", "platform"]
    category: Literal["mitigate", "observe", "prevent"]
    action: str = Field(min_length=4, max_length=200)


class IncidentReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    severity: Literal["SEV-1", "SEV-2", "SEV-3", "SEV-4"]
    risk_score: int = Field(ge=0, le=100)
    root_cause_status: Literal["unverified"] = "unverified"
    input_profile: IncidentInputProfile
    findings: list[str] = Field(min_length=1, max_length=8)
    action_items: list[IncidentActionItem] = Field(min_length=1, max_length=8)
    summary: str = Field(min_length=10, max_length=1_000)


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
    status: Literal["completed", "warning", "failed", "waiting_approval"]
    message: str
    duration_ms: float
    details: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    """One bounded executor step derived from a versioned Skill manifest."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    order: int = Field(ge=1, le=32)
    kind: Literal["control", "tool"]
    tool_name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    depends_on: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_tool_binding(self) -> PlanStep:
        if self.kind == "tool" and self.tool_name is None:
            raise ValueError("tool step 必须绑定 tool_name")
        if self.kind == "control" and self.tool_name is not None:
            raise ValueError("control step 不能绑定 tool_name")
        return self


class ExecutionPlan(BaseModel):
    """Validated plan plus an observable deterministic-execution outcome."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(pattern=r"^[a-f0-9]{12}$")
    skill: str
    skill_version: str
    planner: str
    steps: list[PlanStep] = Field(min_length=1, max_length=32)
    max_tool_calls: int = Field(ge=1, le=50)
    planned_tool_calls: int = Field(ge=0, le=50)
    validated: bool = False
    completed_steps: list[str] = Field(default_factory=list)
    skipped_steps: list[str] = Field(default_factory=list)
    failed_step: str | None = None
    paused_step: str | None = None
    rejected_step: str | None = None


class ApprovalRequest(BaseModel):
    """Public, token-free representation of one human approval decision."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    run_id: str = Field(pattern=r"^[a-f0-9]{12}$")
    plan_id: str = Field(pattern=r"^[a-f0-9]{12}$")
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    tool_name: str = Field(min_length=2, max_length=128)
    arguments_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    effect: Literal["read", "external", "write", "privileged"]
    risk: Literal["low", "medium", "high"]
    status: Literal["pending", "approved", "denied", "expired", "consumed"]
    reason: str = Field(min_length=1, max_length=300)
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    consumed_at: datetime | None = None
    decided_by: str | None = Field(default=None, max_length=80)


class ApprovalGrant(BaseModel):
    """One-time bearer credential returned only by an explicit approval action."""

    model_config = ConfigDict(extra="forbid")

    approval: ApprovalRequest
    approval_token: str = Field(min_length=50, max_length=200, repr=False)


class ResumeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_token: str = Field(min_length=50, max_length=200, repr=False)


class ThreatSignal(BaseModel):
    category: str
    confidence: float = Field(ge=0, le=1)
    evidence: str


class IntentClassification(BaseModel):
    """Untrusted semantic classification returned by the router model."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "backtest_strategy",
        "query_market_data",
        "external_research",
        "incident_review",
        "other",
    ]
    reason: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False


class IntentDecision(BaseModel):
    intent: Literal[
        "backtest_strategy",
        "query_market_data",
        "external_research",
        "incident_review",
        "other",
    ]
    skill: Literal[
        "backtest-strategy",
        "query-market-data",
        "external-research",
        "incident-review",
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
    incident_spec: IncidentSpec | None = None
    incident_result: IncidentReviewResult | None = None
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
        executions = [
            item
            for item in run.tool_audit
            if item.get("phase") == "execution"
            # A cache hit must still resolve the immutable data version. Only
            # count work that reuse actually avoids.
            and item.get("tool") != "resolve_backtest_data_version"
        ]
        return cls(
            interpreter=run.interpreter,
            strategy=run.strategy,
            market_query=run.market_query,
            market_result=run.market_result,
            research_spec=run.research_spec,
            research_result=run.research_result,
            incident_spec=run.incident_spec,
            incident_result=run.incident_result,
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
    status: Literal["completed", "failed", "rejected", "pending_approval"]
    skill: str
    interpreter: str
    question: str
    route: IntentDecision | None = None
    plan: ExecutionPlan | None = None
    execution_mode: Literal["tool_chain", "cache", "direct", "rejected", "approval"] = "tool_chain"
    requested_cache_policy: Literal["use", "refresh", "bypass"] = "use"
    cache_status: Literal["miss", "exact_hit", "semantic_candidate", "refresh", "bypass"] = "bypass"
    cache: CacheInfo | None = None
    strategy: StrategySpec | None = None
    market_query: MarketQuerySpec | None = None
    market_result: MarketQueryResult | None = None
    research_spec: ExternalResearchSpec | None = None
    research_result: ExternalResearchResult | None = None
    incident_spec: IncidentSpec | None = None
    incident_result: IncidentReviewResult | None = None
    data_profile: DataProfile | None = None
    metrics: BacktestMetrics | None = None
    trades: list[Trade] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    summary: str
    warnings: list[str] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    tool_audit: list[dict[str, Any]] = Field(default_factory=list)
    approval: ApprovalRequest | None = None
    created_at: datetime


class SkillDescriptor(BaseModel):
    name: str
    version: str
    description: str
    allowed_tools: list[str] = Field(min_length=1, max_length=50)
    max_tool_calls: int = Field(ge=1, le=50)
    steps: list[str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_unique_capabilities(self) -> SkillDescriptor:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("Skill allowed_tools 不能重复")
        if len(self.steps) != len(set(self.steps)):
            raise ValueError("Skill steps 不能重复")
        return self


class SkillListResponse(BaseModel):
    skills: list[SkillDescriptor]
