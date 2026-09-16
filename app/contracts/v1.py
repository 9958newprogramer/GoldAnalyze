"""Language-neutral v1 contracts for the GoldAnalyze service boundary.

These models are intentionally separate from persistence models. The Java control
plane owns durable state; Python receives bounded commands and returns artifacts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator

from app.models import (
    AgentEvent,
    ApprovalRequest,
    BacktestMetrics,
    CacheInfo,
    DataProfile,
    EquityPoint,
    ExecutionPlan,
    ExternalResearchResult,
    ExternalResearchSpec,
    IncidentReviewResult,
    IncidentSpec,
    IntentDecision,
    MarketQueryResult,
    MarketQuerySpec,
    RunResponse,
    StrategySpec,
    Trade,
)

OpaqueId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=16, max_length=128),
]
TraceParent = Annotated[
    str,
    StringConstraints(pattern=r"^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$"),
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MessageMetadata(StrictContract):
    """Metadata carried in Kafka headers or a message envelope.

    Topic names and reply destinations are deployment configuration, never caller
    input. This prevents an untrusted request from turning into an SSRF-like broker
    routing primitive.
    """

    schema_version: Literal["message-metadata-v1"] = "message-metadata-v1"
    message_id: UUID
    correlation_id: OpaqueId
    causation_id: OpaqueId | None = None
    producer: Literal["control-plane", "agent-service", "backtest-service"]
    occurred_at: datetime
    traceparent: TraceParent | None = None
    tracestate: str | None = Field(default=None, max_length=512)
    attempt: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def require_utc_timestamp(self) -> MessageMetadata:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return self


class BacktestPlanRequest(StrictContract):
    """Java 请求 Python 为一个父任务生成批量回测子任务。"""

    schema_version: Literal["backtest-plan-request-v1"] = "backtest-plan-request-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    symbol: str = Field(min_length=1, max_length=32)
    start_date: date
    end_date: date
    interval_days: int = Field(default=30, ge=1, le=365)
    lookback_days: int = Field(default=60, ge=1, le=365)
    forward_days: int = Field(default=60, ge=1, le=365)

    @model_validator(mode="after")
    def validate_date_range(self) -> BacktestPlanRequest:
        """确保规划时间范围合法。"""
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be later than start_date")
        return self


class BacktestSubtaskPlan(StrictContract):
    """Python Planner 返回给 Java 的单个逻辑回测子任务。"""

    subtask_id: OpaqueId
    symbol: str = Field(min_length=1, max_length=32)
    anchor_date: date
    start_date: date
    end_date: date


class BacktestPlanResponse(StrictContract):
    """Python Planner 为一个父任务生成的完整子任务计划。"""

    schema_version: Literal["backtest-plan-result-v1"] = "backtest-plan-result-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    total: int = Field(ge=0, le=100_000)
    subtasks: list[BacktestSubtaskPlan] = Field(
        default_factory=list,
        max_length=100_000,
    )


class BacktestExecuteRequest(StrictContract):
    """Bounded deterministic calculation request from the control plane/Agent."""

    schema_version: Literal["backtest-request-v1"] = "backtest-request-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    run_id: OpaqueId
    idempotency_key: IdempotencyKey
    strategy: StrategySpec
    expected_data_version: str | None = Field(default=None, min_length=3, max_length=128)
    max_bars: int = Field(default=250_000, ge=100, le=500_000)
    max_trades: int = Field(default=5_000, ge=1, le=10_000)
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deadline_at: datetime | None = None

    @model_validator(mode="after")
    def require_bounded_time_range(self) -> BacktestExecuteRequest:
        start = self.strategy.start_date
        end = self.strategy.end_date
        if start is None or end is None:
            raise ValueError("microservice backtests require start_date and end_date")
        maximum = timedelta(days=366 * (5 if self.strategy.timeframe == "1h" else 20))
        if (
            datetime.combine(end, datetime.min.time())
            - datetime.combine(start, datetime.min.time())
            > maximum
        ):
            raise ValueError("requested backtest range exceeds the timeframe limit")
        if self.requested_at.tzinfo is None:
            raise ValueError("requested_at must include a timezone")
        if self.requested_at > datetime.now(UTC) + timedelta(minutes=1):
            raise ValueError("requested_at cannot be in the future")
        if self.deadline_at is not None:
            if self.deadline_at.tzinfo is None:
                raise ValueError("deadline_at must include a timezone")
            if self.deadline_at <= self.requested_at:
                raise ValueError("deadline_at must be later than requested_at")
            if self.deadline_at - self.requested_at > timedelta(minutes=5):
                raise ValueError("deadline_at cannot exceed the five-minute execution budget")
        return self


class BacktestDataVersionRequest(StrictContract):
    """Small preflight request used to build a cache-safe task fingerprint."""

    schema_version: Literal["backtest-data-version-request-v1"] = "backtest-data-version-request-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    run_id: OpaqueId
    strategy: StrategySpec


class BacktestDataVersionResponse(StrictContract):
    schema_version: Literal["backtest-data-version-result-v1"] = "backtest-data-version-result-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    run_id: OpaqueId
    engine_version: Literal["sma-crossover-v1"] = "sma-crossover-v1"
    data_version: str = Field(min_length=3, max_length=128)


class BacktestExecuteResponse(StrictContract):
    schema_version: Literal["backtest-result-v1"] = "backtest-result-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    run_id: OpaqueId
    status: Literal["completed"] = "completed"
    engine_version: Literal["sma-crossover-v1"] = "sma-crossover-v1"
    data_version: str = Field(min_length=3, max_length=128)
    strategy: StrategySpec
    data_profile: DataProfile
    metrics: BacktestMetrics
    trades: list[Trade] = Field(default_factory=list, max_length=10_000)
    equity_curve: list[EquityPoint] = Field(default_factory=list, max_length=321)
    warnings: list[str] = Field(default_factory=list, max_length=32)
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_ms: float = Field(ge=0)


class EventCondition(StrictContract):
    """One return condition evaluated relative to an event anchor trading day."""

    offset: int = Field(ge=-30, le=0)
    field: Literal["return_pct"] = "return_pct"
    operator: Literal["gt", "gte", "lt", "lte", "between"]
    value: FiniteFloat | None = None
    min: FiniteFloat | None = None
    max: FiniteFloat | None = None

    @model_validator(mode="after")
    def validate_operator_arguments(self) -> EventCondition:
        """Require exactly the operands used by the selected operator."""

        if self.operator == "between":
            if self.min is None or self.max is None:
                raise ValueError("between requires min and max")
            if self.min > self.max:
                raise ValueError("between requires min <= max")
            if self.value is not None:
                raise ValueError("between does not allow value")
        else:
            if self.value is None:
                raise ValueError(f"{self.operator} requires value")
            if self.min is not None or self.max is not None:
                raise ValueError(f"{self.operator} does not allow min or max")
        return self


class EventStudyRequest(StrictContract):
    """Bounded daily XAUUSD historical-event study request."""

    schema_version: Literal["event-study-request-v1"] = "event-study-request-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    event_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    symbol: Literal["XAUUSD"] = "XAUUSD"
    timeframe: Literal["1d"] = "1d"
    start_date: date
    end_date: date
    conditions: list[EventCondition] = Field(min_length=1, max_length=64)
    forward_days: list[Annotated[int, Field(strict=True, gt=0)]] = Field(
        min_length=1,
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_event_study(self) -> EventStudyRequest:
        """Validate date, anchor-condition, and horizon invariants."""

        if self.end_date <= self.start_date:
            raise ValueError("end_date must be later than start_date")
        if not any(condition.offset == 0 for condition in self.conditions):
            raise ValueError("conditions must include at least one offset=0 condition")
        if len(self.forward_days) != len(set(self.forward_days)):
            raise ValueError("forward_days must not contain duplicates")
        return self


class EventStudyEvent(StrictContract):
    """One matched event and its trading-day forward returns."""

    event_date: date
    event_return_pct: FiniteFloat
    forward_returns: dict[str, FiniteFloat | None]


class EventHorizonStatistics(StrictContract):
    """Aggregate performance for one forward trading-day horizon."""

    sample_count: int = Field(ge=0)
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    positive_rate_pct: FiniteFloat
    average_return_pct: FiniteFloat | None
    median_return_pct: FiniteFloat | None
    min_return_pct: FiniteFloat | None
    max_return_pct: FiniteFloat | None


class EventStudyResponse(StrictContract):
    """Deterministic event details, horizon statistics, and source identity."""

    schema_version: Literal["event-study-result-v1"] = "event-study-result-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    event_name: str = Field(min_length=1, max_length=128)
    symbol: Literal["XAUUSD"] = "XAUUSD"
    timeframe: Literal["1d"] = "1d"
    start_date: date
    end_date: date
    event_count: int = Field(ge=0)
    events: list[EventStudyEvent]
    statistics: dict[str, EventHorizonStatistics]
    data_profile: DataProfile
    data_version: str = Field(min_length=3, max_length=128)

    @model_validator(mode="after")
    def validate_result_counts(self) -> EventStudyResponse:
        """Keep the declared event count consistent with event details."""

        if self.event_count != len(self.events):
            raise ValueError("event_count must equal the number of events")
        return self


class ProblemDetails(StrictContract):
    """Stable RFC 7807-style error body; internal exception text is excluded."""

    type: str = Field(default="about:blank", max_length=256)
    title: str = Field(min_length=1, max_length=120)
    status: int = Field(ge=400, le=599)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    detail: str = Field(min_length=1, max_length=300)
    request_id: OpaqueId | None = None
    trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    retryable: bool = False


class ServiceHealth(StrictContract):
    schema_version: Literal["service-health-v1"] = "service-health-v1"
    service: Literal["agent-service", "backtest-service"]
    status: Literal["ok", "degraded"]
    version: str = Field(min_length=1, max_length=32)
    dependencies: dict[str, Literal["ok", "degraded", "unavailable"]] = Field(default_factory=dict)


class AgentExecuteRequest(StrictContract):
    """Synchronous internal request from Java to the Python Agent Service."""

    schema_version: Literal["agent-execute-request-v1"] = "agent-execute-request-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    run_id: OpaqueId
    idempotency_key: IdempotencyKey
    question: str = Field(min_length=4, max_length=1_000)
    cache_policy: Literal["use", "refresh", "bypass"] = "use"
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deadline_at: datetime | None = None

    @model_validator(mode="after")
    def require_bounded_deadline(self) -> AgentExecuteRequest:
        if self.requested_at.tzinfo is None:
            raise ValueError("requested_at must include a timezone")
        if self.requested_at > datetime.now(UTC) + timedelta(minutes=1):
            raise ValueError("requested_at cannot be in the future")
        if self.deadline_at is not None:
            if self.deadline_at.tzinfo is None:
                raise ValueError("deadline_at must include a timezone")
            if self.deadline_at <= self.requested_at:
                raise ValueError("deadline_at must be later than requested_at")
            if self.deadline_at - self.requested_at > timedelta(minutes=5):
                raise ValueError("deadline_at cannot exceed the five-minute execution budget")
        return self


class AgentRunRequestedCommand(StrictContract):
    schema_version: Literal["agent-run-requested-v1"] = "agent-run-requested-v1"
    event_type: Literal["agent.run.requested.v1"] = "agent.run.requested.v1"
    metadata: MessageMetadata
    job_id: OpaqueId
    run_id: OpaqueId
    idempotency_key: IdempotencyKey
    question: str = Field(min_length=4, max_length=1_000)
    cache_policy: Literal["use", "refresh", "bypass"] = "use"

    @model_validator(mode="after")
    def require_control_plane_producer(self) -> AgentRunRequestedCommand:
        if self.metadata.producer != "control-plane":
            raise ValueError("agent commands must be produced by the control plane")
        return self


class BacktestRunRequestedCommand(StrictContract):
    schema_version: Literal["backtest-run-requested-v1"] = "backtest-run-requested-v1"
    event_type: Literal["backtest.run.requested.v1"] = "backtest.run.requested.v1"
    metadata: MessageMetadata
    payload: BacktestExecuteRequest

    @model_validator(mode="after")
    def require_authorized_producer(self) -> BacktestRunRequestedCommand:
        if self.metadata.producer not in {"control-plane", "agent-service"}:
            raise ValueError("backtest commands require an authorized producer")
        return self


class AgentResultArtifact(StrictContract):
    """Control-plane projection that deliberately omits the original question."""

    status: Literal["completed", "failed", "rejected", "pending_approval"]
    skill: str = Field(min_length=1, max_length=80)
    interpreter: str = Field(min_length=1, max_length=80)
    route: IntentDecision | None = None
    plan: ExecutionPlan | None = None
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
    trades: list[Trade] = Field(default_factory=list, max_length=10_000)
    equity_curve: list[EquityPoint] = Field(default_factory=list, max_length=321)
    summary: str = Field(max_length=4_000)
    warnings: list[str] = Field(default_factory=list, max_length=32)
    events: list[AgentEvent] = Field(default_factory=list, max_length=64)
    tool_audit: list[dict[str, object]] = Field(default_factory=list, max_length=128)
    approval: ApprovalRequest | None = None

    @classmethod
    def from_run(cls, run: RunResponse) -> AgentResultArtifact:
        return cls(
            status=run.status,
            skill=run.skill,
            interpreter=run.interpreter,
            route=run.route,
            plan=run.plan,
            cache=run.cache,
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
            events=run.events,
            tool_audit=run.tool_audit,
            approval=run.approval,
        )


class AgentExecuteResponse(StrictContract):
    schema_version: Literal["agent-execute-result-v1"] = "agent-execute-result-v1"
    request_id: OpaqueId
    job_id: OpaqueId
    run_id: OpaqueId
    execution_id: OpaqueId
    artifact: AgentResultArtifact
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_ms: float = Field(ge=0)


class AgentRunResultEvent(StrictContract):
    schema_version: Literal["agent-run-result-v1"] = "agent-run-result-v1"
    event_type: Literal[
        "agent.run.completed.v1",
        "agent.run.rejected.v1",
        "agent.run.approval-required.v1",
        "agent.run.failed.v1",
    ]
    metadata: MessageMetadata
    job_id: OpaqueId
    run_id: OpaqueId
    artifact: AgentResultArtifact | None = None
    error: ProblemDetails | None = None

    @model_validator(mode="after")
    def require_exactly_one_outcome(self) -> AgentRunResultEvent:
        if (self.artifact is None) == (self.error is None):
            raise ValueError("exactly one of artifact or error is required")
        if self.metadata.producer != "agent-service":
            raise ValueError("agent events must be produced by the agent service")
        if self.event_type == "agent.run.failed.v1" and self.error is None:
            raise ValueError("failed agent events require an error")
        if self.event_type != "agent.run.failed.v1" and self.artifact is None:
            raise ValueError("non-failed agent events require an artifact")
        return self


class BacktestRunResultEvent(StrictContract):
    schema_version: Literal["backtest-run-result-v1"] = "backtest-run-result-v1"
    event_type: Literal["backtest.run.completed.v1", "backtest.run.failed.v1"]
    metadata: MessageMetadata
    job_id: OpaqueId
    run_id: OpaqueId
    result: BacktestExecuteResponse | None = None
    error: ProblemDetails | None = None

    @model_validator(mode="after")
    def require_exactly_one_outcome(self) -> BacktestRunResultEvent:
        if (self.result is None) == (self.error is None):
            raise ValueError("exactly one of result or error is required")
        if self.metadata.producer != "backtest-service":
            raise ValueError("backtest events must be produced by the backtest service")
        if self.event_type == "backtest.run.failed.v1" and self.error is None:
            raise ValueError("failed backtest events require an error")
        if self.event_type == "backtest.run.completed.v1" and self.result is None:
            raise ValueError("completed backtest events require a result")
        return self
