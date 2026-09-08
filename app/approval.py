"""SQLite-backed, one-time human approval state machine for governed Tool calls."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from app.models import ApprovalGrant, ApprovalRequest

MAX_APPROVAL_ARGUMENT_BYTES = 64 * 1024


class ApprovalError(RuntimeError):
    """Base error safe for API translation without exposing stored credentials."""


class ApprovalNotFoundError(ApprovalError):
    pass


class ApprovalStateError(ApprovalError):
    pass


class ApprovalTokenError(ApprovalError):
    pass


class ApprovalBindingError(ApprovalError):
    pass


class ApprovalRequired(ApprovalError):
    """Control-flow signal raised before the reviewed Tool consumes budget or executes."""

    def __init__(self, approval: ApprovalRequest):
        super().__init__("Tool call requires human approval")
        self.approval = approval


@dataclass(frozen=True)
class ApprovalBinding:
    run_id: str
    plan_id: str
    step_id: str
    tool_name: str
    arguments_digest: str
    effect: Literal["read", "external", "write", "privileged"]
    risk: Literal["low", "medium", "high"]
    reason: str


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported approval argument type: {type(value).__name__}")


def arguments_digest(arguments: Mapping[str, Any]) -> str:
    """Bind approval to canonical arguments without persisting their raw values."""
    try:
        encoded = json.dumps(
            _json_safe(arguments),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ApprovalBindingError("Tool arguments cannot be bound to an approval") from exc
    if len(encoded) > MAX_APPROVAL_ARGUMENT_BYTES:
        raise ApprovalBindingError("Tool arguments exceed the approval binding limit")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ApprovalRepository:
    """Persist state transitions atomically and store only hashes of bearer tokens."""

    def __init__(
        self,
        path: Path,
        *,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] = _utc_now,
    ):
        if not 30 <= ttl_seconds <= 3_600:
            raise ValueError("approval ttl must be between 30 and 3600 seconds")
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._now = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    approval_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_digest TEXT NOT NULL,
                    effect TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    token_hash TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    consumed_at TEXT,
                    decided_by TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_approval_binding
                ON approval_requests(run_id, plan_id, step_id, tool_name, arguments_digest)
                """
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def request(self, binding: ApprovalBinding) -> ApprovalRequest:
        now = self._aware_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_matching(connection, binding, now)
            row = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE run_id = ? AND plan_id = ? AND step_id = ? AND tool_name = ?
                  AND arguments_digest = ? AND status IN ('pending', 'approved')
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    binding.run_id,
                    binding.plan_id,
                    binding.step_id,
                    binding.tool_name,
                    binding.arguments_digest,
                ),
            ).fetchone()
            if row is None:
                approval_id = secrets.token_hex(8)
                expires_at = now + timedelta(seconds=self.ttl_seconds)
                connection.execute(
                    """
                    INSERT INTO approval_requests(
                        approval_id, run_id, plan_id, step_id, tool_name, arguments_digest,
                        effect, risk, status, reason, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        approval_id,
                        binding.run_id,
                        binding.plan_id,
                        binding.step_id,
                        binding.tool_name,
                        binding.arguments_digest,
                        binding.effect,
                        binding.risk,
                        binding.reason,
                        now.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
                row = self._load(connection, approval_id)
            return self._public(row)

    def get(self, approval_id: str) -> ApprovalRequest:
        now = self._aware_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._load(connection, approval_id)
            row = self._expire_row(connection, row, now)
            return self._public(row)

    def approve(self, approval_id: str, *, decided_by: str) -> ApprovalGrant:
        now = self._aware_now()
        secret = secrets.token_urlsafe(32)
        token = f"{approval_id}.{secret}"
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_row(connection, self._load(connection, approval_id), now)
            if row["status"] != "pending":
                connection.commit()
                raise ApprovalStateError("approval is not pending")
            updated = connection.execute(
                """
                UPDATE approval_requests
                SET status = 'approved', token_hash = ?, decided_at = ?, decided_by = ?
                WHERE approval_id = ? AND status = 'pending'
                """,
                (_token_hash(token), now.isoformat(), decided_by, approval_id),
            )
            if updated.rowcount != 1:
                raise ApprovalStateError("approval state changed concurrently")
            approved = self._public(self._load(connection, approval_id))
        return ApprovalGrant(approval=approved, approval_token=token)

    def deny(self, approval_id: str, *, decided_by: str) -> ApprovalRequest:
        now = self._aware_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_row(connection, self._load(connection, approval_id), now)
            if row["status"] != "pending":
                connection.commit()
                raise ApprovalStateError("approval is not pending")
            updated = connection.execute(
                """
                UPDATE approval_requests
                SET status = 'denied', decided_at = ?, decided_by = ?, token_hash = NULL
                WHERE approval_id = ? AND status = 'pending'
                """,
                (now.isoformat(), decided_by, approval_id),
            )
            if updated.rowcount != 1:
                raise ApprovalStateError("approval state changed concurrently")
            return self._public(self._load(connection, approval_id))

    def validate_token(self, token: str, *, approval_id: str, run_id: str) -> ApprovalRequest:
        token_approval_id = self._parse_token(token)
        if not hmac.compare_digest(token_approval_id, approval_id):
            raise ApprovalTokenError("approval token is bound to a different request")
        now = self._aware_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_row(connection, self._load(connection, approval_id), now)
            if row["status"] != "approved":
                connection.commit()
                raise ApprovalStateError("approval token is not active")
            self._assert_token_and_run(row, token, run_id)
            return self._public(row)

    def consume(self, token: str, binding: ApprovalBinding) -> ApprovalRequest:
        approval_id = self._parse_token(token)
        now = self._aware_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_row(connection, self._load(connection, approval_id), now)
            if row["status"] != "approved":
                connection.commit()
                raise ApprovalStateError("approval token is not active")
            self._assert_token_and_run(row, token, binding.run_id)
            expected = (
                binding.plan_id,
                binding.step_id,
                binding.tool_name,
                binding.arguments_digest,
                binding.effect,
                binding.risk,
            )
            actual = (
                row["plan_id"],
                row["step_id"],
                row["tool_name"],
                row["arguments_digest"],
                row["effect"],
                row["risk"],
            )
            if not all(
                hmac.compare_digest(str(left), str(right))
                for left, right in zip(expected, actual, strict=True)
            ):
                raise ApprovalBindingError("approval does not match this Tool call")
            updated = connection.execute(
                """
                UPDATE approval_requests
                SET status = 'consumed', consumed_at = ?, token_hash = NULL
                WHERE approval_id = ? AND status = 'approved' AND token_hash = ?
                """,
                (now.isoformat(), approval_id, _token_hash(token)),
            )
            if updated.rowcount != 1:
                raise ApprovalStateError("approval was already consumed")
            return self._public(self._load(connection, approval_id))

    def pending_count(self) -> int:
        now = self._aware_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE approval_requests SET status = 'expired', token_hash = NULL
                WHERE status IN ('pending', 'approved') AND expires_at <= ?
                """,
                (now.isoformat(),),
            )
            row = connection.execute(
                "SELECT COUNT(*) FROM approval_requests WHERE status = 'pending'"
            ).fetchone()
            return int(row[0])

    def _aware_now(self) -> datetime:
        value = self._now()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _parse_token(token: str) -> str:
        parts = token.split(".", maxsplit=1)
        if (
            len(parts) != 2
            or len(parts[0]) != 16
            or any(character not in "0123456789abcdef" for character in parts[0])
            or len(parts[1]) < 32
        ):
            raise ApprovalTokenError("invalid approval token")
        return parts[0]

    @staticmethod
    def _load(connection: sqlite3.Connection, approval_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM approval_requests WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ApprovalNotFoundError("approval request not found")
        return row

    @staticmethod
    def _public(row: sqlite3.Row) -> ApprovalRequest:
        payload = dict(row)
        payload.pop("token_hash", None)
        return ApprovalRequest.model_validate(payload)

    @staticmethod
    def _assert_token_and_run(row: sqlite3.Row, token: str, run_id: str) -> None:
        stored_hash = row["token_hash"] or ""
        if not hmac.compare_digest(row["run_id"], run_id):
            raise ApprovalBindingError("approval is bound to a different run")
        if not hmac.compare_digest(stored_hash, _token_hash(token)):
            raise ApprovalTokenError("invalid approval token")

    @staticmethod
    def _expire_row(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now: datetime,
    ) -> sqlite3.Row:
        if (
            row["status"] in {"pending", "approved"}
            and datetime.fromisoformat(row["expires_at"]) <= now
        ):
            connection.execute(
                """
                UPDATE approval_requests SET status = 'expired', token_hash = NULL
                WHERE approval_id = ? AND status IN ('pending', 'approved')
                """,
                (row["approval_id"],),
            )
            return ApprovalRepository._load(connection, row["approval_id"])
        return row

    @staticmethod
    def _expire_matching(
        connection: sqlite3.Connection,
        binding: ApprovalBinding,
        now: datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE approval_requests SET status = 'expired', token_hash = NULL
            WHERE run_id = ? AND plan_id = ? AND step_id = ? AND tool_name = ?
              AND arguments_digest = ? AND status IN ('pending', 'approved')
              AND expires_at <= ?
            """,
            (
                binding.run_id,
                binding.plan_id,
                binding.step_id,
                binding.tool_name,
                binding.arguments_digest,
                now.isoformat(),
            ),
        )
