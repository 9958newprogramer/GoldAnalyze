package com.goldanalyze.controlplane.agent;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.http.client.JdkClientHttpRequestFactory;

import java.net.http.HttpClient;

@Component
public class AgentClient {

    private final RestClient restClient;

    /**
     * 创建用于调用 Python Agent Service 的 HTTP 客户端，
     * 并显式使用 HTTP/1.1 以兼容 Uvicorn 服务。
     */
    public AgentClient() {
        HttpClient httpClient = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .build();

        JdkClientHttpRequestFactory requestFactory =
                new JdkClientHttpRequestFactory(httpClient);

        this.restClient = RestClient.builder()
                .requestFactory(requestFactory)
                .baseUrl("http://127.0.0.1:8011")
                .build();
    }

    public String execute(AgentExecuteRequest request) {
        return restClient.post()
                .uri("/internal/v1/agent-runs/execute")
                .body(request)
                .retrieve()
                .body(String.class);
    }
}