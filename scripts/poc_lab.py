"""Controlled local Docker lab provisioner for the egress gate (docs/16 section 3).

Everything is ``internal: true`` - the lab has no route to the public internet, satisfying the
"never test public targets" rule. The gateway's only upstream default route points at the canary leak
detector, so any forwarded packet to an off-lab destination is observable.

This module is imported by ``verify_gateway_poc.py``. It requires a reachable Docker daemon; if none exists
it raises ``LabUnavailable`` and the gate reports every test ``NOT RUN``.
"""

from __future__ import annotations

import io
import json
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from docker.errors import DockerException

    import docker
except Exception as _exc:  # pragma: no cover - import guard
    docker = None  # type: ignore[assignment]
    _IMPORT_ERR = _exc

REPO_ROOT = Path(__file__).resolve().parent.parent
SID = "poc"
INTERNAL_SUBNET = "172.30.0.0/24"
UPSTREAM_SUBNET = "198.51.100.0/24"
GATEWAY_IP = "172.30.0.254"
KALI_IP = "172.30.0.2"
CANARY_IP = "198.51.100.5"
TARGET_IP = "198.51.100.10"
DEDICATED_IP = "198.51.100.20"
LABDNS_IP = "198.51.100.53"


class LabUnavailable(RuntimeError):
    pass


@dataclass
class ExecResult:
    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class Lab:
    """Provisions and tears down the reconciled PoC topology."""

    def __init__(self, *, build: bool = False) -> None:
        if docker is None:
            raise LabUnavailable(f"docker SDK unavailable: {_IMPORT_ERR}")
        try:
            self.client = docker.from_env()
            self.client.ping()
        except DockerException as exc:
            raise LabUnavailable(f"no reachable Docker daemon: {exc}") from exc
        self._build = build
        self._created: list[object] = []
        self._networks: list[object] = []

    # -- image builds ------------------------------------------------------------------------------------
    def build_images(self) -> dict[str, str]:  # pragma: no cover - requires docker
        out: dict[str, str] = {}
        images = {
            "bletiner-kali:poc": "docker/kali/Dockerfile",
            "bletiner-gateway:poc": "docker/gateway/Dockerfile",
            "bletiner-lab-target:poc": "docker/lab/Dockerfile.target",
            "bletiner-lab-canary:poc": "docker/lab/Dockerfile.canary",
            "bletiner-lab-dns:poc": "docker/lab/Dockerfile.labdns",
        }
        for tag, dockerfile in images.items():
            _, logs = self.client.images.build(
                path=str(REPO_ROOT), dockerfile=dockerfile, tag=tag, rm=True, pull=True
            )
            out[tag] = "built"
            for line in logs:
                if isinstance(line, dict) and "error" in line:
                    raise LabUnavailable(f"build {tag} failed: {line['error']}")
        return out

    # -- provisioning ------------------------------------------------------------------------------------
    def up(self, manifest: dict[str, object]) -> None:  # pragma: no cover - requires docker
        if self._build:
            self.build_images()
        ipam = docker.types.IPAMConfig
        pool = docker.types.IPAMPool
        # docs/16 R-32: the Kali network is a normal bridge, NOT internal:true. On Docker, an internal
        # network drops a container's forwarded (off-subnet-destination) frames at the host bridge before
        # they reach the gateway, so transparent L3 routing through the gateway is impossible under
        # internal:true. Fail-closed is instead enforced by (a) Kali's sole default route = the gateway
        # (injected; Docker's default via .1 is replaced), (b) Kali has zero NET_ADMIN/NET_RAW so it cannot
        # add routes or craft packets to use the host bridge .1 as an escape, and (c) the gateway's default
        # FORWARD DROP. The upstream lab network stays internal:true, so the lab has no public route at all.
        internal = self.client.networks.create(
            f"bletiner-internal-{SID}",
            driver="bridge",
            internal=False,
            ipam=ipam(pool_configs=[pool(subnet=INTERNAL_SUBNET, gateway="172.30.0.1")]),
            options={"com.docker.network.bridge.enable_icc": "true"},
            labels={"com.bletiner.managed": "true"},
        )
        upstream = self.client.networks.create(
            f"bletiner-lab-upstream-{SID}",
            driver="bridge",
            internal=True,
            ipam=ipam(pool_configs=[pool(subnet=UPSTREAM_SUBNET, gateway="198.51.100.1")]),
            labels={"com.bletiner.managed": "true"},
        )
        self._networks += [internal, upstream]

        canary = self._run("bletiner-lab-canary:poc", f"bletiner-canary-{SID}", upstream, CANARY_IP)
        labdns = self._run("bletiner-lab-dns:poc", f"bletiner-labdns-{SID}", upstream, LABDNS_IP)
        target = self._run("bletiner-lab-target:poc", f"bletiner-target-{SID}", upstream, TARGET_IP)
        dedicated = self._run(
            "bletiner-lab-target:poc",
            f"bletiner-dedicated-{SID}",
            upstream,
            DEDICATED_IP,
            environment={"LAB_MODE": "dedicated", "LAB_PORT": "8443"},
        )

        # Gateway: dual-homed. net.ipv4.ip_forward only when IP targets exist (R-24).
        fwd = "1" if manifest.get("ip_targets") else "0"
        gw_manifest = dict(manifest)
        gw_manifest.update(
            {
                "internal_if": "eth0",
                "upstream_if": "eth1",
                "internal_subnet": INTERNAL_SUBNET,
                "upstream_dns": LABDNS_IP,
            }
        )
        gateway = self.client.containers.create(
            "bletiner-gateway:poc",
            name=f"bletiner-gateway-{SID}",
            cap_add=["NET_ADMIN"],
            cap_drop=["ALL"],
            sysctls={"net.ipv4.ip_forward": fwd, "net.ipv6.conf.all.disable_ipv6": "1"},
            network=internal.name,
            restart_policy={"Name": "no"},
            labels={"com.bletiner.managed": "true"},
        )
        self._created.append(gateway)
        self._put_file(gateway, "/etc/bletiner/scope-manifest.json", json.dumps(gw_manifest).encode())
        # Pin the gateway's internal address to GATEWAY_IP (.254) BEFORE start, so the entrypoint's iptables
        # and the ready sentinel come up on the address Kali's injected default route points at. Docker
        # otherwise auto-assigns the first free address (.2), which would leave .254 dark and break egress.
        net_int = self.client.networks.get(internal.id)
        net_int.disconnect(gateway.id)
        net_int.connect(gateway.id, ipv4_address=GATEWAY_IP)
        gateway.start()
        self.client.networks.get(upstream.id).connect(gateway.id, ipv4_address="198.51.100.2")
        # The gateway needs a default route to the canary so leaks are observable.
        self._exec(gateway, ["ip", "route", "replace", "default", "via", CANARY_IP])
        if not self._wait_ready(gateway):
            raise LabUnavailable("gateway did not reach ready state")

        kali = self.client.containers.create(
            "bletiner-kali:poc",
            name=f"bletiner-kali-{SID}",
            user="1000:1000",
            read_only=True,
            cap_drop=["ALL"],  # R-09 accepted: zero added caps; nmap falls back to -sT
            privileged=False,
            security_opt=["no-new-privileges:true"],
            dns=[GATEWAY_IP],
            network=internal.name,
            restart_policy={"Name": "no"},
            sysctls={"net.ipv6.conf.all.disable_ipv6": "1"},
            tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=256m"},
            labels={"com.bletiner.managed": "true"},
            command=["tail", "-f", "/dev/null"],
        )
        self._created.append(kali)
        # Pin Kali to KALI_IP (.2) so its address matches the topology/policy and audit evidence.
        net_int.disconnect(kali.id)
        net_int.connect(kali.id, ipv4_address=KALI_IP)
        kali.start()
        # ADR-016 host-side route injection; Kali never holds NET_ADMIN.
        self.client.containers.run(
            "alpine:3.19",
            command=["ip", "route", "replace", "default", "via", GATEWAY_IP],
            network_mode=f"container:{kali.id}",
            cap_add=["NET_ADMIN"],
            cap_drop=["ALL"],
            remove=True,
        )
        self.kali = kali
        self.gateway = gateway
        self.canary = canary
        self.target = target
        self.dedicated = dedicated
        self.labdns = labdns

    def _run(self, image, name, network, ip, **kw):  # pragma: no cover - requires docker
        c = self.client.containers.run(
            image,
            name=name,
            network=network.name,
            detach=True,
            restart_policy={"Name": "no"},
            labels={"com.bletiner.managed": "true"},
            **kw,
        )
        # Reconnect with a fixed IP (run() attaches before we can pin the address on internal nets).
        self.client.networks.get(network.id).disconnect(c.id)
        self.client.networks.get(network.id).connect(c.id, ipv4_address=ip)
        self._created.append(c)
        return c

    def _put_file(self, container, path: str, data: bytes) -> None:  # pragma: no cover
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=path.lstrip("/"))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        container.put_archive("/", buf.read())

    def _exec(self, container, argv: list[str], user: str | None = None) -> ExecResult:  # pragma: no cover
        res = container.exec_run(argv, user=user or "root", demux=False)
        out = res.output.decode("utf-8", "replace") if res.output else ""
        return ExecResult(exit_code=int(res.exit_code), output=out)

    def _wait_ready(self, gateway, attempts: int = 80) -> bool:  # pragma: no cover
        for _ in range(attempts):
            if self._exec(gateway, ["test", "-f", "/run/bletiner/gateway.ready"]).ok:
                return True
            time.sleep(0.25)
        return False

    # -- test helpers ------------------------------------------------------------------------------------
    def kali_exec(self, argv: list[str], *, launcher: bool = False) -> ExecResult:  # pragma: no cover
        # Always unprivileged (R-09): the launcher is a plain passthrough, never a root/elevation path.
        cmd = ["/usr/local/bin/bletiner-exec-launcher", *argv] if launcher else argv
        return self._exec(self.kali, cmd, user="bletiner")

    def canary_leaks(self) -> str:  # pragma: no cover
        logs = self.canary.logs().decode("utf-8", "replace")
        return "\n".join(ln for ln in logs.splitlines() if "CANARY-LEAK" in ln)

    def gateway_log_tail(self, n: int = 20) -> str:  # pragma: no cover
        return "\n".join(self.gateway.logs().decode("utf-8", "replace").splitlines()[-n:])

    def save_pcap(self, dest: Path) -> None:  # pragma: no cover
        try:
            bits, _ = self.canary.get_archive("/captures/leak.pcap")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                for chunk in bits:
                    fh.write(chunk)
        except Exception:  # noqa: S110
            pass

    def down(self) -> None:  # pragma: no cover
        for c in reversed(self._created):
            try:
                c.remove(force=True)
            except Exception:  # noqa: S110
                pass
        for n in self._networks:
            try:
                n.remove()
            except Exception:  # noqa: S110
                pass
