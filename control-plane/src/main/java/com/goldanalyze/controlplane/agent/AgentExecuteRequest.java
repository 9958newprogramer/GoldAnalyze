    package com.goldanalyze.controlplane.agent;

import tools.jackson.databind.annotation.JsonNaming;
import tools.jackson.databind.PropertyNamingStrategies;


/**
 * Java Control Plane 调用 Python Agent Service 时使用的请求数据对象。
 */
@JsonNaming (PropertyNamingStrategies.SnakeCaseStrategy.class)
public record AgentExecuteRequest(

        String requestId,

        String jobId,

        String runId,

        String idempotencyKey,

        String question
) {
}

