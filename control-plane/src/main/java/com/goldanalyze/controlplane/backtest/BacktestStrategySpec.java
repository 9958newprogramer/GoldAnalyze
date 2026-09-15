package com.goldanalyze.controlplane.backtest;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.time.LocalDate;


/**
 * Java 调用 Python Backtest Engine 时使用的策略参数。
 *
 * @param symbol        回测标的，例如 XAUUSD
 * @param timeframe     K线周期，当前支持 1d、1h
 * @param strategyType  策略类型，当前为 sma_crossover
 * @param fastWindow    快速均线窗口
 * @param slowWindow    慢速均线窗口
 * @param startDate     回测开始日期
 * @param endDate       回测结束日期
 * @param initialCash   初始资金
 * @param feeBps        手续费，单位为基点
 * @param slippageBps   滑点，单位为基点
 * @param execution     成交方式，当前为 next_bar_open
 * @param longOnly      是否只做多
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record BacktestStrategySpec(
        String symbol,
        String timeframe,
        String strategyType,
        int fastWindow,
        int slowWindow,
        LocalDate startDate,
        LocalDate endDate,
        double initialCash,
        double feeBps,
        double slippageBps,
        String execution,
        boolean longOnly
) {
}