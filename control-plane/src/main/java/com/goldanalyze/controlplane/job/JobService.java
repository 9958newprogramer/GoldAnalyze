package com.goldanalyze.controlplane.job;

import org.springframework.stereotype.Service;

import java.util.UUID;

import com.goldanalyze.controlplane.agent.AgentClient;
import com.goldanalyze.controlplane.agent.AgentExecuteRequest;

import org.springframework.dao.DuplicateKeyException;

@Service
public class JobService {

    private final AgentJobRepository repository;
    private final AgentClient agentClient;

    public JobService(AgentJobRepository repository, AgentClient agentClient) {
        this.repository = repository;
        this.agentClient = agentClient;
    }
/**
 * 创建任务、持久化任务信息，并调用下游 Agent 执行任务。
 */
    public AgentJob createJob(String question, String idempotencyKey) {

        var existingJob = repository.findByIdempotencyKey(idempotencyKey);
        if (existingJob.isPresent()) {
            return existingJob.get();
        }
        String jobId = UUID.randomUUID()
                .toString()
                .replace("-", "")
                .substring(0, 16);

        String requestId = UUID.randomUUID().toString();
        try{
            repository.save(jobId, requestId, idempotencyKey, question);
        }   
        catch (DuplicateKeyException e){
            // 如果出现重复键异常，说明已经有相同的 idempotencyKey 的任务存在，直接返回该任务
            return repository.findByIdempotencyKey(idempotencyKey)
                    .orElseThrow(() -> new IllegalStateException("幂等创建冲突，但未找到已有任务"));
        }
        // 任务即将开始实际执行，更新状态并记录开始时间
        repository.markRunning(jobId);

        String runId = UUID.randomUUID()
            .toString()
            .replace("-", "")
            .substring(0, 16);
        try{
            String resultJson = agentClient.execute(new AgentExecuteRequest(
                    requestId,
                    jobId,
                    runId,
                    idempotencyKey,
                    question
            ));
            repository.markCompleted(jobId, resultJson);
        }
        catch (Exception e){
            // 任务执行失败，更新状态为 FAILED
            repository.markFailed(jobId, "AGENT_EXECUTION_FAILED", e.getMessage());
            throw new RuntimeException("任务执行失败: " + e.getMessage(), e);
        }
        
        

        return repository.findByJobId(jobId)
                .orElseThrow(() -> new IllegalStateException("Job 创建失败"));
    }



    public AgentJob getJob(String jobId) {
        return repository.findByJobId(jobId)
                .orElseThrow(() -> new IllegalArgumentException("Job 不存在"));
    }
}
