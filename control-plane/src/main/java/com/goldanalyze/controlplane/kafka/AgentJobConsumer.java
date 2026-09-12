package com.goldanalyze.controlplane.kafka;

import com.goldanalyze.controlplane.agent.AgentClient;
import com.goldanalyze.controlplane.agent.AgentExecuteRequest;
import com.goldanalyze.controlplane.job.AgentJob;
import com.goldanalyze.controlplane.job.AgentJobRepository;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import java.util.UUID;

import com.goldanalyze.controlplane.job.JobStatus;
import com.goldanalyze.controlplane.job.JobStatusCache;

@Component
public class AgentJobConsumer {

    private final AgentJobRepository repository;
    private final AgentClient agentClient;
    private final JobStatusCache jobStatusCache;

    /**
     * 初始化 Kafka 任务消费者，并注入任务仓库和 Python Agent 客户端。
     */
    public AgentJobConsumer(
            AgentJobRepository repository,
            AgentClient agentClient,
            JobStatusCache jobStatusCache
    ) {
        this.repository = repository;
        this.agentClient = agentClient;
        this.jobStatusCache = jobStatusCache;
    }

    /**
     * 消费 Kafka 中的任务消息，并调用 Python Agent 完成后台执行。
     */
    @KafkaListener(
            topics = "${app.kafka.agent-command-topic}",
            groupId = "goldanalyze-agent-worker-v1"
    )
    public void consumeJob(String jobId) {

        AgentJob job = repository.findByJobId(jobId)
                .orElseThrow(() ->
                        new IllegalStateException("Kafka任务不存在: " + jobId));

        repository.markRunning(jobId);
        jobStatusCache.put(jobId, JobStatus.RUNNING);

        String runId = UUID.randomUUID()
                .toString()
                .replace("-", "")
                .substring(0, 16);

        try {
            String resultJson = agentClient.execute(
                    new AgentExecuteRequest(
                            job.requestId(),
                            job.jobId(),
                            runId,
                            job.idempotencyKey(),
                            job.question()
                    )
            );

            repository.markCompleted(jobId, resultJson);
            jobStatusCache.put(jobId, JobStatus.COMPLETED);

        } catch (Exception e) {
            repository.markFailed(
                    jobId,
                    "AGENT_EXECUTION_FAILED",
                    e.getMessage()
            );
            jobStatusCache.put(jobId, JobStatus.FAILED);

            throw e;
        }
    }
}