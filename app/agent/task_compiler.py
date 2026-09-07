"""Compile non-backtest requests into bounded, typed task specifications."""

from __future__ import annotations

import re
from datetime import date

from app.agent.parsing import parse_timeframe
from app.models import ExternalResearchSpec, MarketQuerySpec


def compile_market_query(question: str) -> MarketQuerySpec:
    years = [int(value) for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)]
    start_date = date(min(years), 1, 1) if years else None
    end_date = date(max(years), 12, 31) if len(years) >= 2 else None
    limit_match = re.search(r"(?:最近|近)\s*([0-9]{1,3})\s*(?:根|条|條)", question)
    limit = min(int(limit_match.group(1)), 100) if limit_match else 20
    return MarketQuerySpec(
        timeframe=parse_timeframe(question),
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


def compile_external_research(question: str) -> ExternalResearchSpec:
    return ExternalResearchSpec(query=question.strip(), max_results=5)
