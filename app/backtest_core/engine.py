"""Deterministic, database-free backtest execution engine."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from app.backtest_core.metrics import calculate_metrics
from app.backtest_core.models import BacktestResult, EquityPoint, Kline, Portfolio, Signal
from app.backtest_core.strategy import Strategy


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    force_close: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be a positive finite number")
        for name, value in (("fee_bps", self.fee_bps), ("slippage_bps", self.slippage_bps)):
            if not math.isfinite(value) or not 0 <= value <= 10_000:
                raise ValueError(f"{name} must be between 0 and 10000")


class BacktestEngine:
    """Stateless engine suitable for reuse across threads or worker processes."""

    def run(
        self,
        klines: Sequence[Kline],
        strategy: Strategy,
        config: BacktestConfig | None = None,
    ) -> BacktestResult:
        bars = tuple(klines)
        settings = config or BacktestConfig()
        self._validate_input(bars, strategy)
        signals = tuple(strategy.generate_signals(bars))
        if len(signals) != len(bars):
            raise ValueError("strategy must return exactly one signal per kline")
        if any(not isinstance(signal, Signal) for signal in signals):
            raise ValueError("strategy returned an unsupported signal")

        symbol = bars[0].symbol
        fee_rate = settings.fee_bps / 10_000
        slippage_rate = settings.slippage_bps / 10_000
        portfolio = Portfolio(settings.initial_cash)
        trades = []
        equity_curve = []

        for index, bar in enumerate(bars):
            # Signal i-1 is produced only after that bar closes and therefore
            # executes no earlier than bar i open.
            pending_signal = signals[index - 1] if index > 0 else Signal.HOLD
            if pending_signal is Signal.BUY and not portfolio.has_position:
                portfolio.open_long(
                    symbol=symbol,
                    at=bar.timestamp,
                    price=bar.open * (1 + slippage_rate),
                    fee_rate=fee_rate,
                )
            elif pending_signal is Signal.SELL and portfolio.has_position:
                trades.append(
                    portfolio.close_long(
                        at=bar.timestamp,
                        price=bar.open * (1 - slippage_rate),
                        fee_rate=fee_rate,
                        reason="signal",
                    )
                )
            equity_curve.append(EquityPoint(at=bar.timestamp, equity=portfolio.equity(bar.close)))

        if settings.force_close and portfolio.has_position:
            last = bars[-1]
            trades.append(
                portfolio.close_long(
                    at=last.timestamp,
                    price=last.close * (1 - slippage_rate),
                    fee_rate=fee_rate,
                    reason="end_of_data",
                )
            )
            equity_curve[-1] = EquityPoint(at=last.timestamp, equity=portfolio.cash)

        metrics = calculate_metrics(
            initial_cash=settings.initial_cash,
            equity_curve=equity_curve,
            trades=trades,
        )
        return BacktestResult(
            symbol=symbol,
            initial_cash=settings.initial_cash,
            final_equity=equity_curve[-1].equity,
            metrics=metrics,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
        )

    @staticmethod
    def _validate_input(klines: tuple[Kline, ...], strategy: Strategy) -> None:
        if not klines:
            raise ValueError("at least one kline is required")
        if len(klines) <= strategy.warmup_period:
            raise ValueError(
                f"at least {strategy.warmup_period + 1} klines are required for this strategy"
            )
        symbol = klines[0].symbol
        timestamps = [bar.timestamp for bar in klines]
        if any(bar.symbol != symbol for bar in klines):
            raise ValueError("all klines in one run must have the same symbol")
        if any(current <= previous for previous, current in pairwise(timestamps)):
            raise ValueError("klines must be strictly ordered with unique timestamps")
