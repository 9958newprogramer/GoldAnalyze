"""Public API for the GoldAnalyze Python backtest core."""

from app.backtest_core.engine import BacktestConfig, BacktestEngine
from app.backtest_core.models import (
    BacktestResult,
    EquityPoint,
    Kline,
    PerformanceMetrics,
    Portfolio,
    Signal,
    Trade,
)
from app.backtest_core.strategy import MovingAverageCrossStrategy, Strategy

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "EquityPoint",
    "Kline",
    "MovingAverageCrossStrategy",
    "PerformanceMetrics",
    "Portfolio",
    "Signal",
    "Strategy",
    "Trade",
]
