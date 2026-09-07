"""SQLite persistence for completed Agent runs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.evals.models import EvalReport
from app.models import RunResponse


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


class RunRepository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    question TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def connect(self) -> sqlite3.Connection:
        return _connect(self.path)

    def save(self, run: RunResponse) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO runs(run_id, status, question, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.status,
                    run.question,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                ),
            )

    def get(self, run_id: str) -> RunResponse | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return RunResponse.model_validate_json(row[0]) if row else None

    def list_recent(self, limit: int = 10) -> list[RunResponse]:
        safe_limit = min(max(limit, 1), 50)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM runs ORDER BY created_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [RunResponse.model_validate_json(row[0]) for row in rows]


class EvalReportRepository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_reports (
                    eval_run_id TEXT PRIMARY KEY,
                    dataset_version TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    score REAL NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def connect(self) -> sqlite3.Connection:
        return _connect(self.path)

    def save(self, report: EvalReport) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO eval_reports(
                    eval_run_id, dataset_version, passed, score, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report.eval_run_id,
                    report.dataset_version,
                    int(report.passed),
                    report.score,
                    report.model_dump_json(),
                    report.created_at.isoformat(),
                ),
            )

    def latest(self) -> EvalReport | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM eval_reports ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return EvalReport.model_validate_json(row[0]) if row else None
