"""Mid-turn operator steering (DOC-03, ADR of dynamic steering).

Instructions are queued asynchronously and validated for scope and safety in code before incorporation —
never applied blindly. Rejection reasons are explicit so the UI and audit log can show why.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from bletiner.models.netaddr import host_in_scope
from bletiner.models.scope import ScopeSpec

# Patterns that indicate an operator is trying to push the agent toward destructive/out-of-policy action.
_UNSAFE_MARKERS = (
    "sqlmap",
    "--os-shell",
    "masscan",
    "hydra",
    "metasploit",
    "rm -rf",
    "dos",
    "ddos",
    "flood",
    "exploit",
    "reverse shell",
    "drop table",
    "format ",
    "brute force",
    "bruteforce",
)


class SteeringVerdict(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED_SCOPE = "REJECTED_SCOPE"
    REJECTED_SAFETY = "REJECTED_SAFETY"


@dataclass(frozen=True)
class SteeringResult:
    verdict: SteeringVerdict
    instruction: str
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.verdict is SteeringVerdict.ACCEPTED


def validate_instruction(text: str, scope: ScopeSpec) -> SteeringResult:
    lowered = text.lower()
    for marker in _UNSAFE_MARKERS:
        if marker in lowered:
            return SteeringResult(SteeringVerdict.REJECTED_SAFETY, text, f"matched unsafe marker {marker!r}")
    # A bare out-of-scope hostname/URL token is rejected; in-scope references pass.
    for token in lowered.replace(",", " ").split():
        host = token.split("//")[-1].split("/")[0]
        bare = host.replace(".", "").replace("-", "").replace(":", "")
        looks_like_host = "." in host and not bare.isdigit()
        if looks_like_host and scope.domains and not host_in_scope(scope.domains, host):
            return SteeringResult(SteeringVerdict.REJECTED_SCOPE, text, f"out-of-scope host {host!r}")
    return SteeringResult(SteeringVerdict.ACCEPTED, text)


class SteeringQueue:
    """Thread-safe-enough single-consumer queue of validated steering instructions."""

    def __init__(self, scope: ScopeSpec) -> None:
        self._scope = scope
        self._pending: deque[str] = deque()

    def submit(self, text: str) -> SteeringResult:
        result = validate_instruction(text, self._scope)
        if result.accepted:
            self._pending.append(text)
        return result

    def drain(self) -> list[str]:
        items = list(self._pending)
        self._pending.clear()
        return items

    def has_pending(self) -> bool:
        return bool(self._pending)
