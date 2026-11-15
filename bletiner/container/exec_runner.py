"""Structured-argv command execution (DOCK-011; DOC-14 rule 6; docs/16 R-09).

Agent tool invocations are always ``list[str]`` and run as the unprivileged ``bletiner`` user (UID 1000) with
zero added capabilities. Per the accepted R-09 resolution the sandbox does NOT grant ``CAP_NET_RAW``; raw
SYN scans are unavailable and tools fall back to TCP connect scans (``nmap -sT``). No shell is ever involved.
"""

from __future__ import annotations

from dataclasses import dataclass

from bletiner.models.errors import UnsafeCommandError

LAUNCHER = "/usr/local/bin/bletiner-exec-launcher"
EXEC_USER = "bletiner"
# argv[0] values that imply shell interpretation of a following string.
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "/bin/sh", "/bin/bash"})


def validate_argv(argv: list[str]) -> list[str]:
    """Reject anything that is not a clean argv list or that smuggles a shell ``-c`` string."""
    if not isinstance(argv, list) or not argv:
        raise UnsafeCommandError("command must be a non-empty list[str]")
    if not all(isinstance(a, str) for a in argv):
        raise UnsafeCommandError("every argv element must be a str")
    if any("\x00" in a for a in argv):
        raise UnsafeCommandError("null byte in argv")
    head = argv[0].rsplit("/", 1)[-1]
    if head in {s.rsplit("/", 1)[-1] for s in _SHELLS} and "-c" in argv:
        raise UnsafeCommandError(f"shell -c string execution is forbidden: {argv!r}")
    return argv


@dataclass(frozen=True)
class ExecSpec:
    argv: list[str]
    timeout_s: float = 300.0
    # The launcher is a plain passthrough choke point (it does not elevate; R-09 fallback). It can be
    # bypassed for direct execs, but either way the command runs unprivileged as ``bletiner``.
    use_launcher: bool = True

    def container_argv(self) -> list[str]:
        """Final argv handed to the Docker Exec API."""
        validate_argv(self.argv)
        if self.timeout_s <= 0:
            raise UnsafeCommandError("timeout must be positive")
        if self.use_launcher:
            return [LAUNCHER, *self.argv]
        return list(self.argv)

    def exec_create_kwargs(self, container_id: str) -> dict[str, object]:
        """kwargs for ``docker.APIClient.exec_create`` (no proxy env vars - transparent L3 routing)."""
        return {
            "container": container_id,
            "cmd": self.container_argv(),
            "user": EXEC_USER,  # always unprivileged; the sandbox has no capability to elevate
            "stdout": True,
            "stderr": True,
            "stdin": False,
            "tty": False,
        }
