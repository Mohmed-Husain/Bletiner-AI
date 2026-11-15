"""Live gateway runtime: transparent SNI/Host proxy + scoped DNS resolver (ADR-012/014/017/018).

Runs inside ``bletiner-gateway-<id>``. Pure decision logic lives in :mod:`bletiner.network.proxy`,
:mod:`bletiner.network.sni` and :mod:`bletiner.network.dns_filter` and is unit-tested; the socket plumbing
here is driven only by the live lab gate and is excluded from unit coverage.

Flow:
  * the DNS resolver answers in-scope names (stripping HTTPS/SVCB), records each resolved A record into
    :class:`ResolvedBindings`, and installs the matching ``REDIRECT`` nat rule so that IP's web ports reach
    this proxy (docs/16 R-08);
  * the transparent proxy recovers the original destination via ``SO_ORIGINAL_DST``, inspects the TLS SNI or
    HTTP Host, and relays only when the host is in scope AND bound to that destination IP (R-07, R-27).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
import subprocess
from dataclasses import dataclass

from bletiner.network.dns_filter import STRIPPED_TYPES
from bletiner.network.policy import GatewayPolicy
from bletiner.network.proxy import (
    ProxyDecider,
    ResolvedBindings,
    extract_host_header,
    peek_tls_client_hello,
)
from bletiner.network.sni import ClientHelloError

SO_ORIGINAL_DST = 80
IDLE_TIMEOUT = 30.0


@dataclass
class GatewayRuntime:  # pragma: no cover - live lab only
    policy: GatewayPolicy
    bindings: ResolvedBindings
    internal_if: str

    def install_web_redirect(self, ip: str) -> None:
        """Idempotently add the nat REDIRECT for an in-scope-domain IP (docs/16 R-08)."""
        addr = ipaddress.IPv4Address(ip)
        rule = self.policy.web_redirect_rule(addr)
        check = [*rule[:4], "-C", *rule[5:]]  # replace '-A' with '-C' to test for existence
        if subprocess.run(check, capture_output=True, check=False).returncode == 0:  # noqa: S603
            return
        subprocess.run(rule, capture_output=True, check=False)  # noqa: S603


def _original_dst(sock: socket.socket) -> tuple[str, int]:  # pragma: no cover - live lab only
    data = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    port, raw = struct.unpack("!2xH4s8x", data)
    return socket.inet_ntoa(raw), port


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:  # pragma: no cover
    try:
        while chunk := await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT):
            writer.write(chunk)
            await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        pass
    finally:
        writer.close()


class TransparentProxy:  # pragma: no cover - live lab only
    def __init__(self, decider: ProxyDecider, bindings: ResolvedBindings) -> None:
        self._decider = decider
        self._bindings = bindings

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        try:
            dest_ip, dest_port = _original_dst(sock)
        except OSError:
            writer.close()
            return
        try:
            if dest_port == 443:
                await self._handle_tls(reader, writer, dest_ip, dest_port)
            else:
                await self._handle_http(reader, writer, dest_ip, dest_port)
        except (ClientHelloError, ConnectionError, OSError, TimeoutError):
            writer.close()

    async def _relay(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        dest_ip: str,
        dest_port: int,
        preface: bytes,
    ) -> None:
        up_r, up_w = await asyncio.open_connection(dest_ip, dest_port)
        if preface:
            up_w.write(preface)
            await up_w.drain()
        await asyncio.gather(_pipe(reader, up_w), _pipe(up_r, writer))

    async def _handle_tls(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, dest_ip: str, dest_port: int
    ) -> None:
        info, consumed = await peek_tls_client_hello(reader)
        verdict = self._decider.decide_tls(info, dest_ip)
        if not verdict.allowed:
            writer.close()
            return
        await self._relay(reader, writer, dest_ip, dest_port, consumed)

    async def _handle_http(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, dest_ip: str, dest_port: int
    ) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), IDLE_TIMEOUT)
        host = extract_host_header(head)
        verdict = self._decider.decide_http(host, dest_ip)
        if not verdict.allowed:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        # R-27: one request per plaintext connection, force close downstream.
        await self._relay(reader, writer, dest_ip, dest_port, head)


__all__ = [
    "STRIPPED_TYPES",
    "GatewayRuntime",
    "ProxyDecider",
    "ResolvedBindings",
    "TransparentProxy",
    "extract_host_header",
]
