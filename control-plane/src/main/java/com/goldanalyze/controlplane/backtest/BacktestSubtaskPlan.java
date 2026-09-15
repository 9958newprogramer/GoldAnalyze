package com.goldanalyze.controlplane.backtest;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.time.LocalDate;


/**
 * 表示 Python Planner 为父任务生成的一个逻辑回测子任务。
 *
 * @param subtaskId  子任务唯一标识
 * @param symbol     回测标的
 * @param anchorDate 历史事件锚点日期
 * @param startDate  子任务回测区间开始日期
 * @param endDate    子任务回测区间结束日期
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record BacktestSubtaskPlan(
        String subtaskId,
        String symbol,
        LocalDate anchorDate,
        LocalDate startDate,
        LocalDate endDate
) {
}