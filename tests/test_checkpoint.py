import asyncio

import pytest

from app.agent.checkpoint import AgentExecutionCancelled, AgentStageTimeout
from app.bootstrap import build_services
from app.config import Settings
from app.storage import JobRepository


class SimulatedWorkerCrash(BaseException):
    pass


class CountingRepository:
    source_name = "counting-demo"
    synthetic = True

    def __init__(self, delegate):
        self.delegate = delegate
        self.loads = 0

    def load(self, spec):
        self.loads += 1
        return self.delegate.load(spec)

    def data_version(self, spec):
        return self.delegate.data_version(spec)


class SlowInterpreter:
    async def interpret(self, question):
        await asyncio.sleep(0.05)


async def test_checkpoint_recovery_skips_completed_plan_steps(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runtime.db")))
    jobs = JobRepository(tmp_path / "runtime.db")
    job, _ = jobs.create("查询黄金最近5根日K线。", "bypass", 2, idempotency_key=None)
    repository = CountingRepository(services.agent.market_repository)
    services.agent.market_repository = repository

    def crash_after_query(checkpoint):
        jobs.save_checkpoint_and_event(checkpoint)
        if checkpoint.completed_steps == [
            "compile_market_query",
            "cache_lookup",
            "query_market_data",
        ]:
            raise SimulatedWorkerCrash

    with pytest.raises(SimulatedWorkerCrash):
        await services.agent.run_checkpointed(
            job.question,
            job_id=job.job_id,
            cache_policy="bypass",
            checkpoint_sink=crash_after_query,
            cancel_probe=lambda: False,
            stage_timeout_seconds=5,
        )

    persisted = jobs.get_checkpoint(job.job_id)
    assert persisted is not None
    assert persisted.completed_steps[-1] == "query_market_data"
    assert repository.loads == 1

    restored = await services.agent.run_checkpointed(
        job.question,
        job_id=job.job_id,
        cache_policy="bypass",
        checkpoint_sink=jobs.save_checkpoint_and_event,
        cancel_probe=lambda: False,
        stage_timeout_seconds=5,
        checkpoint=persisted,
    )

    assert restored.status == "completed"
    assert repository.loads == 1
    assert restored.plan is not None
    assert restored.plan.completed_steps == [step.step_id for step in restored.plan.steps]
    assert len(restored.events) == len({event.stage for event in restored.events}) == 7
    assert [event.data["step_id"] for event in jobs.events_after(job.job_id, 0)[1:]] == [
        "compile_market_query",
        "cache_lookup",
        "query_market_data",
        "summarize_market_query",
    ]


async def test_checkpointed_execution_observes_soft_cancel_before_first_step(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runtime.db")))

    with pytest.raises(AgentExecutionCancelled):
        await services.agent.run_checkpointed(
            "查询黄金最近5根日K线。",
            job_id="a" * 16,
            cache_policy="bypass",
            checkpoint_sink=lambda checkpoint: None,
            cancel_probe=lambda: True,
            stage_timeout_seconds=5,
        )


async def test_each_async_stage_has_a_hard_timeout(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runtime.db")))
    services.agent.interpreter = SlowInterpreter()

    with pytest.raises(AgentStageTimeout) as caught:
        await services.agent.run_checkpointed(
            "黄金日K，20日均线上穿60日均线做多。",
            job_id="b" * 16,
            cache_policy="bypass",
            checkpoint_sink=lambda checkpoint: None,
            cancel_probe=lambda: False,
            stage_timeout_seconds=0.001,
        )

    assert caught.value.stage == "interpret_strategy"
