"""Submission service and bounded Redis Streams Worker."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from opentelemetry.trace import SpanKind

from app.agent.checkpoint import AgentExecutionCancelled, AgentStageTimeout
from app.models import AgentCheckpoint, AgentJob, RunResponse
from app.observability import Telemetry, TraceCarrier
from app.security import contains_assigned_secret
from app.storage import InvalidJobTransitionError, JobRepository

from .broker import RedisStreamBroker, StreamMessage


class CheckpointedAgent(Protocol):
    async def run_checkpointed(
        self,
        question: str,
        *,
        job_id: str,
        cache_policy: str,
        checkpoint_sink,
        cancel_probe,
        stage_timeout_seconds: float,
        checkpoint: AgentCheckpoint | None = None,
        trusted_consumed_approval=None,
    ) -> RunResponse: ...


class RetryableJobError(RuntimeError):
    """A classified transient failure that may consume one bounded retry."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SensitiveJobInputError(ValueError):
    """Raised before persistence when an async payload appears to contain a credential."""


@dataclass(frozen=True)
class JobSubmissionService:
    jobs: JobRepository
    broker: RedisStreamBroker
    telemetry: Telemetry | None = None

    async def submit(
        self,
        question: str,
        cache_policy: str,
        max_attempts: int,
        *,
        idempotency_key: str | None,
    ) -> tuple[AgentJob, bool]:
        if contains_assigned_secret(question):
            raise SensitiveJobInputError("任务文本疑似包含凭据，请脱敏后重试")
        scope = (
            self.telemetry.span("aurumlab.job.submit", kind=SpanKind.PRODUCER)
            if self.telemetry is not None
            else nullcontext()
        )
        with scope as span:
            carrier = self.telemetry.inject() if self.telemetry is not None else TraceCarrier()
            job, created = self.jobs.create(
                question,
                cache_policy,
                max_attempts,
                idempotency_key=idempotency_key,
                traceparent=carrier.traceparent,
                tracestate=carrier.tracestate,
            )
            if span is not None:
                span.set_attribute("aurumlab.job.id", job.job_id)
                span.set_attribute("aurumlab.job.status", job.status)
            # Re-publish an existing queued Job as an outbox repair after a prior
            # Redis outage. Duplicate messages are harmless because claim is CAS.
            if job.status == "queued":
                await self.broker.initialize()
                await self.broker.enqueue(job.job_id)
            if self.telemetry is not None:
                self.telemetry.count(
                    "aurumlab.job.submissions",
                    attributes={"created": created, "status": job.status},
                )
            return job, created


class AgentWorker:
    def __init__(
        self,
        *,
        worker_id: str,
        agent: CheckpointedAgent,
        jobs: JobRepository,
        broker: RedisStreamBroker,
        lease_seconds: int = 30,
        stage_timeout_seconds: float = 30.0,
        telemetry: Telemetry | None = None,
    ):
        self.worker_id = worker_id
        self.agent = agent
        self.jobs = jobs
        self.broker = broker
        self.lease_seconds = lease_seconds
        self.stage_timeout_seconds = stage_timeout_seconds
        self.telemetry = telemetry or getattr(agent, "telemetry", None)

    async def run_once(self, *, block_ms: int = 1_000, reclaim_idle_ms: int = 30_000) -> bool:
        reclaimed = await self.broker.reclaim(
            self.worker_id,
            min_idle_ms=reclaim_idle_ms,
            count=1,
        )
        messages = reclaimed or await self.broker.read(
            self.worker_id,
            count=1,
            block_ms=block_ms,
        )
        if not messages:
            return False
        await self._process(messages[0])
        return True

    async def _process(self, message: StreamMessage) -> None:
        traceparent, tracestate = self.jobs.get_trace_context(message.job_id)
        parent = (
            self.telemetry.extract(TraceCarrier(traceparent, tracestate))
            if self.telemetry is not None
            else None
        )
        scope = (
            self.telemetry.span(
                "aurumlab.job.process",
                attributes={"aurumlab.job.id": message.job_id},
                kind=SpanKind.CONSUMER,
                context=parent,
            )
            if self.telemetry is not None
            else nullcontext()
        )
        with scope as span:
            await self._process_claimed(message, span)

    async def _process_claimed(self, message: StreamMessage, span) -> None:
        job = self.jobs.claim(message.job_id, self.worker_id, self.lease_seconds)
        if job is None:
            current = self.jobs.get(message.job_id)
            if current is None or current.status in {
                *self.jobs.terminal_statuses,
                "waiting_approval",
            }:
                await self.broker.ack(message.message_id)
            if self.telemetry is not None:
                self.telemetry.count("aurumlab.job.attempts", attributes={"result": "not_claimed"})
            return
        if span is not None:
            span.set_attribute("aurumlab.job.attempt", job.attempts)
        checkpoint = self.jobs.get_checkpoint(job.job_id)
        if checkpoint is not None:
            self.jobs.append_checkpoint_restored(job.job_id, checkpoint.completed_steps)
            if span is not None:
                span.set_attribute(
                    "aurumlab.checkpoint.restored_steps", len(checkpoint.completed_steps)
                )
            if self.telemetry is not None:
                self.telemetry.count(
                    "aurumlab.job.checkpoint.restores",
                    attributes={"result": "restored"},
                )
        try:
            response = await self.agent.run_checkpointed(
                job.question,
                job_id=job.job_id,
                cache_policy=job.cache_policy,
                checkpoint_sink=self.jobs.save_checkpoint_and_event,
                cancel_probe=lambda: self.jobs.should_cancel(job.job_id),
                stage_timeout_seconds=self.stage_timeout_seconds,
                checkpoint=checkpoint,
                trusted_consumed_approval=self.jobs.get_job_approval(job.job_id),
            )
            if response.status == "pending_approval":
                latest = self.jobs.get_checkpoint(job.job_id)
                if latest is not None:
                    self.jobs.save_checkpoint(
                        latest.model_copy(
                            update={
                                "events": response.events,
                                "tool_audit": response.tool_audit,
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )
                self.jobs.transition_waiting_approval(job.job_id, self.worker_id, response.run_id)
            elif response.status == "failed":
                self.jobs.transition_terminal(
                    job.job_id,
                    self.worker_id,
                    "failed",
                    run_id=response.run_id,
                    error_code="agent_failed",
                    error_message=response.summary,
                )
            else:
                self.jobs.transition_terminal(
                    job.job_id,
                    self.worker_id,
                    "completed",
                    run_id=response.run_id,
                )
        except AgentExecutionCancelled:
            self.jobs.transition_terminal(job.job_id, self.worker_id, "cancelled")
        except AgentStageTimeout as exc:
            self.jobs.transition_terminal(
                job.job_id,
                self.worker_id,
                "timed_out",
                error_code="stage_timeout",
                error_message=str(exc),
            )
        except RetryableJobError as exc:
            current = self.jobs.get(job.job_id)
            if current is not None and current.attempts < current.max_attempts:
                self.jobs.schedule_retry(job.job_id, self.worker_id, exc.code, str(exc))
                await self.broker.enqueue(job.job_id)
            else:
                self.jobs.transition_terminal(
                    job.job_id,
                    self.worker_id,
                    "dead_letter",
                    error_code=exc.code,
                    error_message=str(exc),
                )
        except Exception as exc:  # noqa: BLE001 -- isolate one bad Job from the Worker loop
            if span is not None:
                self.telemetry.mark_error(span, type(exc).__name__)
            try:
                self.jobs.transition_terminal(
                    job.job_id,
                    self.worker_id,
                    "failed",
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:300],
                )
            except InvalidJobTransitionError:
                # A concurrent cancellation/lease expiry already established
                # the durable truth. Never overwrite it from a stale Worker.
                pass
        final = self.jobs.get(job.job_id)
        outcome = final.status if final is not None else "missing"
        if span is not None:
            span.set_attribute("aurumlab.job.status", outcome)
        if self.telemetry is not None:
            self.telemetry.count("aurumlab.job.attempts", attributes={"result": outcome})
        await self.broker.ack(message.message_id)
