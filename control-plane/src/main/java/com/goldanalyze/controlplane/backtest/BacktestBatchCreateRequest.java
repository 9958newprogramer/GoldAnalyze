package com.goldanalyze.controlplane.backtest;

import java.time.LocalDate;


/**
 * 表示客户端创建批量回测父任务时提交的参数。
 *
 * @param symbol         回测标的，例如 XAUUSD
 * @param startDate      回测规划开始日期
 * @param endDate        回测规划结束日期
 * @param intervalDays   Planner 锚点之间的日期间隔
 * @param lookbackDays   每个锚点向前观察的天数
 * @param forwardDays    每个锚点向后观察的天数
 * @param idempotencyKey 客户端提供的幂等键，用于避免重复创建任务
 */
public record BacktestBatchCreateRequest(
        String symbol,
        LocalDate startDate,
        LocalDate endDate,
        int intervalDays,
        int lookbackDays,
        int forwardDays,
        String idempotencyKey
) {
}