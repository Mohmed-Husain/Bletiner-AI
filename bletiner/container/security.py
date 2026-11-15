"""Hardened Kali container configuration (DOC-04 section 5; DOC-14 Phase 2; docs/16 R-09, R-25).

``kali_host_config`` returns the exact kwargs passed to ``docker.client.containers.create``. Every value is
asserted by :func:`assert_secure` and by the Phase 2 unit tests, so a regression that weakens the sandbox
fails CI rather than shipping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bletiner.models.errors import InsecureContainerConfigError

KALI_UID = 1000
KALI_GID = 1000
DROPPED = "ALL"
# R-09 accepted resolution: ZERO added capabilities. Raw sockets (CAP_NET_RAW) are intentionally withheld;
# tools fall back to TCP connect scans (nmap -sT). Stricter than ADR-010, which still allowed NET_RAW.
ADDED_CAPS: tuple[str, ...] = ()
FORBIDDEN_CAPS = frozenset({"NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_PTRACE", "SETUID", "SETGID", "ALL"})


@dataclass(frozen=True)
class KaliLimits:
    cpus: float = 2.0
    memory: str = "4g"
    pids: int = 256
    workspace_tmpfs: bool = False


@dataclass(frozen=True)
class KaliContainerConfig:
    image: str
    name: str
    hostname: str
    network: str
    workspace_host_path: str
    dns: tuple[str, ...]
    limits: KaliLimits = field(default_factory=KaliLimits)

    def host_config(self) -> dict[str, Any]:
        """kwargs for ``containers.create`` (python docker SDK)."""
        nano_cpus = int(self.limits.cpus * 1_000_000_000)
        return {
            "image": self.image,
            "name": self.name,
            "hostname": self.hostname,
            "network": self.network,
            "user": f"{KALI_UID}:{KALI_GID}",
            "read_only": True,
            "cap_drop": [DROPPED],
            "cap_add": list(ADDED_CAPS),
            "privileged": False,
            "security_opt": ["no-new-privileges:true"],
            "dns": list(self.dns),
            "pids_limit": self.limits.pids,
            "nano_cpus": nano_cpus,
            "mem_limit": self.limits.memory,
            "memswap_limit": self.limits.memory,
            "restart_policy": {"Name": "no"},
            "network_disabled": False,
            "tmpfs": {
                "/tmp": "rw,noexec,nosuid,nodev,size=512m",
                "/run": "rw,noexec,nosuid,nodev,size=64m",
                "/home/bletiner": "rw,noexec,nosuid,nodev,size=256m",
            },
            "mounts": [
                {
                    "Target": "/workspace",
                    "Source": self.workspace_host_path,
                    "Type": "bind",
                    "ReadOnly": False,
                    "BindOptions": {"NonRecursive": True},
                }
            ],
            "sysctls": {"net.ipv6.conf.all.disable_ipv6": "1", "net.ipv6.conf.default.disable_ipv6": "1"},
            "labels": {"com.bletiner.managed": "true", "app": "bletiner"},
            "command": ["tail", "-f", "/dev/null"],
        }


def assert_secure(cfg: dict[str, Any]) -> None:
    """Raise ``InsecureContainerConfigError`` if any hardening invariant is missing (used at create time)."""
    problems: list[str] = []
    if cfg.get("privileged"):
        problems.append("privileged=True is forbidden")
    if cfg.get("user") != f"{KALI_UID}:{KALI_GID}":
        problems.append("container must run as UID:GID 1000:1000")
    if not cfg.get("read_only"):
        problems.append("root filesystem must be read-only")
    if cfg.get("cap_drop") != ["ALL"]:
        problems.append("cap_drop must be exactly ['ALL']")
    added = cfg.get("cap_add") or []
    if added:
        # R-09 accepted resolution: the sandbox adds no capabilities at all.
        problems.append(f"no capabilities may be added (found {sorted(added)})")
    if "no-new-privileges:true" not in cfg.get("security_opt", []):
        problems.append("no-new-privileges:true is required")
    for mount in cfg.get("mounts", []):
        src = str(mount.get("Source", ""))
        if "docker.sock" in src:
            problems.append("docker socket must never be mounted")
    if cfg.get("sysctls", {}).get("net.ipv6.conf.all.disable_ipv6") != "1":
        problems.append("IPv6 must be disabled in the container")
    if problems:
        raise InsecureContainerConfigError("; ".join(problems))
