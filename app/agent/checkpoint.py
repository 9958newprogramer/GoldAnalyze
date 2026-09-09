"""Safe JSON codecs and lifecycle helpers for deterministic Agent checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.agent.interpreter import Interpretation
from app.agent.planner import PlanRuntime
from app.backtest_service.port import BacktestExecution
from app.domain.backtest import BacktestResult
from app.domain.market_data import Bar
from app.memory import CacheLookup
from app.models import (
    AgentCheckpoint,
    AgentEvent,
    CacheInfo,
    DataProfile,
    ExecutionPlan,
    ExternalResearchResult,
    ExternalResearchSpec,
    IncidentActionItem,
    IncidentAssessment,
    IncidentReviewResult,
    IncidentSpec,
    IntentDecision,
    MarketQueryResult,
    MarketQuerySpec,
    ResearchSource,
)


class AgentExecutionCancelled(RuntimeError):
    """Cooperative cancellation observed at a deterministic step boundary."""


class AgentStageTimeout(TimeoutError):
    def __init__(self, stage: str):
        super().__init__(f"stage timed out: {stage}")
        self.stage = stage


def question_fingerprint(question: str) -> str:
    return hashlib.sha256(question.encode()).hexdigest()


def encode_step_output(stage: str, output: Any) -> Any:
    if stage == "interpret_strategy":
        return {
            "spec": output.spec.model_dump(mode="json"),
            "interpreter": output.interpreter,
            "warnings": output.warnings,
        }
    if stage == "cache_lookup":
        return {
            "info": output.info.model_dump(mode="json"),
            "source": output.source.model_dump(mode="json") if output.source else None,
        }
    if stage == "inspect_market_data":
        return {
            "bars": [
                {
                    "at": bar.at.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in output["bars"]
            ],
            "profile": output["profile"].model_dump(mode="json"),
        }
    if stage == "run_backtest":
        return {
            "metrics": output.metrics.model_dump(mode="json"),
            "trades": [item.model_dump(mode="json") for item in output.trades],
            "equity_curve": [item.model_dump(mode="json") for item in output.equity_curve],
        }
    if stage == "execute_backtest":
        return {
            "data_version": output.data_version,
            "profile": output.profile.model_dump(mode="json"),
            "metrics": output.result.metrics.model_dump(mode="json"),
            "trades": [item.model_dump(mode="json") for item in output.result.trades],
            "equity_curve": [item.model_dump(mode="json") for item in output.result.equity_curve],
            "warnings": output.warnings,
            "result_digest": output.result_digest,
        }
    if stage == "compile_market_query":
        return output.model_dump(mode="json")
    if stage == "query_market_data":
        return {
            "result": output["result"].model_dump(mode="json"),
            "profile": output["profile"].model_dump(mode="json"),
        }
    if stage == "compile_research_query":
        return output.model_dump(mode="json")
    if stage == "search_external_knowledge":
        return {
            "sources": [item.model_dump(mode="json") for item in output["sources"]],
            "warning": output["warning"],
        }
    if stage == "summarize_external_research":
        return output.model_dump(mode="json")
    if stage in {"compile_incident_spec", "assess_incident_impact", "summarize_incident_review"}:
        return output.model_dump(mode="json")
    if stage == "build_incident_action_plan":
        return [item.model_dump(mode="json") for item in output]
    if stage == "mcp__runtime__profile_text":
        return output
    if stage in {
        "validate_strategy_spec",
        "resolve_backtest_data_version",
        "summarize_result",
        "summarize_market_query",
        "compose_general_response",
        "validate_incident_spec",
    }:
        return output
    raise ValueError(f"Checkpoint 不支持未知步骤输出：{stage}")


def decode_step_output(stage: str, value: Any) -> Any:
    from app.models import ArtifactSnapshot, BacktestMetrics, EquityPoint, StrategySpec, Trade

    if stage == "interpret_strategy":
        return Interpretation(
            spec=StrategySpec.model_validate(value["spec"]),
            interpreter=value["interpreter"],
            warnings=list(value["warnings"]),
        )
    if stage == "cache_lookup":
        return CacheLookup(
            info=CacheInfo.model_validate(value["info"]),
            source=(ArtifactSnapshot.model_validate(value["source"]) if value["source"] else None),
        )
    if stage == "inspect_market_data":
        return {
            "bars": [
                Bar(
                    at=datetime.fromisoformat(item["at"]),
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=float(item["volume"]),
                )
                for item in value["bars"]
            ],
            "profile": DataProfile.model_validate(value["profile"]),
        }
    if stage == "run_backtest":
        return BacktestResult(
            metrics=BacktestMetrics.model_validate(value["metrics"]),
            trades=[Trade.model_validate(item) for item in value["trades"]],
            equity_curve=[EquityPoint.model_validate(item) for item in value["equity_curve"]],
        )
    if stage == "execute_backtest":
        return BacktestExecution(
            data_version=value["data_version"],
            profile=DataProfile.model_validate(value["profile"]),
            result=BacktestResult(
                metrics=BacktestMetrics.model_validate(value["metrics"]),
                trades=[Trade.model_validate(item) for item in value["trades"]],
                equity_curve=[EquityPoint.model_validate(item) for item in value["equity_curve"]],
            ),
            warnings=list(value["warnings"]),
            result_digest=value["result_digest"],
        )
    if stage == "compile_market_query":
        return MarketQuerySpec.model_validate(value)
    if stage == "query_market_data":
        return {
            "result": MarketQueryResult.model_validate(value["result"]),
            "profile": DataProfile.model_validate(value["profile"]),
        }
    if stage == "compile_research_query":
        return ExternalResearchSpec.model_validate(value)
    if stage == "search_external_knowledge":
        return {
            "sources": [ResearchSource.model_validate(item) for item in value["sources"]],
            "warning": value["warning"],
        }
    if stage == "summarize_external_research":
        return ExternalResearchResult.model_validate(value)
    if stage == "compile_incident_spec":
        return IncidentSpec.model_validate(value)
    if stage == "assess_incident_impact":
        return IncidentAssessment.model_validate(value)
    if stage == "build_incident_action_plan":
        return [IncidentActionItem.model_validate(item) for item in value]
    if stage == "summarize_incident_review":
        return IncidentReviewResult.model_validate(value)
    if stage == "mcp__runtime__profile_text":
        return dict(value)
    if stage == "validate_strategy_spec":
        return list(value)
    if stage == "validate_incident_spec":
        return list(value)
    if stage in {
        "summarize_result",
        "summarize_market_query",
        "compose_general_response",
    }:
        return str(value)
    raise ValueError(f"Checkpoint 包含未知步骤输出：{stage}")


CheckpointSink = Callable[[AgentCheckpoint], None]
CancelProbe = Callable[[], bool]
AuditProvider = Callable[[], list[dict[str, Any]]]


@dataclass
class AgentExecutionSession:
    job_id: str
    run_id: str
    question: str
    route: IntentDecision
    plan: ExecutionPlan
    created_at: datetime
    sink: CheckpointSink
    cancel_probe: CancelProbe
    audit_provider: AuditProvider
    stage_timeout_seconds: float
    outputs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: AgentCheckpoint,
        *,
        question: str,
        plan: ExecutionPlan,
        sink: CheckpointSink,
        cancel_probe: CancelProbe,
        audit_provider: AuditProvider,
        stage_timeout_seconds: float,
    ) -> AgentExecutionSession:
        if checkpoint.question_fingerprint != question_fingerprint(question):
            raise ValueError("Checkpoint 与当前问题不匹配")
        if checkpoint.plan_id != plan.plan_id:
            raise ValueError("Checkpoint 与当前 Plan 版本不匹配")
        return cls(
            job_id=checkpoint.job_id,
            run_id=checkpoint.run_id,
            question=question,
            route=checkpoint.route,
            plan=plan,
            created_at=checkpoint.created_at,
            sink=sink,
            cancel_probe=cancel_probe,
            audit_provider=audit_provider,
            stage_timeout_seconds=stage_timeout_seconds,
            outputs=dict(checkpoint.step_outputs),
        )

    def restore(self, stage: str) -> tuple[bool, Any]:
        if stage not in self.outputs:
            return False, None
        return True, decode_step_output(stage, self.outputs[stage])

    def before_step(self) -> None:
        if self.cancel_probe():
            raise AgentExecutionCancelled("job cancellation requested")

    async def bounded(self, stage: str, action: Callable[[], Any]) -> Any:
        self.before_step()
        try:
            async with asyncio.timeout(self.stage_timeout_seconds):
                return await action()
        except TimeoutError as exc:
            raise AgentStageTimeout(stage) from exc

    def persist(
        self,
        stage: str,
        output: Any,
        runtime: PlanRuntime,
        events: list[AgentEvent],
    ) -> None:
        self.outputs[stage] = encode_step_output(stage, output)
        now = datetime.now(UTC)
        self.sink(
            AgentCheckpoint(
                job_id=self.job_id,
                run_id=self.run_id,
                question_fingerprint=question_fingerprint(self.question),
                route=self.route,
                plan_id=self.plan.plan_id,
                completed_steps=runtime.snapshot().completed_steps,
                step_outputs=dict(self.outputs),
                events=list(events),
                tool_audit=list(self.audit_provider()),
                created_at=self.created_at,
                updated_at=now,
            )
        )
