"""Idempotency engine for safe task resumption (DOC-14 Phase 7).

On resume, an interrupted task is auto-replayed only when it is safe (read-only recon). State-altering
or ambiguous tasks are held for operator confirmation rather than silently re-executed.
"""

from __future__ import annotations

from enum import StrEnum

from bletiner.models.entities import TaskType

# Task types that only read/observe the target and can be replayed without side effects.
_SAFE_REPLAY = frozenset({TaskType.RECON, TaskType.DISCOVERY, TaskType.ANALYZE, TaskType.REPORT})
# PROBE/VERIFY may send active payloads; replaying could double-submit, so they need confirmation.
_NEEDS_CONFIRM = frozenset({TaskType.PROBE, TaskType.VERIFY})


class ResumeDecision(StrEnum):
    REPLAY = "REPLAY"  # safe, idempotent read-only task: re-run automatically
    SKIP_COMPLETED = "SKIP_COMPLETED"  # already finished: do not re-run
    HOLD_FOR_CONFIRM = "HOLD_FOR_CONFIRM"  # state-altering: ask the operator


def decide_resume(task_type: TaskType, *, completed: bool, is_idempotent: bool) -> ResumeDecision:
    if completed:
        return ResumeDecision.SKIP_COMPLETED
    if task_type in _SAFE_REPLAY and is_idempotent:
        return ResumeDecision.REPLAY
    if task_type in _NEEDS_CONFIRM or not is_idempotent:
        return ResumeDecision.HOLD_FOR_CONFIRM
    return ResumeDecision.HOLD_FOR_CONFIRM
