"""ADR-016 host-side netns default-route injection (docs/16 R-28).

Kali never holds ``CAP_NET_ADMIN``. A transient helper container joins Kali's network namespace, replaces the
default route via the gateway, and exits. This module only *builds* the helper spec and validates it; the
container manager runs it. Keeping it pure makes the exact ``docker run`` reproducible and testable.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

HELPER_IMAGE = "alpine:3.19"


@dataclass(frozen=True)
class NetnsHelperSpec:
    """Everything needed to launch the ADR-016 helper (consumed by the Docker SDK, never a shell)."""

    kali_container: str
    gateway_ip: str
    image: str = HELPER_IMAGE
    command: tuple[str, ...] = field(init=False)
    network_mode: str = field(init=False)
    cap_add: tuple[str, ...] = ("NET_ADMIN",)
    cap_drop: tuple[str, ...] = ("ALL",)
    remove: bool = True

    def __post_init__(self) -> None:
        gw = ipaddress.IPv4Address(self.gateway_ip)  # raises on malformed input
        if not self.kali_container:
            raise ValueError("kali_container is required")
        object.__setattr__(self, "command", ("ip", "route", "replace", "default", "via", str(gw)))
        object.__setattr__(self, "network_mode", f"container:{self.kali_container}")

    def docker_run_argv(self) -> list[str]:
        """Equivalent ``docker run`` argv - for evidence logs and manual reproduction only."""
        return [
            "docker",
            "run",
            "--rm",
            f"--network={self.network_mode}",
            f"--cap-drop={self.cap_drop[0]}",
            *[f"--cap-add={c}" for c in self.cap_add],
            self.image,
            *self.command,
        ]


def verify_route_argv(kali_container: str) -> list[str]:
    """Command run *inside Kali as UID 1000* to confirm the injected default route (TC-ROUTE-02)."""
    return ["ip", "route", "show", "default"]


def forbidden_route_argv() -> list[str]:
    """A route mutation Kali must NOT be able to perform (TC-ROUTE-01); expected to fail with EPERM."""
    return ["ip", "route", "add", "198.51.100.0/24", "via", "172.30.0.254"]
