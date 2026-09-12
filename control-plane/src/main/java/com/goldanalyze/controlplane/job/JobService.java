package com.goldanalyze.controlplane.job;

import org.springframework.stereotype.Service;

import java.util.UUID;

import com.goldanalyze.controlplane.agent.AgentClient;
import com.goldanalyze.controlplane.agent.AgentExecuteRequest;

import org.springframework.dao.DuplicateKeyException;

import com.goldanalyze.controlplane.kafka.AgentJobProducer;
import org.springframework.beans.factory.annotation.Value;

@Service
public class JobService {

    private final AgentJobRepository repository;
    private final AgentClient agentClient;
    private final AgentJobProducer agentJobProducer;
    private final JobStatusCache jobStatusCache;
    private final boolean jobStatusCacheEnabled;


    /**
     * 初始化任务服务，并注入任务仓库、Agent客户端、Kafka生产者、Redis状态缓存和缓存开关。
     */
    public JobService(AgentJobRepository repository, AgentClient agentClient,AgentJobProducer agentJobProducer,
         JobStatusCache jobStatusCache, @Value("${job.status.cache.enabled:false}") boolean jobStatusCacheEnabled) {
        this.repository = repository;
        this.agentClient = agentClient;
        this.agentJobProducer = agentJobProducer;
        this.jobStatusCache = jobStatusCache;
        this.jobStatusCacheEnabled = jobStatusCacheEnabled;
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

            agentJobProducer.sendJob(jobId);
        }   
        catch (DuplicateKeyException e){
            // 如果出现重复键异常，说明已经有相同的 idempotencyKey 的任务存在，直接返回该任务
            return repository.findByIdempotencyKey(idempotencyKey)
                    .orElseThrow(() -> new IllegalStateException("幂等创建冲突，但未找到已有任务"));
        }
        

        return repository.findByJobId(jobId)
                .orElseThrow(() -> new IllegalStateException("Job 创建失败"));
    }



    public AgentJob getJob(String jobId) {
        return repository.findByJobId(jobId)
                .orElseThrow(() -> new IllegalArgumentException("Job 不存在"));
    }

    /**
     * 查询任务状态；优先读取 Redis，缓存未命中时回退 MySQL，并将状态写回缓存。
     */
    public JobStatus getJobStatus(String jobId) {
        if (jobStatusCacheEnabled) {
            JobStatus status = jobStatusCache.get(jobId);
            if (status != null) {
                return status;
            }
        }

        AgentJob job = repository.findByJobId(jobId)
                .orElseThrow(() -> new IllegalArgumentException("Job 不存在"));

        if (jobStatusCacheEnabled) {
            jobStatusCache.put(jobId, job.status());
        }
        return job.status();
    }

}
