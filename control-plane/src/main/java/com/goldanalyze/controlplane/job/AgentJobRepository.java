package com.goldanalyze.controlplane.job;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Optional;

@Repository
public class AgentJobRepository {

    private final JdbcTemplate jdbcTemplate;

    public AgentJobRepository(JdbcTemplate jdbcTemplate) {
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
}
