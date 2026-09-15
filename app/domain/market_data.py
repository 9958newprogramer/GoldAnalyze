"""Read-only market data adapters with an immediately runnable demo fallback."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import psycopg

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.models import DataProfile, StrategySpec


@dataclass(frozen=True)
class Bar:
    at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataRepository(Protocol):
    source_name: str
    synthetic: bool

    def load(self, spec: StrategySpec) -> list[Bar]: ...

    def data_version(self, spec: StrategySpec) -> str: ...


def _within_range(value: datetime, start: date | None, end: date | None) -> bool:
    value_date = value.date()
    return (start is None or value_date >= start) and (end is None or value_date <= end)


class DemoMarketDataRepository:
    """Generate deterministic OHLCV bars so a fresh clone works without private data."""

    source_name = "deterministic-demo"
    synthetic = True

    def data_version(self, spec: StrategySpec) -> str:
        return f"demo-v1:{spec.symbol}:{spec.timeframe}"

    @staticmethod
    def _random(state: int) -> tuple[int, float]:
        state = (1_664_525 * state + 1_013_904_223) % (2**32)
        return state, state / (2**32)

    def load(self, spec: StrategySpec) -> list[Bar]:
        state = 2_026_090_6
        previous_close = 1_270.0
        bars: list[Bar] = []
        current = date(2018, 1, 1)
        end = date(2025, 12, 31)
        index = 0

        while current <= end:
            if current.weekday() < 5:
                hours = [0] if spec.timeframe == "1d" else list(range(24))
                for hour in hours:
                    at = datetime.combine(current, time(hour=hour), tzinfo=UTC)
                    state, noise_1 = self._random(state)
                    state, noise_2 = self._random(state)
                    state, noise_3 = self._random(state)

                    trend = 0.018 if spec.timeframe == "1d" else 0.001
                    cycle = math.sin(index / (42 if spec.timeframe == "1d" else 360))
                    volatility = 9.0 if spec.timeframe == "1d" else 2.1
                    gap = (noise_1 - 0.5) * volatility * 0.35
                    change = trend + cycle * volatility * 0.08 + (noise_2 - 0.5) * volatility
                    open_price = max(100.0, previous_close + gap)
                    close = max(100.0, open_price + change)
                    wick = 0.5 + noise_3 * volatility * 0.5
                    high = max(open_price, close) + wick
                    low = max(1.0, min(open_price, close) - wick)
                    volume = 10_000 + (noise_1 + noise_2) * 25_000
                    previous_close = close
                    index += 1

                    if _within_range(at, spec.start_date, spec.end_date):
                        bars.append(
                            Bar(
                                at=at,
                                open=round(open_price, 4),
                                high=round(high, 4),
                                low=round(low, 4),
                                close=round(close, 4),
                                volume=round(volume, 2),
                            )
                        )
            current += timedelta(days=1)

        return bars


_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_identifier(value: str) -> str:
    if not _SQL_IDENTIFIER.fullmatch(value):
        raise ValueError(f"不安全的 SQLite 标识符：{value!r}")
    return f'"{value}"'


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

class PostgresMarketDataRepository:
    """从只读 PostgreSQL 黄金行情库加载真实 OHLCV 数据。"""

    synthetic = False
    source_name = "postgresql:gold"

    def __init__(self, settings: Settings):
        if not settings.market_postgres_dsn:
            raise ValueError("MARKET_POSTGRES_DSN 未配置")

        self.dsn = settings.market_postgres_dsn

    def data_version(self, spec: StrategySpec) -> str:
        """根据当前查询范围和行情更新时间生成稳定的数据版本。"""

        if spec.timeframe == "1d":
            sql = """
                SELECT
                    COUNT(*),
                    MIN(trade_date),
                    MAX(trade_date),
                    MAX(as_of)
                FROM gold.daily_bar
                WHERE symbol = %s
                  AND trade_date >= %s
                  AND trade_date <= %s
            """
            params = (
                spec.symbol,
                spec.start_date,
                spec.end_date,
            )

        elif spec.timeframe == "1h":
            sql = """
                SELECT
                    COUNT(*),
                    MIN(bar_time),
                    MAX(bar_time),
                    MAX(as_of)
                FROM gold.hourly_bar
                WHERE symbol = %s
                  AND bar_time >= %s
                  AND bar_time < %s
            """

            start_time = datetime.combine(
                spec.start_date,
                time.min,
                tzinfo=UTC,
            )
            end_time = datetime.combine(
                spec.end_date + timedelta(days=1),
                time.min,
                tzinfo=UTC,
            )

            params = (
                spec.symbol,
                start_time,
                end_time,
            )

        else:
            raise ValueError(
                f"PostgreSQL 暂不支持该回测周期：{spec.timeframe}"
            )

        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()

        material = {
            "source": self.source_name,
            "symbol": spec.symbol,
            "timeframe": spec.timeframe,
            "start_date": spec.start_date,
            "end_date": spec.end_date,
            "row_count": row[0],
            "first_bar": row[1],
            "last_bar": row[2],
            "as_of": row[3],
        }

        digest = hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()[:16]

        return f"postgres-v1:{digest}"

    def load(self, spec: StrategySpec) -> list[Bar]:
        """按 StrategySpec 的标的、周期和时间范围读取真实 K 线。"""

        if spec.timeframe == "1d":
            sql = """
                SELECT
                    trade_date,
                    open,
                    high,
                    low,
                    close,
                    volume
                FROM gold.daily_bar
                WHERE symbol = %s
                  AND trade_date >= %s
                  AND trade_date <= %s
                ORDER BY trade_date ASC
            """

            params = (
                spec.symbol,
                spec.start_date,
                spec.end_date,
            )

        elif spec.timeframe == "1h":
            sql = """
                SELECT
                    bar_time,
                    open,
                    high,
                    low,
                    close,
                    volume
                FROM gold.hourly_bar
                WHERE symbol = %s
                  AND bar_time >= %s
                  AND bar_time < %s
                ORDER BY bar_time ASC
            """

            start_time = datetime.combine(
                spec.start_date,
                time.min,
                tzinfo=UTC,
            )
            end_time = datetime.combine(
                spec.end_date + timedelta(days=1),
                time.min,
                tzinfo=UTC,
            )

            params = (
                spec.symbol,
                start_time,
                end_time,
            )

        else:
            raise ValueError(
                f"PostgreSQL 暂不支持该回测周期：{spec.timeframe}"
            )

        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()

        return [
            Bar(
                at=_parse_timestamp(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5] or 0),
            )
            for row in rows
        ]


class SQLiteMarketDataRepository:
    """Load OHLCV bars through a parameterized, read-only SQLite connection."""

    synthetic = False

    def __init__(self, settings: Settings):
        if not settings.market_db_path:
            raise ValueError("MARKET_DB_PATH 未配置")
        self.settings = settings
        self.path = Path(settings.market_db_path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"行情数据库不存在：{self.path}")
        self.source_name = f"sqlite:{self.path.name}"

    def data_version(self, spec: StrategySpec) -> str:
        """Fingerprint file and adapter metadata without exposing the absolute path."""
        stat = self.path.stat()
        material = {
            "path": str(self.path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "table": self.settings.market_table,
            "symbol": spec.symbol,
            "timeframe": spec.timeframe,
            "columns": [
                self.settings.market_time_column,
                self.settings.market_open_column,
                self.settings.market_high_column,
                self.settings.market_low_column,
                self.settings.market_close_column,
                self.settings.market_volume_column,
            ],
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        return f"sqlite-v1:{digest}"

    def load(self, spec: StrategySpec) -> list[Bar]:
        columns = [
            self.settings.market_time_column,
            self.settings.market_open_column,
            self.settings.market_high_column,
            self.settings.market_low_column,
            self.settings.market_close_column,
            self.settings.market_volume_column,
        ]
        selected = ", ".join(_safe_identifier(item) for item in columns)
        table = _safe_identifier(self.settings.market_table)
        symbol_column = _safe_identifier(self.settings.market_symbol_column)
        timeframe_column = _safe_identifier(self.settings.market_timeframe_column)
        time_column = _safe_identifier(self.settings.market_time_column)

        query = (
            f"SELECT {selected} FROM {table} "
            f"WHERE {symbol_column} = ? AND {timeframe_column} = ? "
            f"ORDER BY {time_column} ASC"
        )
        uri = f"file:{self.path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute(query, (spec.symbol, spec.timeframe)).fetchall()

        bars = [
            Bar(
                at=_parse_timestamp(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5] or 0),
            )
            for row in rows
        ]
        return [bar for bar in bars if _within_range(bar.at, spec.start_date, spec.end_date)]


def build_market_repository(settings: Settings) -> MarketDataRepository:
    """根据配置选择真实 PostgreSQL、SQLite 或 Demo 行情源。"""

    if settings.market_postgres_dsn:
        return PostgresMarketDataRepository(settings)

    if settings.market_db_path:
        return SQLiteMarketDataRepository(settings)

    return DemoMarketDataRepository()


def profile_bars(
    repository: MarketDataRepository,
    spec: StrategySpec,
    bars: list[Bar],
) -> DataProfile:
    if not bars:
        raise ValueError("选定的数据范围没有可用 K 线")

    timestamps = [bar.at for bar in bars]
    duplicate_count = len(timestamps) - len(set(timestamps))
    non_positive = sum(1 for bar in bars if min(bar.open, bar.high, bar.low, bar.close) <= 0)
    warnings: list[str] = []
    if repository.synthetic:
        warnings.append("当前使用确定性合成数据，仅用于演示 Agent 工程闭环。")
    if duplicate_count:
        warnings.append(f"检测到 {duplicate_count} 条重复时间戳。")
    if non_positive:
        warnings.append(f"检测到 {non_positive} 条非正价格。")
    if len(bars) < spec.slow_window + 5:
        warnings.append("数据量接近指标预热下限，结果缺乏代表性。")

    return DataProfile(
        source=repository.source_name,
        synthetic=repository.synthetic,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        row_count=len(bars),
        start_at=bars[0].at,
        end_at=bars[-1].at,
        duplicate_timestamps=duplicate_count,
        non_positive_prices=non_positive,
        warnings=warnings,
    )
