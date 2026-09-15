package com.goldanalyze.controlplane.backtest;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;


/**
 * 使用 Java 有界线程池并发执行 Python Planner 生成的多个回测子任务。
 */
@Service
public class BacktestBatchExecutor {

    private final BacktestExecutionClient executionClient;
    private final Executor backtestExecutor;

    /**
     * 创建批量回测执行器。
     *
     * @param executionClient 单个 Python 回测任务客户端
     * @param backtestExecutor 批量回测专用线程池
     */
    public BacktestBatchExecutor(
            BacktestExecutionClient executionClient,
            @Qualifier("backtestExecutor") Executor backtestExecutor
    ) {
        this.executionClient = executionClient;
        this.backtestExecutor = backtestExecutor;
    }

    /**
     * 将 Planner 返回的所有子任务提交到 Java 回测线程池，
     * 并按原始子任务顺序收集执行结果。
     *
     * @param plan Python Planner 生成的批量回测计划
     * @return 每个子任务对应的回测结果
     */
    public List<BacktestExecuteResponse> executeAll(BacktestPlanResponse plan) {
        List<CompletableFuture<BacktestExecuteResponse>> futures =
                plan.subtasks()
                        .stream()
                        .map(subtask ->
                                CompletableFuture.supplyAsync(
                                        () -> executeSubtask(plan.jobId(), subtask),
                                        backtestExecutor
                                )
                        )
                        .toList();

        return futures.stream()
                .map(CompletableFuture::join)
                .toList();
    }

    /**
     * 将一个 Planner 子任务转换成 Python Backtest Engine 请求并执行。
     *
     * @param parentJobId 父任务 ID
     * @param subtask Planner 生成的单个子任务
     * @return 单个子任务的回测结果
     */
    private BacktestExecuteResponse executeSubtask(
            String parentJobId,
            BacktestSubtaskPlan subtask
    ) {
        BacktestStrategySpec strategy = new BacktestStrategySpec(
                subtask.symbol(),
                "1d",
                "sma_crossover",
                5,
                20,
                subtask.startDate(),
                subtask.endDate(),
                100000.0,
                2.0,
                3.0,
                "next_bar_open",
                true
        );

        BacktestExecuteRequest request = new BacktestExecuteRequest(
                "execute-" + subtask.subtaskId(),
                parentJobId,
                subtask.subtaskId(),
                "batch-execute-" + subtask.subtaskId(),
                strategy
        );

        return executionClient.execute(request);
    }
}