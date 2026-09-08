"""Long-running Redis Streams Worker entry point."""

from __future__ import annotations

import asyncio
import signal
import socket
from uuid import uuid4

from app.bootstrap import build_services
from app.jobs.runtime import AgentWorker


async def serve() -> None:
    services = build_services()
    await services.mcp_clients.start()
    services.mcp_clients.register_tools(services.agent.tools)
    await services.job_broker.initialize()
    worker = AgentWorker(
        worker_id=f"{socket.gethostname()}-{uuid4().hex[:8]}",
        agent=services.agent,
        jobs=services.jobs,
        broker=services.job_broker,
        lease_seconds=services.settings.job_lease_seconds,
        stage_timeout_seconds=services.settings.job_stage_timeout_seconds,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, stopping.set)
    try:
        while not stopping.is_set():
            await worker.run_once(
                block_ms=1_000,
                reclaim_idle_ms=services.settings.job_reclaim_idle_ms,
            )
    finally:
        await services.mcp_clients.stop()
        await services.job_broker.close()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
