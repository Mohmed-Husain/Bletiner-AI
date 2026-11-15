"""Dual-homed network provisioning plan (DOC-04 section 3; ADR-009; docs/16 R-10).

Pure plan builder. ``bletiner-internal-<id>`` is created ``internal: true`` (fail-closed); ICC stays enabled
because only Kali and its own gateway ever share it (R-10). The gateway is dual-homed to the internal network
and an external network.
"""

from __future__ import annotations

from dataclasses import dataclass

from bletiner.network.addressing import SubnetPlan

PROXY_PORT = 8080
DNS_PORT = 5353


@dataclass(frozen=True)
class NetworkSpec:
    name: str
    internal: bool
    subnet: str | None
    gateway: str | None
    enable_icc: bool
    labels: dict[str, str]

    def create_kwargs(self) -> dict[str, object]:
        ipam_pool: list[dict[str, str]] = []
        if self.subnet:
            pool: dict[str, str] = {"subnet": self.subnet}
            if self.gateway:
                pool["gateway"] = self.gateway
            ipam_pool.append(pool)
        return {
            "name": self.name,
            "driver": "bridge",
            "internal": self.internal,
            "check_duplicate": True,
            "options": {"com.docker.network.bridge.enable_icc": "true" if self.enable_icc else "false"},
            "labels": self.labels,
            "ipam_pool": ipam_pool,
        }


@dataclass(frozen=True)
class TopologyPlan:
    session_id: str
    internal: NetworkSpec
    upstream: NetworkSpec
    gateway_name: str
    kali_name: str
    plan: SubnetPlan

    @property
    def gateway_ip(self) -> str:
        return str(self.plan.gateway_ip)

    @property
    def kali_ip(self) -> str:
        return str(self.plan.kali_ip)


def build_topology(
    session_id: str, subnet_plan: SubnetPlan, *, upstream_subnet: str | None = None, lab: bool = False
) -> TopologyPlan:
    labels = {"com.bletiner.managed": "true", "com.bletiner.session": session_id}
    internal = NetworkSpec(
        name=f"bletiner-internal-{session_id}",
        internal=True,
        subnet=str(subnet_plan.subnet),
        gateway=str(subnet_plan.bridge_ip),
        enable_icc=True,  # R-10: icc=false would also drop Kali -> gateway
        labels=labels,
    )
    # In the lab, the upstream is also internal (no real internet). In production it is a normal bridge.
    upstream = NetworkSpec(
        name=f"bletiner-{'lab-upstream' if lab else 'external'}-{session_id}",
        internal=lab,
        subnet=upstream_subnet,
        gateway=None,
        enable_icc=True,
        labels=labels,
    )
    return TopologyPlan(
        session_id=session_id,
        internal=internal,
        upstream=upstream,
        gateway_name=f"bletiner-gateway-{session_id}",
        kali_name=f"bletiner-kali-{session_id}",
        plan=subnet_plan,
    )
