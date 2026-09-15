package com.goldanalyze.controlplane.backtest;

import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.net.http.HttpClient;


/**
 * 调用 Python Backtest Service，
 * 执行一个具体的回测子任务。
 */
@Component
public class BacktestExecutionClient {

    private final RestClient restClient;

    /**
     * 创建访问 Python Backtest Service 的 HTTP 客户端。
     */
    public BacktestExecutionClient() {
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
     * 调用 Python 回测引擎执行一个子任务，
     * 并将响应反序列化为 Java 回测结果对象。
     *
     * @param request 单个回测任务请求
     * @return Python 返回的结构化回测结果
     */
    public BacktestExecuteResponse execute(BacktestExecuteRequest request) {
        return restClient.post()
                .uri("/internal/v1/backtests/execute")
                .body(request)
                .retrieve()
                .body(BacktestExecuteResponse.class);
    }
}
