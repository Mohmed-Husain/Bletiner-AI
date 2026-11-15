"""Autonomous agent loop FSM (DOC-03 section 2, DOC-14 Phase 4).

Plan -> Execute -> Observe -> Analyze -> Replan, with code-enforced Tier-4 blocking, mid-turn steering,
loop-budget limits, and state-tree checkpoints. Deterministic: given a scripted provider and a fake tool
executor the whole run is reproducible (DOC-10 "Determinism First"). No network, no Docker here — the real
executor is injected by the service layer, which first calls ``require_gate3_certified``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.agent.state_tree import StateTree
from bletiner.agent.steering import SteeringQueue
from bletiner.models.errors import PolicyViolationError, Tier4ToolBlockedError
from bletiner.providers.base import (
    AgentMessage,
    ModelProfile,
    ProviderAdapter,
    Role,
    ToolSpec,
    ToolUse,
)
from bletiner.tools.registry import InvestigationMode, ToolRegistry

RECORD_FINDING = "record_finding"
FINISH = "finish_investigation"


@dataclass(frozen=True)
class ToolOutcome:
    exit_code: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@runtime_checkable
class ToolExecutor(Protocol):
    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome: ...


@dataclass
class LoopBudget:
    max_iterations: int = 20
    max_tokens: int = 100_000


@dataclass
class AgentConfig:
    profile: ModelProfile
    mode: InvestigationMode = InvestigationMode.BASIC
    budget: LoopBudget = field(default_factory=LoopBudget)
    system_prompt: str = "You are an autonomous, non-destructive security investigator."


@dataclass
class RecordedFinding:
    title: str
    severity: str
    confidence: str
    asset: str
    summary: str


@dataclass
class AgentResult:
    final_state: AgentState
    findings: list[RecordedFinding]
    iterations: int
    tokens_used: int
    events: list[AgentEvent]
    stop_reason: str


def _meta_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            RECORD_FINDING,
            "Record a verified or candidate security finding.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "severity": {"type": "string"},
                    "confidence": {"type": "string"},
                    "asset": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["title", "severity", "confidence", "asset", "summary"],
            },
        ),
        ToolSpec(FINISH, "Signal the investigation is complete.", {"type": "object", "properties": {}}),
    ]


class AgentLoop:
    def __init__(
        self,
        provider: ProviderAdapter,
        registry: ToolRegistry,
        executor: ToolExecutor,
        config: AgentConfig,
        steering: SteeringQueue | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._executor = executor
        self._cfg = config
        self._steering = steering
        self._on_event = on_event
        self.tree = StateTree()
        self._events: list[AgentEvent] = []
        self._messages: list[AgentMessage] = []
        self._findings: list[RecordedFinding] = []
        self._tokens = 0

    def _emit(self, etype: EventType, **payload: object) -> None:
        ev = AgentEvent(etype, payload=dict(payload))
        self._events.append(ev)
        if self._on_event is not None:
            self._on_event(ev)

    def _transition(self, state: AgentState) -> None:
        self.tree.commit(state.value, {"messages": len(self._messages)}, context_tokens=self._tokens)
        self._emit(EventType.STATE_CHANGED, state=state.value)

    def _tool_specs(self) -> list[ToolSpec]:
        specs = [
            ToolSpec(
                t.name,
                t.description,
                {"type": "object", "properties": t.parameters, "required": list(t.parameters)},
            )
            for t in self._registry.schedulable(self._cfg.mode)
        ]
        return specs + _meta_tool_specs()

    async def run(self, objective: str) -> AgentResult:
        self._transition(AgentState.INITIALIZING)
        self._messages = [
            AgentMessage(Role.SYSTEM, self._cfg.system_prompt),
            AgentMessage(Role.USER, objective),
        ]
        stop_reason = "completed"
        state = AgentState.PLANNING
        iterations = 0
        for _ in range(self._cfg.budget.max_iterations):
            iterations += 1
            self._apply_steering()
            if self._tokens >= self._cfg.budget.max_tokens:
                stop_reason, state = "budget_exhausted", AgentState.REPORT_GENERATION
                self._emit(EventType.BUDGET, reason="tokens", tokens=self._tokens)
                break
            self._transition(AgentState.PLANNING)
            response = self._provider.complete(self._cfg.profile, self._messages, tools=self._tool_specs())
            self._tokens += response.usage.total
            self._messages.append(response.message)
            if not response.requested_tools:
                stop_reason, state = "model_finished", AgentState.REPORT_GENERATION
                break
            finished = await self._handle_tools(response.requested_tools)
            if finished:
                stop_reason, state = "finish_called", AgentState.REPORT_GENERATION
                break
        else:
            stop_reason, state = "iteration_budget", AgentState.REPORT_GENERATION
            self._emit(EventType.BUDGET, reason="iterations", iterations=self._cfg.budget.max_iterations)

        self._transition(state)
        self._transition(AgentState.COMPLETED)
        return AgentResult(
            final_state=AgentState.COMPLETED,
            findings=list(self._findings),
            iterations=iterations,
            tokens_used=self._tokens,
            events=list(self._events),
            stop_reason=stop_reason,
        )

    def _apply_steering(self) -> None:
        if self._steering is None:
            return
        for instruction in self._steering.drain():
            self._messages.append(AgentMessage(Role.USER, f"[operator steering] {instruction}"))
            self._emit(EventType.STEERING_ACCEPTED, instruction=instruction)

    async def _handle_tools(self, tool_uses: tuple[ToolUse, ...]) -> bool:
        for tu in tool_uses:
            if tu.name == FINISH:
                return True
            if tu.name == RECORD_FINDING:
                self._record_finding(tu)
                self._append_tool_result(tu.id, "finding recorded")
                continue
            try:
                tool = self._registry.assert_schedulable(tu.name, self._cfg.mode)
            except Tier4ToolBlockedError as exc:
                self._emit(EventType.POLICY_BLOCK, tool=tu.name, tier=4, error=str(exc))
                self._append_tool_result(tu.id, f"BLOCKED: {exc}")
                continue
            except PolicyViolationError as exc:
                self._emit(EventType.POLICY_BLOCK, tool=tu.name, error=str(exc))
                self._append_tool_result(tu.id, f"REJECTED: {exc}")
                continue
            params = {k: str(v) for k, v in tu.arguments.items()}
            try:
                argv = tool.render_argv(params)
            except PolicyViolationError as exc:
                self._append_tool_result(tu.id, f"REJECTED: {exc}")
                continue
            self._transition(AgentState.EXECUTING)
            self._emit(EventType.TOOL_STARTED, tool=tool.name, argv=argv)
            outcome = await self._executor.run(tool.name, argv, timeout_s=300.0)
            self._transition(AgentState.OBSERVING)
            self._emit(
                EventType.TOOL_COMPLETED,
                tool=tool.name,
                exit_code=outcome.exit_code,
                timed_out=outcome.timed_out,
            )
            observation = outcome.stdout if outcome.ok else f"[exit {outcome.exit_code}] {outcome.stderr}"
            self._append_tool_result(tu.id, observation[:8000])
            self._transition(AgentState.ANALYZING)
        self._transition(AgentState.REPLANNING)
        return False

    def _record_finding(self, tu: ToolUse) -> None:
        a = tu.arguments
        finding = RecordedFinding(
            title=str(a.get("title", "")),
            severity=str(a.get("severity", "INFO")),
            confidence=str(a.get("confidence", "LOW")),
            asset=str(a.get("asset", "")),
            summary=str(a.get("summary", "")),
        )
        self._findings.append(finding)
        self._emit(EventType.FINDING, title=finding.title, severity=finding.severity, asset=finding.asset)

    def _append_tool_result(self, call_id: str, content: str) -> None:
        self._messages.append(AgentMessage(Role.TOOL, content, tool_call_id=call_id))
