"""Finding verification lifecycle FSM (DOC-08 section 4, DOC-14 Phase 6).

Detected -> Needs Triage -> Under Investigation -> {Verified | False Positive | Inconclusive} -> Reported.
Terminal-ish states may still move to Reported or be re-opened to Under Investigation (Deep mode). Illegal
transitions raise, so a finding can never silently skip triage.
"""

from __future__ import annotations

from bletiner.models.entities import VerificationStatus as VS
from bletiner.models.errors import BletinerError

_ALLOWED: dict[VS, frozenset[VS]] = {
    VS.DETECTED: frozenset({VS.NEEDS_TRIAGE}),
    VS.NEEDS_TRIAGE: frozenset({VS.UNDER_INVESTIGATION, VS.FALSE_POSITIVE, VS.ACCEPTED_RISK}),
    VS.UNDER_INVESTIGATION: frozenset({VS.VERIFIED, VS.FALSE_POSITIVE, VS.INCONCLUSIVE, VS.ACCEPTED_RISK}),
    VS.VERIFIED: frozenset({VS.UNDER_INVESTIGATION, VS.ACCEPTED_RISK}),
    VS.INCONCLUSIVE: frozenset({VS.UNDER_INVESTIGATION, VS.FALSE_POSITIVE, VS.ACCEPTED_RISK}),
    VS.FALSE_POSITIVE: frozenset({VS.UNDER_INVESTIGATION}),
    VS.ACCEPTED_RISK: frozenset({VS.UNDER_INVESTIGATION}),
}

# Findings in these states are eligible to appear in a report.
REPORTABLE: frozenset[VS] = frozenset({VS.VERIFIED, VS.INCONCLUSIVE, VS.FALSE_POSITIVE, VS.ACCEPTED_RISK})


class InvalidTransitionError(BletinerError):
    pass


def can_transition(current: VS, target: VS) -> bool:
    return target in _ALLOWED.get(current, frozenset())


def transition(current: VS, target: VS) -> VS:
    if current == target:
        return current
    if not can_transition(current, target):
        raise InvalidTransitionError(f"illegal finding transition {current.value} -> {target.value}")
    return target


def is_reportable(status: VS) -> bool:
    return status in REPORTABLE
