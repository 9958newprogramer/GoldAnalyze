package com.goldanalyze.controlplane.backtest;

import com.goldanalyze.controlplane.job.AgentJob;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;


/**
 * 提供批量回测父任务的 REST API。
 */
@RestController
@RequestMapping("/api/v1/backtests/batches")
public class BacktestBatchController {

    private final BacktestBatchJobService batchJobService;

    /**
     * 初始化批量回测 Controller。
     *
     * @param batchJobService 批量回测父任务服务
     */
    public BacktestBatchController(
            BacktestBatchJobService batchJobService
    ) {
        this.batchJobService = batchJobService;
    }

    /**
     * 创建并执行一个批量回测父任务。
     *
     * @param request 客户端提交的批量回测参数
     * @return 执行完成后的父任务记录
     */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public AgentJob createBatch(
            @RequestBody BacktestBatchCreateRequest request
    ) {
        return batchJobService.executeBatch(
                request.symbol(),
                request.startDate(),
                request.endDate(),
                request.intervalDays(),
                request.lookbackDays(),
                request.forwardDays(),
                request.idempotencyKey()
        );
    }
}