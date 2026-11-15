"""Redaction of sensitive tokens in report views (DOC-08, DOC-14 section 6.1 Phase 6).

Extends the shared secret redaction with JWT and HTTP basic-auth patterns that can appear in captured tool
output embedded in a finding's evidence.
"""

from __future__ import annotations

import re

from bletiner.models.redaction import REDACTED
from bletiner.models.redaction import redact as _redact_secrets

_JWT = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
_BASIC = re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}")
_PRIVKEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)


def sanitize(text: str) -> str:
    out = _PRIVKEY.sub(REDACTED, text)
    out = _JWT.sub(REDACTED, out)
    out = _BASIC.sub(REDACTED, out)
    return _redact_secrets(out)
