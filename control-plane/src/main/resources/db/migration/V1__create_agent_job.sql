CREATE TABLE agent_job (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    job_id VARCHAR(64) NOT NULL,
    request_id VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    question VARCHAR(2000) NOT NULL,
    job_type VARCHAR(32) NOT NULL DEFAULT 'AGENT',
    status VARCHAR(32) NOT NULL DEFAULT 'QUEUED',

    result_json JSON NULL,
    error_code VARCHAR(64) NULL,
    error_message VARCHAR(500) NULL,

    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,

    UNIQUE KEY uk_agent_job_job_id (job_id),
    UNIQUE KEY uk_agent_job_idempotency_key (idempotency_key),
    KEY idx_agent_job_status (status),
    KEY idx_agent_job_created_at (created_at)
);
