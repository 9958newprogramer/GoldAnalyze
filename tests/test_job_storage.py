from concurrent.futures import ThreadPoolExecutor

import pytest

from app.storage import IdempotencyConflictError, JobRepository


def test_idempotency_key_returns_same_job_and_rejects_payload_change(tmp_path):
    jobs = JobRepository(tmp_path / "jobs.db")

    first, created = jobs.create(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key="request-42"
    )
    repeated, repeated_created = jobs.create(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key="request-42"
    )

    assert created is True
    assert repeated_created is False
    assert repeated.job_id == first.job_id
    assert len(jobs.events_after(first.job_id, 0)) == 1
    with pytest.raises(IdempotencyConflictError):
        jobs.create("查询黄金最近6根日K线。", "bypass", 2, idempotency_key="request-42")


def test_idempotency_key_is_stored_only_as_a_digest(tmp_path):
    jobs = JobRepository(tmp_path / "jobs.db")
    job, _ = jobs.create("查询黄金最近5根日K线。", "use", 2, idempotency_key="secret-key")

    with jobs.connect() as connection:
        row = connection.execute(
            "SELECT idempotency_digest FROM agent_jobs WHERE job_id = ?", (job.job_id,)
        ).fetchone()

    assert row[0] != "secret-key"
    assert len(row[0]) == 64


def test_two_workers_cannot_claim_the_same_job(tmp_path):
    jobs = JobRepository(tmp_path / "jobs.db")
    job, _ = jobs.create("查询黄金最近5根日K线。", "use", 2, idempotency_key=None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda worker: jobs.claim(job.job_id, worker, lease_seconds=30),
                ["worker-a", "worker-b"],
            )
        )

    assert sum(item is not None for item in claims) == 1
    assert jobs.get(job.job_id).attempts == 1


def test_cancel_wins_a_race_before_terminal_commit(tmp_path):
    jobs = JobRepository(tmp_path / "jobs.db")
    job, _ = jobs.create("查询黄金最近5根日K线。", "use", 2, idempotency_key=None)
    assert jobs.claim(job.job_id, "worker-a", lease_seconds=30) is not None

    cancelling = jobs.request_cancel(job.job_id)
    terminal = jobs.transition_terminal(job.job_id, "worker-a", "completed", run_id="a" * 12)

    assert cancelling.status == "cancelling"
    assert terminal.status == "cancelled"
    assert jobs.events_after(job.job_id, 0)[-1].event_type == "job_cancelled"


def test_monotonic_event_cursor_supports_reconnect(tmp_path):
    jobs = JobRepository(tmp_path / "jobs.db")
    job, _ = jobs.create("查询黄金最近5根日K线。", "use", 2, idempotency_key=None)
    jobs.claim(job.job_id, "worker-a", lease_seconds=30)
    jobs.append_step_event(job.job_id, "compile_market_query")

    first_page = jobs.events_after(job.job_id, 0)
    second_page = jobs.events_after(job.job_id, first_page[0].event_id)

    assert [event.event_id for event in first_page] == [1, 2, 3]
    assert [event.event_id for event in second_page] == [2, 3]


def test_queued_job_can_be_cancelled_without_a_worker(tmp_path):
    jobs = JobRepository(tmp_path / "jobs.db")
    job, _ = jobs.create("查询黄金最近5根日K线。", "use", 2, idempotency_key=None)

    cancelled = jobs.request_cancel(job.job_id)

    assert cancelled.status == "cancelled"
    assert jobs.claim(job.job_id, "worker-a", lease_seconds=30) is None
