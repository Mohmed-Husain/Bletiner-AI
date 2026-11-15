"""Phase 7 Tier B: reconciliation, idempotency, and secret store/redaction."""

from __future__ import annotations

import pytest

from bletiner.models.entities import SessionStatus, TaskType
from bletiner.services.idempotency import ResumeDecision, decide_resume
from bletiner.services.recovery import (
    ContainerInfo,
    ReconcileAction,
    reconcile,
)
from bletiner.services.secrets import SecretStore, redact


class TestReconcile:
    def test_reattach_when_both_alive(self) -> None:
        d = reconcile(
            {"s1": SessionStatus.RUNNING},
            {"s1": ContainerInfo("s1", "running", "running")},
        )
        assert d == [type(d[0])("s1", ReconcileAction.REATTACH, d[0].reason)]

    def test_crashed_when_gateway_dead(self) -> None:
        d = reconcile({"s1": SessionStatus.RUNNING}, {"s1": ContainerInfo("s1", "running", "exited")})
        assert d[0].action is ReconcileAction.MARK_CRASHED

    def test_interrupted_when_containers_missing(self) -> None:
        d = reconcile({"s1": SessionStatus.RUNNING}, {})
        assert d[0].action is ReconcileAction.MARK_INTERRUPTED

    def test_sweep_orphan_for_finished_session(self) -> None:
        d = reconcile({"s1": SessionStatus.COMPLETED}, {"s1": ContainerInfo("s1", "running", "running")})
        assert any(x.action is ReconcileAction.SWEEP_ORPHAN for x in d)

    def test_sweep_unlabelled_and_unknown_containers(self) -> None:
        d = reconcile(
            {},
            {
                "c1": ContainerInfo(None, "running", "running"),
                "c2": ContainerInfo("ghost", "running", "running"),
            },
        )
        assert [x.action for x in d] == [ReconcileAction.SWEEP_ORPHAN, ReconcileAction.SWEEP_ORPHAN]

    def test_no_double_sweep_for_terminal_known_session(self) -> None:
        # A terminal session with containers yields exactly one sweep, not two.
        d = reconcile({"s1": SessionStatus.STOPPED}, {"s1": ContainerInfo("s1", "running", "running")})
        assert sum(1 for x in d if x.action is ReconcileAction.SWEEP_ORPHAN) == 1


class TestIdempotency:
    @pytest.mark.parametrize("t", [TaskType.RECON, TaskType.DISCOVERY, TaskType.ANALYZE, TaskType.REPORT])
    def test_safe_tasks_replay(self, t: TaskType) -> None:
        assert decide_resume(t, completed=False, is_idempotent=True) is ResumeDecision.REPLAY

    @pytest.mark.parametrize("t", [TaskType.PROBE, TaskType.VERIFY])
    def test_active_tasks_hold(self, t: TaskType) -> None:
        assert decide_resume(t, completed=False, is_idempotent=True) is ResumeDecision.HOLD_FOR_CONFIRM

    def test_completed_is_skipped(self) -> None:
        assert decide_resume(TaskType.RECON, completed=True, is_idempotent=True) is (
            ResumeDecision.SKIP_COMPLETED
        )

    def test_non_idempotent_recon_holds(self) -> None:
        assert decide_resume(TaskType.RECON, completed=False, is_idempotent=False) is (
            ResumeDecision.HOLD_FOR_CONFIRM
        )


class TestSecrets:
    def test_redact_patterns(self) -> None:
        text = "key sk-ABCDEFGHIJKLMNOP and Authorization: Bearer abc123def456 end"
        out = redact(text)
        assert "sk-ABCDEFGHIJKLMNOP" not in out and "Bearer abc123def456" not in out

    def test_store_round_trip_memory_backend(self) -> None:
        store = SecretStore(service="bletiner-test", use_keyring=False)
        assert store.backend == "memory"
        store.set("openai", "sk-secret")
        assert store.get("openai") == "sk-secret"
        store.delete("openai")
        assert store.get("openai") is None

    def test_backend_reported(self) -> None:
        assert SecretStore().backend in {"keyring", "memory"}
