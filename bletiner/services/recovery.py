"""Startup reconciliation and crash recovery (DOC-04 section 10.2, DOC-14 Phase 7).

On launch the service compares Docker containers labelled ``com.bletiner.managed=true`` against the SQLite
statuses and decides an action per session. The decision logic is pure (:func:`reconcile`) so it is fully
unit-testable without Docker; the thin driver that lists containers and applies actions wraps it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bletiner.models.entities import SessionStatus

# Session statuses that mean "this session believed it was live".
_RUNNING = frozenset(
    {SessionStatus.RUNNING, SessionStatus.PAUSING, SessionStatus.RESUMING, SessionStatus.INITIALIZING}
)
# Statuses that mean the session is finished/abandoned; any surviving containers are orphans.
_TERMINAL = frozenset({SessionStatus.COMPLETED, SessionStatus.STOPPED, SessionStatus.FAILED})
_ALIVE_CONTAINER = frozenset({"running", "restarting", "created"})


class ReconcileAction(StrEnum):
    REATTACH = "REATTACH"  # containers alive and session running -> reconnect bridges
    MARK_CRASHED = "MARK_CRASHED"  # a container died under a running session
    MARK_INTERRUPTED = "MARK_INTERRUPTED"  # session running but containers gone -> needs re-provision
    SWEEP_ORPHAN = "SWEEP_ORPHAN"  # containers exist for a finished/unknown session -> remove
    NONE = "NONE"


@dataclass(frozen=True)
class ContainerInfo:
    session_id: str | None  # from the com.bletiner.session label; None if unlabelled
    kali_status: str | None  # docker status string, or None if absent
    gateway_status: str | None


@dataclass(frozen=True)
class ReconcileDecision:
    session_id: str | None
    action: ReconcileAction
    reason: str


def _both_alive(info: ContainerInfo) -> bool:
    return (info.kali_status in _ALIVE_CONTAINER) and (info.gateway_status in _ALIVE_CONTAINER)


def reconcile(
    sessions: dict[str, SessionStatus], containers: dict[str, ContainerInfo]
) -> list[ReconcileDecision]:
    """Decide an action for every session and every stray container set. Deterministic, ordered by id."""
    decisions: list[ReconcileDecision] = []

    for sid in sorted(sessions):
        status = sessions[sid]
        info = containers.get(sid)
        if status in _RUNNING:
            if info is None:
                decisions.append(
                    ReconcileDecision(
                        sid, ReconcileAction.MARK_INTERRUPTED, "session RUNNING but no containers found"
                    )
                )
            elif _both_alive(info):
                decisions.append(ReconcileDecision(sid, ReconcileAction.REATTACH, "both containers alive"))
            else:
                decisions.append(
                    ReconcileDecision(
                        sid, ReconcileAction.MARK_CRASHED, "a container is not alive under a running session"
                    )
                )
        elif status in _TERMINAL and info is not None:
            decisions.append(
                ReconcileDecision(
                    sid, ReconcileAction.SWEEP_ORPHAN, f"containers survive a {status.value} session"
                )
            )

    # Containers whose session label matches no known session, or is unlabelled, are orphans to sweep.
    for cid, info in sorted(containers.items()):
        if info.session_id is None or info.session_id not in sessions:
            decisions.append(
                ReconcileDecision(
                    info.session_id,
                    ReconcileAction.SWEEP_ORPHAN,
                    f"container set {cid} maps to no active session",
                )
            )
    return decisions
