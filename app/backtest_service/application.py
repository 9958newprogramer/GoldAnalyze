"""Application layer for bounded, deterministic backtest execution."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from time import perf_counter

from app.contracts import BacktestExecuteRequest, BacktestExecuteResponse
from app.domain.backtest import run_sma_crossover
from app.domain.market_data import MarketDataRepository, profile_bars


class BacktestApplicationError(RuntimeError):
    code = "BACKTEST_EXECUTION_FAILED"
    retryable = False


class DataVersionConflict(BacktestApplicationError):
    code = "DATA_VERSION_CONFLICT"


class WorkloadLimitExceeded(BacktestApplicationError):
    code = "WORKLOAD_LIMIT_EXCEEDED"


class ExecutionDeadlineExceeded(BacktestApplicationError):
    code = "EXECUTION_DEADLINE_EXCEEDED"
    retryable = True


def _result_digest(response_fields: dict[str, object]) -> str:
    payload = json.dumps(
        response_fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class BacktestApplication:
    """Pure application service; transport and persistence stay outside this layer."""

    def __init__(self, repository: MarketDataRepository):
        self.repository = repository

    def execute(self, request: BacktestExecuteRequest) -> BacktestExecuteResponse:
        started = perf_counter()
        now = datetime.now(UTC)
        if request.deadline_at is not None and now >= request.deadline_at:
            raise ExecutionDeadlineExceeded("request deadline elapsed before execution")

        data_version = self.repository.data_version(request.strategy)
        if (
            request.expected_data_version is not None
            and request.expected_data_version != data_version
        ):
            raise DataVersionConflict("configured market data does not match expected version")

        bars = self.repository.load(request.strategy)
        if len(bars) > request.max_bars:
            raise WorkloadLimitExceeded("market data exceeds the requested bar limit")

        profile = profile_bars(self.repository, request.strategy, bars)
        result = run_sma_crossover(request.strategy, bars)
        if len(result.trades) > request.max_trades:
            raise WorkloadLimitExceeded("backtest result exceeds the requested trade limit")

        digest_fields: dict[str, object] = {
            "engine_version": "sma-crossover-v1",
            "data_version": data_version,
            "strategy": request.strategy.model_dump(mode="json"),
            "data_profile": profile.model_dump(mode="json"),
            "metrics": result.metrics.model_dump(mode="json"),
            "trades": [item.model_dump(mode="json") for item in result.trades],
            "equity_curve": [item.model_dump(mode="json") for item in result.equity_curve],
        }
        return BacktestExecuteResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            run_id=request.run_id,
            data_version=data_version,
            strategy=request.strategy,
            data_profile=profile,
            metrics=result.metrics,
            trades=result.trades,
            equity_curve=result.equity_curve,
            warnings=profile.warnings,
            result_digest=_result_digest(digest_fields),
            duration_ms=round((perf_counter() - started) * 1_000, 2),
        )
