package com.goldanalyze.controlplane.backtest;


/**
 * 表示一个批量回测父任务完成后的聚合统计结果。
 *
 * @param totalTasks          子任务总数
 * @param completedTasks      成功完成的子任务数
 * @param failedTasks         失败子任务数
 * @param averageReturnPct    所有成功子任务的平均收益率
 * @param bestReturnPct       最佳子任务收益率
 * @param worstReturnPct      最差子任务收益率
 * @param positiveTasks       收益率大于 0 的子任务数量
 * @param totalTrades         所有子任务的交易总数
 * @param averageDurationMs   Python 单任务平均执行耗时
 */
public record BacktestBatchSummary(
        int totalTasks,
        int completedTasks,
        int failedTasks,
        double averageReturnPct,
        double bestReturnPct,
        double worstReturnPct,
        int positiveTasks,
        int totalTrades,
        double averageDurationMs
) {
}