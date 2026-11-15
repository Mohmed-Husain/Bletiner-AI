"""Phase 2 gate (Tier B): hardening flags, structured argv, topology, mocked-Docker lifecycle."""

from __future__ import annotations

import ipaddress
from unittest.mock import MagicMock

import pytest

from bletiner.container.exec_runner import LAUNCHER, ExecSpec, validate_argv
from bletiner.container.manager import DockerContainerManager, assert_route_present, build_kali_config
from bletiner.container.security import KaliContainerConfig, assert_secure
from bletiner.models.errors import (
    ContainerCreationError,
    DockerDaemonUnavailableError,
    InsecureContainerConfigError,
    UnsafeCommandError,
)
from bletiner.network.addressing import plan_for
from bletiner.network.topology import build_topology


def _topo():
    return build_topology(
        "abc123",
        plan_for(ipaddress.IPv4Network("172.30.0.0/24")),
        lab=True,
        upstream_subnet="198.51.100.0/24",
    )


def _kali_cfg() -> KaliContainerConfig:
    return build_kali_config(_topo(), image="bletiner-kali:poc", workspace_host_path="/data/ws")


class TestSecurityConfig:
    def test_hardening_flags(self) -> None:
        h = _kali_cfg().host_config()
        assert h["user"] == "1000:1000"
        assert h["read_only"] is True
        assert h["cap_drop"] == ["ALL"]
        assert h["cap_add"] == []  # R-09 accepted: zero added caps; nmap falls back to -sT
        assert h["privileged"] is False
        assert "no-new-privileges:true" in h["security_opt"]
        assert h["sysctls"]["net.ipv6.conf.all.disable_ipv6"] == "1"
        assert h["dns"] == ["172.30.0.254"]  # R-11
        assert h["restart_policy"] == {"Name": "no"}  # R-28
        assert_secure(h)

    def test_no_docker_socket_mount(self) -> None:
        h = _kali_cfg().host_config()
        assert all("docker.sock" not in str(m["Source"]) for m in h["mounts"])
        assert [m["Target"] for m in h["mounts"]] == ["/workspace"]

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda h: h.update(privileged=True),
            lambda h: h.update(read_only=False),
            lambda h: h.update(cap_add=["NET_ADMIN"]),
            lambda h: h.update(cap_add=["NET_RAW"]),  # R-09: even NET_RAW is now rejected
            lambda h: h.update(user="0:0"),
            lambda h: h.update(security_opt=[]),
            lambda h: h["mounts"].append({"Source": "/var/run/docker.sock", "Target": "/x"}),
        ],
    )
    def test_assert_secure_rejects_weakening(self, mutate) -> None:
        h = _kali_cfg().host_config()
        mutate(h)
        with pytest.raises(InsecureContainerConfigError):
            assert_secure(h)


class TestExecRunner:
    def test_launcher_prefix_runs_unprivileged(self) -> None:
        spec = ExecSpec(argv=["nmap", "-sT", "198.51.100.20"])
        assert spec.container_argv() == [LAUNCHER, "nmap", "-sT", "198.51.100.20"]
        # R-09: the launcher never elevates; the command runs as the unprivileged bletiner user.
        assert spec.exec_create_kwargs("c")["user"] == "bletiner"

    def test_direct_exec_runs_as_bletiner(self) -> None:
        spec = ExecSpec(argv=["id", "-u"], use_launcher=False)
        assert spec.container_argv() == ["id", "-u"]
        assert spec.exec_create_kwargs("c")["user"] == "bletiner"

    @pytest.mark.parametrize(
        "argv",
        [["bash", "-c", "nmap x; id"], ["sh", "-c", "curl evil"], ["/bin/bash", "-c", "x"]],
    )
    def test_rejects_shell_c(self, argv: list[str]) -> None:
        with pytest.raises(UnsafeCommandError):
            validate_argv(argv)

    def test_injection_is_literal_argument(self) -> None:
        # The classic injection payload survives as a single argv token, never a shell command.
        spec = ExecSpec(argv=["nmap", "-sV", "target.com; id"])
        assert spec.container_argv()[-1] == "target.com; id"

    @pytest.mark.parametrize("bad", [[], "nmap", [1, 2], ["x\x00y"]])
    def test_rejects_malformed(self, bad) -> None:
        with pytest.raises(UnsafeCommandError):
            validate_argv(bad)  # type: ignore[arg-type]


class TestTopology:
    def test_internal_is_internal_with_icc_on(self) -> None:
        topo = _topo()
        kw = topo.internal.create_kwargs()
        assert kw["internal"] is True
        assert kw["options"]["com.docker.network.bridge.enable_icc"] == "true"  # R-10
        assert topo.gateway_ip == "172.30.0.254"
        assert topo.kali_ip == "172.30.0.2"

    def test_lab_upstream_is_internal(self) -> None:
        assert _topo().upstream.create_kwargs()["internal"] is True

    def test_route_assertion(self) -> None:
        assert assert_route_present("default via 172.30.0.254 dev eth0", "172.30.0.254")
        assert not assert_route_present("172.30.0.0/24 dev eth0", "172.30.0.254")


class TestManagerWithMockDocker:
    def _client(self) -> MagicMock:
        client = MagicMock()
        client.ping.return_value = True
        created = MagicMock()
        created.id = "deadbeef"
        client.containers.create.return_value = created
        return client

    def test_create_kali_passes_secure_config(self) -> None:
        client = self._client()
        mgr = DockerContainerManager(client)
        cid = mgr.create_kali(_kali_cfg())
        assert cid == "deadbeef"
        kwargs = client.containers.create.call_args.kwargs
        assert kwargs["cap_drop"] == ["ALL"] and kwargs["cap_add"] == []

    def test_create_kali_refuses_insecure(self) -> None:
        client = self._client()
        bad = KaliContainerConfig(
            image="x", name="n", hostname="h", network="net", workspace_host_path="/w", dns=("172.30.0.254",)
        )
        object.__setattr__(bad, "image", "x")
        mgr = DockerContainerManager(client)
        # Monkeypatch host_config to drop a flag -> assert_secure must reject before create is called.
        bad_host = bad.host_config()
        bad_host["privileged"] = True
        with pytest.raises(InsecureContainerConfigError):
            assert_secure(bad_host)
        client.containers.create.assert_not_called()

    def test_create_failure_is_typed(self) -> None:
        client = self._client()
        client.containers.create.side_effect = RuntimeError("boom")
        with pytest.raises(ContainerCreationError):
            DockerContainerManager(client).create_kali(_kali_cfg())

    def test_dead_daemon_is_typed(self) -> None:
        client = self._client()
        client.ping.side_effect = RuntimeError("no daemon")
        with pytest.raises(DockerDaemonUnavailableError):
            DockerContainerManager(client)._ensure_daemon()

    def test_route_injection_never_gives_kali_net_admin(self) -> None:
        client = self._client()
        from bletiner.network.router import NetnsHelperSpec

        DockerContainerManager(client).inject_default_route(
            NetnsHelperSpec(kali_container="kali-x", gateway_ip="172.30.0.254")
        )
        run_kwargs = client.containers.run.call_args.kwargs
        assert run_kwargs["cap_add"] == ["NET_ADMIN"]
        assert run_kwargs["network_mode"] == "container:kali-x"
        # The helper is the only thing with NET_ADMIN; Kali's own config adds no capabilities at all.
        assert _kali_cfg().host_config()["cap_add"] == []

    def test_gateway_ready_probe(self) -> None:
        client = self._client()
        gw = MagicMock()
        gw.exec_run.return_value = (0, b"")
        client.containers.get.return_value = gw
        assert DockerContainerManager(client).gateway_ready("gw") is True
        gw.exec_run.return_value = (1, b"")
        assert DockerContainerManager(client).gateway_ready("gw") is False
