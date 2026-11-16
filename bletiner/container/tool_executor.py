"""Docker-backed tool executor (DOC-04 section 4.1) implementing the agent's ``ToolExecutor`` protocol.

Runs a validated ``argv`` inside the Kali container through the Docker Exec API as the unprivileged bletiner
user (R-09: no capabilities, nmap uses -sT). The Docker handle is injected, so this is unit-testable with a
mock; it is only constructed by the service layer after Gate 3 is certified.
"""

import asyncio
from typing import Any

from bletiner.agent.loop import ToolOutcome
from bletiner.container.exec_runner import ExecSpec


def _exec_exit_code(result: Any) -> int:
    value = getattr(result, "exit_code", None)
    if value is not None:
        return int(value)
    try:
        return int(result[0])
    except (TypeError, IndexError, ValueError):
        return 1


def _exec_output(result: Any) -> tuple[bytes, bytes]:
    value = getattr(result, "output", None)
    if value is None:
        try:
            value = result[1]
        except (TypeError, IndexError):
            return b"", b""
    if isinstance(value, tuple):
        stdout, stderr = value
        return stdout or b"", stderr or b""
    return value or b"", b""


class DockerToolExecutor:
    """Runs structured-argv tools inside a Kali container without blocking the asyncio event loop.

    The Docker SDK is synchronous, so every exec is dispatched to a worker thread. A wall-clock timeout is
    enforced with asyncio.wait_for: the abandoned worker thread keeps running to completion (Docker has no
    kill-exec API), but the caller is released and reports the timeout honestly.
    """

    def __init__(self, container: Any) -> None:  # docker container object
        self._container = container

    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_sync, tool_name, argv, timeout_s),
                timeout=max(1.0, float(timeout_s)) + 5.0,
            )
        except TimeoutError:
            return ToolOutcome(
                exit_code=124,
                stdout="",
                stderr=f"Tool '{tool_name}' exceeded the {timeout_s:.0f}s timeout and was abandoned.",
                timed_out=True,
            )

    def _run_sync(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        spec = ExecSpec(argv=argv, timeout_s=timeout_s)
        kwargs = spec.exec_create_kwargs(getattr(self._container, "id", "container"))
        result = self._container.exec_run(
            cmd=kwargs["cmd"], user=kwargs["user"], stdout=True, stderr=True, demux=True
        )
        exit_code = _exec_exit_code(result)
        stdout_b, stderr_b = _exec_output(result)

        # If launcher returned 127 (e.g. launcher binary missing in container), fall back to direct argv
        if exit_code == 127 and spec.use_launcher:
            direct_spec = ExecSpec(argv=argv, timeout_s=timeout_s, use_launcher=False)
            direct_kwargs = direct_spec.exec_create_kwargs(getattr(self._container, "id", "container"))
            result = self._container.exec_run(
                cmd=direct_kwargs["cmd"], user=direct_kwargs["user"], stdout=True, stderr=True, demux=True
            )
            exit_code = _exec_exit_code(result)
            stdout_b, stderr_b = _exec_output(result)

        return ToolOutcome(
            exit_code=exit_code,
            stdout=(stdout_b or b"").decode("utf-8", "replace"),
            stderr=(stderr_b or b"").decode("utf-8", "replace"),
        )
