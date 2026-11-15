"""Docker-backed tool executor (DOC-04 section 4.1) implementing the agent's ``ToolExecutor`` protocol.

Runs a validated ``argv`` inside the Kali container through the Docker Exec API as the unprivileged bletiner
user (R-09: no capabilities, nmap uses -sT). The Docker handle is injected, so this is unit-testable with a
mock; it is only constructed by the service layer after Gate 3 is certified.
"""

from __future__ import annotations

from typing import Any

from bletiner.agent.loop import ToolOutcome
from bletiner.container.exec_runner import ExecSpec


class DockerToolExecutor:
    def __init__(self, container: Any) -> None:  # docker container object
        self._container = container

    async def run(self, tool_name: str, argv: list[str], timeout_s: float) -> ToolOutcome:
        spec = ExecSpec(argv=argv, timeout_s=timeout_s)
        kwargs = spec.exec_create_kwargs(getattr(self._container, "id", "container"))
        result = self._container.exec_run(
            cmd=kwargs["cmd"], user=kwargs["user"], stdout=True, stderr=True, demux=True
        )
        exit_code = int(getattr(result, "exit_code", result[0]))
        output = getattr(result, "output", result[1])
        stdout_b, stderr_b = output if isinstance(output, tuple) else (output, b"")
        return ToolOutcome(
            exit_code=exit_code,
            stdout=(stdout_b or b"").decode("utf-8", "replace"),
            stderr=(stderr_b or b"").decode("utf-8", "replace"),
        )
