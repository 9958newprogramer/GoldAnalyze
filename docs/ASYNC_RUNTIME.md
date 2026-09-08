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
  │                 │  │  ├─approval──────────> waiting_approval
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
substitute for Consumer Group/Pending Entry fault scenarios. A real Redis container and the
complete worker command are added before v0.8 is marked verified.
