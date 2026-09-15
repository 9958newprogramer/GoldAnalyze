package com.goldanalyze.controlplane.job;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Optional;

import org.springframework.beans.factory.annotation.Qualifier;

@Repository
public class AgentJobRepository {

    private final JdbcTemplate jdbcTemplate;

    /**
     * 初始化任务仓库，并显式使用 MySQL 控制面 JdbcTemplate。
     *
     * @param jdbcTemplate MySQL 主业务数据库 JdbcTemplate
     */
    public AgentJobRepository(
            @Qualifier("appJdbcTemplate") JdbcTemplate jdbcTemplate
    ) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public void save(
            String jobId,
            String requestId,
            String idempotencyKey,
            String question
    ) {
        jdbcTemplate.update("""
                INSERT INTO agent_job
                (job_id, request_id, idempotency_key, question, job_type, status)
                VALUES (?, ?, ?, ?, 'AGENT', 'QUEUED')
                """,
                jobId,
                requestId,
                idempotencyKey,
                question
        );
    }

    /**
     * 创建批量回测父任务。
     *
     * <p>当前 MVP 继续复用 agent_job 表，
     * 通过 job_type=BACKTEST_BATCH 区分普通 Agent 任务与批量回测任务。</p>
     *
     * @param jobId          父任务 ID
     * @param requestId      请求 ID
     * @param idempotencyKey 幂等键
     * @param description    批量回测任务描述
     */
    public void saveBacktestBatch(
            String jobId,
            String requestId,
            String idempotencyKey,
            String description
    ) {
        jdbcTemplate.update("""
                INSERT INTO agent_job
                (job_id, request_id, idempotency_key, question, job_type, status)
                VALUES (?, ?, ?, ?, 'BACKTEST_BATCH', 'QUEUED')
                """,
                jobId,
                requestId,
                idempotencyKey,
                description
        );
    }

/**
 * 将指定任务标记为运行中，并记录开始执行时间。
 */
    public void markRunning(String jobId) {
        jdbcTemplate.update("""
                UPDATE agent_job
                SET status = 'RUNNING',
                    started_at = CURRENT_TIMESTAMP(6)
                WHERE job_id = ?
                """,
                jobId
        );
    }
/**
 * 将指定任务标记为已完成，并保存下游 Agent 返回的执行结果。
 */
    public void markCompleted(String jobId, String resultJson) {
        jdbcTemplate.update("""
            UPDATE agent_job
            SET status = 'COMPLETED',
                result_json = ?,
                finished_at = CURRENT_TIMESTAMP(6)
            WHERE job_id = ?
            """,
            resultJson,
            jobId
        );
    }

    /**
 * 将指定任务标记为失败，并记录错误信息和结束时间。
 */
    public void markFailed(
            String jobId,
            String errorCode,
            String errorMessage
    ) {
        jdbcTemplate.update("""
                UPDATE agent_job
                SET status = 'FAILED',
                    error_code = ?,
                    error_message = ?,
                    finished_at = CURRENT_TIMESTAMP(6)
                WHERE job_id = ?
                """,
                errorCode,
                errorMessage,
                jobId
        );
    }

    public Optional<AgentJob> findByJobId(String jobId) {
        return jdbcTemplate.query(
                "SELECT * FROM agent_job WHERE job_id = ?",
                this::mapRow,
                jobId
        ).stream().findFirst();
    }

    private AgentJob mapRow(ResultSet rs, int rowNum) throws SQLException {
        return new AgentJob(
                rs.getLong("id"),
                rs.getString("job_id"),
                rs.getString("request_id"),
                rs.getString("idempotency_key"),
                rs.getString("question"),
                rs.getString("job_type"),
                JobStatus.valueOf(rs.getString("status")),
                rs.getString("result_json"),
                rs.getString("error_code"),
                rs.getString("error_message"),
                rs.getTimestamp("created_at").toLocalDateTime(),
                rs.getTimestamp("updated_at").toLocalDateTime(),
                rs.getTimestamp("started_at") == null ? null :
                        rs.getTimestamp("started_at").toLocalDateTime(),
                rs.getTimestamp("finished_at") == null ? null :
                        rs.getTimestamp("finished_at").toLocalDateTime()
        );
    }

    /**
     * 根据幂等键查询已经创建的任务，用于避免重复请求创建和执行同一个任务。
     */
    public Optional<AgentJob> findByIdempotencyKey(String idempotencyKey) {
        return jdbcTemplate.query(
                "SELECT * FROM agent_job WHERE idempotency_key = ?",
                this::mapRow,
                idempotencyKey
        ).stream().findFirst();
    }
}
