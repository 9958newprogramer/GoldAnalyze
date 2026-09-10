package com.goldanalyze.controlplane.job;

import org.springframework.stereotype.Service;

import java.util.UUID;

@Service
public class JobService {

    private final AgentJobRepository repository;

    public JobService(AgentJobRepository repository) {
        this.repository = repository;
    }

    public AgentJob createJob(String question, String idempotencyKey) {
        String jobId = UUID.randomUUID()
                .toString()
                .replace("-", "")
                .substring(0, 16);

        String requestId = UUID.randomUUID().toString();

        repository.save(jobId, requestId, idempotencyKey, question);

        return repository.findByJobId(jobId)
                .orElseThrow(() -> new IllegalStateException("Job 创建失败"));
    }

    public AgentJob getJob(String jobId) {
        return repository.findByJobId(jobId)
                .orElseThrow(() -> new IllegalArgumentException("Job 不存在"));
    }
}
