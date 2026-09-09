"""Small fail-closed helpers for keeping obvious credentials out of persisted artifacts."""

from __future__ import annotations

import re

_ASSIGNED_SECRET = re.compile(
    r"(?P<label>api[_ -]?key|access[_ -]?token|token|password|passwd|secret|密码|密钥)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[^\s,;，；]{4,})",
    re.IGNORECASE,
)
_BEARER_SECRET = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)


def redact_sensitive_text(value: str) -> str:
    """Redact high-confidence credential shapes while retaining useful request context."""
    redacted = _ASSIGNED_SECRET.sub(
        lambda match: f"{match.group('label')}{match.group('separator')}[REDACTED]",
        value,
    )
    return _BEARER_SECRET.sub("Bearer [REDACTED]", redacted)


def contains_assigned_secret(value: str) -> bool:
    return redact_sensitive_text(value) != value
