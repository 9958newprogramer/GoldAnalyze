"""Long-running Redis Streams Worker entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
from uuid import uuid4

from redis.exceptions import RedisError

from app.bootstrap import build_services
from app.jobs.runtime import AgentWorker

logger = logging.getLogger("aurumlab.worker")


async def _bounded_backoff(stopping: asyncio.Event, seconds: float = 1.0) -> None:
    try:
        await asyncio.wait_for(stopping.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def serve() -> None:
    services = build_services()
    await services.mcp_clients.start()
    services.mcp_clients.register_tools(services.agent.tools)
    worker = AgentWorker(
        worker_id=f"{socket.gethostname()}-{uuid4().hex[:8]}",
        agent=services.agent,
        jobs=services.jobs,
        broker=services.job_broker,
        lease_seconds=services.settings.job_lease_seconds,
        stage_timeout_seconds=services.settings.job_stage_timeout_seconds,
        telemetry=services.telemetry,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, stopping.set)
    try:
        while not stopping.is_set():
            try:
                await services.job_broker.initialize()
                await worker.run_once(
                    block_ms=1_000,
                    reclaim_idle_ms=services.settings.job_reclaim_idle_ms,
                )
            except RedisError as exc:
                logger.warning("Redis unavailable; Worker will reconnect: %s", type(exc).__name__)
                await _bounded_backoff(stopping)
    finally:
        await services.mcp_clients.stop()
        await services.job_broker.close()
        services.telemetry.shutdown()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
