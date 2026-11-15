"""Headless UI view-models (DOC-05). Pure presentation logic, no Qt import, fully unit-testable.

The PySide6 widgets in ``app.py`` are thin shells that bind to these; all formatting, filtering and badge
math lives here so it can be tested under ``QT_QPA_PLATFORM=offscreen``-free CI.
"""

from __future__ import annotations

import secrets
from collections import deque
from dataclasses import dataclass, field

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.models.entities import Finding, Severity, VerificationStatus
from bletiner.models.redaction import redact

# Operational events shown in the activity feed (no raw chain-of-thought, DOC-03 section 1).
_FEED_TYPES = frozenset(
    {
        EventType.STATE_CHANGED,
        EventType.TASK_PLANNED,
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.FINDING,
        EventType.STEERING_ACCEPTED,
        EventType.STEERING_REJECTED,
        EventType.POLICY_BLOCK,
        EventType.ERROR,
        EventType.BUDGET,
    }
)

_STATE_LABELS: dict[AgentState, str] = {
    AgentState.IDLE: "Idle",
    AgentState.INITIALIZING: "Initializing",
    AgentState.PLANNING: "Planning",
    AgentState.EXECUTING: "Running tool",
    AgentState.OBSERVING: "Observing",
    AgentState.ANALYZING: "Analyzing",
    AgentState.REPLANNING: "Re-planning",
    AgentState.PAUSED: "Paused",
    AgentState.COMPLETED: "Completed",
    AgentState.STOPPED: "Stopped",
    AgentState.ERROR_HANDLING: "Handling error",
    AgentState.REPORT_GENERATION: "Generating report",
}


@dataclass
class FeedViewModel:
    """Virtualized activity feed of redacted, human-readable operational lines."""

    max_lines: int = 500
    _lines: deque[str] = field(default_factory=lambda: deque(maxlen=500))

    def __post_init__(self) -> None:
        self._lines = deque(maxlen=self.max_lines)

    def ingest(self, event: AgentEvent) -> str | None:
        if event.type not in _FEED_TYPES:
            return None
        line = f"{event.timestamp.strftime('%H:%M:%S')}  {self._format(event)}"
        self._lines.append(line)
        return line

    def _format(self, e: AgentEvent) -> str:
        p = e.payload
        match e.type:
            case EventType.STATE_CHANGED:
                state = AgentState(p["state"]) if p.get("state") in AgentState.__members__.values() else None
                return _STATE_LABELS.get(state, str(p.get("state"))) if state else str(p.get("state"))
            case EventType.TOOL_STARTED:
                return f"▶ {p.get('tool')} {' '.join(map(str, p.get('argv', [])))}"
            case EventType.TOOL_COMPLETED:
                return f"✓ {p.get('tool')} (exit {p.get('exit_code')})"
            case EventType.FINDING:
                return f"★ {p.get('severity')} finding: {p.get('title')} on {p.get('asset')}"
            case EventType.POLICY_BLOCK:
                return f"⛔ blocked {p.get('tool')}: {p.get('error')}"
            case EventType.STEERING_ACCEPTED:
                return f"➜ steering: {p.get('instruction')}"
            case _:
                return f"{e.type.value}: {redact(str(p))}"

    def lines(self) -> list[str]:
        return list(self._lines)


@dataclass(frozen=True)
class FindingBadges:
    by_severity: dict[str, int]
    confirmed: int
    potential: int

    @classmethod
    def from_findings(cls, findings: list[Finding]) -> FindingBadges:
        by_sev = {s.value: 0 for s in Severity}
        confirmed = potential = 0
        for f in findings:
            by_sev[f.severity.value] += 1
            if f.verification_status is VerificationStatus.VERIFIED:
                confirmed += 1
            elif f.verification_status in (
                VerificationStatus.DETECTED,
                VerificationStatus.NEEDS_TRIAGE,
                VerificationStatus.UNDER_INVESTIGATION,
            ):
                potential += 1
        return cls(by_sev, confirmed, potential)


def status_label(state: AgentState) -> str:
    return _STATE_LABELS.get(state, state.value)


def new_terminal_token() -> str:
    """Cryptographically random token authenticating the loopback xterm.js WebSocket (DOC-05)."""
    return secrets.token_urlsafe(32)


def token_matches(expected: str, provided: str) -> bool:
    return secrets.compare_digest(expected, provided)
