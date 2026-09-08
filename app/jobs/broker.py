"""Redis Streams transport; durable job state remains in SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError


@dataclass(frozen=True)
class StreamMessage:
    message_id: str
    job_id: str


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


class RedisStreamBroker:
    """Small, testable Redis Streams Consumer Group boundary.

    Messages intentionally contain only an opaque job ID. Questions, approval
    credentials, checkpoints, and results never enter the Redis stream.
    """

    def __init__(
        self,
        client: Redis,
        *,
        stream_name: str = "aurumlab:jobs:v1",
        group_name: str = "aurumlab-workers-v1",
    ):
        self.client = client
        self.stream_name = stream_name
        self.group_name = group_name

    async def initialize(self) -> None:
        try:
            await self.client.xgroup_create(
                name=self.stream_name,
                groupname=self.group_name,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, job_id: str) -> str:
        message_id = await self.client.xadd(self.stream_name, {"job_id": job_id})
        return _text(message_id)

    async def read(
        self,
        consumer_name: str,
        *,
        count: int = 1,
        block_ms: int = 1_000,
    ) -> list[StreamMessage]:
        response = await self.client.xreadgroup(
            groupname=self.group_name,
            consumername=consumer_name,
            streams={self.stream_name: ">"},
            count=min(max(count, 1), 20),
            block=max(block_ms, 0),
        )
        return self._messages(response)

    async def reclaim(
        self,
        consumer_name: str,
        *,
        min_idle_ms: int,
        count: int = 10,
    ) -> list[StreamMessage]:
        response = await self.client.xautoclaim(
            name=self.stream_name,
            groupname=self.group_name,
            consumername=consumer_name,
            min_idle_time=max(min_idle_ms, 0),
            start_id="0-0",
            count=min(max(count, 1), 20),
        )
        claimed: Any
        if not response:
            return []
        # RESP2 returns [next_id, messages, deleted_ids]; RESP3 clients may
        # omit deleted_ids. redis-py normalizes both to this list-like shape.
        claimed = response[1]
        return self._entries(claimed)

    async def ack(self, message_id: str) -> bool:
        return bool(await self.client.xack(self.stream_name, self.group_name, message_id))

    async def close(self) -> None:
        await self.client.aclose()

    def _messages(self, response: Any) -> list[StreamMessage]:
        output: list[StreamMessage] = []
        for _, entries in response or []:
            output.extend(self._entries(entries))
        return output

    @staticmethod
    def _entries(entries: Any) -> list[StreamMessage]:
        output: list[StreamMessage] = []
        for message_id, fields in entries or []:
            decoded = {_text(key): _text(value) for key, value in fields.items()}
            job_id = decoded.get("job_id")
            if job_id is not None:
                output.append(StreamMessage(message_id=_text(message_id), job_id=job_id))
        return output
