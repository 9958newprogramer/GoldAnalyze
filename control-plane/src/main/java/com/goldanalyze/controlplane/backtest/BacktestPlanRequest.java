package com.goldanalyze.controlplane.backtest;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.time.LocalDate;


/**
 * Java Control Plane 请求 Python Planner 生成批量回测子任务时使用的请求对象。
 *
 * @param requestId     本次 Planner 请求 ID
 * @param jobId         Java 父任务 ID
 * @param symbol        回测标的，例如 XAUUSD
 * @param startDate     规划区间开始日期
 * @param endDate       规划区间结束日期
 * @param intervalDays  测试版 Planner 的锚点间隔天数
 * @param lookbackDays  每个锚点向前扩展的天数
 * @param forwardDays   每个锚点向后扩展的天数
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record BacktestPlanRequest(
        String requestId,
        String jobId,
        String symbol,
        LocalDate startDate,
        LocalDate endDate,
        int intervalDays,
        int lookbackDays,
        int forwardDays
) {
}