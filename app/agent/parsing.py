"""Shared deterministic parsing helpers used by multiple task compilers."""

from __future__ import annotations

import re
from typing import Literal

_HOURLY_PATTERN = re.compile(
    r"(?:1\s*(?:小时|小時)(?:K\s*线|K\s*線|线|線)?|"
    r"1\s*h(?:our)?(?:ly)?\b|小时线|小時線)",
    re.IGNORECASE,
)


def parse_timeframe(question: str) -> Literal["1d", "1h"]:
    """Normalize supported hourly expressions; default to daily for the MVP."""
    return "1h" if _HOURLY_PATTERN.search(question) else "1d"
