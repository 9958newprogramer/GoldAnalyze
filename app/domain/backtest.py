"""Small deterministic backtest engine used as an Agent tool, not as project focus."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, pstdev

from app.domain.market_data import Bar
from app.models import BacktestMetrics, EquityPoint, StrategySpec, Trade


@dataclass(frozen=True)
class BacktestResult:
    metrics: BacktestMetrics
    trades: list[Trade]
    equity_curve: list[EquityPoint]


def _rolling_mean(values: list[float], window: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    running_sum = 0.0
    for index, value in enumerate(values):
        running_sum += value
        if index >= window:
            running_sum -= values[index - window]
        if index >= window - 1:
            output[index] = running_sum / window
    return output


def _downsample(points: list[EquityPoint], maximum: int = 320) -> list[EquityPoint]:
    if len(points) <= maximum:
        return points
    step = max(1, math.ceil(len(points) / maximum))
    sampled = points[::step]
    if sampled[-1].at != points[-1].at:
        sampled.append(points[-1])
    return sampled


def run_sma_crossover(spec: StrategySpec, bars: list[Bar]) -> BacktestResult:
    if len(bars) < spec.slow_window + 5:
        raise ValueError(f"至少需要 {spec.slow_window + 5} 条 K 线才能运行策略")

    closes = [bar.close for bar in bars]
    fast = _rolling_mean(closes, spec.fast_window)
    slow = _rolling_mean(closes, spec.slow_window)
    cash = spec.initial_cash
    quantity = 0.0
    entry_at = None
    entry_price = 0.0
    equity_points: list[EquityPoint] = []
    trades: list[Trade] = []

    for index, bar in enumerate(bars):
        # A signal observed at bar i-1 can only execute at bar i open.
        signal_index = index - 1
        prior_index = index - 2
        can_signal = (
            prior_index >= 0
            and fast[prior_index] is not None
            and slow[prior_index] is not None
            and fast[signal_index] is not None
            and slow[signal_index] is not None
        )

        if can_signal:
            crossed_up = (
                fast[prior_index] <= slow[prior_index]  # type: ignore[operator]
                and fast[signal_index] > slow[signal_index]  # type: ignore[operator]
            )
            crossed_down = (
                fast[prior_index] >= slow[prior_index]  # type: ignore[operator]
                and fast[signal_index] < slow[signal_index]  # type: ignore[operator]
            )

            if crossed_up and quantity == 0:
                fill_price = bar.open * (1 + spec.slippage_bps / 10_000)
                quantity = cash / (fill_price * (1 + spec.fee_bps / 10_000))
                cash -= quantity * fill_price * (1 + spec.fee_bps / 10_000)
                entry_at = bar.at
                entry_price = fill_price
            elif crossed_down and quantity > 0 and entry_at is not None:
                fill_price = bar.open * (1 - spec.slippage_bps / 10_000)
                proceeds = quantity * fill_price * (1 - spec.fee_bps / 10_000)
                invested = quantity * entry_price * (1 + spec.fee_bps / 10_000)
                pnl = proceeds - invested
                trades.append(
                    Trade(
                        entry_at=entry_at,
                        exit_at=bar.at,
                        entry_price=round(entry_price, 4),
                        exit_price=round(fill_price, 4),
                        quantity=round(quantity, 6),
                        pnl=round(pnl, 2),
                        return_pct=round((proceeds / invested - 1) * 100, 4),
                        exit_reason="cross_down",
                    )
                )
                cash = proceeds
                quantity = 0.0
                entry_at = None

        equity_points.append(EquityPoint(at=bar.at, equity=round(cash + quantity * bar.close, 2)))

    if quantity > 0 and entry_at is not None:
        last = bars[-1]
        fill_price = last.close * (1 - spec.slippage_bps / 10_000)
        proceeds = quantity * fill_price * (1 - spec.fee_bps / 10_000)
        invested = quantity * entry_price * (1 + spec.fee_bps / 10_000)
        pnl = proceeds - invested
        trades.append(
            Trade(
                entry_at=entry_at,
                exit_at=last.at,
                entry_price=round(entry_price, 4),
                exit_price=round(fill_price, 4),
                quantity=round(quantity, 6),
                pnl=round(pnl, 2),
                return_pct=round((proceeds / invested - 1) * 100, 4),
                exit_reason="end_of_data",
            )
        )
        cash = proceeds
        equity_points[-1] = EquityPoint(at=last.at, equity=round(cash, 2))

    equities = [point.equity for point in equity_points]
    peak = equities[0]
    max_drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - 1)

    returns = [
        equities[i] / equities[i - 1] - 1 for i in range(1, len(equities)) if equities[i - 1]
    ]
    periods_per_year = 252 if spec.timeframe == "1d" else 252 * 24
    deviation = pstdev(returns) if len(returns) >= 2 else 0.0
    sharpe = fmean(returns) / deviation * math.sqrt(periods_per_year) if deviation else 0.0
    winners = sum(1 for trade in trades if trade.pnl > 0)
    final_equity = equities[-1]

    metrics = BacktestMetrics(
        total_return_pct=round((final_equity / spec.initial_cash - 1) * 100, 2),
        max_drawdown_pct=round(max_drawdown * 100, 2),
        sharpe_ratio=round(sharpe, 2),
        trade_count=len(trades),
        win_rate_pct=round(winners / len(trades) * 100, 2) if trades else 0.0,
        final_equity=round(final_equity, 2),
    )
    return BacktestResult(metrics=metrics, trades=trades, equity_curve=_downsample(equity_points))
