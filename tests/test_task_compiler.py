from app.agent.task_compiler import compile_market_query


def test_market_query_recognizes_hourly_kline_expression():
    spec = compile_market_query("给我黄金最近12根1小时K线。")

    assert spec.timeframe == "1h"
    assert spec.limit == 12
