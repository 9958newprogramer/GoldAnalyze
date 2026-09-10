package com.goldanalyze.controlplane.job;

import java.time.LocalDateTime;

public record AgentJob(
        Long id,
        String jobId,
        String requestId,
        String idempotencyKey,
        String question,
        String jobType,
        JobStatus status,
        String resultJson,
        String errorCode,
        String errorMessage,
        LocalDateTime createdAt,
        LocalDateTime updatedAt,
        LocalDateTime startedAt,
        LocalDateTime finishedAt
) {
}
