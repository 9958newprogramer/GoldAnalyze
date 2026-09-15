package com.goldanalyze.controlplane.backtest;

import com.goldanalyze.controlplane.job.AgentJob;
import com.goldanalyze.controlplane.job.AgentJobRepository;
import com.goldanalyze.controlplane.job.JobStatus;
import com.goldanalyze.controlplane.job.JobStatusCache;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.util.List;
import java.util.UUID;


/**
 * 负责批量回测父任务的完整生命周期：
 * 创建父任务、调用 Python Planner、并发执行子任务、
 * 聚合结果并更新最终任务状态。
 */
@Service
public class BacktestBatchJobService {

    private final AgentJobRepository repository;
    private final JobStatusCache jobStatusCache;
    private final BacktestPlannerClient plannerClient;
    private final BacktestBatchExecutor batchExecutor;
    private final BacktestResultAggregator aggregator;
    private final ObjectMapper objectMapper;

    /**
     * 初始化批量回测父任务服务。
     *
     * @param repository 任务持久化仓库
     * @param jobStatusCache Redis 状态缓存
     * @param plannerClient Python Planner 客户端
     * @param batchExecutor Java 批量并发执行器
     * @param aggregator 子任务结果聚合器
     * @param objectMapper JSON 序列化器
     */
    public BacktestBatchJobService(
            AgentJobRepository repository,
            JobStatusCache jobStatusCache,
            BacktestPlannerClient plannerClient,
            BacktestBatchExecutor batchExecutor,
            BacktestResultAggregator aggregator,
            ObjectMapper objectMapper
    ) {
        this.repository = repository;
        this.jobStatusCache = jobStatusCache;
        this.plannerClient = plannerClient;
        this.batchExecutor = batchExecutor;
        this.aggregator = aggregator;
        this.objectMapper = objectMapper;
    }

    /**
     * 创建并执行一个批量回测父任务。
     *
     * @param symbol 回测标的
     * @param startDate 规划开始日期
     * @param endDate 规划结束日期
     * @param intervalDays Planner 锚点间隔
     * @param lookbackDays 锚点向前窗口
     * @param forwardDays 锚点向后窗口
     * @param idempotencyKey 父任务幂等键
     * @return 最终父任务记录
     */
    public AgentJob executeBatch(
            String symbol,
            LocalDate startDate,
            LocalDate endDate,
            int intervalDays,
            int lookbackDays,
            int forwardDays,
            String idempotencyKey
    ) {
        var existing = repository.findByIdempotencyKey(idempotencyKey);

        if (existing.isPresent()) {
            return existing.get();
        }

        String jobId = UUID.randomUUID()
                .toString()
                .replace("-", "")
                .substring(0, 16);

        String requestId = UUID.randomUUID().toString();

        String description =
                symbol + " "
                        + startDate
                        + "~"
                        + endDate
                        + " batch backtest";

        try {
            repository.saveBacktestBatch(
                    jobId,
                    requestId,
                    idempotencyKey,
                    description
            );
        } catch (DuplicateKeyException exception) {
            return repository.findByIdempotencyKey(idempotencyKey)
                    .orElseThrow(() ->
                            new IllegalStateException(
                                    "批量回测幂等创建冲突，但未找到已有任务"
                            )
                    );
        }

        repository.markRunning(jobId);
        jobStatusCache.put(jobId, JobStatus.RUNNING);

        try {
            BacktestPlanRequest planRequest =
                    new BacktestPlanRequest(
                            requestId,
                            jobId,
                            symbol,
                            startDate,
                            endDate,
                            intervalDays,
                            lookbackDays,
                            forwardDays
                    );

            BacktestPlanResponse plan =
                    plannerClient.plan(planRequest);

            List<BacktestExecuteResponse> results =
                    batchExecutor.executeAll(plan);

            BacktestBatchSummary summary =
                    aggregator.aggregate(results);

            String resultJson =
                    objectMapper.writeValueAsString(summary);

            repository.markCompleted(jobId, resultJson);
            jobStatusCache.put(jobId, JobStatus.COMPLETED);

        } catch (Exception exception) {
            repository.markFailed(
                    jobId,
                    "BACKTEST_BATCH_FAILED",
                    "Batch backtest execution failed"
            );

            jobStatusCache.put(jobId, JobStatus.FAILED);

            throw new IllegalStateException(
                    "批量回测父任务执行失败: " + jobId,
                    exception
            );
        }

        return repository.findByJobId(jobId)
                .orElseThrow(() ->
                        new IllegalStateException(
                                "批量回测父任务完成后无法读取: " + jobId
                        )
                );
    }
}