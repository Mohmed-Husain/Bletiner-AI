"""Phase 8 Tier B: InvestigationService gate enforcement and the Docker tool executor (mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bletiner.agent.loop import AgentConfig, ToolOutcome
from bletiner.container.tool_executor import DockerToolExecutor
from bletiner.models.errors import PoCVerificationFailedError
from bletiner.models.scope import ScopeSpec
from bletiner.providers.base import AgentMessage, ModelProfile, ProviderResponse, Role, ToolUse, Usage
from bletiner.providers.fake import FakeProvider
from bletiner.services.investigation import InvestigationRequest, InvestigationService
from bletiner.tools.catalog import default_registry
from bletiner.tools.registry import InvestigationMode


class _Executor:
    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        return ToolOutcome(0, "ok")


def _cert(dir_: Path, *, live: bool = True, passed: bool = True) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "gate3_x.json").write_text(
        json.dumps(
            {
                "mode": "live" if live else "not-run",
                "gate_passed": passed,
                "totals": {"total": 23, "pass": 23, "fail": 0, "not_run": 0},
            }
        )
    )


def _request() -> InvestigationRequest:
    return InvestigationRequest(
        objective="investigate local-lab.test",
        scope=ScopeSpec(domains=("local-lab.test",)),
        config=AgentConfig(ModelProfile("fake", "fake-1"), mode=InvestigationMode.BASIC),
    )


class TestGateEnforcement:
    async def test_start_blocked_without_certification(self, tmp_path: Path) -> None:
        svc = InvestigationService(FakeProvider([]), default_registry(), evidence_dir=tmp_path)
        with pytest.raises(PoCVerificationFailedError):
            await svc.start(_request(), _Executor())

    async def test_start_blocked_on_notrun_report(self, tmp_path: Path) -> None:
        _cert(tmp_path, live=False, passed=False)
        svc = InvestigationService(FakeProvider([]), default_registry(), evidence_dir=tmp_path)
        with pytest.raises(PoCVerificationFailedError):
            await svc.start(_request(), _Executor())

    async def test_start_runs_when_certified(self, tmp_path: Path) -> None:
        _cert(tmp_path)
        provider = FakeProvider(
            [
                ProviderResponse(
                    AgentMessage(Role.ASSISTANT, "", (ToolUse("1", "finish_investigation", {}),)), Usage(1, 1)
                ),
            ]
        )
        svc = InvestigationService(provider, default_registry(), evidence_dir=tmp_path)
        result = await svc.start(_request(), _Executor())
        assert result.stop_reason == "finish_called"


class TestDockerToolExecutor:
    async def test_decodes_demuxed_output(self) -> None:
        container = MagicMock()
        container.id = "abc"
        container.exec_run.return_value = MagicMock(exit_code=0, output=(b"stdout data", b""))
        outcome = await DockerToolExecutor(container).run("nmap_fast", ["nmap", "-sT", "x"], 30.0)
        assert outcome.ok and outcome.stdout == "stdout data"
        # Runs unprivileged as bletiner (R-09).
        assert container.exec_run.call_args.kwargs["user"] == "bletiner"

    async def test_nonzero_exit_surfaces_stderr(self) -> None:
        container = MagicMock()
        container.id = "abc"
        container.exec_run.return_value = MagicMock(exit_code=1, output=(b"", b"boom"))
        outcome = await DockerToolExecutor(container).run("nmap_fast", ["nmap", "x"], 30.0)
        assert not outcome.ok and outcome.stderr == "boom"
