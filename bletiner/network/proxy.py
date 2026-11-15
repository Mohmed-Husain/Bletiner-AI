"""Transparent L7 egress proxy: TLS SNI + HTTP Host inspection (ADR-012, ADR-014 Tier B).

Decision logic (:class:`ProxyDecider`) is pure and unit-tested. The asyncio relay underneath is driven live
in the lab and is marked ``no cover`` for unit runs.

Key reconciliations (docs/16):
  * R-07  - a matched SNI is only relayed to a destination IP the scoped resolver returned for that name.
  * R-26  - a ClientHello carrying the ECH/ESNI extension is rejected.
  * R-27  - exactly one plaintext HTTP request is served per connection; ``Connection: close`` is forced.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from bletiner.network.sni import ClientHelloError, ClientHelloInfo, parse_client_hello, reassemble_handshake

MAX_HTTP_HEAD = 16 * 1024
SO_ORIGINAL_DST = 80  # Linux, IPv4


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    host: str | None
    reason: str


class ResolvedBindings:
    """IPs the gateway resolver handed out per in-scope name (populated by the DNS filter at runtime)."""

    def __init__(self) -> None:
        self._by_name: dict[str, set[str]] = {}

    def record(self, name: str, ip: str) -> None:
        self._by_name.setdefault(name.rstrip(".").lower(), set()).add(ip)

    def bound(self, name: str, ip: str) -> bool:
        return ip in self._by_name.get(name.rstrip(".").lower(), set())

    def any_for(self, name: str) -> bool:
        return bool(self._by_name.get(name.rstrip(".").lower()))


@runtime_checkable
class HostPolicy(Protocol):
    """Minimal policy surface the proxy depends on (satisfied by ``GatewayPolicy``)."""

    def host_allowed(self, host: str) -> bool: ...


class ProxyDecider:
    """Pure policy decisions for the proxy. Holds a reference to the live policy and DNS bindings."""

    def __init__(self, policy: HostPolicy, bindings: ResolvedBindings) -> None:
        self._policy = policy
        self._bindings = bindings

    def decide_tls(self, info: ClientHelloInfo, dest_ip: str) -> Verdict:
        if info.has_ech:
            return Verdict(False, None, "ECH_REJECTED")  # R-26
        if info.sni is None:
            return Verdict(False, None, "NO_SNI")  # R-06/TC-EGRESS-02b
        host = info.sni.rstrip(".").lower()
        if not self._policy.host_allowed(host):
            return Verdict(False, host, "SNI_OUT_OF_SCOPE")
        # R-07: the SNI must be bound to this very destination IP by the scoped resolver.
        if not self._bindings.bound(host, dest_ip):
            return Verdict(False, host, "SNI_IP_MISMATCH")
        return Verdict(True, host, "ALLOWED")

    def decide_http(self, host_header: str | None, dest_ip: str) -> Verdict:
        if not host_header:
            return Verdict(False, None, "NO_HOST_HEADER")
        host = host_header.split(":")[0].rstrip(".").lower()
        if not self._policy.host_allowed(host):
            return Verdict(False, host, "HOST_OUT_OF_SCOPE")
        if not self._bindings.bound(host, dest_ip):
            return Verdict(False, host, "HOST_IP_MISMATCH")
        return Verdict(True, host, "ALLOWED")


def extract_host_header(head: bytes) -> str | None:
    """Parse the ``Host`` header from an HTTP/1.x request head (bytes up to CRLFCRLF)."""
    try:
        text = head.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    except ValueError:
        return None
    lines = text.split("\r\n")
    if not lines or " HTTP/" not in lines[0]:
        return None
    hosts = [ln.split(":", 1)[1].strip() for ln in lines[1:] if ln.lower().startswith("host:")]
    # Request smuggling guard: exactly one Host header.
    return hosts[0] if len(hosts) == 1 else None


async def peek_tls_client_hello(
    reader: asyncio.StreamReader,
) -> tuple[ClientHelloInfo, bytes]:  # pragma: no cover
    """Read just enough bytes to parse a ClientHello; return the info and the consumed bytes to replay."""
    buf = b""
    while len(buf) < 64 * 1024:
        chunk = await reader.read(4096)
        if not chunk:
            raise ClientHelloError("connection closed before ClientHello completed")
        buf += chunk
        handshake = reassemble_handshake(buf)
        if handshake is not None:
            return parse_client_hello(handshake), buf
    raise ClientHelloError("ClientHello exceeded size limit")
