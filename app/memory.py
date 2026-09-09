"""Deterministic task fingerprints and a SQLite-backed artifact memory."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from app.models import ArtifactSnapshot, CacheInfo, CacheStats, RunResponse

CacheableIntent = Literal[
    "backtest_strategy",
    "query_market_data",
    "external_research",
    "incident_review",
]
CACHE_SCHEMA_VERSION = "artifact-cache-v2"


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _digest(payload: Any, length: int = 64) -> str:
    encoded = json.dumps(
        _canonical(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()[:length]


def _normalized_query(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


@dataclass(frozen=True)
class TaskFingerprint:
    intent: CacheableIntent
    spec: dict[str, Any]
    data_version: str
    fingerprint: str
    semantic_key: str

    @property
    def public_fingerprint(self) -> str:
        return self.fingerprint[:16]


def build_task_fingerprint(
    intent: CacheableIntent,
    spec: BaseModel | dict[str, Any],
    data_version: str,
) -> TaskFingerprint:
    normalized = _canonical(spec)
    if intent == "external_research":
        normalized = {
            **normalized,
            "query": _normalized_query(str(normalized.get("query", ""))),
        }
        semantic_bucket: dict[str, Any] = {"schema": CACHE_SCHEMA_VERSION, "intent": intent}
    elif intent == "incident_review":
        semantic_bucket = {
            "schema": CACHE_SCHEMA_VERSION,
            "intent": intent,
            "service": normalized.get("service"),
        }
    else:
        semantic_bucket = {
            "schema": CACHE_SCHEMA_VERSION,
            "intent": intent,
            "symbol": normalized.get("symbol"),
            "timeframe": normalized.get("timeframe"),
            "strategy_type": normalized.get("strategy_type"),
        }
    material = {
        "schema": CACHE_SCHEMA_VERSION,
        "intent": intent,
        "spec": normalized,
        "data_version": data_version,
    }
    return TaskFingerprint(
        intent=intent,
        spec=normalized,
        data_version=data_version,
        fingerprint=_digest(material),
        semantic_key=_digest(semantic_bucket, length=32),
    )


def _number_similarity(left: Any, right: Any) -> float:
    if left is None and right is None:
        return 1.0
    if left is None or right is None:
        return 0.0
    a, b = float(left), float(right)
    scale = max(abs(a), abs(b), 1.0)
    return max(0.0, 1.0 - abs(a - b) / scale)


def _token_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", left.casefold()))
    right_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", right.casefold()))
    if not left_tokens and not right_tokens:
        return 1.0
    return len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)


def task_similarity(intent: CacheableIntent, left: dict[str, Any], right: dict[str, Any]) -> float:
    """Score structured task similarity; candidates are never auto-executed as answers."""
    if intent == "backtest_strategy":
        numeric = ["fast_window", "slow_window", "fee_bps", "slippage_bps", "initial_cash"]
        scores = [_number_similarity(left.get(field), right.get(field)) for field in numeric]
        scores.extend(
            1.0 if left.get(field) == right.get(field) else 0.0
            for field in ["start_date", "end_date", "execution", "long_only"]
        )
        return round(sum(scores) / len(scores), 4)
    if intent == "query_market_data":
        scores = [
            _number_similarity(left.get("limit"), right.get("limit")),
            1.0 if left.get("start_date") == right.get("start_date") else 0.0,
            1.0 if left.get("end_date") == right.get("end_date") else 0.0,
        ]
        return round(sum(scores) / len(scores), 4)
    if intent == "incident_review":
        numeric = ["error_rate_pct", "p95_latency_ms", "duration_minutes", "affected_requests"]
        scores = [_number_similarity(left.get(field), right.get(field)) for field in numeric]
        scores.append(1.0 if left.get("service") == right.get("service") else 0.0)
        return round(sum(scores) / len(scores), 4)
    return round(_token_similarity(str(left.get("query", "")), str(right.get("query", ""))), 4)


@dataclass(frozen=True)
class CacheLookup:
    info: CacheInfo
    source: ArtifactSnapshot | None = None


class ArtifactCache:
    """Persistent, bounded artifact reuse with version and TTL invalidation."""

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        semantic_threshold: float = 0.82,
        max_entries: int = 200,
    ):
        self.path = path
        self.enabled = enabled
        self.semantic_threshold = semantic_threshold
        self.max_entries = max(1, max_entries)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_cache (
                    fingerprint TEXT PRIMARY KEY,
                    semantic_key TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    last_hit_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_artifact_cache_candidates_v2
                ON artifact_cache(intent, semantic_key, data_version, created_at DESC)
                """
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _is_active(expires_at: str | None, now: datetime) -> bool:
        return expires_at is None or datetime.fromisoformat(expires_at) > now

    def lookup(self, task: TaskFingerprint, *, now: datetime | None = None) -> CacheLookup:
        checked_at = now or datetime.now(UTC)
        if not self.enabled:
            return CacheLookup(
                CacheInfo(
                    status="bypass",
                    fingerprint=task.public_fingerprint,
                    data_version=task.data_version,
                    reason="artifact_cache_disabled",
                )
            )

        with self.connect() as connection:
            exact = connection.execute(
                """
                SELECT source_run_id, payload, expires_at
                FROM artifact_cache WHERE fingerprint = ?
                """,
                (task.fingerprint,),
            ).fetchone()
            if exact and self._is_active(exact[2], checked_at):
                connection.execute(
                    """
                    UPDATE artifact_cache
                    SET hit_count = hit_count + 1, last_hit_at = ?
                    WHERE fingerprint = ?
                    """,
                    (checked_at.isoformat(), task.fingerprint),
                )
                source = ArtifactSnapshot.model_validate_json(exact[1])
                return CacheLookup(
                    CacheInfo(
                        status="exact_hit",
                        fingerprint=task.public_fingerprint,
                        data_version=task.data_version,
                        source_run_id=exact[0],
                        similarity_score=1.0,
                        saved_tool_calls=source.source_tool_calls,
                        saved_latency_ms=source.source_tool_latency_ms,
                        expires_at=(datetime.fromisoformat(exact[2]) if exact[2] else None),
                        reason="normalized_spec_and_data_version_match",
                    ),
                    source,
                )

            candidate_rows = connection.execute(
                """
                SELECT source_run_id, spec_json, expires_at
                FROM artifact_cache
                WHERE intent = ? AND semantic_key = ? AND data_version = ?
                  AND fingerprint != ?
                ORDER BY created_at DESC LIMIT 20
                """,
                (task.intent, task.semantic_key, task.data_version, task.fingerprint),
            ).fetchall()
            best: tuple[float, tuple[Any, ...]] | None = None
            for row in candidate_rows:
                if not self._is_active(row[2], checked_at):
                    continue
                score = task_similarity(task.intent, task.spec, json.loads(row[1]))
                if score >= self.semantic_threshold and (best is None or score > best[0]):
                    best = (score, row)
            if best:
                score, row = best
                return CacheLookup(
                    CacheInfo(
                        status="semantic_candidate",
                        fingerprint=task.public_fingerprint,
                        data_version=task.data_version,
                        source_run_id=row[0],
                        similarity_score=score,
                        expires_at=(datetime.fromisoformat(row[2]) if row[2] else None),
                        reason="similar_spec_found_but_execution_required",
                    )
                )

            reason = "no_reusable_artifact"
            if exact:
                reason = "artifact_expired"
            else:
                stale_version = connection.execute(
                    """
                    SELECT 1 FROM artifact_cache
                    WHERE intent = ? AND semantic_key = ? AND data_version != ? LIMIT 1
                    """,
                    (task.intent, task.semantic_key, task.data_version),
                ).fetchone()
                if stale_version:
                    reason = "data_version_changed"
        return CacheLookup(
            CacheInfo(
                status="miss",
                fingerprint=task.public_fingerprint,
                data_version=task.data_version,
                reason=reason,
            )
        )

    def store(
        self,
        task: TaskFingerprint,
        run: RunResponse,
        *,
        ttl_seconds: int | None,
        now: datetime | None = None,
    ) -> None:
        if not self.enabled or run.status != "completed":
            return
        created_at = now or datetime.now(UTC)
        expires_at = created_at + timedelta(seconds=ttl_seconds) if ttl_seconds else None
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO artifact_cache(
                    fingerprint, semantic_key, intent, spec_json, data_version,
                    source_run_id, payload, created_at, expires_at, hit_count, last_hit_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                """,
                (
                    task.fingerprint,
                    task.semantic_key,
                    task.intent,
                    json.dumps(task.spec, ensure_ascii=False, sort_keys=True),
                    task.data_version,
                    run.run_id,
                    ArtifactSnapshot.from_run(run).model_dump_json(),
                    created_at.isoformat(),
                    expires_at.isoformat() if expires_at else None,
                ),
            )
            connection.execute(
                "DELETE FROM artifact_cache WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (created_at.isoformat(),),
            )
            connection.execute(
                """
                DELETE FROM artifact_cache
                WHERE fingerprint IN (
                    SELECT fingerprint FROM artifact_cache
                    ORDER BY created_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.max_entries,),
            )

    def stats(self, *, now: datetime | None = None) -> CacheStats:
        checked_at = (now or datetime.now(UTC)).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN expires_at IS NULL OR expires_at > ? THEN 1 ELSE 0 END),
                    SUM(CASE WHEN expires_at IS NOT NULL AND expires_at <= ? THEN 1 ELSE 0 END),
                    COALESCE(SUM(hit_count), 0)
                FROM artifact_cache
                """,
                (checked_at, checked_at),
            ).fetchone()
        return CacheStats(
            enabled=self.enabled,
            max_entries=self.max_entries,
            entries=int(row[0] or 0),
            active_entries=int(row[1] or 0),
            expired_entries=int(row[2] or 0),
            exact_hits=int(row[3] or 0),
        )
