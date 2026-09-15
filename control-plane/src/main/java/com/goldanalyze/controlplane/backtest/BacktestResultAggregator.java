package com.goldanalyze.controlplane.backtest;

import org.springframework.stereotype.Service;

import java.util.List;


/**
 * 负责将多个回测子任务结果聚合成父任务级统计摘要。
 */
@Service
public class BacktestResultAggregator {

    /**
     * 聚合多个已完成回测结果。
     *
     * <p>当前 MVP 假设传入列表中的结果都是成功返回的 completed 结果。
     * 失败任务统计将在后续父任务状态管理阶段补充。</p>
     *
     * @param results 已成功完成的子任务结果
     * @return 父任务级批量回测统计摘要
     */
    public BacktestBatchSummary aggregate(
            List<BacktestExecuteResponse> results
    ) {
        if (results.isEmpty()) {
            return new BacktestBatchSummary(
                    0,
                    0,
                    0,
                    0.0,
                    0.0,
                    0.0,
                    0,
                    0,
                    0.0
            );
        }

        int completedTasks = results.size();

        double averageReturnPct = results.stream()
                .mapToDouble(result -> result.metrics().totalReturnPct())
                .average()
                .orElse(0.0);

        double bestReturnPct = results.stream()
                .mapToDouble(result -> result.metrics().totalReturnPct())
                .max()
                .orElse(0.0);

        double worstReturnPct = results.stream()
                .mapToDouble(result -> result.metrics().totalReturnPct())
                .min()
                .orElse(0.0);

        int positiveTasks = (int) results.stream()
                .filter(result -> result.metrics().totalReturnPct() > 0)
                .count();

        int totalTrades = results.stream()
                .mapToInt(result -> result.metrics().tradeCount())
                .sum();

        double averageDurationMs = results.stream()
                .mapToDouble(BacktestExecuteResponse::durationMs)
                .average()
                .orElse(0.0);

        return new BacktestBatchSummary(
                completedTasks,
                completedTasks,
                0,
                averageReturnPct,
                bestReturnPct,
                worstReturnPct,
                positiveTasks,
                totalTrades,
                averageDurationMs
        );
    }
}