"""Database-independent value objects for deterministic backtests."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from enum import StrEnum


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _timestamp(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.combine(value, time.min, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Kline:
    """One immutable OHLCV observation.

    ``trade_date`` accepts both PostgreSQL ``date`` values from ``daily_bar``
    and timezone-aware datetimes so the same engine can later process hourly bars.
    """

    symbol: str
    trade_date: date | datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol cannot be empty")
        object.__setattr__(self, "symbol", symbol)

        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        if self.high < max(self.open, self.close):
            raise ValueError("high cannot be below open or close")
        if self.low > min(self.open, self.close):
            raise ValueError("low cannot be above open or close")

    @property
    def timestamp(self) -> datetime:
        """Normalized UTC timestamp used for ordering and result events."""

        return _timestamp(self.trade_date)


class Signal(StrEnum):
    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    entry_at: datetime
    exit_at: datetime
    entry_price: float
    exit_price: float
    quantity: float
    entry_fee: float
    exit_fee: float
    pnl: float
    return_pct: float
    exit_reason: str


@dataclass(slots=True)
class Portfolio:
    """Single-asset cash account used by the minimal long-only engine."""

    initial_cash: float
    cash: float = field(init=False)
    position_quantity: float = field(default=0.0, init=False)
    position_symbol: str | None = field(default=None, init=False)
    entry_at: datetime | None = field(default=None, init=False)
    entry_price: float = field(default=0.0, init=False)
    entry_fee: float = field(default=0.0, init=False)
    total_fees: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.initial_cash = _finite("initial_cash", self.initial_cash)
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.cash = self.initial_cash

    @property
    def has_position(self) -> bool:
        return self.position_quantity > 0

    def equity(self, mark_price: float) -> float:
        price = _finite("mark_price", mark_price)
        if price <= 0:
            raise ValueError("mark_price must be positive")
        return self.cash + self.position_quantity * price

    def open_long(self, *, symbol: str, at: datetime, price: float, fee_rate: float) -> None:
        if self.has_position:
            raise ValueError("portfolio already has an open position")
        price = _finite("price", price)
        fee_rate = _finite("fee_rate", fee_rate)
        if price <= 0 or not 0 <= fee_rate < 1:
            raise ValueError("invalid fill price or fee rate")

        quantity = self.cash / (price * (1 + fee_rate))
        gross_cost = quantity * price
        fee = gross_cost * fee_rate
        self.cash -= gross_cost + fee
        if abs(self.cash) < 1e-8:
            self.cash = 0.0
        self.position_quantity = quantity
        self.position_symbol = symbol
        self.entry_at = at
        self.entry_price = price
        self.entry_fee = fee
        self.total_fees += fee

    def close_long(
        self,
        *,
        at: datetime,
        price: float,
        fee_rate: float,
        reason: str,
    ) -> Trade:
        if not self.has_position or self.position_symbol is None or self.entry_at is None:
            raise ValueError("portfolio has no open position")
        price = _finite("price", price)
        fee_rate = _finite("fee_rate", fee_rate)
        if price <= 0 or not 0 <= fee_rate < 1:
            raise ValueError("invalid fill price or fee rate")
        if at < self.entry_at:
            raise ValueError("exit time cannot be before entry time")

        quantity = self.position_quantity
        gross_proceeds = quantity * price
        exit_fee = gross_proceeds * fee_rate
        net_proceeds = gross_proceeds - exit_fee
        invested = quantity * self.entry_price + self.entry_fee
        pnl = net_proceeds - invested
        trade = Trade(
            symbol=self.position_symbol,
            entry_at=self.entry_at,
            exit_at=at,
            entry_price=self.entry_price,
            exit_price=price,
            quantity=quantity,
            entry_fee=self.entry_fee,
            exit_fee=exit_fee,
            pnl=pnl,
            return_pct=pnl / invested * 100,
            exit_reason=reason,
        )

        self.cash += net_proceeds
        self.position_quantity = 0.0
        self.position_symbol = None
        self.entry_at = None
        self.entry_price = 0.0
        self.entry_fee = 0.0
        self.total_fees += exit_fee
        return trade


@dataclass(frozen=True, slots=True)
class EquityPoint:
    at: datetime
    equity: float


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    total_return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    trade_count: int


@dataclass(frozen=True, slots=True)
class BacktestResult:
    symbol: str
    initial_cash: float
    final_equity: float
    metrics: PerformanceMetrics
    trades: tuple[Trade, ...]
    equity_curve: tuple[EquityPoint, ...]
