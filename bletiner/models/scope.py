"""Authorized scope specification (ADR-018 domain vs dedicated-IP separation)."""

from __future__ import annotations

import ipaddress
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bletiner.models.netaddr import (
    normalize_domain_pattern,
    overlaps_denied,
    overlaps_never_authorizable,
)

MIN_IPV4_PREFIX = 16  # refuse absurdly broad CIDR authorizations
DEFAULT_WEB_PORTS: tuple[int, ...] = (80, 443)


def _check_ports(ports: tuple[int, ...]) -> tuple[int, ...]:
    for p in ports:
        if not 1 <= p <= 65535:
            raise ValueError(f"invalid port {p}")
    return tuple(sorted(set(ports)))


class AuthorizedIPTarget(BaseModel):
    """A dedicated IP/CIDR the investigator explicitly authorized for raw packet forwarding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cidr: str
    tcp_ports: tuple[int, ...] = Field(default=())
    udp_ports: tuple[int, ...] = Field(default=())

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, v: str) -> str:
        net = ipaddress.ip_network(v.strip(), strict=True)
        if net.version != 4:
            raise ValueError("IPv6 targets are not supported in v1 (IPv6 egress is disabled)")
        if net.prefixlen < MIN_IPV4_PREFIX:
            raise ValueError(f"CIDR {v} is broader than /{MIN_IPV4_PREFIX}")
        if overlaps_never_authorizable(net):
            raise ValueError(f"{v} overlaps loopback/link-local/metadata/reserved space")
        return str(net)

    @field_validator("tcp_ports", "udp_ports")
    @classmethod
    def _ports(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        return _check_ports(v)

    @model_validator(mode="after")
    def _some_port(self) -> AuthorizedIPTarget:
        if not self.tcp_ports and not self.udp_ports:
            raise ValueError("an IP target must authorize at least one TCP or UDP port")
        return self


class ScopeSpec(BaseModel):
    """Immutable authorized scope for one investigation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domains: tuple[str, ...] = ()
    ip_targets: tuple[AuthorizedIPTarget, ...] = ()
    web_ports: tuple[int, ...] = DEFAULT_WEB_PORTS
    # Local-lab mode: permits authorizing private (RFC 1918 / CGNAT) dedicated IPs. Never loopback/metadata.
    allow_private_targets: bool = False

    @field_validator("domains")
    @classmethod
    def _domains(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({normalize_domain_pattern(d) for d in v}))

    @field_validator("web_ports")
    @classmethod
    def _web_ports(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        ports = _check_ports(v)
        if not set(ports) <= {80, 443}:
            raise ValueError("transparent web interception is defined for ports 80 and 443 only")
        return ports

    @model_validator(mode="after")
    def _validate(self) -> ScopeSpec:
        if not self.domains and not self.ip_targets:
            raise ValueError("scope must contain at least one domain or IP target")
        if not self.allow_private_targets:
            for t in self.ip_targets:
                if overlaps_denied(ipaddress.ip_network(t.cidr)):
                    raise ValueError(
                        f"{t.cidr} is a private/reserved range; enable local-lab mode explicitly"
                    )
        return self

    @property
    def requires_kernel_forwarding(self) -> bool:
        """Kernel IP forwarding is only needed for dedicated IP targets (docs/16 R-24)."""
        return bool(self.ip_targets)

    def to_gateway_manifest(self) -> dict[str, Any]:
        """JSON-serialisable manifest consumed by ``bletiner.network.policy.GatewayPolicy``."""
        return {
            "version": 1,
            "domains": list(self.domains),
            "web_ports": list(self.web_ports),
            "allow_private_targets": self.allow_private_targets,
            "ip_targets": [
                {"cidr": t.cidr, "tcp_ports": list(t.tcp_ports), "udp_ports": list(t.udp_ports)}
                for t in self.ip_targets
            ],
        }
