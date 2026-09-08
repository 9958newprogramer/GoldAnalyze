from datetime import date

import pytest
from pydantic import ValidationError

from app.agent.interpreter import RuleBasedStrategyInterpreter
from app.models import StrategySpec


@pytest.mark.asyncio
async def test_rule_interpreter_extracts_strategy_constraints():
    result = await RuleBasedStrategyInterpreter().interpret(
        "使用黄金日K，10日均线上穿30日均线做多，2020年至2025年回测，"
        "手续费4个基点，滑点1个基点，本金10万。"
    )

    assert result.spec.fast_window == 10
    assert result.spec.slow_window == 30
    assert result.spec.start_date == date(2020, 1, 1)
    assert result.spec.end_date == date(2025, 12, 31)
    assert result.spec.fee_bps == 4
    assert result.spec.slippage_bps == 1
    assert result.spec.initial_cash == 100_000
    assert result.spec.execution == "next_bar_open"


@pytest.mark.asyncio
async def test_rule_interpreter_accepts_compact_window_pair():
    result = await RuleBasedStrategyInterpreter().interpret("回测黄金 20/60 日均线交叉策略")

    assert result.spec.fast_window == 20
    assert result.spec.slow_window == 60
    assert result.warnings == []


@pytest.mark.asyncio
async def test_rule_interpreter_accepts_shared_unit_window_pair():
    result = await RuleBasedStrategyInterpreter().interpret(
        "评估黄金日K的10日与30日均线交叉策略，手续费4基点。"
    )

    assert result.spec.fast_window == 10
    assert result.spec.slow_window == 30
    assert result.warnings == []


@pytest.mark.asyncio
async def test_rule_interpreter_accepts_traditional_basis_points():
    result = await RuleBasedStrategyInterpreter().interpret(
        "黃金1小時線，9小時均線上穿36小時均線，手續費2個基點，滑點1個基點。"
    )

    assert result.spec.timeframe == "1h"
    assert result.spec.fee_bps == 2
    assert result.spec.slippage_bps == 1


@pytest.mark.asyncio
async def test_rule_interpreter_recognizes_hourly_k_suffix_regression():
    result = await RuleBasedStrategyInterpreter().interpret(
        "黄金1小时K，20日均线上穿60日均线做多。"
    )

    assert result.spec.timeframe == "1h"
    assert result.spec.fast_window == 20
    assert result.spec.slow_window == 60


@pytest.mark.parametrize(
    "question",
    [
        "黄金1h的5小时均线与20小时均线交叉策略。",
        "黄金hourly行情上的6小时均线和24小时均线策略。",
    ],
)
@pytest.mark.asyncio
async def test_rule_interpreter_recognizes_ascii_hourly_before_chinese(question):
    result = await RuleBasedStrategyInterpreter().interpret(question)

    assert result.spec.timeframe == "1h"


def test_strategy_rejects_invalid_window_relationship():
    with pytest.raises(ValidationError):
        StrategySpec(fast_window=60, slow_window=20)
