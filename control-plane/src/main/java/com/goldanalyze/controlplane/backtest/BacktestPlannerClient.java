package com.goldanalyze.controlplane.backtest;

import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.net.http.HttpClient;


/**
 * 调用 Python Backtest Service 的 Planner 接口，
 * 获取一个父任务对应的批量回测子任务计划。
 */
@Component
public class BacktestPlannerClient {

    private final RestClient restClient;

    /**
     * 创建 Python Backtest Service 的 HTTP 客户端。
     */
    public BacktestPlannerClient() {
        HttpClient httpClient = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .build();

        JdkClientHttpRequestFactory requestFactory =
                new JdkClientHttpRequestFactory(httpClient);

        this.restClient = RestClient.builder()
                .requestFactory(requestFactory)
                .baseUrl("http://127.0.0.1:8020")
                .build();
    }

    /**
     * 请求 Python Planner 生成批量回测子任务。
     *
     * @param request Planner 请求参数
     * @return Python 返回的子任务计划
     */
    public BacktestPlanResponse plan(BacktestPlanRequest request) {
        return restClient.post()
                .uri("/internal/v1/backtests/plan")
                .body(request)
                .retrieve()
                .body(BacktestPlanResponse.class);
    }
}