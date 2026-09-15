package com.goldanalyze.controlplane.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;


/**
 * 配置批量回测使用的有界线程池。
 *
 * <p>逻辑子任务数量可以很大，但实际同时执行的线程数量受限，
 * 避免一次性向 Python Backtest Service 发出过多请求。</p>
 */
@Configuration
public class BacktestExecutorConfig {

    /**
     * 创建批量回测线程池。
     *
     * <p>当前先使用较保守的并发参数：
     * 4 个常驻线程，最多 8 个线程，任务队列最多缓存 100 个任务。</p>
     *
     * @return 批量回测专用 Executor
     */
    @Bean(name = "backtestExecutor")
    public Executor backtestExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();

        executor.setCorePoolSize(4);
        executor.setMaxPoolSize(8);
        executor.setQueueCapacity(100);
        executor.setThreadNamePrefix("backtest-worker-");

        executor.initialize();

        return executor;
    }
}