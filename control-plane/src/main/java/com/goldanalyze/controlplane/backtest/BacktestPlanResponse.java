package com.goldanalyze.controlplane.backtest;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.util.List;


/**
 * 表示 Python Planner 为一个父任务生成的完整批量回测计划。
 *
 * @param schemaVersion 返回契约版本
 * @param requestId     Planner 请求 ID
 * @param jobId         Java 父任务 ID
 * @param total         子任务总数
 * @param subtasks      子任务列表
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record BacktestPlanResponse(
        String schemaVersion,
        String requestId,
        String jobId,
        int total,
        List<BacktestSubtaskPlan> subtasks
) {
}