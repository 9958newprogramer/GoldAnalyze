import fakeredis.aioredis

from app.jobs.broker import RedisStreamBroker


async def test_redis_stream_consumer_group_delivers_and_acknowledges_job_id_only():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    broker = RedisStreamBroker(client)
    await broker.initialize()

    message_id = await broker.enqueue("a" * 16)
    messages = await broker.read("worker-a", block_ms=1)

    assert messages[0].message_id == message_id
    assert messages[0].job_id == "a" * 16
    assert await broker.ack(message_id) is True
    assert await client.xpending(broker.stream_name, broker.group_name) == {
        "pending": 0,
        "min": None,
        "max": None,
        "consumers": [],
    }
    await broker.close()


async def test_crashed_consumers_pending_message_can_be_reclaimed():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    broker = RedisStreamBroker(client)
    await broker.initialize()
    await broker.enqueue("b" * 16)
    delivered = await broker.read("crashed-worker", block_ms=1)

    reclaimed = await broker.reclaim("recovery-worker", min_idle_ms=0)

    assert reclaimed == delivered
    assert await broker.ack(reclaimed[0].message_id) is True
    await broker.close()


async def test_initialize_is_idempotent():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    broker = RedisStreamBroker(client)

    await broker.initialize()
    await broker.initialize()

    assert await client.exists(broker.stream_name) == 1
    await broker.close()
