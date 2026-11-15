"""Pure, stdlib-only address and hostname helpers shared by the host app and the gateway image.

This module must not import anything outside the standard library: it is copied verbatim into the
gateway container (docs/16 R-17).
"""

from __future__ import annotations

import ipaddress
import re

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Destinations that are never reachable unless *explicitly* authorized as a dedicated IP/CIDR target
# while local-lab mode is enabled (DOC-04 section 8.2, DOC-14 section 1.1 item 4).
DENY_NETWORKS: tuple[IPNetwork, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",  # "this" network
        "10.0.0.0/8",  # RFC 1918
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local incl. cloud metadata 169.254.169.254
        "172.16.0.0/12",  # RFC 1918 (includes Docker defaults and the 172.30.0.0/16 session pool)
        "192.0.0.0/24",  # IETF protocol assignments
        "192.168.0.0/16",  # RFC 1918
        "198.18.0.0/15",  # benchmarking
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved + broadcast
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)

# Networks that may *never* be authorized, even in local-lab mode: loopback, link-local/metadata,
# multicast and reserved space have no legitimate investigation use.
NEVER_AUTHORIZABLE: tuple[IPNetwork, ...] = tuple(
    ipaddress.ip_network(n)
    for n in ("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4", "::1/128")
)

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
MAX_HOSTNAME_LEN = 253


def parse_ip(value: str) -> IPAddress | None:
    """Return an IP address object or ``None`` if *value* is not a literal IP."""
    try:
        return ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None


def is_denied_ip(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in DENY_NETWORKS)


def overlaps_never_authorizable(net: IPNetwork) -> bool:
    return any(net.version == bad.version and net.overlaps(bad) for bad in NEVER_AUTHORIZABLE)


def overlaps_denied(net: IPNetwork) -> bool:
    return any(net.version == bad.version and net.overlaps(bad) for bad in DENY_NETWORKS)


def normalize_hostname(value: str) -> str:
    """Lower-case, strip a single trailing dot, IDNA-encode. Raises ``ValueError`` when invalid."""
    host = value.strip().lower()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        raise ValueError("empty hostname")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid hostname {value!r}") from exc
    if len(host) > MAX_HOSTNAME_LEN:
        raise ValueError(f"hostname too long: {value!r}")
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise ValueError(f"invalid hostname {value!r}")
    if parse_ip(host) is not None:
        raise ValueError(f"{value!r} is an IP literal, not a hostname")
    return host


def normalize_domain_pattern(value: str) -> str:
    """Normalize an authorized-domain pattern: ``example.com`` or ``*.example.com`` (leading label only)."""
    raw = value.strip().lower()
    if raw.startswith("*."):
        base = normalize_hostname(raw[2:])
        return f"*.{base}"
    if "*" in raw:
        raise ValueError(f"wildcards are only allowed as the full leading label: {value!r}")
    return normalize_hostname(raw)


def domain_matches(pattern: str, host: str) -> bool:
    """Exact match, or ``*.base`` matching strict subdomains of ``base`` (never ``base`` itself)."""
    try:
        h = normalize_hostname(host)
    except ValueError:
        return False
    if pattern.startswith("*."):
        base = pattern[2:]
        return h.endswith("." + base)
    return h == pattern


def host_in_scope(patterns: list[str] | tuple[str, ...], host: str) -> bool:
    return any(domain_matches(p, host) for p in patterns)
