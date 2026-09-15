"""Application service for real-data historical event studies."""

from __future__ import annotations

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

    def load(self, request: EventStudyRequest) -> list[Bar]:
        """Load requested daily bars in ascending trading-date order."""

        ...

    def data_version(self, request: EventStudyRequest) -> str:
        """Return a stable identity for the selected market data."""

        ...


def _profile_bars(
    repository: EventStudyRepository,
    request: EventStudyRequest,
    bars: list[Bar],
) -> DataProfile:
    """Build the existing data-profile contract for event-study input."""

    if not bars:
        raise ValueError("selected event-study range contains no market data")
    timestamps = [bar.at for bar in bars]
    duplicate_count = len(timestamps) - len(set(timestamps))
    non_positive = sum(1 for bar in bars if min(bar.open, bar.high, bar.low, bar.close) <= 0)
    if duplicate_count:
        raise ValueError("event-study data contains duplicate trading dates")
    if non_positive:
        raise ValueError("event-study data contains non-positive prices")
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

        data_version = self.repository.data_version(request)
        bars = self.repository.load(request)
        profile = _profile_bars(self.repository, request, bars)
        occurrences = scan_historical_events(bars, request.conditions, request.forward_days)
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
            event_count=len(events),
            events=events,
            statistics=statistics,
            data_profile=profile,
            data_version=data_version,
        )
