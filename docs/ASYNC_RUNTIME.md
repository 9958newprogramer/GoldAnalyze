# Async Agent Runtime

## Boundary

v0.8 uses two cooperating stores with deliberately different responsibilities:

- SQLite is the durable source of truth for `AgentJob`, ordered `JobEvent`, checkpoint, lease,
  retry, cancellation, terminal state, and dead-letter metadata.
- Redis Streams is the delivery plane. A stream entry contains only an opaque `job_id`; it does
  not contain the user question, result, Tool arguments, API keys, or approval credentials.
- Workers read through one Consumer Group and acknowledge a message only after a durable state
  transition. An unacknowledged message remains in Redis' Pending Entries List and can be claimed
  by a recovery worker after the lease expires.

This split makes duplicate delivery safe: Redis may deliver at least once, while SQLite compare-
and-set transitions and leases ensure that only one Worker owns an execution attempt.

## State machine

```text
queued ──claim──> running ──success────────────> completed
  │                 │  │  ├─approval──────────> waiting_approval ──grant──> queued
  │                 │  ├─retryable + budget───> queued
  │                 │  ├─retry exhausted──────> dead_letter
  │                 │  ├─non-retryable────────> failed
  │                 │  └─timeout──────────────> timed_out
  │                 └─cancel request──> cancelling ──step boundary──> cancelled
  └─cancel──────────────────────────────────────────────────────────> cancelled
```

Terminal states are immutable. If cancellation is recorded before the Worker commits success,
the repository converts the terminal transition to `cancelled`, so cancellation wins that race.

## Security and reliability invariants

- `Idempotency-Key` is bounded at the HTTP boundary and stored only as SHA-256. Reusing a key
  with identical input returns the existing Job; reusing it with changed input returns conflict.
- Job IDs and public event cursors reveal no request content.
- The Worker lease owner must match every retry or terminal compare-and-set transition.
- Attempts are bounded to 1–3; no exception can create an infinite retry loop.
- Cancellation is cooperative and checked only at deterministic step boundaries, avoiding
  interruption in the middle of a durable state write.
- SSE uses the per-Job monotonic SQLite event ID. `Last-Event-ID` only reads events belonging to
  the Job encoded in the request path.
- Checkpoint JSON is trusted server state, versioned, and validated before restore. Pickle and
  arbitrary code deserialization are forbidden.

## Local development

Production transport uses `redis.asyncio`. Tests use `fakeredis` only as a controllable Redis
substitute for Consumer Group/Pending Entry fault scenarios. Start the pinned Redis service and
separate processes with:

```bash
make redis-up
make run
make worker  # in another terminal
```

Run the opt-in integration test against that real service with:

```bash
AURUMLAB_REDIS_TEST_URL=redis://127.0.0.1:6379/0 \
  .venv/bin/pytest -q -m redis_integration
```

The default verification suite skips this single test when the URL is absent, so CI and local
development do not silently substitute an in-memory implementation for an asserted real-Redis
result.

The Web console retains a Redis-free synchronous mode and adds an Async Job mode that renders
the SSE lifecycle and can request cooperative cancellation. `compose.redis.yaml` binds Redis to
loopback only and enables AOF; this is local-development infrastructure, not a production HA
deployment.
