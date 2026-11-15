"""Phase 4 Tier B: tool registry, state tree, steering, Gate-3 guard, and the agent loop FSM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bletiner.agent.events import AgentState, EventType
from bletiner.agent.gate import is_gate3_certified, require_gate3_certified
from bletiner.agent.loop import AgentConfig, AgentLoop, LoopBudget, ToolOutcome
from bletiner.agent.state_tree import StateTree
from bletiner.agent.steering import SteeringQueue, SteeringVerdict, validate_instruction
from bletiner.models.errors import PoCVerificationFailedError, PolicyViolationError, Tier4ToolBlockedError
from bletiner.models.scope import ScopeSpec
from bletiner.providers.base import AgentMessage, ModelProfile, ProviderResponse, Role, ToolUse, Usage
from bletiner.providers.fake import FakeProvider
from bletiner.tools.catalog import default_registry
from bletiner.tools.registry import InvestigationMode, ToolTier

PROFILE = ModelProfile("fake", "fake-1")


def _resp(tool_uses: tuple[ToolUse, ...] = (), content: str = "", out: int = 3) -> ProviderResponse:
    return ProviderResponse(AgentMessage(Role.ASSISTANT, content, tool_uses), Usage(5, out))


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        self.calls.append((tool_name, argv))
        return ToolOutcome(exit_code=0, stdout=f"output of {tool_name}")


# --- registry --------------------------------------------------------------------------------------------
class TestToolRegistry:
    def test_tier4_blocked_in_every_mode(self) -> None:
        reg = default_registry()
        for mode in (InvestigationMode.BASIC, InvestigationMode.DEEP):
            for t4 in ("masscan", "sqlmap_exploit", "hydra", "metasploit"):
                with pytest.raises(Tier4ToolBlockedError):
                    reg.assert_schedulable(t4, mode)

    def test_tier3_deep_only(self) -> None:
        reg = default_registry()
        with pytest.raises(PolicyViolationError):
            reg.assert_schedulable("nuclei_cve", InvestigationMode.BASIC)
        assert reg.assert_schedulable("nuclei_cve", InvestigationMode.DEEP).tier is ToolTier.INTRUSIVE

    def test_schedulable_never_includes_tier4(self) -> None:
        reg = default_registry()
        for mode in (InvestigationMode.BASIC, InvestigationMode.DEEP):
            assert all(t.tier is not ToolTier.DESTRUCTIVE for t in reg.schedulable(mode))

    def test_render_argv_keeps_injection_literal(self) -> None:
        reg = default_registry()
        argv = reg.get("nmap_fast").render_argv({"target": "x; id"})
        assert argv[-1] == "x; id"

    def test_unknown_tool_rejected(self) -> None:
        with pytest.raises(PolicyViolationError):
            default_registry().assert_schedulable("nope", InvestigationMode.DEEP)


# --- state tree ------------------------------------------------------------------------------------------
class TestStateTree:
    def test_lineage_and_branch(self) -> None:
        t = StateTree()
        a = t.commit("PLANNING", {})
        b = t.commit("EXECUTING", {})
        assert [c.state for c in t.lineage()] == ["PLANNING", "EXECUTING"]
        t.rewind_to(a.checkpoint_id)
        c = t.commit("PLANNING", {"branch": 2})
        assert c.parent_id == a.checkpoint_id
        assert {n.checkpoint_id for n in t.children(a.checkpoint_id)} == {b.checkpoint_id, c.checkpoint_id}


# --- steering --------------------------------------------------------------------------------------------
class TestSteering:
    SCOPE = ScopeSpec(domains=("local-lab.test", "*.local-lab.test"))

    def test_unsafe_rejected(self) -> None:
        assert (
            validate_instruction("run sqlmap --os-shell", self.SCOPE).verdict
            is SteeringVerdict.REJECTED_SAFETY
        )

    def test_out_of_scope_rejected(self) -> None:
        r = validate_instruction("now scan evil-corp.test", self.SCOPE)
        assert r.verdict is SteeringVerdict.REJECTED_SCOPE

    def test_in_scope_accepted(self) -> None:
        r = validate_instruction("focus on api.local-lab.test port 443", self.SCOPE)
        assert r.accepted

    def test_queue_only_keeps_accepted(self) -> None:
        q = SteeringQueue(self.SCOPE)
        q.submit("run hydra")
        q.submit("check login.local-lab.test")
        assert q.drain() == ["check login.local-lab.test"]


# --- gate guard ------------------------------------------------------------------------------------------
class TestGate3Guard:
    def _write(self, d: Path, **totals: object) -> None:
        d.mkdir(parents=True, exist_ok=True)
        (d / "gate3_live_1.json").write_text(
            json.dumps(
                {
                    "mode": totals.pop("mode", "live"),
                    "gate_passed": totals.pop("passed", True),
                    "totals": {"total": 23, "pass": 23, "fail": 0, "not_run": 0, **totals},
                }
            )
        )

    def test_no_report_blocks(self, tmp_path: Path) -> None:
        assert not is_gate3_certified(tmp_path)
        with pytest.raises(PoCVerificationFailedError):
            require_gate3_certified(tmp_path)

    def test_notrun_report_blocks(self, tmp_path: Path) -> None:
        self._write(tmp_path, mode="not-run", passed=False, **{"not_run": 23, "pass": 0})
        assert not is_gate3_certified(tmp_path)

    def test_failing_report_blocks(self, tmp_path: Path) -> None:
        self._write(tmp_path, passed=False, **{"fail": 2})
        assert not is_gate3_certified(tmp_path)

    def test_genuine_live_pass_certifies(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        assert is_gate3_certified(tmp_path)
        require_gate3_certified(tmp_path)  # does not raise

    def test_newer_live_pass_not_shadowed_by_lexically_later_notrun(self, tmp_path: Path) -> None:
        # Audit fix: a stale 'not-run' file whose NAME sorts after the live one must not block certification.
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "gate3_live_20260101T000000Z.json").write_text(
            json.dumps(
                {
                    "mode": "live",
                    "gate_passed": True,
                    "generated_at": "2026-02-01T00:00:00+00:00",
                    "totals": {"total": 23, "pass": 23, "fail": 0, "not_run": 0},
                }
            )
        )
        (tmp_path / "gate3_not-run_20260101T000000Z.json").write_text(
            json.dumps(
                {
                    "mode": "not-run",
                    "gate_passed": False,
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "totals": {"total": 23, "pass": 0, "fail": 0, "not_run": 23},
                }
            )
        )
        assert is_gate3_certified(tmp_path)  # the newer live pass wins by generated_at

    def test_malformed_report_without_timestamp_is_not_hidden(self, tmp_path: Path) -> None:
        # Hardening: a not-run report lacking generated_at must not be silently hidden behind an
        # older valid live pass; it sorts as newest and blocks (fail-closed).
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "gate3_live_old.json").write_text(
            json.dumps(
                {
                    "mode": "live",
                    "gate_passed": True,
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "totals": {"total": 23, "pass": 23, "fail": 0, "not_run": 0},
                }
            )
        )
        (tmp_path / "gate3_not-run_new.json").write_text(
            json.dumps(
                {
                    "mode": "not-run",
                    "gate_passed": False,
                    "totals": {"total": 23, "pass": 0, "fail": 0, "not_run": 23},
                }
            )
        )
        assert not is_gate3_certified(tmp_path)

    def test_newer_notrun_does_block_a_stale_live(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "gate3_live_a.json").write_text(
            json.dumps(
                {
                    "mode": "live",
                    "gate_passed": True,
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "totals": {"total": 23, "pass": 23, "fail": 0, "not_run": 0},
                }
            )
        )
        (tmp_path / "gate3_not-run_b.json").write_text(
            json.dumps(
                {
                    "mode": "not-run",
                    "gate_passed": False,
                    "generated_at": "2026-02-01T00:00:00+00:00",
                    "totals": {"total": 23, "pass": 0, "fail": 0, "not_run": 23},
                }
            )
        )
        assert not is_gate3_certified(tmp_path)  # a genuinely newer not-run reflects current reality


# --- agent loop ------------------------------------------------------------------------------------------
class TestAgentLoop:
    def _loop(self, scripted, mode=InvestigationMode.DEEP, budget=None, steering=None):
        return AgentLoop(
            provider=FakeProvider(scripted),
            registry=default_registry(),
            executor=FakeExecutor(),
            config=AgentConfig(PROFILE, mode=mode, budget=budget or LoopBudget()),
            steering=steering,
        )

    async def test_plan_execute_record_finish(self) -> None:
        scripted = [
            _resp((ToolUse("1", "nmap_fast", {"target": "local-lab.test"}),)),
            _resp(
                (
                    ToolUse(
                        "2",
                        "record_finding",
                        {
                            "title": "Open 443",
                            "severity": "LOW",
                            "confidence": "HIGH",
                            "asset": "local-lab.test",
                            "summary": "https up",
                        },
                    ),
                )
            ),
            _resp((ToolUse("3", "finish_investigation", {}),)),
        ]
        loop = self._loop(scripted)
        result = await loop.run("investigate local-lab.test")
        assert result.final_state is AgentState.COMPLETED
        assert result.stop_reason == "finish_called"
        assert [f.title for f in result.findings] == ["Open 443"]
        assert any(e.type is EventType.FINDING for e in result.events)

    async def test_tier4_request_is_blocked_not_executed(self) -> None:
        executor = FakeExecutor()
        loop = AgentLoop(
            FakeProvider(
                [
                    _resp((ToolUse("1", "masscan", {"target": "local-lab.test"}),)),
                    _resp((ToolUse("2", "finish_investigation", {}),)),
                ]
            ),
            default_registry(),
            executor,
            AgentConfig(PROFILE, mode=InvestigationMode.DEEP),
        )
        result = await loop.run("x")
        assert executor.calls == []  # Tier 4 never reached the executor
        assert any(e.type is EventType.POLICY_BLOCK and e.payload.get("tier") == 4 for e in result.events)

    async def test_iteration_budget_terminates(self) -> None:
        # Model always asks for a tool; budget must stop the loop.
        scripted = [_resp((ToolUse(str(i), "nmap_fast", {"target": "local-lab.test"}),)) for i in range(10)]
        loop = self._loop(scripted, budget=LoopBudget(max_iterations=3))
        result = await loop.run("x")
        assert result.iterations == 3
        assert result.stop_reason == "iteration_budget"

    async def test_model_finish_without_tools(self) -> None:
        loop = self._loop([_resp(content="nothing to do")])
        result = await loop.run("x")
        assert result.stop_reason == "model_finished"

    async def test_steering_injected(self) -> None:
        steering = SteeringQueue(ScopeSpec(domains=("local-lab.test",)))
        steering.submit("look at local-lab.test/admin")
        loop = self._loop([_resp(content="ok")], steering=steering)
        result = await loop.run("x")
        assert any(e.type is EventType.STEERING_ACCEPTED for e in result.events)
