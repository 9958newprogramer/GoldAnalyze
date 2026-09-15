package com.goldanalyze.controlplane.backtest;

import com.goldanalyze.controlplane.config.BacktestExecutorConfig;
import org.junit.jupiter.api.Test;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;


/**
 * 集成测试：
 * 验证 Java 可以调用 Python Planner，
 * 再通过有界线程池批量执行 Planner 生成的多个真实回测子任务。
 */
class BacktestBatchExecutorIT {

    /**
     * 生成 7 个批量回测子任务并并发执行，
     * 验证所有子任务都能够返回 completed。
     */
    @Test
    void shouldPlanAndExecuteSevenBacktests() {
        BacktestPlannerClient plannerClient = new BacktestPlannerClient();
        BacktestExecutionClient executionClient = new BacktestExecutionClient();

        ThreadPoolTaskExecutor executor =
                (ThreadPoolTaskExecutor) new BacktestExecutorConfig().backtestExecutor();

        try {
            BacktestBatchExecutor batchExecutor =
                    new BacktestBatchExecutor(executionClient, executor);

            BacktestPlanRequest planRequest = new BacktestPlanRequest(
                    "plan-batch-java-001",
                    "batch-job-java-001",
                    "XAUUSD",
                    LocalDate.of(2025, 1, 1),
                    LocalDate.of(2025, 6, 30),
                    30,
                    60,
                    60
            );

            BacktestPlanResponse plan = plannerClient.plan(planRequest);

            long started = System.nanoTime();

            List<BacktestExecuteResponse> results =
                    batchExecutor.executeAll(plan);

            BacktestResultAggregator aggregator =
                    new BacktestResultAggregator();

            BacktestBatchSummary summary =
                    aggregator.aggregate(results);

            long elapsedMs =
                    (System.nanoTime() - started) / 1_000_000;

            assertEquals(7, plan.total());
            assertEquals(7, results.size());

            assertTrue(
                    results.stream()
                            .allMatch(result -> "completed".equals(result.status()))
            );

            assertEquals(7, summary.totalTasks());
            assertEquals(7, summary.completedTasks());
            assertEquals(0, summary.failedTasks());
            assertEquals(3, summary.positiveTasks());
            assertEquals(9, summary.totalTrades());
            assertEquals(4.12, summary.bestReturnPct(), 0.01);
            assertEquals(-0.67, summary.worstReturnPct(), 0.01);
            assertEquals(1.20, summary.averageReturnPct(), 0.01);

            System.out.println("planned = " + plan.total());
            System.out.println("completed = " + results.size());
            System.out.println("elapsedMs = " + elapsedMs);

            System.out.println("averageReturnPct = " + summary.averageReturnPct());
            System.out.println("bestReturnPct = " + summary.bestReturnPct());
            System.out.println("worstReturnPct = " + summary.worstReturnPct());
            System.out.println("positiveTasks = " + summary.positiveTasks());
            System.out.println("totalTrades = " + summary.totalTrades());
            System.out.println("averageDurationMs = " + summary.averageDurationMs());

            results.forEach(result ->
                    System.out.println(
                            result.runId()
                                    + " | return="
                                    + result.metrics().totalReturnPct()
                                    + "% | trades="
                                    + result.metrics().tradeCount()
                                    + " | pythonDuration="
                                    + result.durationMs()
                                    + "ms"
                    )
            );
        } finally {
            executor.shutdown();
        }
    }
}