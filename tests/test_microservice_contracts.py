from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts import (
    AgentRunRequestedCommand,
    BacktestExecuteRequest,
    BacktestRunRequestedCommand,
    MessageMetadata,
)
from app.models import StrategySpec


def _strategy(**overrides) -> StrategySpec:
    values = {
        "symbol": "XAUUSD",
        "timeframe": "1d",
        "fast_window": 10,
        "slow_window": 30,
        "start_date": "2020-01-01",
        "end_date": "2021-01-01",
    }
    values.update(overrides)
    return StrategySpec.model_validate(values)


def _metadata() -> MessageMetadata:
    return MessageMetadata(
        message_id=uuid4(),
        correlation_id="correlation-0001",
        producer="control-plane",
        occurred_at=datetime.now(UTC),
        traceparent=f"00-{'1' * 32}-{'2' * 16}-01",
    )


def _request(**overrides) -> BacktestExecuteRequest:
    values = {
        "request_id": "request-00000001",
        "job_id": "job-00000001",
        "run_id": "run-00000001",
        "idempotency_key": "idempotency-key-00000001",
        "strategy": _strategy(),
    }
    values.update(overrides)
    return BacktestExecuteRequest.model_validate(values)


def test_contracts_are_strict_and_language_neutral():
    request = _request()
    command = BacktestRunRequestedCommand(metadata=_metadata(), payload=request)

    payload = command.model_dump(mode="json")
    assert payload["event_type"] == "backtest.run.requested.v1"
    assert payload["payload"]["schema_version"] == "backtest-request-v1"
    assert payload["metadata"]["producer"] == "control-plane"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BacktestExecuteRequest.model_validate({**request.model_dump(), "reply_topic": "attacker"})


@pytest.mark.parametrize(
    "strategy",
    [
        StrategySpec(fast_window=10, slow_window=30),
        _strategy(timeframe="1h", start_date="2018-01-01", end_date="2025-01-01"),
    ],
)
def test_backtest_contract_rejects_unbounded_time_ranges(strategy):
    with pytest.raises(ValidationError, match="require start_date|range exceeds"):
        _request(strategy=strategy)


def test_backtest_contract_caps_deadline_and_trace_context():
    requested_at = datetime.now(UTC)
    with pytest.raises(ValidationError, match="five-minute"):
        _request(
            requested_at=requested_at,
            deadline_at=requested_at + timedelta(minutes=6),
        )
    with pytest.raises(ValidationError, match="String should match pattern"):
        MessageMetadata(
            message_id=uuid4(),
            correlation_id="correlation-0001",
            producer="control-plane",
            occurred_at=requested_at,
            traceparent="forged-trace-context",
        )


def test_agent_command_rejects_unknown_or_oversized_input():
    valid = {
        "metadata": _metadata(),
        "job_id": "job-00000001",
        "run_id": "run-00000001",
        "idempotency_key": "idempotency-key-00000001",
        "question": "请回测黄金10日和30日均线策略",
    }
    command = AgentRunRequestedCommand.model_validate(valid)
    assert command.event_type == "agent.run.requested.v1"

    with pytest.raises(ValidationError):
        AgentRunRequestedCommand.model_validate({**valid, "question": "x" * 1_001})
    with pytest.raises(ValidationError):
        AgentRunRequestedCommand.model_validate({**valid, "callback_url": "http://127.0.0.1"})


def test_command_rejects_a_spoofed_producer():
    metadata = _metadata().model_copy(update={"producer": "backtest-service"})
    with pytest.raises(ValidationError, match="control plane"):
        AgentRunRequestedCommand(
            metadata=metadata,
            job_id="job-00000001",
            run_id="run-00000001",
            idempotency_key="idempotency-key-00000001",
            question="请回测黄金10日和30日均线策略",
        )
