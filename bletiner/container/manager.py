"""Docker lifecycle orchestration (DOC-14 Phase 2/3; DOC-04 section 10 reconciliation).

The manager wires together the pure planners (topology, security, router). It depends on the ``docker`` SDK
only through a minimal structural protocol so unit tests can pass a ``MagicMock``. The provisioning *order*
is the fail-closed contract (docs/16 R-24): gateway up and ready, then Kali, then route injection.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from bletiner.container.security import KaliContainerConfig, KaliLimits, assert_secure
from bletiner.models.errors import ContainerCreationError, DockerDaemonUnavailableError, GatewayNotReadyError
from bletiner.network.router import NetnsHelperSpec
from bletiner.network.topology import TopologyPlan


class DockerLike(Protocol):  # pragma: no cover - structural type
    @property
    def containers(self) -> Any: ...
    @property
    def networks(self) -> Any: ...
    def ping(self) -> bool: ...


@dataclass(frozen=True)
class ProvisionResult:
    internal_network_id: str
    upstream_network_id: str
    gateway_id: str
    kali_id: str
    route_injected: bool


class DockerContainerManager:
    def __init__(self, client: DockerLike) -> None:
        self._client = client

    def _ensure_daemon(self) -> None:
        try:
            if not self._client.ping():
                raise DockerDaemonUnavailableError("docker ping returned falsy")
        except Exception as exc:
            raise DockerDaemonUnavailableError(str(exc)) from exc

    def create_kali(self, cfg: KaliContainerConfig) -> str:
        host = cfg.host_config()
        assert_secure(host)  # fail-closed: never create an under-hardened container
        try:
            container = self._client.containers.create(**host)
        except Exception as exc:
            raise ContainerCreationError(f"failed to create {cfg.name}: {exc}") from exc
        return str(container.id)

    def inject_default_route(self, spec: NetnsHelperSpec) -> None:
        """Run the ADR-016 helper. Kali never gets NET_ADMIN; the helper does, transiently."""
        self._client.containers.run(
            image=spec.image,
            command=list(spec.command),
            network_mode=spec.network_mode,
            cap_add=list(spec.cap_add),
            cap_drop=list(spec.cap_drop),
            remove=spec.remove,
            detach=False,
        )

    def gateway_ready(self, gateway_id: str) -> bool:
        container = self._client.containers.get(gateway_id)
        exit_code, _ = container.exec_run(["test", "-f", "/run/bletiner/gateway.ready"])
        return int(exit_code) == 0

    def provision(
        self,
        topo: TopologyPlan,
        kali_cfg: KaliContainerConfig,
        gateway_cfg: dict[str, Any],
    ) -> ProvisionResult:  # pragma: no cover - full live path, exercised by the lab gate
        """Provision in fail-closed order. Raises if the gateway is not ready before Kali gets a route."""
        self._ensure_daemon()
        internal = self._client.networks.create(**topo.internal.create_kwargs())
        upstream = self._client.networks.create(**topo.upstream.create_kwargs())

        gateway = self._client.containers.create(**gateway_cfg)
        gateway.start()
        self._client.networks.get(upstream.id).connect(gateway.id)

        if not self._wait_ready(gateway.id):
            raise GatewayNotReadyError(f"gateway {topo.gateway_name} did not become ready")

        kali_id = self.create_kali(kali_cfg)
        self._client.containers.get(kali_id).start()

        self.inject_default_route(NetnsHelperSpec(kali_container=kali_id, gateway_ip=topo.gateway_ip))
        return ProvisionResult(
            internal_network_id=str(internal.id),
            upstream_network_id=str(upstream.id),
            gateway_id=str(gateway.id),
            kali_id=kali_id,
            route_injected=True,
        )

    def _wait_ready(self, gateway_id: str, attempts: int = 50) -> bool:  # pragma: no cover - live timing
        import time

        for _ in range(attempts):
            if self.gateway_ready(gateway_id):
                return True
            time.sleep(0.1)
        return False

    def teardown(self, result: ProvisionResult) -> None:  # pragma: no cover - live
        for cid in (result.kali_id, result.gateway_id):
            with suppress(Exception):
                self._client.containers.get(cid).remove(force=True)
        for nid in (result.internal_network_id, result.upstream_network_id):
            with suppress(Exception):
                self._client.networks.get(nid).remove()


def build_kali_config(
    topo: TopologyPlan, image: str, workspace_host_path: str, limits: KaliLimits | None = None
) -> KaliContainerConfig:
    return KaliContainerConfig(
        image=image,
        name=topo.kali_name,
        hostname="bletiner-kali",
        network=topo.internal.name,
        workspace_host_path=workspace_host_path,
        dns=(topo.gateway_ip,),  # R-11: route the embedded resolver's queries to the gateway filter
        limits=limits or KaliLimits(),
    )


def assert_route_present(route_output: str, gateway_ip: str) -> bool:
    return f"default via {gateway_ip}" in route_output
