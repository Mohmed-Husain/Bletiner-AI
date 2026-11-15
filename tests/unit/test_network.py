"""Phase 2/3 Tier B: gateway policy, SNI parsing, DNS filtering, proxy decisions, routing, redirects."""

from __future__ import annotations

import ipaddress
import struct

import pytest

from bletiner.models.errors import RedirectOutOfScopeError
from bletiner.models.scope import AuthorizedIPTarget, ScopeSpec
from bletiner.network import addressing
from bletiner.network.dns_filter import filter_response
from bletiner.network.policy import PROXY_PORT, GatewayPolicy
from bletiner.network.proxy import ProxyDecider, ResolvedBindings, extract_host_header
from bletiner.network.redirects import evaluate_location
from bletiner.network.router import NetnsHelperSpec, forbidden_route_argv
from bletiner.network.sni import ClientHelloError, parse_client_hello, reassemble_handshake

LAB = GatewayPolicy.from_manifest(
    ScopeSpec(
        domains=("local-lab.test", "*.local-lab.test"),
        ip_targets=(AuthorizedIPTarget(cidr="198.51.100.20/32", tcp_ports=(8443, 9999), udp_ports=(53,)),),
        allow_private_targets=False,
    ).to_gateway_manifest()
)


# --- addressing --------------------------------------------------------------------------------------------
class TestAddressing:
    def test_index0_matches_adr016(self) -> None:
        plan = addressing.plan_for(ipaddress.IPv4Network("172.30.0.0/24"))
        assert str(plan.gateway_ip) == "172.30.0.254"
        assert str(plan.kali_ip) == "172.30.0.2"

    def test_allocate_skips_taken(self) -> None:
        plan = addressing.allocate(["172.30.0.0/24", "172.30.1.0/24"])
        assert str(plan.subnet) == "172.30.2.0/24"

    def test_allocate_ignores_unrelated_and_bad(self) -> None:
        plan = addressing.allocate(["10.0.0.0/8", "not-a-cidr"])
        assert str(plan.subnet) == "172.30.0.0/24"


# --- iptables rule builder ---------------------------------------------------------------------------------
class TestPolicyRules:
    def _rules(self) -> list[list[str]]:
        return LAB.build_rules(
            internal_if="eth0", upstream_if="eth1", internal_subnet=ipaddress.IPv4Network("172.30.0.0/24")
        )

    def test_forward_drop_is_first(self) -> None:
        assert self._rules()[0] == ["iptables", "-w", "-P", "FORWARD", "DROP"]

    def test_quic_udp443_dropped(self) -> None:
        joined = [" ".join(r) for r in self._rules()]
        assert any("-p udp --dport 443 -j DROP" in r for r in joined)

    def test_dedicated_ip_target_forward_accept(self) -> None:
        joined = [" ".join(r) for r in self._rules()]
        assert any("198.51.100.20/32 -p tcp -m multiport --dports 8443,9999" in r for r in joined)
        assert any("198.51.100.20/32 -p udp -m multiport --dports 53" in r for r in joined)

    def test_no_forward_rule_for_domains(self) -> None:
        # Domain scope must never produce a raw FORWARD ACCEPT (ADR-018 / R-08).
        domain_only = GatewayPolicy.from_manifest(
            ScopeSpec(domains=("local-lab.test",)).to_gateway_manifest()
        )
        rules = domain_only.build_rules(
            internal_if="eth0", upstream_if="eth1", internal_subnet=ipaddress.IPv4Network("172.30.0.0/24")
        )
        assert not any("ACCEPT" in r and "FORWARD" in r and "-d" in r for r in rules)

    def test_masquerade_present(self) -> None:
        joined = [" ".join(r) for r in self._rules()]
        assert any("POSTROUTING -o eth1" in r and "MASQUERADE" in r for r in joined)

    def test_metadata_and_rfc1918_dropped(self) -> None:
        joined = [" ".join(r) for r in self._rules()]
        assert any("169.254.0.0/16 -j DROP" in r for r in joined)
        assert any("10.0.0.0/8 -j DROP" in r for r in joined)

    def test_web_redirect_rule_targets_proxy(self) -> None:
        rule = " ".join(LAB.web_redirect_rule(ipaddress.IPv4Address("198.51.100.10")))
        assert f"--to-ports {PROXY_PORT}" in rule and "198.51.100.10/32" in rule


# --- SNI / ClientHello parser ------------------------------------------------------------------------------
def _client_hello(server_name: str | None, *, ech: bool = False, dup_sni: bool = False) -> bytes:
    exts = b""
    if server_name is not None:
        host = server_name.encode()
        sni_list = struct.pack("!B", 0) + struct.pack("!H", len(host)) + host
        sni_ext = struct.pack("!H", len(sni_list)) + sni_list
        exts += struct.pack("!HH", 0x0000, len(sni_ext)) + sni_ext
        if dup_sni:
            exts += struct.pack("!HH", 0x0000, len(sni_ext)) + sni_ext
    if ech:
        exts += struct.pack("!HH", 0xFE0D, 1) + b"\x00"
    body = b"\x03\x03" + b"\x00" * 32 + b"\x00" + struct.pack("!H", 2) + b"\x13\x01" + b"\x01\x00"
    body += struct.pack("!H", len(exts)) + exts
    hs = struct.pack("!B", 1) + struct.pack("!I", len(body))[1:] + body
    return hs


def _record(payload: bytes, *, split: int | None = None) -> bytes:
    if split is None:
        return struct.pack("!BHH", 0x16, 0x0301, len(payload)) + payload
    a, b = payload[:split], payload[split:]
    return struct.pack("!BHH", 0x16, 0x0301, len(a)) + a + struct.pack("!BHH", 0x16, 0x0301, len(b)) + b


class TestSNIParser:
    def test_extracts_sni(self) -> None:
        info = parse_client_hello(reassemble_handshake(_record(_client_hello("local-lab.test"))))  # type: ignore[arg-type]
        assert info.sni == "local-lab.test" and not info.has_ech

    def test_detects_ech(self) -> None:
        info = parse_client_hello(reassemble_handshake(_record(_client_hello("cover.test", ech=True))))  # type: ignore[arg-type]
        assert info.has_ech

    def test_no_sni(self) -> None:
        info = parse_client_hello(reassemble_handshake(_record(_client_hello(None))))  # type: ignore[arg-type]
        assert info.sni is None

    def test_reassembles_split_records(self) -> None:
        payload = _client_hello("sub.local-lab.test")
        assert reassemble_handshake(_record(payload, split=20)) is not None

    def test_partial_returns_none(self) -> None:
        full = _record(_client_hello("local-lab.test"))
        assert reassemble_handshake(full[:8]) is None

    def test_duplicate_sni_rejected(self) -> None:
        with pytest.raises(ClientHelloError):
            parse_client_hello(reassemble_handshake(_record(_client_hello("x.test", dup_sni=True))))  # type: ignore[arg-type]

    def test_non_tls_rejected(self) -> None:
        with pytest.raises(ClientHelloError):
            reassemble_handshake(b"GET / HTTP/1.1\r\n\r\n")


# --- DNS filter ------------------------------------------------------------------------------------------
class TestDNSFilter:
    def _q(self, name: str, qtype: str = "A"):
        from dnslib import DNSRecord

        return DNSRecord.question(name, qtype)

    def test_out_of_scope_nxdomain(self) -> None:
        from dnslib import RCODE

        reply = filter_response(LAB, self._q("evil.invalid"), None)
        assert reply.header.rcode == RCODE.NXDOMAIN

    def test_https_type65_stripped_before_upstream(self) -> None:
        from dnslib import RCODE

        reply = filter_response(LAB, self._q("local-lab.test", "HTTPS"), None)
        assert reply.header.rcode == RCODE.NOERROR and not reply.rr

    def test_in_scope_a_record_passes(self) -> None:
        from dnslib import RR, A

        req = self._q("local-lab.test")
        upstream = req.reply()
        upstream.add_answer(RR("local-lab.test", rdata=A("198.51.100.10"), ttl=60))
        upstream.add_answer(*RR.fromZone("local-lab.test 60 IN HTTPS 1 . alpn=h2"))
        out = filter_response(LAB, req, upstream)
        rdatas = [str(r.rdata) for r in out.rr]
        assert "198.51.100.10" in rdatas
        assert all("alpn" not in r for r in rdatas)  # HTTPS record stripped

    def test_rebinding_private_answer_dropped(self) -> None:
        from dnslib import RCODE, RR, A

        req = self._q("local-lab.test")
        upstream = req.reply()
        upstream.add_answer(RR("local-lab.test", rdata=A("192.168.1.1"), ttl=60))
        out = filter_response(LAB, req, upstream)
        assert not out.rr and out.header.rcode == RCODE.NXDOMAIN


# --- proxy decisions -------------------------------------------------------------------------------------
class TestProxyDecider:
    def _decider(self) -> ProxyDecider:
        b = ResolvedBindings()
        b.record("local-lab.test", "198.51.100.10")
        return ProxyDecider(LAB, b)

    def test_allows_matched_sni_to_bound_ip(self) -> None:
        from bletiner.network.sni import ClientHelloInfo

        v = self._decider().decide_tls(ClientHelloInfo("local-lab.test", False, 100), "198.51.100.10")
        assert v.allowed and v.reason == "ALLOWED"

    def test_rejects_unauthorized_sni(self) -> None:
        from bletiner.network.sni import ClientHelloInfo

        v = self._decider().decide_tls(
            ClientHelloInfo("unauthorized-tenant.test", False, 100), "198.51.100.10"
        )
        assert not v.allowed and v.reason == "SNI_OUT_OF_SCOPE"

    def test_rejects_matched_sni_to_unbound_ip(self) -> None:
        from bletiner.network.sni import ClientHelloInfo

        v = self._decider().decide_tls(ClientHelloInfo("local-lab.test", False, 100), "203.0.113.9")
        assert not v.allowed and v.reason == "SNI_IP_MISMATCH"  # R-07

    def test_rejects_ech(self) -> None:
        from bletiner.network.sni import ClientHelloInfo

        v = self._decider().decide_tls(ClientHelloInfo(None, True, 100), "198.51.100.10")
        assert not v.allowed and v.reason == "ECH_REJECTED"

    def test_rejects_missing_sni(self) -> None:
        from bletiner.network.sni import ClientHelloInfo

        v = self._decider().decide_tls(ClientHelloInfo(None, False, 100), "198.51.100.10")
        assert not v.allowed and v.reason == "NO_SNI"

    def test_http_host_out_of_scope(self) -> None:
        v = self._decider().decide_http("unauthorized-tenant.test", "198.51.100.10")
        assert not v.allowed and v.reason == "HOST_OUT_OF_SCOPE"

    def test_http_host_allowed(self) -> None:
        v = self._decider().decide_http("local-lab.test", "198.51.100.10")
        assert v.allowed

    def test_extract_single_host_header(self) -> None:
        assert extract_host_header(b"GET / HTTP/1.1\r\nHost: local-lab.test\r\n\r\n") == "local-lab.test"

    def test_rejects_double_host_header(self) -> None:
        assert extract_host_header(b"GET / HTTP/1.1\r\nHost: a.test\r\nHost: b.test\r\n\r\n") is None


# --- router (ADR-016) ------------------------------------------------------------------------------------
class TestRouter:
    def test_helper_spec(self) -> None:
        spec = NetnsHelperSpec(kali_container="bletiner-kali-abc", gateway_ip="172.30.0.254")
        assert spec.network_mode == "container:bletiner-kali-abc"
        assert spec.command == ("ip", "route", "replace", "default", "via", "172.30.0.254")
        assert "--cap-add=NET_ADMIN" in spec.docker_run_argv()

    def test_bad_gateway_ip_rejected(self) -> None:
        with pytest.raises(ValueError, match="."):
            NetnsHelperSpec(kali_container="k", gateway_ip="not-an-ip")

    def test_forbidden_route_argv_is_mutation(self) -> None:
        assert forbidden_route_argv()[:3] == ["ip", "route", "add"]


# --- redirects (ADR-014 Layer A) -------------------------------------------------------------------------
class TestRedirects:
    SCOPE = ScopeSpec(domains=("local-lab.test", "*.local-lab.test"))

    def test_in_scope_redirect_ok(self) -> None:
        assert (
            evaluate_location(
                self.SCOPE, status=302, location="https://sub.local-lab.test/x", base_host="local-lab.test"
            )
            == "sub.local-lab.test"
        )

    def test_out_of_scope_blocked(self) -> None:
        with pytest.raises(RedirectOutOfScopeError):
            evaluate_location(
                self.SCOPE,
                status=302,
                location="https://unauthorized-tenant.test/",
                base_host="local-lab.test",
            )

    def test_metadata_ip_redirect_blocked(self) -> None:
        with pytest.raises(RedirectOutOfScopeError):
            evaluate_location(
                self.SCOPE, status=302, location="http://169.254.169.254/latest/", base_host="local-lab.test"
            )

    def test_relative_redirect_keeps_host(self) -> None:
        assert (
            evaluate_location(self.SCOPE, status=301, location="/login", base_host="local-lab.test")
            == "local-lab.test"
        )

    def test_non_redirect_passthrough(self) -> None:
        assert evaluate_location(self.SCOPE, status=200, location=None, base_host="local-lab.test") == (
            "local-lab.test"
        )
