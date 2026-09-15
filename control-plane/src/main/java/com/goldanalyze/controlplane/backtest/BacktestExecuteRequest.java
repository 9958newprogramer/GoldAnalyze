package com.goldanalyze.controlplane.backtest;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;


/**
 * Java Control Plane 调用 Python Backtest Engine
 * 执行单个回测子任务时使用的请求对象。
 *
 * @param requestId      本次 HTTP 请求 ID
 * @param jobId          父任务 ID
 * @param runId          当前子任务运行 ID
 * @param idempotencyKey 幂等键，用于识别重复执行请求
 * @param strategy       单个子任务的回测策略参数
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record BacktestExecuteRequest(
        String requestId,
        String jobId,
        String runId,
        String idempotencyKey,
        BacktestStrategySpec strategy
) {
}