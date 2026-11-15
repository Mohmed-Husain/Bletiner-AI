"""Layer A: client-side HTTP redirect scope hook (ADR-014 Tier A; docs/16 R-14).

Pure logic, host-side unit-tested. Phase 4 tool wrappers call :func:`evaluate_location` with
``allow_redirects=False`` and abort on a raised ``RedirectOutOfScopeError``.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from bletiner.models.errors import RedirectOutOfScopeError
from bletiner.models.netaddr import domain_matches, parse_ip
from bletiner.models.scope import ScopeSpec

REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def is_redirect(status: int) -> bool:
    return status in REDIRECT_STATUS


def _host_of(location: str, *, base_host: str) -> str:
    split = urlsplit(location)
    if not split.scheme and not split.netloc:
        return base_host  # relative redirect stays on the same host
    return (split.hostname or "").lower()


def _ip_authorized(scope: ScopeSpec, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not scope.allow_private_targets:
        return False
    return any(ip in ipaddress.ip_network(t.cidr) for t in scope.ip_targets)


def evaluate_location(scope: ScopeSpec, *, status: int, location: str | None, base_host: str) -> str:
    """Return the in-scope destination host, or raise ``RedirectOutOfScopeError``."""
    if not is_redirect(status):
        return base_host
    if not location:
        raise RedirectOutOfScopeError("redirect response had no Location header")
    host = _host_of(location, base_host=base_host)
    if not host:
        raise RedirectOutOfScopeError(f"unparseable redirect Location: {location!r}")
    ip = parse_ip(host)
    if ip is not None:
        if _ip_authorized(scope, ip):
            return host
        raise RedirectOutOfScopeError(f"REDIRECT_OUT_OF_SCOPE: literal IP {host}")
    if scope.domains and any(domain_matches(p, host) for p in scope.domains):
        return host
    raise RedirectOutOfScopeError(f"REDIRECT_OUT_OF_SCOPE: {host}")
