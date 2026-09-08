import fakeredis.aioredis
import pytest

from app.approval import ApprovalBinding
from app.bootstrap import build_services
from app.config import Settings
from app.jobs import AgentWorker, JobSubmissionService, RedisStreamBroker, RetryableJobError
from app.models import ResearchSource
from app.storage import JobRepository


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfterCheckpointAgent:
    def __init__(self, delegate, completed_count):
        self.delegate = delegate
        self.completed_count = completed_count

    async def run_checkpointed(self, question, **kwargs):
        sink = kwargs["checkpoint_sink"]

        def crash_sink(checkpoint):
            sink(checkpoint)
            if len(checkpoint.completed_steps) == self.completed_count:
                raise SimulatedProcessCrash

        return await self.delegate.run_checkpointed(
            question,
            **{**kwargs, "checkpoint_sink": crash_sink},
        )


class TransientAgent:
    def __init__(self, delegate, failures=1):
        self.delegate = delegate
        self.failures = failures

    async def run_checkpointed(self, question, **kwargs):
        if self.failures:
            self.failures -= 1
            raise RetryableJobError("provider_unavailable", "temporary provider outage")
        return await self.delegate.run_checkpointed(question, **kwargs)


class CancelAfterCheckpointAgent:
    def __init__(self, delegate, jobs, job_id):
        self.delegate = delegate
        self.jobs = jobs
        self.job_id = job_id

    async def run_checkpointed(self, question, **kwargs):
        sink = kwargs["checkpoint_sink"]

        def cancel_sink(checkpoint):
            sink(checkpoint)
            if len(checkpoint.completed_steps) == 1:
                self.jobs.request_cancel(self.job_id)

        return await self.delegate.run_checkpointed(
            question,
            **{**kwargs, "checkpoint_sink": cancel_sink},
        )


class InvalidAgent:
    async def run_checkpointed(self, question, **kwargs):
        raise ValueError("invalid persisted request")


class SearchProvider:
    name = "worker-test-search"

    async def search(self, query, max_results):
        return [
            ResearchSource(
                title="Evidence",
                url="https://example.com/evidence",
                snippet=f"Evidence for {query}",
            )
        ][:max_results]


def runtime(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runtime.db")))
    jobs = JobRepository(tmp_path / "runtime.db")
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    broker = RedisStreamBroker(client)
    return services, jobs, broker


async def test_worker_completes_job_and_persists_every_plan_step(tmp_path):
    services, jobs, broker = runtime(tmp_path)
    submission = JobSubmissionService(jobs, broker)
    job, _ = await submission.submit(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key="job-one"
    )
    worker = AgentWorker(worker_id="worker-a", agent=services.agent, jobs=jobs, broker=broker)

    assert await worker.run_once(block_ms=1) is True

    completed = jobs.get(job.job_id)
    assert completed.status == "completed"
    assert completed.run_id is not None
    checkpoint = jobs.get_checkpoint(job.job_id)
    assert checkpoint is not None
    assert checkpoint.completed_steps == [
        "compile_market_query",
        "cache_lookup",
        "query_market_data",
        "summarize_market_query",
    ]
    assert [event.event_type for event in jobs.events_after(job.job_id, 0)] == [
        "job_queued",
        "job_started",
        "step_completed",
        "step_completed",
        "step_completed",
        "step_completed",
        "job_completed",
    ]
    await broker.close()


async def test_unacked_crash_is_reclaimed_and_resumes_after_checkpoint(tmp_path):
    services, jobs, broker = runtime(tmp_path)
    job, _ = await JobSubmissionService(jobs, broker).submit(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key=None
    )
    crashing = AgentWorker(
        worker_id="crashed-worker",
        agent=CrashAfterCheckpointAgent(services.agent, completed_count=3),
        jobs=jobs,
        broker=broker,
        lease_seconds=0,
    )

    with pytest.raises(SimulatedProcessCrash):
        await crashing.run_once(block_ms=1)

    assert jobs.get(job.job_id).status == "running"
    recovered = AgentWorker(
        worker_id="recovery-worker",
        agent=services.agent,
        jobs=jobs,
        broker=broker,
        lease_seconds=30,
    )
    assert await recovered.run_once(block_ms=1, reclaim_idle_ms=0) is True

    assert jobs.get(job.job_id).status == "completed"
    assert jobs.get(job.job_id).attempts == 2
    assert "checkpoint_restored" in [event.event_type for event in jobs.events_after(job.job_id, 0)]
    await broker.close()


async def test_retryable_error_requeues_within_budget_then_completes(tmp_path):
    services, jobs, broker = runtime(tmp_path)
    job, _ = await JobSubmissionService(jobs, broker).submit(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key=None
    )
    worker = AgentWorker(
        worker_id="worker-a",
        agent=TransientAgent(services.agent),
        jobs=jobs,
        broker=broker,
    )

    assert await worker.run_once(block_ms=1) is True
    assert jobs.get(job.job_id).status == "queued"
    assert await worker.run_once(block_ms=1) is True

    assert jobs.get(job.job_id).status == "completed"
    assert jobs.get(job.job_id).attempts == 2
    assert "retry_scheduled" in [event.event_type for event in jobs.events_after(job.job_id, 0)]
    await broker.close()


async def test_retry_budget_exhaustion_creates_dead_letter(tmp_path):
    services, jobs, broker = runtime(tmp_path)
    job, _ = await JobSubmissionService(jobs, broker).submit(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key=None
    )
    worker = AgentWorker(
        worker_id="worker-a",
        agent=TransientAgent(services.agent, failures=2),
        jobs=jobs,
        broker=broker,
    )

    await worker.run_once(block_ms=1)
    await worker.run_once(block_ms=1)

    assert jobs.get(job.job_id).status == "dead_letter"
    with jobs.connect() as connection:
        dead_letter = connection.execute(
            "SELECT error_code FROM job_dead_letters WHERE job_id = ?", (job.job_id,)
        ).fetchone()
    assert dead_letter[0] == "provider_unavailable"
    await broker.close()


async def test_non_retryable_error_fails_without_requeue(tmp_path):
    _, jobs, broker = runtime(tmp_path)
    job, _ = await JobSubmissionService(jobs, broker).submit(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key=None
    )
    worker = AgentWorker(worker_id="worker-a", agent=InvalidAgent(), jobs=jobs, broker=broker)

    await worker.run_once(block_ms=1)

    failed = jobs.get(job.job_id)
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.error_code == "ValueError"
    await broker.close()


async def test_running_job_soft_cancel_stops_at_next_step_boundary(tmp_path):
    services, jobs, broker = runtime(tmp_path)
    job, _ = await JobSubmissionService(jobs, broker).submit(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key=None
    )
    worker = AgentWorker(
        worker_id="worker-a",
        agent=CancelAfterCheckpointAgent(services.agent, jobs, job.job_id),
        jobs=jobs,
        broker=broker,
    )

    await worker.run_once(block_ms=1)

    cancelled = jobs.get(job.job_id)
    checkpoint = jobs.get_checkpoint(job.job_id)
    assert cancelled.status == "cancelled"
    assert checkpoint is not None
    assert checkpoint.completed_steps == ["compile_market_query"]
    assert "cancel_requested" in [event.event_type for event in jobs.events_after(job.job_id, 0)]
    await broker.close()


async def test_reclaim_does_not_ack_message_while_live_worker_owns_lease(tmp_path):
    _, jobs, broker = runtime(tmp_path)
    job, _ = await JobSubmissionService(jobs, broker).submit(
        "查询黄金最近5根日K线。", "bypass", 2, idempotency_key=None
    )
    delivered = await broker.read("worker-a", block_ms=1)
    assert jobs.claim(job.job_id, "worker-a", lease_seconds=60) is not None
    recovery = AgentWorker(worker_id="worker-b", agent=InvalidAgent(), jobs=jobs, broker=broker)

    await recovery.run_once(block_ms=1, reclaim_idle_ms=0)

    pending = await broker.client.xpending(broker.stream_name, broker.group_name)
    assert pending["pending"] == 1
    jobs.transition_terminal(job.job_id, "worker-a", "failed")
    assert await broker.ack(delivered[0].message_id) is True
    await broker.close()


async def test_async_job_resumes_without_persisting_raw_approval_token(tmp_path):
    services, jobs, broker = runtime(tmp_path)
    services.agent.search_provider = SearchProvider()
    job, _ = await JobSubmissionService(jobs, broker).submit(
        "搜索互联网最新黄金新闻并返回来源。", "bypass", 2, idempotency_key=None
    )
    worker = AgentWorker(worker_id="worker-a", agent=services.agent, jobs=jobs, broker=broker)
    await worker.run_once(block_ms=1)

    waiting = jobs.get(job.job_id)
    pending = services.runs.get(waiting.run_id)
    assert waiting.status == "waiting_approval"
    assert pending.approval is not None
    grant = services.approvals.approve(
        pending.approval.approval_id,
        decided_by="test-operator",
    )
    approval = pending.approval
    consumed = services.approvals.consume(
        grant.approval_token,
        ApprovalBinding(
            run_id=approval.run_id,
            plan_id=approval.plan_id,
            step_id=approval.step_id,
            tool_name=approval.tool_name,
            arguments_digest=approval.arguments_digest,
            effect=approval.effect,
            risk=approval.risk,
            reason=approval.reason,
        ),
    )
    jobs.resume_after_approval(job.job_id, consumed)
    await broker.enqueue(job.job_id)
    await worker.run_once(block_ms=1)

    completed = jobs.get(job.job_id)
    run = services.runs.get(completed.run_id)
    assert completed.status == "completed"
    assert completed.attempts == 1
    assert run.research_result.sources[0].url == "https://example.com/evidence"
    with jobs.connect() as connection:
        row = connection.execute(
            "SELECT payload FROM job_approval_grants WHERE job_id = ?", (job.job_id,)
        ).fetchone()
    assert grant.approval_token not in row[0]
    assert grant.approval_token not in run.model_dump_json()
    await broker.close()
