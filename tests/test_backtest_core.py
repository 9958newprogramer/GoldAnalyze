from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest

from app.backtest_core import (
    BacktestConfig,
    BacktestEngine,
    EquityPoint,
    Kline,
    MovingAverageCrossStrategy,
    Portfolio,
)
from app.backtest_core.metrics import calculate_metrics


def _klines(closes: list[float]) -> tuple[Kline, ...]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        Kline(
            symbol="xauusd",
            trade_date=start + timedelta(days=index),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000,
        )
        for index, close in enumerate(closes)
    )


def test_kline_accepts_postgres_date_and_normalizes_symbol_and_timestamp():
    bar = Kline(
        symbol=" xauusd ",
        trade_date=date(2024, 1, 2),
        open=2_060,
        high=2_075,
        low=2_050,
        close=2_070,
        volume=100,
    )

    assert bar.symbol == "XAUUSD"
    assert bar.timestamp == datetime(2024, 1, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    ("high", "low", "volume", "message"),
    [
        (99, 90, 1, "high"),
        (110, 101, 1, "low"),
        (110, 90, -1, "volume"),
    ],
)
def test_kline_rejects_invalid_ohlcv(high: float, low: float, volume: float, message: str):
    with pytest.raises(ValueError, match=message):
        Kline(
            symbol="XAUUSD",
            trade_date=date(2024, 1, 2),
            open=100,
            high=high,
            low=low,
            close=100,
            volume=volume,
        )


def test_portfolio_accounts_for_entry_and_exit_fees():
    portfolio = Portfolio(initial_cash=100.0)
    entry_at = datetime(2024, 1, 2, tzinfo=UTC)
    exit_at = datetime(2024, 1, 3, tzinfo=UTC)

    portfolio.open_long(symbol="XAUUSD", at=entry_at, price=10.0, fee_rate=0.01)
    trade = portfolio.close_long(at=exit_at, price=12.0, fee_rate=0.01, reason="signal")

    assert portfolio.has_position is False
    assert portfolio.cash == pytest.approx(117.6237623762)
    assert portfolio.total_fees == pytest.approx(trade.entry_fee + trade.exit_fee)
    assert trade.pnl == pytest.approx(portfolio.cash - portfolio.initial_cash)
    assert trade.return_pct > 0


def test_ma_signal_executes_at_next_bar_open_without_lookahead():
    bars = _klines([3, 2, 1, 2, 3, 4, 2, 1, 2])
    result = BacktestEngine().run(
        bars,
        MovingAverageCrossStrategy(fast_window=2, slow_window=3),
        BacktestConfig(initial_cash=1_000),
    )

    assert len(result.trades) == 1
    assert result.trades[0].entry_at == bars[5].timestamp
    assert result.trades[0].exit_at == bars[8].timestamp
    assert result.trades[0].entry_price == bars[5].open
    assert result.metrics.trade_count == 1
    assert result.metrics.win_rate_pct == 0
    assert result.metrics.total_return_pct == pytest.approx(-50.0)


def test_metrics_include_peak_to_trough_max_drawdown():
    points = [
        EquityPoint(datetime(2024, 1, day, tzinfo=UTC), equity)
        for day, equity in enumerate([100.0, 120.0, 90.0, 110.0], start=1)
    ]

    metrics = calculate_metrics(initial_cash=100, equity_curve=points, trades=[])

    assert metrics.total_return_pct == pytest.approx(10.0)
    assert metrics.max_drawdown_pct == pytest.approx(25.0)
    assert metrics.win_rate_pct == 0
    assert metrics.trade_count == 0


def test_engine_rejects_duplicate_or_unordered_klines():
    bars = list(_klines([1, 2, 3, 4, 5]))
    bars[4] = Kline(
        symbol="XAUUSD",
        trade_date=bars[3].trade_date,
        open=5,
        high=5,
        low=5,
        close=5,
    )

    with pytest.raises(ValueError, match="strictly ordered"):
        BacktestEngine().run(
            bars,
            MovingAverageCrossStrategy(fast_window=2, slow_window=3),
        )


def test_stateless_engine_supports_1000_parallel_event_backtests():
    """The core has no shared mutable state and can sit behind a future worker pool."""

    bars = _klines([3, 2, 1, 2, 3, 4, 2, 1, 2])
    strategy = MovingAverageCrossStrategy(fast_window=2, slow_window=3)
    config = BacktestConfig(initial_cash=1_000)
    engine = BacktestEngine()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: engine.run(bars, strategy, config), range(1_000)))

    assert len(results) == 1_000
    assert {result.final_equity for result in results} == {500.0}
    assert {result.metrics.trade_count for result in results} == {1}
