"""Strategy contracts and a deliberately small moving-average demo."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from app.backtest_core.models import Kline, Signal


class Strategy(ABC):
    """Pure strategy boundary.

    Strategies inspect immutable bars and emit signals only. They never access a
    database or mutate the portfolio, which keeps execution reproducible and safe
    to run in independent worker processes.
    """

    @property
    @abstractmethod
    def warmup_period(self) -> int:
        """Number of bars required before the strategy may emit a signal."""

    @abstractmethod
    def generate_signals(self, klines: Sequence[Kline]) -> Sequence[Signal]:
        """Return exactly one signal for every input bar."""


def _rolling_mean(values: Sequence[float], window: int) -> list[float | None]:
    means: list[float | None] = [None] * len(values)
    running_sum = 0.0
    for index, value in enumerate(values):
        running_sum += value
        if index >= window:
            running_sum -= values[index - window]
        if index >= window - 1:
            means[index] = running_sum / window
    return means


@dataclass(frozen=True, slots=True)
class MovingAverageCrossStrategy(Strategy):
    """Long on fast-MA cross-up, flat on cross-down."""

    fast_window: int = 20
    slow_window: int = 60

    def __post_init__(self) -> None:
        if self.fast_window < 2:
            raise ValueError("fast_window must be at least 2")
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window")

    @property
    def warmup_period(self) -> int:
        return self.slow_window

    def generate_signals(self, klines: Sequence[Kline]) -> Sequence[Signal]:
        closes = [bar.close for bar in klines]
        fast = _rolling_mean(closes, self.fast_window)
        slow = _rolling_mean(closes, self.slow_window)
        signals = [Signal.HOLD] * len(klines)

        for index in range(1, len(klines)):
            previous_fast = fast[index - 1]
            previous_slow = slow[index - 1]
            current_fast = fast[index]
            current_slow = slow[index]
            if None in (previous_fast, previous_slow, current_fast, current_slow):
                continue
            if previous_fast <= previous_slow and current_fast > current_slow:
                signals[index] = Signal.BUY
            elif previous_fast >= previous_slow and current_fast < current_slow:
                signals[index] = Signal.SELL
        return signals
