package com.goldanalyze.controlplane.job;

import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

@Component
public class JobStatusCache {

    private static final String KEY_PREFIX = "job:status:";

    private final StringRedisTemplate redisTemplate;

    /**
     * 初始化任务状态缓存，并注入 Redis 字符串操作模板。
     */
    public JobStatusCache(StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    /**
     * 将任务状态写入 Redis。
     */
    public void put(String jobId, JobStatus status) {
        redisTemplate.opsForValue()
                .set(KEY_PREFIX + jobId, status.name());
    }

    /**
     * 从 Redis 读取任务状态；不存在时返回 null。
     */
    public JobStatus get(String jobId) {
        String value = redisTemplate.opsForValue()
                .get(KEY_PREFIX + jobId);

        if (value == null) {
            return null;
        }

        return JobStatus.valueOf(value);
    }
}