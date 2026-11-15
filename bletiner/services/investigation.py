"""InvestigationService — the live orchestrator that wires the agent to the sandbox (DOC-14 Phase 4/8).

This is the single place that may start the autonomous loop against a real Kali sandbox, and it is also the
single enforcement point for the Gate-3 live-certification requirement: :meth:`start` calls
``require_gate3_certified`` before anything is provisioned, so the agent can never run against a live gateway
that has not been proven fail-closed (DOC-14 rule 5 / section 10, mandatory instruction #6).

The container-exec wiring is injected, so the service itself is unit-testable; the real executor runs inside
Kali via ``bletiner.container.exec_runner`` and is only constructed once the gate is certified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bletiner.agent.gate import require_gate3_certified
from bletiner.agent.loop import AgentConfig, AgentLoop, AgentResult, ToolExecutor
from bletiner.agent.steering import SteeringQueue
from bletiner.models.scope import ScopeSpec
from bletiner.providers.base import ProviderAdapter
from bletiner.tools.registry import ToolRegistry


@dataclass(frozen=True)
class InvestigationRequest:
    objective: str
    scope: ScopeSpec
    config: AgentConfig


class InvestigationService:
    def __init__(
        self,
        provider: ProviderAdapter,
        registry: ToolRegistry,
        *,
        evidence_dir: Path | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._evidence_dir = evidence_dir

    async def start(self, request: InvestigationRequest, executor: ToolExecutor) -> AgentResult:
        """Begin an investigation. Raises ``PoCVerificationFailedError`` if Gate 3 is not live-certified."""
        # INVIOLABLE: no live autonomous run until the egress gate has passed on real Docker.
        if self._evidence_dir is not None:
            require_gate3_certified(self._evidence_dir)
        else:
            require_gate3_certified()

        loop = AgentLoop(
            provider=self._provider,
            registry=self._registry,
            executor=executor,
            config=request.config,
            steering=SteeringQueue(request.scope),
        )
        return await loop.run(request.objective)
