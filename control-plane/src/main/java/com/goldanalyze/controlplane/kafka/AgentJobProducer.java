package com.goldanalyze.controlplane.kafka;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class AgentJobProducer {

    private final KafkaTemplate<String, String> kafkaTemplate;
    private final String topic;

    /**
     * 初始化 Kafka 任务消息生产者，并读取 Agent 命令 Topic 配置。
     */
    public AgentJobProducer(
            KafkaTemplate<String, String> kafkaTemplate,
            @Value("${app.kafka.agent-command-topic}") String topic
    ) {
        this.kafkaTemplate = kafkaTemplate;
        this.topic = topic;
    }

    /**
     * 将待执行任务的 jobId 发送到 Kafka，供后台消费者异步处理。
     */
    public void sendJob(String jobId) {
        kafkaTemplate.send(topic, jobId);
    }
}