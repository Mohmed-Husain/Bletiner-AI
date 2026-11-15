"""Typed operational events (DOC-03 section 1: zero raw chain-of-thought; structured events only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bletiner.models.redaction import redact


class AgentState(StrEnum):
    IDLE = "IDLE"
    INITIALIZING = "INITIALIZING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    ANALYZING = "ANALYZING"
    REPLANNING = "REPLANNING"
    ERROR_HANDLING = "ERROR_HANDLING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    REPORT_GENERATION = "REPORT_GENERATION"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"


class EventType(StrEnum):
    STATE_CHANGED = "state_changed"
    TASK_PLANNED = "task_planned"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    OBSERVATION = "observation"
    FINDING = "finding"
    STEERING_ACCEPTED = "steering_accepted"
    STEERING_REJECTED = "steering_rejected"
    POLICY_BLOCK = "policy_block"
    ERROR = "error"
    BUDGET = "budget"


@dataclass(frozen=True)
class AgentEvent:
    type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = field(default_factory=dict)

    def redacted(self) -> AgentEvent:
        """Return a copy with secret-looking values masked (DOC-14 section 6.1)."""
        return AgentEvent(self.type, self.timestamp, {k: redact(str(v)) for k, v in self.payload.items()})
