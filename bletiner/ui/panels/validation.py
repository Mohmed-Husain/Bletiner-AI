"""Pure target/scope validation shared by the Targets panel and the New Investigation dialog.

No network access: a target is validated syntactically (domain / IPv4 / IPv6 / URL) so the operator
gets immediate inline feedback, and scope membership is checked locally against the persisted
authorized-scope list.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from urllib.parse import urlsplit

__all__ = [
    "normalize_target",
    "target_in_authorized_scope",
    "validate_scope_entry",
    "validate_target",
]

_LABEL_RE = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")


def normalize_target(raw: str) -> str:
    """Reduce a URL/URL-with-port/bare host to the canonical host or IP string ("" when empty)."""
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" in text:
        try:
            parts = urlsplit(text)
        except ValueError:
            return text.lower()
        text = parts.hostname or ""
    else:
        text = text.split("/", 1)[0]
        if text.startswith("["):  # bracketed IPv6 literal, optionally with a port
            end = text.find("]")
            if end != -1:
                text = text[1:end]
        elif text.count(":") == 1:
            host_part, port_part = text.split(":", 1)
            if port_part.isdigit():
                text = host_part
    return text.strip().strip(".").lower()


def _valid_domain(host: str) -> bool:
    if not host or len(host) > 253 or host.isdigit():
        return False
    labels = host.split(".")
    return all(_LABEL_RE.fullmatch(label) for label in labels)


def validate_target(raw: str) -> tuple[str | None, str | None]:
    """Return ``(cleaned_target, None)`` or ``(None, actionable_error)``."""
    cleaned = normalize_target(raw)
    if not cleaned:
        return None, "Enter a target domain, IP address or URL."
    if any(character.isspace() for character in cleaned):
        return None, "Target must not contain spaces."
    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError:
        address = None
    if address is not None:
        if address.is_unspecified or address.is_multicast or address.is_reserved:
            return None, f"{cleaned} is not a usable unicast target (unspecified/multicast/reserved address)."
        return str(address), None
    if ":" in cleaned:
        return None, f"{cleaned!r} is not a valid IPv6 address or domain."
    if not _valid_domain(cleaned):
        return None, (
            "Target must be a domain (e.g. app.internal.corp), an IP address (10.0.0.5) "
            "or a URL (https://app.internal.corp/path)."
        )
    return cleaned, None


def validate_scope_entry(raw: str) -> tuple[str | None, str | None]:
    """Validate one authorized-scope entry: domain, IP address or CIDR block."""
    text = (raw or "").strip()
    if not text:
        return None, "Enter a domain, IP address or CIDR block."
    if "/" in text:
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            return None, f"{text!r} is not a valid CIDR block (e.g. 10.0.0.0/24)."
        return str(network), None
    cleaned, error = validate_target(text)
    if cleaned is None:
        return None, error
    return cleaned, None


def target_in_authorized_scope(cleaned_target: str, authorized: Sequence[str]) -> bool:
    """True when *cleaned_target* is explicitly covered by one of the *authorized* entries."""
    target = normalize_target(cleaned_target)
    if not target:
        return False
    try:
        target_ip = ipaddress.ip_address(target)
    except ValueError:
        target_ip = None
    for raw in authorized:
        entry = (raw or "").strip()
        if not entry:
            continue
        network: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None
        if "/" in entry:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                network = None
        if network is not None:
            if target_ip is not None and target_ip in network:
                return True
            continue
        host = normalize_target(entry)
        if not host:
            continue
        if target == host or target.endswith("." + host):
            return True
        if host == "localhost" and target in {"127.0.0.1", "::1"}:
            return True
        if target == "localhost" and host in {"127.0.0.1", "::1"}:
            return True
        try:
            host_ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if target_ip is not None and host_ip == target_ip:
            return True
    return False
