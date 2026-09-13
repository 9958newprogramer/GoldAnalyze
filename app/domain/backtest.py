"""Compatibility adapter from application contracts to the pure backtest core."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, pstdev

from app.backtest_core import (
    BacktestConfig,
    BacktestEngine,
    Kline,
    MovingAverageCrossStrategy,
)
from app.domain.market_data import Bar
from app.models import BacktestMetrics, EquityPoint, StrategySpec, Trade


@dataclass(frozen=True)
class BacktestResult:
    """Stable application-facing result used by Agent and HTTP adapters."""

    metrics: BacktestMetrics
    trades: list[Trade]
    equity_curve: list[EquityPoint]


def _downsample(points: list[EquityPoint], maximum: int = 320) -> list[EquityPoint]:
    if len(points) <= maximum:
        return points
    step = max(1, math.ceil(len(points) / maximum))
    sampled = points[::step]
    if sampled[-1].at != points[-1].at:
        sampled.append(points[-1])
    return sampled


def _sharpe_ratio(equities: list[float], timeframe: str) -> float:
    returns = [
        equities[index] / equities[index - 1] - 1
        for index in range(1, len(equities))
        if equities[index - 1]
    ]
    deviation = pstdev(returns) if len(returns) >= 2 else 0.0
    periods_per_year = 252 if timeframe == "1d" else 252 * 24
    return fmean(returns) / deviation * math.sqrt(periods_per_year) if deviation else 0.0


def run_sma_crossover(spec: StrategySpec, bars: list[Bar]) -> BacktestResult:
    """Run the legacy SMA contract through the modular database-free engine."""

    if len(bars) < spec.slow_window + 5:
        raise ValueError(f"至少需要 {spec.slow_window + 5} 条 K 线才能运行策略")

    core_result = BacktestEngine().run(
        [
            Kline(
                symbol=spec.symbol,
                trade_date=bar.at,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in bars
        ],
        MovingAverageCrossStrategy(
            fast_window=spec.fast_window,
            slow_window=spec.slow_window,
        ),
        BacktestConfig(
            initial_cash=spec.initial_cash,
            fee_bps=spec.fee_bps,
            slippage_bps=spec.slippage_bps,
        ),
    )

    trades = [
        Trade(
            entry_at=trade.entry_at,
            exit_at=trade.exit_at,
            entry_price=round(trade.entry_price, 4),
            exit_price=round(trade.exit_price, 4),
            quantity=round(trade.quantity, 6),
            pnl=round(trade.pnl, 2),
            return_pct=round(trade.return_pct, 4),
            exit_reason=("cross_down" if trade.exit_reason == "signal" else trade.exit_reason),
        )
        for trade in core_result.trades
    ]
    equity_curve = [
        EquityPoint(at=point.at, equity=round(point.equity, 2))
        for point in core_result.equity_curve
    ]
    equities = [point.equity for point in equity_curve]
    metrics = BacktestMetrics(
        total_return_pct=round(core_result.metrics.total_return_pct, 2),
        # Keep the established public contract: drawdown is a negative return.
        max_drawdown_pct=round(-core_result.metrics.max_drawdown_pct, 2),
        sharpe_ratio=round(_sharpe_ratio(equities, spec.timeframe), 2),
        trade_count=core_result.metrics.trade_count,
        win_rate_pct=round(core_result.metrics.win_rate_pct, 2),
        final_equity=round(core_result.final_equity, 2),
    )
    return BacktestResult(
        metrics=metrics,
        trades=trades,
        equity_curve=_downsample(equity_curve),
    )
