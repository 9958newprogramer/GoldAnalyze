package com.goldanalyze.controlplane.backtest;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;


/**
 * 表示 Python Backtest Service 返回的单个回测执行结果。
 *
 * <p>当前只映射 Java 批量调度和聚合阶段真正需要的核心字段。
 * trades、equity_curve 等明细暂时不在 Java 侧建模。</p>
 *
 * @param schemaVersion 回测结果契约版本
 * @param requestId     HTTP 请求 ID
 * @param jobId         父任务 ID
 * @param runId         当前子任务运行 ID
 * @param status        执行状态
 * @param engineVersion Python 回测引擎版本
 * @param dataVersion   本次使用的行情数据版本
 * @param metrics       核心回测指标
 * @param resultDigest  确定性结果摘要
 * @param durationMs    Python 单次回测耗时，单位毫秒
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record BacktestExecuteResponse(
        String schemaVersion,
        String requestId,
        String jobId,
        String runId,
        String status,
        String engineVersion,
        String dataVersion,
        BacktestMetricsResponse metrics,
        String resultDigest,
        double durationMs
) {
}