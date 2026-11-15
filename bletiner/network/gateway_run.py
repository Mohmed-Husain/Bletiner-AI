"""Long-running gateway service entrypoint (``python -m bletiner.network.gateway_run``).

Live-only glue (``no cover``): it binds real sockets and installs nat rules. All decisions delegate to the
unit-tested pure logic. It starts the scoped DNS resolver and the transparent proxy, then writes the ready
sentinel so the container manager may inject Kali's default route.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket

from dnslib import QTYPE, DNSRecord

from bletiner.network.dns_filter import filter_response
from bletiner.network.gateway_main import MANIFEST_PATH, READY_SENTINEL
from bletiner.network.gateway_server import GatewayRuntime, ProxyDecider, ResolvedBindings, TransparentProxy
from bletiner.network.policy import DNS_PORT, PROXY_PORT, GatewayPolicy


def _load_policy() -> tuple[GatewayPolicy, str]:  # pragma: no cover - live
    manifest = json.loads(MANIFEST_PATH.read_text())
    upstream_dns = manifest.get("upstream_dns", "198.51.100.53")
    return GatewayPolicy.from_manifest(manifest), upstream_dns


def _resolve_upstream(request: DNSRecord, upstream_dns: str) -> DNSRecord:  # pragma: no cover - live
    reply = request.send(upstream_dns, 53, tcp=False, timeout=3)
    return DNSRecord.parse(reply)


async def _serve_dns(
    policy: GatewayPolicy, bindings: ResolvedBindings, runtime: GatewayRuntime, upstream_dns: str
) -> None:  # pragma: no cover - live
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", DNS_PORT))  # noqa: S104 - gateway listens on all internal addrs
    sock.setblocking(False)
    while True:
        data, addr = await loop.sock_recvfrom(sock, 4096)
        try:
            request = DNSRecord.parse(data)
        except Exception:
            continue
        qname = str(request.q.qname).rstrip(".").lower()
        upstream = None
        if request.q.qtype not in (65, 64) and policy.host_allowed(qname):
            try:
                upstream = await loop.run_in_executor(None, _resolve_upstream, request, upstream_dns)
            except Exception:
                upstream = None
        reply = filter_response(policy, request, upstream)
        for rr in reply.rr:
            if rr.rtype == QTYPE.A:
                ip = str(rr.rdata)
                bindings.record(qname, ip)
                runtime.install_web_redirect(ip)
        await loop.sock_sendto(sock, reply.pack(), addr)


async def _main(internal_if: str, upstream_if: str) -> int:  # pragma: no cover - live
    policy, upstream_dns = _load_policy()
    bindings = ResolvedBindings()
    runtime = GatewayRuntime(policy=policy, bindings=bindings, internal_if=internal_if)
    decider = ProxyDecider(policy, bindings)
    proxy = TransparentProxy(decider, bindings)

    server = await asyncio.start_server(proxy.handle, "0.0.0.0", PROXY_PORT)  # noqa: S104
    dns_task = asyncio.create_task(_serve_dns(policy, bindings, runtime, upstream_dns))

    READY_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
    READY_SENTINEL.write_text("ready\n")
    async with server:
        await asyncio.gather(server.serve_forever(), dns_task)
    return 0


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - live
    parser = argparse.ArgumentParser()
    parser.add_argument("--internal-if", default="eth0")
    parser.add_argument("--upstream-if", default="eth1")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_main(args.internal_if, args.upstream_if))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
