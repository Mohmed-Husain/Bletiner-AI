"""Per-session internal subnet allocation (docs/16 R-01, R-02).

Every session gets its own ``/24`` from ``172.30.0.0/16``. Inside it the host bridge is ``.1``, Kali is ``.2``
and the egress gateway is ``.254`` (ADR-016's ``172.30.0.254`` for index 0).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass

from bletiner.models.errors import SubnetPoolExhaustedError

SESSION_POOL = ipaddress.IPv4Network("172.30.0.0/16")
SESSION_PREFIX = 24
KALI_HOST_OCTET = 2
GATEWAY_HOST_OCTET = 254
BRIDGE_HOST_OCTET = 1


@dataclass(frozen=True)
class SubnetPlan:
    subnet: ipaddress.IPv4Network
    bridge_ip: ipaddress.IPv4Address
    kali_ip: ipaddress.IPv4Address
    gateway_ip: ipaddress.IPv4Address


def plan_for(subnet: ipaddress.IPv4Network) -> SubnetPlan:
    base = int(subnet.network_address)
    return SubnetPlan(
        subnet=subnet,
        bridge_ip=ipaddress.IPv4Address(base + BRIDGE_HOST_OCTET),
        kali_ip=ipaddress.IPv4Address(base + KALI_HOST_OCTET),
        gateway_ip=ipaddress.IPv4Address(base + GATEWAY_HOST_OCTET),
    )


def allocate(existing: Iterable[str]) -> SubnetPlan:
    """Return the lowest ``/24`` in the pool that overlaps none of *existing* (Docker network subnets)."""
    taken: list[ipaddress.IPv4Network] = []
    for cidr in existing:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if isinstance(net, ipaddress.IPv4Network):
            taken.append(net)
    for candidate in SESSION_POOL.subnets(new_prefix=SESSION_PREFIX):
        if not any(candidate.overlaps(t) for t in taken):
            return plan_for(candidate)
    raise SubnetPoolExhaustedError(f"no free /{SESSION_PREFIX} left in {SESSION_POOL}")
