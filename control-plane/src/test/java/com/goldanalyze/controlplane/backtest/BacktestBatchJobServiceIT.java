package com.goldanalyze.controlplane.backtest;

import com.goldanalyze.controlplane.job.AgentJob;
import com.goldanalyze.controlplane.job.JobStatus;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;


/**
 * 验证批量回测父任务从创建、执行、聚合到持久化的完整链路。
 */
@SpringBootTest
class BacktestBatchJobServiceIT {

    @Autowired
    private BacktestBatchJobService batchJobService;

    @Autowired
    private ObjectMapper objectMapper;

    /**
     * 执行一个真实批量回测父任务，
     * 并验证最终状态、时间戳以及聚合结果均已持久化。
     *
     * @throws Exception JSON 反序列化失败时抛出异常
     */
    @Test
    void shouldCompleteAndPersistBatchBacktest() throws Exception {
        String idempotencyKey =
                "backtest-batch-it-" + System.nanoTime();

        AgentJob job = batchJobService.executeBatch(
                "XAUUSD",
                LocalDate.of(2025, 1, 1),
                LocalDate.of(2025, 6, 30),
                30,
                60,
                60,
                idempotencyKey
        );

        assertNotNull(job);
        assertEquals("BACKTEST_BATCH", job.jobType());
        assertEquals(JobStatus.COMPLETED, job.status());

        assertNotNull(job.startedAt());
        assertNotNull(job.finishedAt());
        assertNotNull(job.resultJson());

        BacktestBatchSummary summary =
                objectMapper.readValue(
                        job.resultJson(),
                        BacktestBatchSummary.class
                );

        assertEquals(7, summary.totalTasks());
        assertEquals(7, summary.completedTasks());
        assertEquals(0, summary.failedTasks());
        assertEquals(3, summary.positiveTasks());
        assertEquals(9, summary.totalTrades());

        assertEquals(
                1.1985714285714286,
                summary.averageReturnPct(),
                0.001
        );

        assertEquals(4.12, summary.bestReturnPct(), 0.01);
        assertEquals(-0.67, summary.worstReturnPct(), 0.01);

        assertTrue(
                job.finishedAt().isAfter(job.startedAt())
                        || job.finishedAt().isEqual(job.startedAt())
        );

        System.out.println("jobId = " + job.jobId());
        System.out.println("jobType = " + job.jobType());
        System.out.println("status = " + job.status());
        System.out.println("startedAt = " + job.startedAt());
        System.out.println("finishedAt = " + job.finishedAt());
        System.out.println("resultJson = " + job.resultJson());
    }
}