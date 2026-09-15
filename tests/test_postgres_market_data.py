from datetime import UTC, date, datetime
from decimal import Decimal

from app.config import Settings
from app.contracts import EventStudyRequest
from app.domain import market_data


class CursorStub:
    """Small psycopg cursor double that records one parameterized query."""

    def __init__(self, *, one=None, many=None):
        """Configure rows returned by fetch operations."""

        self.one = one
        self.many = many or []
        self.sql = ""
        self.params = ()

    def __enter__(self):
        """Enter the cursor context."""

        return self

    def __exit__(self, exc_type, exc, traceback):
        """Leave the cursor context without suppressing errors."""

        return False

    def execute(self, sql, params):
        """Record SQL and bound parameters."""

        self.sql = sql
        self.params = params

    def fetchone(self):
        """Return the configured aggregate row."""

        return self.one

    def fetchall(self):
        """Return configured market rows."""

        return self.many


class ConnectionStub:
    """Context-managed psycopg connection double."""

    def __init__(self, cursor: CursorStub):
        """Retain the cursor returned by this connection."""

        self.cursor_instance = cursor

    def __enter__(self):
        """Enter the connection context."""

        return self

    def __exit__(self, exc_type, exc, traceback):
        """Leave the connection context without suppressing errors."""

        return False

    def cursor(self):
        """Return the configured cursor."""

        return self.cursor_instance


def _request() -> EventStudyRequest:
    """Build a minimal valid daily event-study selection."""

    return EventStudyRequest(
        request_id="event-study-001",
        job_id="event-job-001",
        event_name="daily_drop",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        conditions=[{"offset": 0, "operator": "lte", "value": -3}],
        forward_days=[1],
    )


def _repository() -> market_data.PostgresMarketDataRepository:
    """Create a repository with a non-secret test DSN."""

    return market_data.PostgresMarketDataRepository(
        Settings(market_postgres_dsn="postgresql://test.invalid/gold")
    )


def test_postgres_daily_data_version_uses_only_declared_ohlcv_columns(monkeypatch):
    """The version query must not require an undeclared as_of column."""

    cursor = CursorStub(
        one=(
            3,
            date(2024, 1, 1),
            date(2024, 1, 3),
            Decimal(300),
            Decimal(306),
            Decimal(294),
            Decimal(301),
            Decimal(3000),
        )
    )
    monkeypatch.setattr(market_data.psycopg, "connect", lambda dsn: ConnectionStub(cursor))

    version = _repository().data_version(_request())

    normalized_sql = " ".join(cursor.sql.split()).lower()
    assert "from gold.daily_bar" in normalized_sql
    assert "max(as_of)" not in normalized_sql
    assert "sum(close)" in normalized_sql
    assert cursor.params == ("XAUUSD", date(2024, 1, 1), date(2024, 1, 31))
    assert version.startswith("postgres-v1:")


def test_postgres_daily_loader_is_parameterized_and_orders_trade_dates(monkeypatch):
    """Daily rows are selected with bound values and deterministic ordering."""

    cursor = CursorStub(
        many=[
            (date(2024, 1, 2), 2000, 2010, 1990, 2005, 100),
            (date(2024, 1, 3), 2005, 2020, 2000, 2015, None),
        ]
    )
    monkeypatch.setattr(market_data.psycopg, "connect", lambda dsn: ConnectionStub(cursor))

    bars = _repository().load(_request())

    normalized_sql = " ".join(cursor.sql.split()).lower()
    assert "from gold.daily_bar" in normalized_sql
    assert "order by trade_date asc" in normalized_sql
    assert cursor.params == ("XAUUSD", date(2024, 1, 1), date(2024, 1, 31))
    assert [bar.at for bar in bars] == [
        datetime(2024, 1, 2, tzinfo=UTC),
        datetime(2024, 1, 3, tzinfo=UTC),
    ]
    assert bars[1].volume == 0
