from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.contracts import EventCondition, EventStudyRequest
from app.domain.event_study import (
    EventOccurrence,
    calculate_event_statistics,
    condition_matches,
    scan_historical_events,
)
from app.domain.market_data import Bar
from app.event_study import EventStudyApplication


def _bars(closes: list[float], dates: list[date] | None = None) -> list[Bar]:
    """Build valid daily bars for deterministic event-study tests."""

    trading_dates = dates or [date(2024, 1, day) for day in range(1, len(closes) + 1)]
    return [
        Bar(
            at=datetime.combine(trading_date, datetime.min.time(), tzinfo=UTC),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000,
        )
        for trading_date, close in zip(trading_dates, closes, strict=True)
    ]


def _request(
    conditions: list[dict[str, object]],
    *,
    forward_days: list[int] | None = None,
    event_name: str = "test_event",
    start_date: date = date(2024, 1, 1),
    end_date: date = date(2024, 12, 31),
) -> EventStudyRequest:
    """Build a valid event-study request around supplied conditions."""

    return EventStudyRequest.model_validate(
        {
            "request_id": "event-study-001",
            "job_id": "event-job-001",
            "event_name": event_name,
            "symbol": "XAUUSD",
            "timeframe": "1d",
            "start_date": start_date,
            "end_date": end_date,
            "conditions": conditions,
            "forward_days": forward_days or [1, 3, 5],
        }
    )


class RealRepositoryStub:
    """Non-synthetic repository double for application-layer tests."""

    source_name = "postgresql:gold:test-double"
    synthetic = False

    def __init__(self, bars: list[Bar]):
        """Retain deterministic bars supplied by a test."""

        self.bars = bars

    def load_event_study(self, request: EventStudyRequest) -> list[Bar]:
        """Return a copy of the configured context and anchor rows."""

        return list(self.bars)


class SyntheticRepositoryStub(RealRepositoryStub):
    """Synthetic source double used to prove fail-closed behavior."""

    source_name = "deterministic-demo"
    synthetic = True


def test_event_condition_contract_enforces_operator_operands():
    """Operators must receive exactly their supported operand shape."""

    between = EventCondition(operator="between", min=-1, max=1, offset=0)
    assert condition_matches(-1.0, between)
    assert condition_matches(1.0, between)

    with pytest.raises(ValidationError, match="between requires min and max"):
        EventCondition(operator="between", min=-1, offset=0)
    with pytest.raises(ValidationError, match="does not allow value"):
        EventCondition(operator="between", min=-1, max=1, value=0, offset=0)
    with pytest.raises(ValidationError, match="requires value"):
        EventCondition(operator="lte", offset=0)
    with pytest.raises(ValidationError, match="does not allow min or max"):
        EventCondition(operator="lte", value=-3, min=-4, offset=0)


@pytest.mark.parametrize(
    ("operator", "threshold", "actual", "expected"),
    [
        ("gt", 1.0, 1.0, False),
        ("gt", 1.0, 1.1, True),
        ("gte", 1.0, 1.0, True),
        ("lt", -1.0, -1.0, False),
        ("lt", -1.0, -1.1, True),
        ("lte", -1.0, -1.0, True),
    ],
)
def test_scalar_condition_operators(
    operator: str,
    threshold: float,
    actual: float,
    expected: bool,
):
    """Scalar comparison operators preserve strict and inclusive boundaries."""

    condition = EventCondition(offset=0, operator=operator, value=threshold)
    assert condition_matches(actual, condition) is expected


def test_event_study_request_enforces_anchor_dates_symbol_and_unique_horizons():
    """Request validation must reject ambiguous or unsupported studies."""

    payload = _request([{"offset": 0, "field": "return_pct", "operator": "lte", "value": -3}])
    raw = payload.model_dump(mode="json")

    with pytest.raises(ValidationError, match="offset=0"):
        EventStudyRequest.model_validate(
            {**raw, "conditions": [{"offset": -1, "operator": "lte", "value": -3}]}
        )
    with pytest.raises(ValidationError, match="duplicates"):
        EventStudyRequest.model_validate({**raw, "forward_days": [1, 1]})
    with pytest.raises(ValidationError):
        EventStudyRequest.model_validate({**raw, "forward_days": [0]})
    with pytest.raises(ValidationError):
        EventStudyRequest.model_validate({**raw, "symbol": "EURUSD"})
    with pytest.raises(ValidationError):
        EventStudyRequest.model_validate({**raw, "timeframe": "1h"})
    with pytest.raises(ValidationError, match="later"):
        EventStudyRequest.model_validate(
            {**raw, "start_date": "2024-01-02", "end_date": "2024-01-01"}
        )


def test_example_a_uses_trading_day_offsets_and_forward_1_3_5_returns():
    """Consecutive drops across a weekend match one anchor and trading horizons."""

    trading_dates = [
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 9),
        date(2024, 1, 10),
        date(2024, 1, 11),
        date(2024, 1, 12),
        date(2024, 1, 15),
    ]
    bars = _bars([100, 96, 92.16, 93.0816, 94, 96.768, 97, 101.376], trading_dates)
    request = _request(
        [
            {"offset": -1, "field": "return_pct", "operator": "lte", "value": -3.0},
            {"offset": 0, "field": "return_pct", "operator": "lte", "value": -3.0},
        ],
        event_name="two_consecutive_drop_over_3pct",
    )

    events = scan_historical_events(
        bars,
        request.conditions,
        request.forward_days,
        start_date=request.start_date,
        end_date=request.end_date,
    )

    assert len(events) == 1
    assert events[0].event_date == date(2024, 1, 8)
    assert events[0].event_return_pct == -4.0
    assert events[0].forward_returns == {1: 1.0, 3: 5.0, 5: 10.0}


def test_example_b_between_boundaries_and_three_day_pattern():
    """A drop followed by inclusive -1/+1 sideways days matches Example B."""

    bars = _bars(
        [
            100,
            96,
            96.96,
            95.9904,
            97.910208,
            96,
            93.110688,
            95,
            99.830016,
        ]
    )
    request = _request(
        [
            {"offset": -2, "field": "return_pct", "operator": "lte", "value": -3.0},
            {"offset": -1, "field": "return_pct", "operator": "between", "min": -1, "max": 1},
            {"offset": 0, "field": "return_pct", "operator": "between", "min": -1, "max": 1},
        ],
        event_name="drop_over_3pct_then_two_sideways_days",
    )

    events = scan_historical_events(
        bars,
        request.conditions,
        request.forward_days,
        start_date=request.start_date,
        end_date=request.end_date,
    )

    assert len(events) == 1
    assert events[0].event_date == date(2024, 1, 4)
    assert events[0].event_return_pct == -1.0
    assert events[0].forward_returns == {1: 2.0, 3: -3.0, 5: 4.0}


def test_conditions_are_and_combined_and_tail_horizons_are_unavailable():
    """Partial matches are rejected and missing future positions remain null."""

    bars = _bars([100, 96, 96])
    consecutive = _request(
        [
            {"offset": -1, "operator": "lte", "value": -3},
            {"offset": 0, "operator": "lte", "value": -3},
        ]
    )
    assert (
        scan_historical_events(
            bars,
            consecutive.conditions,
            consecutive.forward_days,
            start_date=consecutive.start_date,
            end_date=consecutive.end_date,
        )
        == []
    )

    tail = _request([{"offset": 0, "operator": "lte", "value": -3}])
    tail_events = scan_historical_events(
        _bars([100, 96]),
        tail.conditions,
        tail.forward_days,
        start_date=tail.start_date,
        end_date=tail.end_date,
    )
    assert len(tail_events) == 1
    assert tail_events[0].forward_returns == {1: None, 3: None, 5: None}


def test_statistics_exclude_unavailable_values_and_count_signs_correctly():
    """Statistics use only available samples while preserving zero returns."""

    events = [
        EventOccurrence(date(2024, 1, 1), -3, {1: 1.0}),
        EventOccurrence(date(2024, 1, 2), -3, {1: -2.0}),
        EventOccurrence(date(2024, 1, 3), -3, {1: 0.0}),
        EventOccurrence(date(2024, 1, 4), -3, {1: None}),
    ]

    statistics = calculate_event_statistics(events, [1])[1]

    assert statistics.sample_count == 3
    assert statistics.positive_count == 1
    assert statistics.negative_count == 1
    assert statistics.positive_rate_pct == pytest.approx(33.333333)
    assert statistics.average_return_pct == pytest.approx(-0.333333)
    assert statistics.median_return_pct == 0
    assert statistics.min_return_pct == -2
    assert statistics.max_return_pct == 1


def test_application_is_deterministic_and_reuses_profile_and_data_version():
    """Repeated application calls return identical versioned responses."""

    bars = _bars([100, 96, 92.16, 93.0816, 94, 96.768, 97, 101.376])
    request = _request(
        [
            {"offset": -1, "operator": "lte", "value": -3},
            {"offset": 0, "operator": "lte", "value": -3},
        ]
    )
    application = EventStudyApplication(RealRepositoryStub(bars))

    first = application.execute(request)
    second = application.execute(request)

    assert first == second
    assert first.event_count == 1
    assert first.data_version.startswith("postgres-event-v1:")
    assert first.data_profile.source == "postgresql:gold:test-double"
    assert first.data_profile.synthetic is False
    assert first.statistics["1"].sample_count == 1
    assert first.statistics["1"].positive_rate_pct == 100
    assert first.start_date == request.start_date
    assert first.end_date == request.end_date


def test_application_rejects_synthetic_market_data():
    """Event studies must fail closed instead of silently using demo data."""

    with pytest.raises(ValueError, match="real PostgreSQL"):
        EventStudyApplication(SyntheticRepositoryStub(_bars([100, 96])))


def test_start_boundary_uses_preceding_trading_row_for_anchor_return():
    """An anchor at start_date can use a predecessor outside the study window."""

    bars = _bars(
        [100, 96, 97],
        [date(2020, 8, 18), date(2020, 8, 19), date(2020, 8, 20)],
    )
    request = _request(
        [{"offset": 0, "operator": "lte", "value": -3}],
        forward_days=[1],
        start_date=date(2020, 8, 19),
        end_date=date(2020, 8, 20),
    )

    events = scan_historical_events(
        bars,
        request.conditions,
        request.forward_days,
        start_date=request.start_date,
        end_date=request.end_date,
    )

    assert [event.event_date for event in events] == [date(2020, 8, 19)]
    assert events[0].event_return_pct == -4


def test_end_boundary_uses_following_trading_rows_across_weekend():
    """Forward returns use row positions beyond end_date, including across weekends."""

    anchor_close = 100.0
    predecessor_close = anchor_close / (1 - 0.03679174)
    bars = _bars(
        [
            predecessor_close,
            anchor_close,
            100.927184,
            100.5,
            100.006223,
            100.75,
            101.336846,
        ],
        [
            date(2020, 8, 18),
            date(2020, 8, 19),
            date(2020, 8, 20),
            date(2020, 8, 21),
            date(2020, 8, 24),
            date(2020, 8, 25),
            date(2020, 8, 26),
        ],
    )
    request = _request(
        [{"offset": 0, "operator": "lte", "value": -3}],
        start_date=date(2020, 8, 18),
        end_date=date(2020, 8, 21),
    )

    events = scan_historical_events(
        bars,
        request.conditions,
        request.forward_days,
        start_date=request.start_date,
        end_date=request.end_date,
    )

    assert len(events) == 1
    assert events[0].event_date == date(2020, 8, 19)
    assert events[0].event_return_pct == pytest.approx(-3.679174)
    assert events[0].forward_returns == {1: 0.927184, 3: 0.006223, 5: 1.336846}


def test_offset_minus_two_can_use_t_minus_three_close_before_start_boundary():
    """The t-2 return remains calculable because its t-3 close is in context."""

    bars = _bars(
        [100, 96, 96.5, 96.75, 97],
        [
            date(2024, 1, 4),
            date(2024, 1, 5),
            date(2024, 1, 8),
            date(2024, 1, 9),
            date(2024, 1, 10),
        ],
    )
    request = _request(
        [
            {"offset": -2, "operator": "lte", "value": -3},
            {"offset": 0, "operator": "between", "min": -1, "max": 1},
        ],
        forward_days=[1],
        start_date=date(2024, 1, 9),
        end_date=date(2024, 1, 10),
    )

    events = scan_historical_events(
        bars,
        request.conditions,
        request.forward_days,
        start_date=request.start_date,
        end_date=request.end_date,
    )

    assert [event.event_date for event in events] == [date(2024, 1, 9)]


def test_context_rows_cannot_become_event_anchors():
    """Matching context rows support calculations but never emit out-of-window events."""

    bars = _bars(
        [100, 96, 92, 88, 84],
        [
            date(2024, 1, 5),
            date(2024, 1, 8),
            date(2024, 1, 9),
            date(2024, 1, 10),
            date(2024, 1, 11),
        ],
    )
    request = _request(
        [{"offset": 0, "operator": "lte", "value": -3}],
        forward_days=[1],
        start_date=date(2024, 1, 9),
        end_date=date(2024, 1, 10),
    )

    events = scan_historical_events(
        bars,
        request.conditions,
        request.forward_days,
        start_date=request.start_date,
        end_date=request.end_date,
    )

    assert [event.event_date for event in events] == [date(2024, 1, 9), date(2024, 1, 10)]


def test_application_profiles_only_anchor_window_and_versions_all_context_rows():
    """Public profile stays scoped while data_version fingerprints both context sides."""

    dates = [
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 9),
        date(2024, 1, 10),
    ]
    request = _request(
        [
            {"offset": -1, "operator": "between", "min": 0, "max": 3},
            {"offset": 0, "operator": "lte", "value": -3},
        ],
        forward_days=[1],
        start_date=date(2024, 1, 8),
        end_date=date(2024, 1, 9),
    )
    baseline = EventStudyApplication(RealRepositoryStub(_bars([100, 101, 96, 98], dates))).execute(
        request
    )
    changed_pre = EventStudyApplication(
        RealRepositoryStub(_bars([99, 101, 96, 98], dates))
    ).execute(request)
    changed_post = EventStudyApplication(
        RealRepositoryStub(_bars([100, 101, 96, 99], dates))
    ).execute(request)

    assert baseline.data_profile.row_count == 2
    assert baseline.data_profile.start_at.date() == request.start_date
    assert baseline.data_profile.end_at.date() == request.end_date
    assert baseline.events[0].event_date == request.end_date
    assert baseline.events[0].forward_returns["1"] != changed_post.events[0].forward_returns["1"]
    assert baseline.data_version != changed_pre.data_version
    assert baseline.data_version != changed_post.data_version
