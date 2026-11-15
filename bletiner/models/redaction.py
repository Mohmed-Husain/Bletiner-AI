"""Pure secret-redaction helper (DOC-14 section 6.1). Lives in models so any layer may use it."""

from __future__ import annotations

import re

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(r"xoxb-[A-Za-z0-9\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*\S+"),
)
REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    out = text
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    return out
