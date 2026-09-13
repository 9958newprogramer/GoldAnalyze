"""Small, deterministic performance metric calculations."""

from __future__ import annotations

from collections.abc import Sequence

from app.backtest_core.models import EquityPoint, PerformanceMetrics, Trade


def calculate_metrics(
    *,
    initial_cash: float,
    equity_curve: Sequence[EquityPoint],
    trades: Sequence[Trade],
) -> PerformanceMetrics:
    if not equity_curve:
        raise ValueError("equity_curve cannot be empty")

    final_equity = equity_curve[-1].equity
    peak = equity_curve[0].equity
    max_drawdown = 0.0
    for point in equity_curve:
        peak = max(peak, point.equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - point.equity) / peak)

    winners = sum(trade.pnl > 0 for trade in trades)
    return PerformanceMetrics(
        total_return_pct=(final_equity / initial_cash - 1) * 100,
        max_drawdown_pct=max_drawdown * 100,
        win_rate_pct=(winners / len(trades) * 100) if trades else 0.0,
        trade_count=len(trades),
    )
