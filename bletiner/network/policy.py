"""Gateway policy engine and iptables rule builder (ADR-012, ADR-018; docs/16 R-08, R-13).

Stdlib only - runs inside the gateway image. Rules are produced as argv lists and executed without a shell.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from bletiner.models.netaddr import (
    DENY_NETWORKS,
    IPAddress,
    host_in_scope,
    is_denied_ip,
    normalize_domain_pattern,
)

PROXY_PORT = 8080
DNS_PORT = 5353
WEB_CHAIN = "BLETINER_WEB"
MULTIPORT_MAX = 15


@dataclass(frozen=True)
class IPTarget:
    network: ipaddress.IPv4Network
    tcp_ports: tuple[int, ...]
    udp_ports: tuple[int, ...]


@dataclass(frozen=True)
class GatewayPolicy:
    domains: tuple[str, ...]
    ip_targets: tuple[IPTarget, ...] = ()
    web_ports: tuple[int, ...] = (80, 443)
    allow_private_targets: bool = False
    exact_domains: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "exact_domains", tuple(d for d in self.domains if not d.startswith("*.")))

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> GatewayPolicy:
        if manifest.get("version") != 1:
            raise ValueError(f"unsupported manifest version {manifest.get('version')!r}")
        domains = tuple(sorted({normalize_domain_pattern(str(d)) for d in manifest.get("domains", [])}))
        targets: list[IPTarget] = []
        for t in manifest.get("ip_targets", []):
            net = ipaddress.ip_network(str(t["cidr"]), strict=True)
            if not isinstance(net, ipaddress.IPv4Network):
                raise ValueError("only IPv4 targets are supported")
            targets.append(
                IPTarget(
                    network=net,
                    tcp_ports=tuple(int(p) for p in t.get("tcp_ports", [])),
                    udp_ports=tuple(int(p) for p in t.get("udp_ports", [])),
                )
            )
        web_ports = tuple(int(p) for p in manifest.get("web_ports", [80, 443]))
        if not set(web_ports) <= {80, 443}:
            raise ValueError("web_ports must be a subset of {80, 443}")
        return cls(
            domains=domains,
            ip_targets=tuple(targets),
            web_ports=web_ports,
            allow_private_targets=bool(manifest.get("allow_private_targets", False)),
        )

    # --- decisions ---------------------------------------------------------------------------------------
    def host_allowed(self, host: str) -> bool:
        return host_in_scope(self.domains, host)

    def ip_explicitly_authorized(self, ip: IPAddress) -> bool:
        return isinstance(ip, ipaddress.IPv4Address) and any(ip in t.network for t in self.ip_targets)

    def resolved_ip_acceptable(self, ip: IPAddress) -> bool:
        """DNS-rebinding guard: resolve only to a non-denied IP, or an explicitly authorized target."""
        if ip.version != 4:
            return False
        return not is_denied_ip(ip) or (self.allow_private_targets and self.ip_explicitly_authorized(ip))

    # --- iptables ----------------------------------------------------------------------------------------
    def build_rules(
        self,
        *,
        internal_if: str,
        upstream_if: str,
        internal_subnet: ipaddress.IPv4Network,
    ) -> list[list[str]]:
        """Full rule set, in application order. The FORWARD DROP policy is always the very first rule."""
        sub = str(internal_subnet)
        r: list[list[str]] = [
            ["iptables", "-w", "-P", "FORWARD", "DROP"],
            ["iptables", "-w", "-P", "INPUT", "DROP"],
            ["iptables", "-w", "-P", "OUTPUT", "ACCEPT"],
            # INPUT: the gateway accepts only its own proxy/DNS services from the internal side.
            ["iptables", "-w", "-A", "INPUT", "-i", "lo", "-j", "ACCEPT"],
            [
                "iptables",
                "-w",
                "-A",
                "INPUT",
                "-m",
                "conntrack",
                "--ctstate",
                "ESTABLISHED,RELATED",
                "-j",
                "ACCEPT",
            ],
            [
                "iptables",
                "-w",
                "-A",
                "INPUT",
                "-i",
                internal_if,
                "-s",
                sub,
                "-p",
                "tcp",
                "--dport",
                str(PROXY_PORT),
                "-j",
                "ACCEPT",
            ],
            [
                "iptables",
                "-w",
                "-A",
                "INPUT",
                "-i",
                internal_if,
                "-s",
                sub,
                "-p",
                "udp",
                "--dport",
                str(DNS_PORT),
                "-j",
                "ACCEPT",
            ],
            [
                "iptables",
                "-w",
                "-A",
                "INPUT",
                "-i",
                internal_if,
                "-s",
                sub,
                "-p",
                "tcp",
                "--dport",
                str(DNS_PORT),
                "-j",
                "ACCEPT",
            ],
            ["iptables", "-w", "-A", "INPUT", "-j", "DROP"],
            # FORWARD
            ["iptables", "-w", "-A", "FORWARD", "-m", "conntrack", "--ctstate", "INVALID", "-j", "DROP"],
            [
                "iptables",
                "-w",
                "-A",
                "FORWARD",
                "-i",
                upstream_if,
                "-o",
                internal_if,
                "-m",
                "conntrack",
                "--ctstate",
                "ESTABLISHED,RELATED",
                "-j",
                "ACCEPT",
            ],
            [
                "iptables",
                "-w",
                "-A",
                "FORWARD",
                "-i",
                internal_if,
                "-o",
                upstream_if,
                "-m",
                "conntrack",
                "--ctstate",
                "ESTABLISHED,RELATED",
                "-j",
                "ACCEPT",
            ],
            # QUIC / HTTP3 is never forwarded (DOCK-008)
            ["iptables", "-w", "-A", "FORWARD", "-p", "udp", "--dport", "443", "-j", "DROP"],
        ]
        # Explicit dedicated IP/CIDR targets (ADR-018). Domain-target IPs never appear here.
        for t in self.ip_targets:
            for proto, ports in (("tcp", t.tcp_ports), ("udp", t.udp_ports)):
                for chunk in _chunks(sorted(set(ports)), MULTIPORT_MAX):
                    r.append(
                        [
                            "iptables",
                            "-w",
                            "-A",
                            "FORWARD",
                            "-i",
                            internal_if,
                            "-o",
                            upstream_if,
                            "-s",
                            sub,
                            "-d",
                            str(t.network),
                            "-p",
                            proto,
                            "-m",
                            "multiport",
                            "--dports",
                            ",".join(str(p) for p in chunk),
                            "-m",
                            "conntrack",
                            "--ctstate",
                            "NEW",
                            "-j",
                            "ACCEPT",
                        ]
                    )
        # Everything else is dropped. Deny ranges get their own rules so their counters are evidence.
        for net in DENY_NETWORKS:
            if net.version == 4:
                r.append(["iptables", "-w", "-A", "FORWARD", "-d", str(net), "-j", "DROP"])
        r.append(["iptables", "-w", "-A", "FORWARD", "-j", "DROP"])

        # NAT: DNS interception, dynamic transparent-proxy chain, source NAT for forwarded flows.
        r += [
            ["iptables", "-w", "-t", "nat", "-N", WEB_CHAIN],
            [
                "iptables",
                "-w",
                "-t",
                "nat",
                "-A",
                "PREROUTING",
                "-i",
                internal_if,
                "-p",
                "udp",
                "--dport",
                "53",
                "-j",
                "REDIRECT",
                "--to-ports",
                str(DNS_PORT),
            ],
            [
                "iptables",
                "-w",
                "-t",
                "nat",
                "-A",
                "PREROUTING",
                "-i",
                internal_if,
                "-p",
                "tcp",
                "--dport",
                "53",
                "-j",
                "REDIRECT",
                "--to-ports",
                str(DNS_PORT),
            ],
            [
                "iptables",
                "-w",
                "-t",
                "nat",
                "-A",
                "PREROUTING",
                "-i",
                internal_if,
                "-p",
                "tcp",
                "-j",
                WEB_CHAIN,
            ],
            [
                "iptables",
                "-w",
                "-t",
                "nat",
                "-A",
                "POSTROUTING",
                "-o",
                upstream_if,
                "-s",
                sub,
                "-j",
                "MASQUERADE",
            ],
        ]
        return r

    def web_redirect_rule(self, ip: ipaddress.IPv4Address) -> list[str]:
        """Rule sending in-scope-domain IP web traffic into the SNI/Host proxy (docs/16 R-08)."""
        return [
            "iptables",
            "-w",
            "-t",
            "nat",
            "-A",
            WEB_CHAIN,
            "-d",
            f"{ip}/32",
            "-p",
            "tcp",
            "-m",
            "multiport",
            "--dports",
            ",".join(str(p) for p in self.web_ports),
            "-j",
            "REDIRECT",
            "--to-ports",
            str(PROXY_PORT),
        ]


def ipv6_lockdown_rules() -> list[list[str]]:
    """Defense in depth on top of ``disable_ipv6`` sysctls. Best effort: ip6tables may be unavailable."""
    return [["ip6tables", "-w", "-P", chain, "DROP"] for chain in ("INPUT", "FORWARD", "OUTPUT")]


def _chunks(items: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
