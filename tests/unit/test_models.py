"""Phase 1: domain model and scope validation (Tier B - implementation correctness)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bletiner.models.entities import Investigation, Verdict
from bletiner.models.netaddr import domain_matches, is_denied_ip, normalize_domain_pattern, parse_ip
from bletiner.models.scope import AuthorizedIPTarget, ScopeSpec


class TestNetaddr:
    @pytest.mark.parametrize(
        ("pattern", "host", "expected"),
        [
            ("local-lab.test", "local-lab.test", True),
            ("local-lab.test", "LOCAL-LAB.TEST.", True),
            ("local-lab.test", "evil-local-lab.test", False),
            ("local-lab.test", "sub.local-lab.test", False),
            ("*.local-lab.test", "sub.local-lab.test", True),
            ("*.local-lab.test", "a.b.local-lab.test", True),
            ("*.local-lab.test", "local-lab.test", False),
            ("*.local-lab.test", "local-lab.test.evil.test", False),
            ("local-lab.test", "198.51.100.10", False),
        ],
    )
    def test_domain_matches(self, pattern: str, host: str, expected: bool) -> None:
        assert domain_matches(pattern, host) is expected

    @pytest.mark.parametrize("bad", ["*", "*.com.*", "a*.test", "foo..test", "-a.test", "localhost", ""])
    def test_rejects_bad_patterns(self, bad: str) -> None:
        with pytest.raises(ValueError, match=r"."):
            normalize_domain_pattern(bad)

    @pytest.mark.parametrize(
        "ip",
        [
            "10.1.2.3",
            "172.17.0.1",
            "172.30.0.1",
            "192.168.1.1",
            "127.0.0.1",
            "169.254.169.254",
            "::1",
            "::ffff:169.254.169.254",
            "fe80::1",
            "100.64.0.1",
        ],
    )
    def test_denied(self, ip: str) -> None:
        addr = parse_ip(ip)
        assert addr is not None and is_denied_ip(addr)

    @pytest.mark.parametrize("ip", ["198.51.100.10", "203.0.113.5", "93.184.216.34"])
    def test_not_denied(self, ip: str) -> None:
        addr = parse_ip(ip)
        assert addr is not None and not is_denied_ip(addr)


class TestScopeSpec:
    def test_manifest_round_trip(self) -> None:
        s = ScopeSpec(
            domains=("Local-Lab.test", "*.local-lab.test"),
            ip_targets=(AuthorizedIPTarget(cidr="198.51.100.20/32", tcp_ports=(8443, 8443)),),
        )
        m = s.to_gateway_manifest()
        assert m["domains"] == ["*.local-lab.test", "local-lab.test"]
        assert m["ip_targets"] == [{"cidr": "198.51.100.20/32", "tcp_ports": [8443], "udp_ports": []}]
        assert s.requires_kernel_forwarding

    def test_domain_only_scope_needs_no_forwarding(self) -> None:
        assert not ScopeSpec(domains=("local-lab.test",)).requires_kernel_forwarding

    def test_empty_scope_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScopeSpec()

    @pytest.mark.parametrize("cidr", ["127.0.0.1/32", "169.254.169.254/32", "0.0.0.0/0", "10.0.0.0/8"])
    def test_never_authorizable_even_in_lab_mode(self, cidr: str) -> None:
        with pytest.raises(ValidationError):
            ScopeSpec(
                ip_targets=(AuthorizedIPTarget(cidr=cidr, tcp_ports=(80,)),), allow_private_targets=True
            )

    def test_private_requires_lab_mode(self) -> None:
        t = AuthorizedIPTarget(cidr="192.168.50.0/24", tcp_ports=(80,))
        with pytest.raises(ValidationError):
            ScopeSpec(ip_targets=(t,))
        assert ScopeSpec(ip_targets=(t,), allow_private_targets=True).ip_targets == (t,)

    def test_ip_target_needs_ports(self) -> None:
        with pytest.raises(ValidationError):
            AuthorizedIPTarget(cidr="198.51.100.20/32")

    def test_ipv6_targets_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuthorizedIPTarget(cidr="2001:db8::/64", tcp_ports=(80,))

    def test_frozen(self) -> None:
        s = ScopeSpec(domains=("local-lab.test",))
        with pytest.raises(ValidationError):
            s.domains = ()  # type: ignore[misc]


def test_investigation_rejects_non_uuid_terms() -> None:
    with pytest.raises(ValidationError):
        Investigation(title="t", primary_target="local-lab.test", terms_acceptance_id="nope")


def test_verdict_values_match_doc10() -> None:
    assert {v.value for v in Verdict} == {"PASS", "FAIL", "NOT RUN", "PENDING LAB EXECUTION"}
