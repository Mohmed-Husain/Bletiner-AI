"""Unit tests for InvestigationController (Phase 5 UI bridge)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bletiner.agent.events import AgentEvent, AgentState
from bletiner.agent.steering import SteeringVerdict
from bletiner.ui.controller import (
    InvestigationController,
    _clean_target,
    is_docker_available,
)


def test_clean_target() -> None:
    assert _clean_target("https://example.com/some/path") == "example.com"
    assert _clean_target("http://192.168.1.1:8080/") == "192.168.1.1"
    assert _clean_target("  sub.domain.test  ") == "sub.domain.test"


def test_docker_available_is_bool() -> None:
    assert isinstance(is_docker_available(), bool)


class TestInvestigationController:
    def test_initial_state(self, tmp_path: Path) -> None:
        controller = InvestigationController(app_data_dir=tmp_path)
        assert controller.current_state == AgentState.IDLE
        assert not controller.is_running
        assert not controller.is_paused
        assert not controller.is_stopped
        assert controller.current_paths is None
        assert controller.last_result is None

    async def test_start_simulation_mode(self, tmp_path: Path) -> None:
        events: list[AgentEvent] = []
        tool_outputs: list[tuple[str, list[str], str]] = []
        states: list[AgentState] = []
        completed_results: list[object] = []

        controller = InvestigationController(
            app_data_dir=tmp_path,
            on_event=events.append,
            on_tool_output=lambda t, a, o: tool_outputs.append((t, a, o)),
            on_state_change=states.append,
            on_completed=lambda res, paths: completed_results.append((res, paths)),
        )

        result = await controller.start("example.com", mode="deep", step_delay_s=0.0)

        # Assert result and findings
        assert result.stop_reason == "finish_called"
        assert len(result.findings) >= 2
        assert controller.current_state == AgentState.COMPLETED
        assert controller.last_result is result

        # Assert callbacks fired
        assert len(events) > 0
        assert len(tool_outputs) >= 3
        assert AgentState.INITIALIZING in states
        assert AgentState.PLANNING in states
        assert AgentState.COMPLETED in states
        assert len(completed_results) == 1

        # Assert artifact files created and non-empty
        paths = controller.current_paths
        assert paths is not None
        assert (paths.reports / "report.md").is_file()
        assert (paths.reports / "report.html").is_file()
        assert (paths.evidence / "findings.json").is_file()
        assert (paths.logs / "agent_runtime.log").is_file()
        assert (paths.root / "session.json").is_file()

        md_text = (paths.reports / "report.md").read_text(encoding="utf-8")
        assert "# Investigation Report: example.com" in md_text
        assert "Outdated Apache" in md_text

        html_text = (paths.reports / "report.html").read_text(encoding="utf-8")
        assert "<!doctype html>" in html_text
        assert "example.com" in html_text

    async def test_steering_injection(self, tmp_path: Path) -> None:
        controller = InvestigationController(app_data_dir=tmp_path)

        # Before start, steering queue is None
        bad_res = controller.submit_steering("test")
        assert not bad_res.accepted

        # Start an investigation with a valid target
        await controller.start("local-lab.test", mode="basic", step_delay_s=0.0)

        # Active queue exists now
        accepted = controller.submit_steering("Check port 80 headers")
        assert accepted.accepted
        assert accepted.verdict == SteeringVerdict.ACCEPTED

        # Unsafe instruction rejected
        unsafe = controller.submit_steering("sqlmap -u http://local-lab.test --os-shell")
        assert not unsafe.accepted
        assert unsafe.verdict == SteeringVerdict.REJECTED_SAFETY

    def test_pause_and_resume(self, tmp_path: Path) -> None:
        states: list[AgentState] = []
        controller = InvestigationController(app_data_dir=tmp_path, on_state_change=states.append)

        # Pause toggles
        is_p = controller.pause()
        assert is_p
        assert controller.is_paused
        assert controller.current_state == AgentState.PAUSED
        assert AgentState.PAUSED in states

        # Resume
        controller.resume()
        assert not controller.is_paused
        assert controller.current_state != AgentState.PAUSED

        # Pause again then toggle off via pause()
        controller.pause()
        assert controller.is_paused
        controller.pause()
        assert not controller.is_paused

    def test_stop(self, tmp_path: Path) -> None:
        states: list[AgentState] = []
        controller = InvestigationController(app_data_dir=tmp_path, on_state_change=states.append)

        controller.stop()
        assert controller.is_stopped
        assert controller.current_state == AgentState.STOPPED
        assert AgentState.STOPPED in states

    async def test_lifecycle_start_callbacks_steering_and_pausing(self, tmp_path: Path) -> None:
        """Comprehensive verification of starting, event callbacks, steering injection, and pausing."""
        events: list[AgentEvent] = []
        tool_outputs: list[tuple[str, list[str], str]] = []
        states: list[AgentState] = []
        completed_results: list[object] = []

        controller = InvestigationController(
            app_data_dir=tmp_path,
            on_event=events.append,
            on_tool_output=lambda t, a, o: tool_outputs.append((t, a, o)),
            on_state_change=states.append,
            on_completed=lambda res, paths: completed_results.append((res, paths)),
        )

        task = asyncio.create_task(controller.start("scanme.nmap.org", mode="deep", step_delay_s=0.03))

        # Allow the loop to spin up
        await asyncio.sleep(0.01)
        assert controller.is_running

        # Verify mid-turn steering injection while running
        steering = controller.submit_steering("Inspect SSL certificates on port 443")
        assert steering.accepted
        assert steering.verdict == SteeringVerdict.ACCEPTED

        # Verify pause mechanism suspends loop
        assert controller.pause() is True
        assert controller.is_paused
        assert controller.current_state == AgentState.PAUSED
        assert AgentState.PAUSED in states

        # Verify resume restores execution
        await asyncio.sleep(0.02)
        controller.resume()
        assert not controller.is_paused

        # Wait for investigation run to finish
        result = await task

        # Assert final result and events
        assert result.stop_reason == "finish_called"
        assert len(result.findings) >= 2
        assert len(events) > 0
        assert len(tool_outputs) > 0
        assert AgentState.COMPLETED in states
        assert len(completed_results) == 1

        paths = controller.current_paths
        assert paths is not None
        assert (paths.reports / "report.md").is_file()
        assert (paths.reports / "report.html").is_file()
        assert (paths.evidence / "findings.json").is_file()

    async def test_execute_terminal_command(self, tmp_path: Path) -> None:
        controller = InvestigationController(app_data_dir=tmp_path)

        # Empty command returns immediately
        code, out, err = await controller.execute_terminal_command("")
        assert code == 0
        assert out == ""
        assert err == ""

        # Test command execution (live docker or offline notice)
        code, out, err = await controller.execute_terminal_command("echo 'test-inline-terminal'")
        if is_docker_available():
            assert code == 0
            assert "test-inline-terminal" in out
        else:
            assert code != 0
            assert "Docker" in err
