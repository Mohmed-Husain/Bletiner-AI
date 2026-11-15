"""Scoped DNS stub resolver (ADR-017 ECH mitigation, DOCK-010 rebinding; docs/16 R-11).

Runs in the gateway image on UDP/TCP 5353. Responsibilities:
  * strip DNS HTTPS (type 65) and SVCB (type 64) records from every answer, so clients fall back to
    plaintext-SNI TLS 1.3 (ADR-017);
  * refuse out-of-scope names (NXDOMAIN) so a direct ``dig @8.8.8.8`` cannot resolve them;
  * drop any upstream A/AAAA answer that points at a denied IP unless it is an explicitly authorized
    dedicated target (DNS-rebinding guard).

The decision logic is pure (:func:`filter_response`) and unit-tested; the socket server is a thin shell.
"""

from __future__ import annotations

import socketserver
from collections.abc import Callable

from dnslib import QTYPE, RCODE, DNSRecord

from bletiner.models.netaddr import parse_ip
from bletiner.network.policy import GatewayPolicy

TYPE_HTTPS = 65
TYPE_SVCB = 64
STRIPPED_TYPES = frozenset({TYPE_HTTPS, TYPE_SVCB})
Resolver = Callable[[DNSRecord], DNSRecord]


def _question_name(req: DNSRecord) -> str:
    return str(req.q.qname).rstrip(".").lower()


def filter_response(policy: GatewayPolicy, request: DNSRecord, upstream: DNSRecord | None) -> DNSRecord:
    """Produce the response to return to Kali. Pure; no I/O."""
    qname = _question_name(request)
    qtype = request.q.qtype

    # ECH mitigation: never serve HTTPS/SVCB, regardless of scope.
    if qtype in STRIPPED_TYPES:
        reply = request.reply()
        reply.header.rcode = RCODE.NOERROR
        return reply

    allowed = policy.host_allowed(qname) or _is_authorized_ip_ptr(policy, qname)
    if not allowed:
        reply = request.reply()
        reply.header.rcode = RCODE.NXDOMAIN
        return reply

    if upstream is None:
        reply = request.reply()
        reply.header.rcode = RCODE.SERVFAIL
        return reply

    reply = request.reply()
    reply.header.rcode = upstream.header.rcode
    for rr in upstream.rr:
        if rr.rtype in STRIPPED_TYPES:
            continue  # strip ECH keys even if an in-scope name carries them
        if rr.rtype in (QTYPE.A, QTYPE.AAAA):
            ip = parse_ip(str(rr.rdata))
            if ip is None or not policy.resolved_ip_acceptable(ip):
                continue  # rebinding / out-of-scope address: drop this record
        reply.add_answer(rr)
    if not reply.rr and reply.header.rcode == RCODE.NOERROR:
        reply.header.rcode = RCODE.NXDOMAIN
    return reply


def _is_authorized_ip_ptr(policy: GatewayPolicy, qname: str) -> bool:
    return qname.endswith(".in-addr.arpa") and bool(policy.ip_targets)


class _Handler(socketserver.BaseRequestHandler):
    policy: GatewayPolicy
    resolve_upstream: Resolver

    def handle(self) -> None:  # pragma: no cover - exercised only in the live lab
        data, sock = self.request
        try:
            request = DNSRecord.parse(data)
        except Exception:
            return
        try:
            upstream = self.resolve_upstream(request) if _needs_upstream(self.policy, request) else None
        except Exception:
            upstream = None
        reply = filter_response(self.policy, request, upstream)
        sock.sendto(reply.pack(), self.client_address)


def _needs_upstream(policy: GatewayPolicy, request: DNSRecord) -> bool:
    if request.q.qtype in STRIPPED_TYPES:
        return False
    return policy.host_allowed(_question_name(request)) or _is_authorized_ip_ptr(
        policy, _question_name(request)
    )


def build_udp_server(
    bind: tuple[str, int], policy: GatewayPolicy, resolve_upstream: Resolver
) -> socketserver.UDPServer:  # pragma: no cover - live lab only
    handler = type("Handler", (_Handler,), {"policy": policy, "resolve_upstream": resolve_upstream})
    server = socketserver.ThreadingUDPServer(bind, handler)
    return server
