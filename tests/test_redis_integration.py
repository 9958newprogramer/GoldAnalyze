import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.jobs import RedisStreamBroker


@pytest.mark.redis_integration
async def test_real_redis_consumer_group_ack_and_pending_reclaim():
    redis_url = os.getenv("AURUMLAB_REDIS_TEST_URL")
    if not redis_url:
        pytest.skip("set AURUMLAB_REDIS_TEST_URL to run the real Redis smoke test")
    suffix = uuid4().hex
    client = Redis.from_url(redis_url, decode_responses=True, protocol=2)
    broker = RedisStreamBroker(
        client,
        stream_name=f"aurumlab:test:{suffix}",
        group_name=f"aurumlab-test-workers-{suffix}",
    )
    try:
        assert await client.ping() is True
        await broker.initialize()
        first_id = await broker.enqueue("a" * 16)
        delivered = await broker.read("crashed-worker", block_ms=10)
        assert delivered[0].message_id == first_id
        pending = await client.xpending(broker.stream_name, broker.group_name)
        assert pending["pending"] == 1

        reclaimed = await broker.reclaim("recovery-worker", min_idle_ms=0)
        assert reclaimed == delivered
        assert await broker.ack(first_id) is True
        assert (await client.xpending(broker.stream_name, broker.group_name))["pending"] == 0
    finally:
        await client.delete(broker.stream_name)
        await broker.close()
