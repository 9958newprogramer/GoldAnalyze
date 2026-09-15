package com.goldanalyze.controlplane.backtest;

import org.junit.jupiter.api.Test;

import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;


/**
 * 集成测试：
 * 验证 Java Control Plane 可以调用 Python Backtest Service
 * 执行真实 PostgreSQL 黄金行情回测。
 */
class BacktestExecutionClientIT {

    /**
     * 使用 5/20 均线策略执行一笔真实 XAUUSD 日线回测，
     * 并检查 Python 返回的核心结果。
     */
    @Test
    void shouldExecuteRealBacktestThroughPython() {
        BacktestExecutionClient client = new BacktestExecutionClient();

        BacktestStrategySpec strategy = new BacktestStrategySpec(
                "XAUUSD",
                "1d",
                "sma_crossover",
                5,
                20,
                LocalDate.of(2025, 1, 1),
                LocalDate.of(2025, 6, 30),
                100000.0,
                2.0,
                3.0,
                "next_bar_open",
                true
        );

        BacktestExecuteRequest request = new BacktestExecuteRequest(
                "execute-java-001",
                "batch-java-001",
                "run-java-001",
                "execute-java-idempotency-001",
                strategy
        );

        BacktestExecuteResponse response = client.execute(request);

        assertNotNull(response);
        assertEquals("completed", response.status());
        assertNotNull(response.metrics());
        assertEquals(3, response.metrics().tradeCount());
        assertEquals(3.01, response.metrics().totalReturnPct(), 0.01);
        assertEquals(103005.93, response.metrics().finalEquity(), 0.01);

        System.out.println("status = " + response.status());
        System.out.println("tradeCount = " + response.metrics().tradeCount());
        System.out.println("totalReturnPct = " + response.metrics().totalReturnPct());
        System.out.println("finalEquity = " + response.metrics().finalEquity());
        System.out.println("durationMs = " + response.durationMs());
    }
}