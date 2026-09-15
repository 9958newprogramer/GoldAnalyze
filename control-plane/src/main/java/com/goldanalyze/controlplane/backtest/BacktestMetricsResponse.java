package com.goldanalyze.controlplane.backtest;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;


/**
 * 表示 Python Backtest Engine 返回的核心回测指标。
 *
 * @param totalReturnPct  总收益率百分比
 * @param maxDrawdownPct  最大回撤百分比
 * @param sharpeRatio     夏普比率
 * @param tradeCount      完成交易数量
 * @param winRatePct      胜率百分比
 * @param finalEquity     最终账户权益
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record BacktestMetricsResponse(
        double totalReturnPct,
        double maxDrawdownPct,
        double sharpeRatio,
        int tradeCount,
        double winRatePct,
        double finalEquity
) {
}