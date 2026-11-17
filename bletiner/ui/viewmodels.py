"""Headless UI view-models and presentation rules (no Qt import).

The PySide6 widgets in desktop.py are thin shells: every label, badge count, progress value and
readiness decision is computed here so it can be unit-tested without a display. Nothing in this module
invents data -- the helpers only reformat real AgentEvent payloads, Finding entities and readiness
snapshots.
"""

from __future__ import annotations

import secrets
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.models.entities import Finding, Severity, VerificationStatus
from bletiner.models.redaction import redact

# --- Agent state presentation -----------------------------------------------------------------------------
_STATE_LABELS: dict[AgentState, str] = {
    AgentState.IDLE: "Idle",
    AgentState.INITIALIZING: "Initializing",
    AgentState.PLANNING: "Planning",
    AgentState.EXECUTING: "Running tool",
    AgentState.OBSERVING: "Observing",
    AgentState.ANALYZING: "Analyzing",
    AgentState.REPLANNING: "Re-planning",
    AgentState.ERROR_HANDLING: "Handling error",
    AgentState.PAUSING: "Pausing",
    AgentState.PAUSED: "Paused",
    AgentState.RESUMING: "Resuming",
    AgentState.REPORT_GENERATION: "Generating report",
    AgentState.STOPPING: "Stopping",
    AgentState.STOPPED: "Stopped",
    AgentState.COMPLETED: "Completed",
}

# Phase progress is derived from real AgentState transitions only (never a static demo percentage).
_PHASE_PROGRESS: dict[AgentState, int] = {
    AgentState.IDLE: 0,
    AgentState.INITIALIZING: 5,
    AgentState.PLANNING: 12,
    AgentState.EXECUTING: 30,
    AgentState.OBSERVING: 45,
    AgentState.ANALYZING: 55,
    AgentState.REPLANNING: 70,
    AgentState.REPORT_GENERATION: 90,
    AgentState.PAUSING: 0,
    AgentState.RESUMING: 0,
    AgentState.STOPPING: 0,
    AgentState.STOPPED: 0,
    AgentState.ERROR_HANDLING: 0,
    AgentState.COMPLETED: 100,
}

_RUNNING_STATES = frozenset(
    {
        AgentState.INITIALIZING,
        AgentState.PLANNING,
        AgentState.EXECUTING,
        AgentState.OBSERVING,
        AgentState.ANALYZING,
        AgentState.REPLANNING,
        AgentState.PAUSING,
        AgentState.PAUSED,
        AgentState.RESUMING,
        AgentState.REPORT_GENERATION,
        AgentState.STOPPING,
    }
)

_PILL_VARIANTS: dict[AgentState, str] = {
    AgentState.IDLE: "gray",
    AgentState.INITIALIZING: "blue",
    AgentState.PLANNING: "blue",
    AgentState.EXECUTING: "green",
    AgentState.OBSERVING: "green",
    AgentState.ANALYZING: "blue",
    AgentState.REPLANNING: "blue",
    AgentState.ERROR_HANDLING: "red",
    AgentState.PAUSING: "yellow",
    AgentState.PAUSED: "yellow",
    AgentState.RESUMING: "yellow",
    AgentState.REPORT_GENERATION: "blue",
    AgentState.STOPPING: "yellow",
    AgentState.STOPPED: "gray",
    AgentState.COMPLETED: "green",
}


def _coerce_state(value: object) -> AgentState | None:
    if isinstance(value, AgentState):
        return value
    try:
        return AgentState(str(value))
    except ValueError:
        return None


def status_label(state: AgentState) -> str:
    """Human label for an agent state."""
    return _STATE_LABELS.get(state, state.value)


def is_running_state(state: AgentState) -> bool:
    """True while the agent/bridge considers an investigation in flight (pause included)."""
    return state in _RUNNING_STATES


def progress_for_state(state: AgentState, current: int = 0) -> int:
    """Deterministic phase progress in percent, monotonic within a run.

    Progress advances only on real state transitions; a repeated or earlier phase never moves the bar
    backwards, and stopped/errored runs are not given a fabricated 100%.
    """
    current = max(0, min(100, int(current)))
    if state in (AgentState.STOPPED, AgentState.ERROR_HANDLING):
        return current
    if state in (AgentState.PAUSING, AgentState.RESUMING, AgentState.STOPPING):
        return current
    target = _PHASE_PROGRESS.get(state, 0)
    return max(current, target)


def state_pill_variant(state: AgentState) -> str:
    """Colour variant for the truthful status pill."""
    return _PILL_VARIANTS.get(state, "gray")


def phase_description(state: AgentState, *, tools: int = 0, findings: int = 0) -> str:
    """Short factual phase line for the investigation card (counts are real run counters)."""
    label = status_label(state)
    extras: list[str] = []
    if tools:
        extras.append(f"{tools} tool run{'s' if tools != 1 else ''}")
    if findings:
        extras.append(f"{findings} finding{'s' if findings != 1 else ''}")
    if state in (AgentState.IDLE, AgentState.COMPLETED, AgentState.STOPPED, AgentState.ERROR_HANDLING):
        return label
    return f"{label} \u00b7 " + " \u00b7 ".join(extras) if extras else label


# --- Activity feed ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FeedRow:
    """One rendered activity-feed row: human text, glyph and semantic colour kind."""

    timestamp: str
    text: str
    glyph: str
    kind: str


_KIND_COLORS: dict[str, str] = {
    "state": "#38bdf8",
    "tool": "#38bdf8",
    "success": "#10b981",
    "finding": "#f59e0b",
    "error": "#ef4444",
    "block": "#ef4444",
    "steering": "#7dd3fc",
    "info": "#8892b0",
    "budget": "#f59e0b",
}


def feed_row_color(kind: str) -> str:
    """Feed row accent colour for a row kind."""
    return _KIND_COLORS.get(kind, "#8892b0")


def feed_row(event: AgentEvent) -> FeedRow | None:
    """Format one real agent event as an activity-feed row (redacted, no chain-of-thought)."""
    payload = event.payload
    stamp = event.timestamp.strftime("%H:%M:%S")
    match event.type:
        case EventType.STATE_CHANGED:
            state = _coerce_state(payload.get("state"))
            if state is None:
                return FeedRow(stamp, redact(str(payload.get("state", "state"))), "\u25cf", "state")
            return FeedRow(stamp, status_label(state), "\u25cf", "state")
        case EventType.TASK_PLANNED:
            tasks = payload.get("tasks")
            count = len(tasks) if isinstance(tasks, list) else payload.get("count")
            text = f"Planned {count} tasks" if count else "Plan updated"
            return FeedRow(stamp, text, "\u2630", "info")
        case EventType.TOOL_STARTED:
            argv = payload.get("argv", [])
            command = " ".join(str(part) for part in argv) if isinstance(argv, list) else str(argv)
            tool = str(payload.get("tool", "tool"))
            text = f"{tool}: {command}".strip(": ")
            return FeedRow(stamp, text, "\u25b6", "tool")
        case EventType.TOOL_COMPLETED:
            tool = str(payload.get("tool", "tool"))
            code = payload.get("exit_code")
            detail = f"exit {code}" if code is not None else "completed"
            if payload.get("timed_out"):
                detail = "timed out"
            return FeedRow(stamp, f"{tool} finished ({detail})", "\u2713", "success")
        case EventType.OBSERVATION:
            message = (
                payload.get("message") or payload.get("note") or payload.get("observation") or "Observation"
            )
            return FeedRow(stamp, redact(str(message)), "\u24d8", "info")
        case EventType.FINDING:
            severity = str(payload.get("severity", "INFO")).upper()
            title = str(payload.get("title", "finding"))
            asset = str(payload.get("asset", ""))
            text = f"{severity} finding: {title}" + (f" on {asset}" if asset else "")
            return FeedRow(stamp, redact(text), "\u2605", "finding")
        case EventType.STEERING_ACCEPTED:
            return FeedRow(
                stamp, f"Steering accepted: {payload.get('instruction', '')}", "\u27a4", "steering"
            )
        case EventType.STEERING_REJECTED:
            reason = payload.get("reason") or payload.get("error") or "rejected"
            return FeedRow(stamp, f"Steering rejected: {reason}", "\u26d4", "error")
        case EventType.POLICY_BLOCK:
            tool = payload.get("tool", "tool")
            error = payload.get("error") or payload.get("reason") or "blocked by policy"
            return FeedRow(stamp, f"Blocked {tool}: {error}", "\u26d4", "block")
        case EventType.ERROR:
            tool = payload.get("tool")
            error = payload.get("error") or payload.get("message") or "error"
            text = f"{tool}: {error}" if tool else str(error)
            return FeedRow(stamp, redact(text), "\u2715", "error")
        case EventType.BUDGET:
            reason = payload.get("reason") or "budget"
            return FeedRow(stamp, f"Budget limit reached ({reason})", "\u25d4", "budget")
    return None


# Operational events shown in the activity feed; redacted so no raw chain-of-thought reaches the UI.
_FEED_TYPES = frozenset(EventType)


@dataclass
class FeedViewModel:
    """Bounded activity feed of redacted, human-readable operational lines (legacy text view-model)."""

    max_lines: int = 500
    _lines: deque[str] = field(default_factory=lambda: deque(maxlen=500))

    def __post_init__(self) -> None:
        self._lines = deque(maxlen=self.max_lines)

    def ingest(self, event: AgentEvent) -> str | None:
        """Append an event's text line and return it, or None for non-operational events."""
        if event.type not in _FEED_TYPES:
            return None
        row = feed_row(event)
        if row is None:
            return None
        line = f"{row.timestamp}  {row.text}"
        self._lines.append(line)
        return line

    def ingest_row(self, event: AgentEvent) -> FeedRow | None:
        """Return the rich row for an event while keeping the text history in sync."""
        if event.type not in _FEED_TYPES:
            return None
        row = feed_row(event)
        if row is not None:
            self._lines.append(f"{row.timestamp}  {row.text}")
        return row

    def lines(self) -> list[str]:
        return list(self._lines)


# --- Findings ---------------------------------------------------------------------------------------------
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


def badge_tooltip(badges: FindingBadges) -> str:
    """Real severity breakdown tooltip for the findings buttons."""
    parts = [f"{count} {severity}" for severity, count in badges.by_severity.items() if count]
    return "Findings by severity: " + (", ".join(parts) if parts else "none")


# --- Readiness / start gating -----------------------------------------------------------------------------
@dataclass(frozen=True)
class ReadinessIssue:
    """One actionable prerequisite problem displayed in the investigation card banner."""

    key: str
    severity: str  # 'error' | 'warning' | 'info'
    title: str
    detail: str
    action: str  # 'settings' | 'retry' | 'none'


def readiness_issues(
    *,
    available: bool,
    docker_running: bool,
    gate_certified: bool,
    kali_container: str | None,
    ai_ready: bool,
    engine: str,
    provider: str = "",
    model: str = "",
    checking: bool = False,
) -> list[ReadinessIssue]:
    """Derive the banner issues from the real readiness snapshot."""
    issues: list[ReadinessIssue] = []
    if checking:
        return [
            ReadinessIssue(
                "checking",
                "info",
                "Checking sandbox readiness",
                "The background probe is verifying Docker, the Kali container and the egress gate.",
                "retry",
            )
        ]
    if not available:
        return [
            ReadinessIssue(
                "controller",
                "info",
                "Investigation controller not attached",
                "Launch the desktop app to connect the investigation controller.",
                "none",
            )
        ]
    if not docker_running:
        issues.append(
            ReadinessIssue(
                "docker",
                "error",
                "Docker Desktop is offline",
                "Start Docker Desktop. The Kali sandbox container is required for real tool execution.",
                "retry",
            )
        )
    elif kali_container is None:
        issues.append(
            ReadinessIssue(
                "kali",
                "error",
                "Kali sandbox container not found",
                "Start the 'my-kali' container so tools can execute inside the sandbox.",
                "retry",
            )
        )
    if not gate_certified:
        if engine == "ai":
            issues.append(
                ReadinessIssue(
                    "gate",
                    "error",
                    "AI engine needs the certified egress gate",
                    "No Gate 3 certification report was found; the autonomous AI path stays blocked "
                    "until the egress gate is certified. The local recon engine does not use it.",
                    "retry",
                )
            )
        else:
            issues.append(
                ReadinessIssue(
                    "gate",
                    "warning",
                    "Egress gate is not certified",
                    "Gate 3 certification was not found, so the autonomous AI engine stays blocked. "
                    "Deterministic local recon does not use the egress gate.",
                    "retry",
                )
            )
    if engine == "ai" and not ai_ready:
        issues.append(
            ReadinessIssue(
                "ai_key",
                "error",
                "AI engine needs a configured provider key and model",
                f"The AI provider is not ready ({provider or 'no provider'} / {model or 'no model selected'}). "
                "Add a key in Settings and select a model in Models, or switch to Local recon.",
                "settings",
            )
        )
    return issues


def start_block_reason(
    *,
    available: bool,
    docker_running: bool,
    gate_certified: bool,
    kali_container: str | None,
    ai_ready: bool,
    engine: str,
    checking: bool = False,
) -> str | None:
    """Return why Start must stay disabled, or None when the run can start."""
    if checking:
        return "Sandbox readiness is still being checked."
    if not available:
        return "The investigation controller is not attached."
    if not docker_running:
        return "Docker Desktop is offline."
    if kali_container is None:
        return "The Kali sandbox container is not running."
    if engine == "ai":
        if not ai_ready:
            return "The AI engine has no configured provider API key."
        if not gate_certified:
            return "The AI engine needs the certified egress gate."
    return None


def ai_engine_block_reason(
    *,
    docker_running: bool,
    gate_certified: bool,
    kali_container: str | None,
    ai_ready: bool,
) -> str | None:
    """Return why the AI engine toggle is unavailable, or None when the AI engine can be selected."""
    if not ai_ready:
        return "no provider key/model configured (see Settings)"
    if not docker_running:
        return "Docker Desktop is offline"
    if kali_container is None:
        return "the Kali sandbox container is not running"
    if not gate_certified:
        return "the egress gate is not certified"
    return None


def readiness_pill(available: bool, docker_running: bool, kali_container: str | None) -> tuple[str, str]:
    """Sidebar/footer Docker pill text and colour variant from the real snapshot."""
    if not available:
        return ("Docker: not checked", "gray")
    if not docker_running:
        return ("Docker: Offline", "red")
    if kali_container is None:
        return ("Docker: Running", "yellow")
    return ("Docker: Running", "green")


def engine_label(engine: str) -> str:
    """Human label for the execution engine identifier."""
    return "AI agent" if str(engine).lower() == "ai" else "Local recon"


def mode_label(mode: str) -> str:
    """Human label for an investigation mode identifier."""
    return "Deep investigation" if str(mode).lower().startswith("deep") else "Fast discovery"


def model_label(provider: str, model: str) -> str:
    """Human label for the active model; never invents a model name."""
    if not provider and not model:
        return "No AI model configured"
    if model and provider:
        return f"{model} ({provider})"
    return model or provider


# --- Formatting helpers -----------------------------------------------------------------------------------
def format_duration(seconds: float | None) -> str:
    """Compact duration text for telemetry footers."""
    if seconds is None:
        return "\u2014"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(round(seconds), 60)
    return f"{minutes}m {remainder:02d}s"


def format_tokens(tokens: int) -> str:
    """Compact token count."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M tokens"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k tokens"
    return f"{tokens} tokens"


def format_relative_time(value: datetime | None) -> str:
    """Relative timestamp for session summaries ('3m ago', '2026-11-16')."""
    if value is None:
        return "unknown time"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - value
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return value.strftime("%Y-%m-%d %H:%M")
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3600}h ago"
    if seconds < 7 * 86_400:
        return f"{seconds // 86_400}d ago"
    return value.strftime("%Y-%m-%d")


def summarize_outcome(*, findings: int, duration_s: float | None, stop_reason: str) -> str:
    """One-line truthful outcome summary for the feed / terminal."""
    reason = stop_reason.replace("_", " ")
    return (
        f"Investigation finished ({reason}) \u2014 {findings} finding"
        f"{'s' if findings != 1 else ''} in {format_duration(duration_s)}"
    )


# --- Terminal bridge token (loopback xterm.js WebSocket authentication) -----------------------------------
def new_terminal_token() -> str:
    """Cryptographically random token authenticating the loopback terminal bridge."""
    return secrets.token_urlsafe(32)


def token_matches(expected: str, provided: str) -> bool:
    return secrets.compare_digest(expected, provided)
