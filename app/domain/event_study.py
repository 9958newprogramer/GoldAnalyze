"""Pure historical-event scanning and forward-return calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from statistics import fmean, median

from app.contracts import EventCondition
from app.domain.market_data import Bar


@dataclass(frozen=True)
class EventOccurrence:
    """Internal representation of one event anchor and its horizon returns."""

    event_date: date
    event_return_pct: float
    forward_returns: dict[int, float | None]


@dataclass(frozen=True)
class HorizonStatistics:
    """Internal aggregate for one forward trading-day horizon."""

    sample_count: int
    positive_count: int
    negative_count: int
    positive_rate_pct: float
    average_return_pct: float | None
    median_return_pct: float | None
    min_return_pct: float | None
    max_return_pct: float | None


def _return_pct(start_close: float, end_close: float) -> float:
    """Calculate a percentage return using decimalized input prices."""

    if not math.isfinite(start_close) or not math.isfinite(end_close):
        raise ValueError("close prices must be finite")
    if start_close <= 0 or end_close <= 0:
        raise ValueError("close prices must be positive")
    start = Decimal(str(start_close))
    end = Decimal(str(end_close))
    return float((end / start - Decimal(1)) * Decimal(100))


def calculate_daily_returns(bars: list[Bar]) -> list[float | None]:
    """Return close-to-close percentages aligned with the supplied bars."""

    if not bars:
        return []
    if any(current.at <= previous.at for previous, current in pairwise(bars)):
        raise ValueError("bars must be strictly ordered by unique trading date")
    returns: list[float | None] = [None]
    returns.extend(
        _return_pct(previous.close, current.close) for previous, current in pairwise(bars)
    )
    return returns


def condition_matches(actual: float, condition: EventCondition) -> bool:
    """Evaluate one validated event condition against an observed return."""

    if condition.operator == "between":
        assert condition.min is not None and condition.max is not None
        return condition.min <= actual <= condition.max
    assert condition.value is not None
    if condition.operator == "gt":
        return actual > condition.value
    if condition.operator == "gte":
        return actual >= condition.value
    if condition.operator == "lt":
        return actual < condition.value
    return actual <= condition.value


def calculate_forward_returns(
    bars: list[Bar],
    anchor_index: int,
    forward_days: list[int],
) -> dict[int, float | None]:
    """Calculate close returns at future trading positions or mark them unavailable."""

    if not 0 <= anchor_index < len(bars):
        raise ValueError("anchor_index is outside the supplied bars")
    anchor_close = bars[anchor_index].close
    return {
        horizon: (
            round(_return_pct(anchor_close, bars[anchor_index + horizon].close), 6)
            if anchor_index + horizon < len(bars)
            else None
        )
        for horizon in forward_days
    }


def scan_historical_events(
    bars: list[Bar],
    conditions: list[EventCondition],
    forward_days: list[int],
    *,
    start_date: date,
    end_date: date,
) -> list[EventOccurrence]:
    """Find in-window anchors while using all supplied rows as calculation context."""

    returns = calculate_daily_returns(bars)
    events: list[EventOccurrence] = []
    for anchor_index, anchor_bar in enumerate(bars):
        anchor_date = anchor_bar.at.date()
        if anchor_date < start_date or anchor_date > end_date:
            continue
        matched = True
        for condition in conditions:
            condition_index = anchor_index + condition.offset
            if condition_index < 0:
                matched = False
                break
            actual = returns[condition_index]
            if actual is None or not condition_matches(actual, condition):
                matched = False
                break
        if not matched:
            continue

        anchor_return = returns[anchor_index]
        if anchor_return is None:
            continue
        events.append(
            EventOccurrence(
                event_date=anchor_date,
                event_return_pct=round(anchor_return, 6),
                forward_returns=calculate_forward_returns(bars, anchor_index, forward_days),
            )
        )
    return events


def calculate_event_statistics(
    events: list[EventOccurrence],
    forward_days: list[int],
) -> dict[int, HorizonStatistics]:
    """Aggregate each horizon while excluding unavailable tail observations."""

    result: dict[int, HorizonStatistics] = {}
    for horizon in forward_days:
        values = [
            value for event in events if (value := event.forward_returns.get(horizon)) is not None
        ]
        positive_count = sum(value > 0 for value in values)
        negative_count = sum(value < 0 for value in values)
        sample_count = len(values)
        result[horizon] = HorizonStatistics(
            sample_count=sample_count,
            positive_count=positive_count,
            negative_count=negative_count,
            positive_rate_pct=round(positive_count / sample_count * 100, 6)
            if sample_count
            else 0.0,
            average_return_pct=round(fmean(values), 6) if values else None,
            median_return_pct=round(median(values), 6) if values else None,
            min_return_pct=round(min(values), 6) if values else None,
            max_return_pct=round(max(values), 6) if values else None,
        )
    return result
