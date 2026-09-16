"""Application service for real-data historical event studies."""

from __future__ import annotations

import hashlib
import json
from itertools import pairwise
from typing import Protocol

from app.contracts import (
    EventHorizonStatistics,
    EventStudyEvent,
    EventStudyRequest,
    EventStudyResponse,
)
from app.domain.event_study import calculate_event_statistics, scan_historical_events
from app.domain.market_data import Bar
from app.models import DataProfile


class EventStudyRepository(Protocol):
    """Read-only market-data operations required by the event study."""

    source_name: str
    synthetic: bool

    def load_event_study(self, request: EventStudyRequest) -> list[Bar]:
        """Load anchor bars and required trading-row context in ascending order."""

        ...


def _validate_calculation_bars(bars: list[Bar]) -> None:
    """Fail closed on invalid context because every row can affect a result."""

    timestamps = [bar.at for bar in bars]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("event-study data contains duplicate trading dates")
    if any(current <= previous for previous, current in pairwise(timestamps)):
        raise ValueError("event-study data must be ordered by trading date")
    if any(min(bar.open, bar.high, bar.low, bar.close) <= 0 for bar in bars):
        raise ValueError("event-study data contains non-positive prices")


def _event_study_data_version(
    repository: EventStudyRepository,
    request: EventStudyRequest,
    bars: list[Bar],
) -> str:
    """Fingerprint every OHLCV row that can affect conditions or forward returns."""

    material = {
        "source": repository.source_name,
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "minimum_offset": min(condition.offset for condition in request.conditions),
        "maximum_forward_days": max(request.forward_days),
        "bars": [
            {
                "at": bar.at,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ],
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:16]
    return f"postgres-event-v1:{digest}"


def _profile_bars(
    repository: EventStudyRepository,
    request: EventStudyRequest,
    bars: list[Bar],
) -> DataProfile:
    """Build the existing data-profile contract for event-study input."""

    if not bars:
        raise ValueError("selected event-study range contains no market data")
    return DataProfile(
        source=repository.source_name,
        synthetic=False,
        symbol=request.symbol,
        timeframe=request.timeframe,
        row_count=len(bars),
        start_at=bars[0].at,
        end_at=bars[-1].at,
        duplicate_timestamps=0,
        non_positive_prices=0,
        warnings=[],
    )


class EventStudyApplication:
    """Coordinate repository reads and deterministic event-study calculations."""

    def __init__(self, repository: EventStudyRepository):
        """Require an explicitly non-synthetic repository."""

        if repository.synthetic:
            raise ValueError("event studies require real PostgreSQL market data")
        self.repository = repository

    def execute(self, request: EventStudyRequest) -> EventStudyResponse:
        """Execute one event study and return versioned details and statistics."""

        bars = self.repository.load_event_study(request)
        _validate_calculation_bars(bars)
        anchor_bars = [
            bar for bar in bars if request.start_date <= bar.at.date() <= request.end_date
        ]
        profile = _profile_bars(self.repository, request, anchor_bars)
        data_version = _event_study_data_version(self.repository, request, bars)
        occurrences = scan_historical_events(
            bars,
            request.conditions,
            request.forward_days,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        aggregates = calculate_event_statistics(occurrences, request.forward_days)
        events = [
            EventStudyEvent(
                event_date=occurrence.event_date,
                event_return_pct=occurrence.event_return_pct,
                forward_returns={
                    str(horizon): occurrence.forward_returns[horizon]
                    for horizon in request.forward_days
                },
            )
            for occurrence in occurrences
        ]
        statistics = {
            str(horizon): EventHorizonStatistics(
                sample_count=aggregates[horizon].sample_count,
                positive_count=aggregates[horizon].positive_count,
                negative_count=aggregates[horizon].negative_count,
                positive_rate_pct=aggregates[horizon].positive_rate_pct,
                average_return_pct=aggregates[horizon].average_return_pct,
                median_return_pct=aggregates[horizon].median_return_pct,
                min_return_pct=aggregates[horizon].min_return_pct,
                max_return_pct=aggregates[horizon].max_return_pct,
            )
            for horizon in request.forward_days
        }
        return EventStudyResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            event_name=request.event_name,
            symbol=request.symbol,
            timeframe=request.timeframe,
            start_date=request.start_date,
            end_date=request.end_date,
            event_count=len(events),
            events=events,
            statistics=statistics,
            data_profile=profile,
            data_version=data_version,
        )
