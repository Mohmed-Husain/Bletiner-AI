"""Agent-loop controls: non-blocking provider calls + pause/stop at safe boundaries."""

from __future__ import annotations

import asyncio
import time

import pytest

from bletiner.agent.loop import AgentConfig, AgentLoop, LoopBudget, ToolOutcome
from bletiner.providers.base import AgentMessage, ModelProfile, ProviderResponse, Role, Usage
from bletiner.tools.catalog import default_registry


class EndProvider:
    """Returns a plain final message (no tools) so one iteration finishes the run."""

    name = "stub"

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0

    def complete(self, profile, messages, *, tools=None, api_key=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return ProviderResponse(AgentMessage(Role.ASSISTANT, "done"), Usage(1, 1), stop_reason="end_turn")


class NoopExecutor:
    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        return ToolOutcome(exit_code=0, stdout="ok")


def make_loop(provider: EndProvider, **kwargs) -> AgentLoop:
    config = AgentConfig(profile=ModelProfile("stub", "stub-model"), budget=LoopBudget(max_iterations=3))
    return AgentLoop(provider, default_registry(), NoopExecutor(), config, **kwargs)


@pytest.mark.asyncio
async def test_provider_call_does_not_block_event_loop() -> None:
    loop = make_loop(EndProvider(delay=0.25))
    task = asyncio.create_task(loop.run("objective"))
    await asyncio.sleep(0.05)
    assert not task.done()  # the provider call runs in a worker thread; the loop is free
    result = await asyncio.wait_for(task, timeout=5.0)
    assert result.stop_reason == "model_finished"


@pytest.mark.asyncio
async def test_pause_blocks_until_resumed() -> None:
    pause_event = asyncio.Event()
    pause_event.clear()
    provider = EndProvider()
    loop = make_loop(provider, pause_event=pause_event)
    task = asyncio.create_task(loop.run("objective"))
    for _ in range(5):
        await asyncio.sleep(0.02)
    assert provider.calls == 0
    assert not task.done()
    pause_event.set()
    result = await asyncio.wait_for(task, timeout=5.0)
    assert result.stop_reason == "model_finished"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_stop_hook_cancels_before_provider_call() -> None:
    loop = make_loop(EndProvider(), check_stopped=lambda: True)
    with pytest.raises(asyncio.CancelledError):
        await loop.run("objective")


@pytest.mark.asyncio
async def test_stop_hook_cancels_mid_run() -> None:
    state = {"stopped": False}
    provider = EndProvider(delay=0.1)
    loop = make_loop(provider, check_stopped=lambda: state["stopped"])
    task = asyncio.create_task(loop.run("objective"))
    await asyncio.sleep(0.02)
    state["stopped"] = True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5.0)
