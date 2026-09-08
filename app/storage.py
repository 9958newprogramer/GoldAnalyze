"""SQLite persistence for Agent runs and the durable asynchronous control plane."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import uuid4

from app.evals.models import EvalReport
from app.models import AgentCheckpoint, AgentJob, ApprovalRequest, JobEvent, RunResponse


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


class IdempotencyConflictError(RuntimeError):
    """The same caller key was reused for a different request."""


class InvalidJobTransitionError(RuntimeError):
    """A compare-and-set job transition lost a race or was not allowed."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _request_fingerprint(question: str, cache_policy: str, max_attempts: int) -> str:
    payload = json.dumps(
        {
            "cache_policy": cache_policy,
            "max_attempts": max_attempts,
            "question": question,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _idempotency_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class JobRepository:
    """SQLite source of truth with transactional state transitions and event cursors."""

    terminal_statuses: ClassVar[set[str]] = {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "dead_letter",
    }

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    question TEXT NOT NULL,
                    cache_policy TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    idempotency_digest TEXT UNIQUE,
                    traceparent TEXT,
                    tracestate TEXT,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    run_id TEXT,
                    last_event_id INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS job_events (
                    job_id TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, event_id),
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS agent_checkpoints (
                    job_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS job_dead_letters (
                    job_id TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL,
                    error_code TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS job_approval_grants (
                    job_id TEXT PRIMARY KEY,
                    approval_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_jobs_status_lease
                ON agent_jobs(status, lease_expires_at);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(agent_jobs)").fetchall()
            }
            if "traceparent" not in columns:
                connection.execute("ALTER TABLE agent_jobs ADD COLUMN traceparent TEXT")
            if "tracestate" not in columns:
                connection.execute("ALTER TABLE agent_jobs ADD COLUMN tracestate TEXT")

    def connect(self) -> sqlite3.Connection:
        return _connect(self.path)

    @staticmethod
    def _job(row: sqlite3.Row) -> AgentJob:
        return AgentJob(
            job_id=row["job_id"],
            status=row["status"],
            question=row["question"],
            cache_policy=row["cache_policy"],
            request_fingerprint=row["request_fingerprint"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            run_id=row["run_id"],
            last_event_id=row["last_event_id"],
            cancel_requested=bool(row["cancel_requested"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
        )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        status: str,
        message: str,
        data: dict[str, Any] | None = None,
        *,
        now: datetime | None = None,
    ) -> int:
        created_at = now or _utc_now()
        row = connection.execute(
            "SELECT last_event_id FROM agent_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError("job not found")
        event_id = int(row[0]) + 1
        connection.execute(
            """
            INSERT INTO job_events(job_id, event_id, event_type, status, message, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                event_id,
                event_type,
                status,
                message,
                json.dumps(data or {}, ensure_ascii=False, separators=(",", ":")),
                created_at.isoformat(),
            ),
        )
        connection.execute(
            "UPDATE agent_jobs SET last_event_id = ?, updated_at = ? WHERE job_id = ?",
            (event_id, created_at.isoformat(), job_id),
        )
        return event_id

    def create(
        self,
        question: str,
        cache_policy: Literal["use", "refresh", "bypass"],
        max_attempts: int,
        *,
        idempotency_key: str | None,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> tuple[AgentJob, bool]:
        fingerprint = _request_fingerprint(question, cache_policy, max_attempts)
        key_digest = _idempotency_digest(idempotency_key) if idempotency_key else None
        now = _utc_now()
        job_id = uuid4().hex[:16]
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if key_digest is not None:
                existing = connection.execute(
                    "SELECT * FROM agent_jobs WHERE idempotency_digest = ?", (key_digest,)
                ).fetchone()
                if existing is not None:
                    if existing["request_fingerprint"] != fingerprint:
                        raise IdempotencyConflictError(
                            "idempotency key is already bound to another request"
                        )
                    return self._job(existing), False
            connection.execute(
                """
                INSERT INTO agent_jobs(
                    job_id, status, question, cache_policy, request_fingerprint,
                    idempotency_digest, traceparent, tracestate, attempts, max_attempts,
                    created_at, updated_at
                ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    job_id,
                    question,
                    cache_policy,
                    fingerprint,
                    key_digest,
                    traceparent,
                    tracestate,
                    max_attempts,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._append_event(
                connection,
                job_id,
                "job_queued",
                "queued",
                "异步任务已进入队列",
                now=now,
            )
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job(row), True

    def get(self, job_id: str) -> AgentJob | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job(row) if row else None

    def get_trace_context(self, job_id: str) -> tuple[str | None, str | None]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT traceparent, tracestate FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError("job not found")
        return row["traceparent"], row["tracestate"]

    def claim(self, job_id: str, worker_id: str, lease_seconds: int) -> AgentJob | None:
        now = _utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            lease_expired = (
                row["lease_expires_at"] is not None
                and datetime.fromisoformat(row["lease_expires_at"]) <= now
            )
            can_claim = row["status"] == "queued" or (row["status"] == "running" and lease_expired)
            if not can_claim or bool(row["cancel_requested"]):
                return None
            started_at = row["started_at"] or now.isoformat()
            changed = connection.execute(
                """
                UPDATE agent_jobs
                SET status = 'running', attempts = attempts + 1, lease_owner = ?,
                    lease_expires_at = ?, started_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND attempts = ?
                """,
                (
                    worker_id,
                    lease_until.isoformat(),
                    started_at,
                    now.isoformat(),
                    job_id,
                    row["status"],
                    row["attempts"],
                ),
            ).rowcount
            if changed != 1:
                return None
            self._append_event(
                connection,
                job_id,
                "job_started",
                "running",
                "Worker 已取得任务租约",
                {"attempt": row["attempts"] + 1},
                now=now,
            )
            claimed = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job(claimed)

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        now = _utc_now()
        with self.connect() as connection:
            changed = connection.execute(
                """
                UPDATE agent_jobs SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND status IN ('running', 'cancelling') AND lease_owner = ?
                """,
                (
                    (now + timedelta(seconds=lease_seconds)).isoformat(),
                    now.isoformat(),
                    job_id,
                    worker_id,
                ),
            ).rowcount
        return changed == 1

    def should_cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        return job is None or job.cancel_requested

    def request_cancel(self, job_id: str) -> AgentJob:
        now = _utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError("job not found")
            if row["status"] in self.terminal_statuses:
                return self._job(row)
            status = (
                "cancelled" if row["status"] in {"queued", "waiting_approval"} else "cancelling"
            )
            finished_at = now.isoformat() if status == "cancelled" else None
            connection.execute(
                """
                UPDATE agent_jobs SET status = ?, cancel_requested = 1, updated_at = ?,
                    finished_at = COALESCE(?, finished_at)
                WHERE job_id = ?
                """,
                (status, now.isoformat(), finished_at, job_id),
            )
            self._append_event(
                connection,
                job_id,
                "job_cancelled" if status == "cancelled" else "cancel_requested",
                status,
                "任务已取消" if status == "cancelled" else "已请求软取消，Worker 将在步骤边界停止",
                now=now,
            )
            updated = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job(updated)

    def append_step_event(self, job_id: str, step_id: str) -> int:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._append_event(
                connection,
                job_id,
                "step_completed",
                "running",
                f"步骤 {step_id} 已完成并保存 Checkpoint",
                {"step_id": step_id},
            )

    def save_checkpoint(self, checkpoint: AgentCheckpoint) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_checkpoints(job_id, payload, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    checkpoint.job_id,
                    checkpoint.model_dump_json(),
                    checkpoint.updated_at.isoformat(),
                ),
            )

    def save_checkpoint_and_event(self, checkpoint: AgentCheckpoint) -> int:
        """Atomically publish a checkpoint and its observable step event."""
        step_id = checkpoint.completed_steps[-1]
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO agent_checkpoints(job_id, payload, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    checkpoint.job_id,
                    checkpoint.model_dump_json(),
                    checkpoint.updated_at.isoformat(),
                ),
            )
            return self._append_event(
                connection,
                checkpoint.job_id,
                "step_completed",
                "running",
                f"步骤 {step_id} 已完成并保存 Checkpoint",
                {"step_id": step_id},
                now=checkpoint.updated_at,
            )

    def get_checkpoint(self, job_id: str) -> AgentCheckpoint | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM agent_checkpoints WHERE job_id = ?", (job_id,)
            ).fetchone()
        return AgentCheckpoint.model_validate_json(row[0]) if row else None

    def events_after(self, job_id: str, event_id: int, limit: int = 100) -> list[JobEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM job_events WHERE job_id = ? AND event_id > ?
                ORDER BY event_id ASC LIMIT ?
                """,
                (job_id, max(event_id, 0), min(max(limit, 1), 500)),
            ).fetchall()
        return [
            JobEvent(
                job_id=row["job_id"],
                event_id=row["event_id"],
                event_type=row["event_type"],
                status=row["status"],
                message=row["message"],
                data=json.loads(row["data"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def append_checkpoint_restored(self, job_id: str, completed_steps: list[str]) -> int:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError("job not found")
            return self._append_event(
                connection,
                job_id,
                "checkpoint_restored",
                row["status"],
                "Worker 已从持久化 Checkpoint 恢复",
                {"completed_steps": completed_steps},
            )

    def transition_waiting_approval(self, job_id: str, worker_id: str, run_id: str) -> AgentJob:
        now = _utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE agent_jobs SET status = 'waiting_approval', run_id = ?,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                """,
                (run_id, now.isoformat(), job_id, worker_id),
            ).rowcount
            if changed != 1:
                raise InvalidJobTransitionError("waiting transition lost its lease")
            self._append_event(
                connection,
                job_id,
                "approval_required",
                "waiting_approval",
                "Agent 等待人工审批，受控 Tool 尚未执行",
                {"run_id": run_id},
                now=now,
            )
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job(row)

    def resume_after_approval(self, job_id: str, approval: ApprovalRequest) -> AgentJob:
        if approval.status != "consumed":
            raise InvalidJobTransitionError("only consumed approvals can resume a Job")
        now = _utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError("job not found")
            if row["status"] != "waiting_approval" or row["run_id"] != approval.run_id:
                raise InvalidJobTransitionError("Job is not waiting for this approval")
            connection.execute(
                """
                INSERT INTO job_approval_grants(job_id, approval_id, payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, approval.approval_id, approval.model_dump_json(), now.isoformat()),
            )
            connection.execute(
                """
                UPDATE agent_jobs SET status = 'queued', attempts = MAX(attempts - 1, 0),
                    updated_at = ?
                WHERE job_id = ? AND status = 'waiting_approval'
                """,
                (now.isoformat(), job_id),
            )
            self._append_event(
                connection,
                job_id,
                "approval_resumed",
                "queued",
                "人工审批已原子消费，任务重新入队",
                {"approval_id": approval.approval_id},
                now=now,
            )
            updated = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job(updated)

    def get_job_approval(self, job_id: str) -> ApprovalRequest | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM job_approval_grants WHERE job_id = ?", (job_id,)
            ).fetchone()
        return ApprovalRequest.model_validate_json(row[0]) if row else None

    def transition_terminal(
        self,
        job_id: str,
        worker_id: str,
        status: Literal["completed", "failed", "cancelled", "timed_out", "dead_letter"],
        *,
        run_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentJob:
        now = _utc_now()
        event_types = {
            "completed": "job_completed",
            "failed": "job_failed",
            "cancelled": "job_cancelled",
            "timed_out": "job_timed_out",
            "dead_letter": "dead_lettered",
        }
        messages = {
            "completed": "异步任务执行完成",
            "failed": "异步任务安全失败",
            "cancelled": "异步任务已在步骤边界取消",
            "timed_out": "异步任务超过执行时限",
            "dead_letter": "重试预算耗尽，任务进入死信",
        }
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError("job not found")
            if row["lease_owner"] != worker_id or row["status"] not in {
                "running",
                "cancelling",
            }:
                raise InvalidJobTransitionError("worker no longer owns the job lease")
            final_status = "cancelled" if row["cancel_requested"] else status
            changed = connection.execute(
                """
                UPDATE agent_jobs SET status = ?, run_id = COALESCE(?, run_id),
                    error_code = ?, error_message = ?, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ?, finished_at = ?
                WHERE job_id = ? AND lease_owner = ? AND status IN ('running', 'cancelling')
                """,
                (
                    final_status,
                    run_id,
                    error_code,
                    error_message,
                    now.isoformat(),
                    now.isoformat(),
                    job_id,
                    worker_id,
                ),
            ).rowcount
            if changed != 1:
                raise InvalidJobTransitionError("terminal transition lost its lease")
            self._append_event(
                connection,
                job_id,
                event_types[final_status],
                final_status,
                messages[final_status],
                {"error_code": error_code} if error_code else {},
                now=now,
            )
            if final_status == "dead_letter":
                connection.execute(
                    """
                    INSERT OR REPLACE INTO job_dead_letters(
                        job_id, attempts, error_code, error_message, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        row["attempts"],
                        error_code or "retry_exhausted",
                        error_message or "retry budget exhausted",
                        now.isoformat(),
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job(updated)

    def schedule_retry(
        self, job_id: str, worker_id: str, error_code: str, error_message: str
    ) -> AgentJob:
        now = _utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError("job not found")
            if row["lease_owner"] != worker_id or row["status"] != "running":
                raise InvalidJobTransitionError("worker no longer owns the job lease")
            if row["attempts"] >= row["max_attempts"]:
                raise InvalidJobTransitionError("retry budget exhausted")
            connection.execute(
                """
                UPDATE agent_jobs SET status = 'queued', error_code = ?, error_message = ?,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND lease_owner = ? AND status = 'running'
                """,
                (error_code, error_message, now.isoformat(), job_id, worker_id),
            )
            self._append_event(
                connection,
                job_id,
                "retry_scheduled",
                "queued",
                "可重试错误，已在预算内重新入队",
                {"attempt": row["attempts"], "error_code": error_code},
                now=now,
            )
            updated = connection.execute(
                "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job(updated)


class RunRepository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    question TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def connect(self) -> sqlite3.Connection:
        return _connect(self.path)

    def save(self, run: RunResponse) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO runs(run_id, status, question, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.status,
                    run.question,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                ),
            )

    def get(self, run_id: str) -> RunResponse | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return RunResponse.model_validate_json(row[0]) if row else None

    def list_recent(self, limit: int = 10) -> list[RunResponse]:
        safe_limit = min(max(limit, 1), 50)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM runs ORDER BY created_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [RunResponse.model_validate_json(row[0]) for row in rows]


class EvalReportRepository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_reports (
                    eval_run_id TEXT PRIMARY KEY,
                    dataset_version TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    score REAL NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def connect(self) -> sqlite3.Connection:
        return _connect(self.path)

    def save(self, report: EvalReport) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO eval_reports(
                    eval_run_id, dataset_version, passed, score, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report.eval_run_id,
                    report.dataset_version,
                    int(report.passed),
                    report.score,
                    report.model_dump_json(),
                    report.created_at.isoformat(),
                ),
            )

    def latest(self) -> EvalReport | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM eval_reports ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return EvalReport.model_validate_json(row[0]) if row else None
