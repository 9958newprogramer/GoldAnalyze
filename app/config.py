"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _port_from_env(name: str, default: int) -> int:
    """Return a valid TCP port without letting a malformed env value break imports."""
    try:
        port = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return port if 1 <= port <= 65535 else default


def _bounded_float_from_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


def _bool_from_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int_from_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


@dataclass(frozen=True)
class Settings:
    app_name: str = "AurumLab"
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = _port_from_env("APP_PORT", 8010)
    app_database_path: str = os.getenv("APP_DATABASE_PATH", "var/aurumlab.db")
    eval_dataset_path: str = os.getenv("EVAL_DATASET_PATH", "evals/golden.v5.jsonl")
    router_llm_api_key: str | None = os.getenv("ROUTER_LLM_API_KEY") or None
    router_llm_base_url: str = os.getenv("ROUTER_LLM_BASE_URL", "https://api.openai.com/v1")
    router_llm_model: str = os.getenv("ROUTER_LLM_MODEL", "gpt-5-mini")
    router_llm_timeout_seconds: float = _bounded_float_from_env(
        "ROUTER_LLM_TIMEOUT_SECONDS", 8.0, 1.0, 30.0
    )
    router_llm_disable_thinking: bool = _bool_from_env("ROUTER_LLM_DISABLE_THINKING")
    llm_api_key: str | None = os.getenv("LLM_API_KEY") or None
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-5-mini")
    tavily_api_key: str | None = os.getenv("TAVILY_API_KEY") or None
    tavily_base_url: str = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com")
    market_db_path: str | None = os.getenv("MARKET_DB_PATH") or None
    market_table: str = os.getenv("MARKET_TABLE", "gold_bars")
    market_time_column: str = os.getenv("MARKET_TIME_COLUMN", "timestamp")
    market_symbol_column: str = os.getenv("MARKET_SYMBOL_COLUMN", "symbol")
    market_timeframe_column: str = os.getenv("MARKET_TIMEFRAME_COLUMN", "timeframe")
    market_open_column: str = os.getenv("MARKET_OPEN_COLUMN", "open")
    market_high_column: str = os.getenv("MARKET_HIGH_COLUMN", "high")
    market_low_column: str = os.getenv("MARKET_LOW_COLUMN", "low")
    market_close_column: str = os.getenv("MARKET_CLOSE_COLUMN", "close")
    market_volume_column: str = os.getenv("MARKET_VOLUME_COLUMN", "volume")
    market_symbol: str = os.getenv("MARKET_SYMBOL", "XAUUSD")
    artifact_cache_enabled: bool = _bool_from_env("ARTIFACT_CACHE_ENABLED", True)
    artifact_cache_max_entries: int = _bounded_int_from_env(
        "ARTIFACT_CACHE_MAX_ENTRIES", 200, 10, 10_000
    )
    market_cache_ttl_seconds: int = _bounded_int_from_env(
        "MARKET_CACHE_TTL_SECONDS", 300, 5, 86_400
    )
    research_cache_ttl_seconds: int = _bounded_int_from_env(
        "RESEARCH_CACHE_TTL_SECONDS", 900, 5, 86_400
    )
    approval_ttl_seconds: int = _bounded_int_from_env("APPROVAL_TTL_SECONDS", 300, 30, 3_600)
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    job_stream_name: str = os.getenv("JOB_STREAM_NAME", "aurumlab:jobs:v1")
    job_consumer_group: str = os.getenv("JOB_CONSUMER_GROUP", "aurumlab-workers-v1")
    job_lease_seconds: int = _bounded_int_from_env("JOB_LEASE_SECONDS", 60, 5, 600)
    job_stage_timeout_seconds: float = _bounded_float_from_env(
        "JOB_STAGE_TIMEOUT_SECONDS", 30.0, 0.05, 300.0
    )
    job_reclaim_idle_ms: int = _bounded_int_from_env("JOB_RECLAIM_IDLE_MS", 60_000, 1_000, 600_000)
    sse_poll_interval_seconds: float = _bounded_float_from_env(
        "SSE_POLL_INTERVAL_SECONDS", 0.25, 0.01, 5.0
    )
    sse_heartbeat_seconds: float = _bounded_float_from_env("SSE_HEARTBEAT_SECONDS", 10.0, 0.1, 60.0)
    cache_semantic_threshold: float = _bounded_float_from_env(
        "CACHE_SEMANTIC_THRESHOLD", 0.82, 0.5, 1.0
    )

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    @property
    def resolved_app_database_path(self) -> Path:
        path = Path(self.app_database_path)
        return path if path.is_absolute() else self.project_root / path

    @property
    def resolved_eval_dataset_path(self) -> Path:
        path = Path(self.eval_dataset_path)
        return path if path.is_absolute() else self.project_root / path


settings = Settings()
