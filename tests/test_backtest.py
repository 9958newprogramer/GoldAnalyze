from datetime import UTC, datetime, timedelta

from app.domain.backtest import run_sma_crossover
from app.domain.market_data import Bar, DemoMarketDataRepository, profile_bars
from app.models import StrategySpec


def _bars_from_closes(closes: list[float]) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Bar(
            at=start + timedelta(days=index),
            open=close,
            high=close + 1,
            low=max(close - 1, 0.01),
            close=close,
            volume=1_000,
        )
        for index, close in enumerate(closes)
    ]


def test_signal_executes_on_next_bar_open():
    bars = _bars_from_closes([3, 2, 1, 2, 3, 4, 2, 1, 2])
    result = run_sma_crossover(
        StrategySpec(fast_window=2, slow_window=3, fee_bps=0, slippage_bps=0),
        bars,
    )

    assert len(result.trades) == 1
    # Cross-up is observable at index 4, so execution must occur at index 5.
    assert result.trades[0].entry_at == bars[5].at
    # Cross-down is observable at index 7, so execution must occur at index 8.
    assert result.trades[0].exit_at == bars[8].at


def test_demo_data_and_backtest_are_reproducible():
    spec = StrategySpec(fast_window=20, slow_window=60)
    repository = DemoMarketDataRepository()
    first_bars = repository.load(spec)
    second_bars = repository.load(spec)
    first = run_sma_crossover(spec, first_bars)
    second = run_sma_crossover(spec, second_bars)

    assert first_bars == second_bars
    assert first.metrics == second.metrics
    assert first.trades == second.trades
    profile = profile_bars(repository, spec, first_bars)
    assert profile.synthetic is True
    assert profile.row_count > 1_000


def test_costs_do_not_improve_identical_strategy_result():
    repository = DemoMarketDataRepository()
    free_spec = StrategySpec(fast_window=20, slow_window=60, fee_bps=0, slippage_bps=0)
    costly_spec = StrategySpec(fast_window=20, slow_window=60, fee_bps=5, slippage_bps=5)
    bars = repository.load(free_spec)

    free = run_sma_crossover(free_spec, bars)
    costly = run_sma_crossover(costly_spec, bars)

    assert costly.metrics.final_equity <= free.metrics.final_equity
