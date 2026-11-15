"""Gateway container entrypoint (runs *inside* ``bletiner-gateway-<id>``).

Ordering matters for fail-closed correctness (docs/16 R-24):
  1. set the default FORWARD policy to DROP *first*;
  2. apply the full rule set from :class:`GatewayPolicy`;
  3. only then start the proxy and DNS services and (for dedicated IP targets) rely on the
     create-time ``net.ipv4.ip_forward`` sysctl.

The gateway is started and confirmed ready BEFORE Kali receives its default route, so no window exists where
Kali can egress past an unconfigured gateway.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import sys
from pathlib import Path

from bletiner.network.policy import GatewayPolicy, ipv6_lockdown_rules

MANIFEST_PATH = Path("/etc/bletiner/scope-manifest.json")
READY_SENTINEL = Path("/run/bletiner/gateway.ready")


def _run(argv: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    # argv list, never shell=True (DOC-14 rule 6).
    return subprocess.run(argv, capture_output=True, text=True, check=check)


def apply_policy(
    policy: GatewayPolicy,
    *,
    internal_if: str,
    upstream_if: str,
    internal_subnet: ipaddress.IPv4Network,
    runner: object | None = None,
) -> list[list[str]]:
    """Apply every rule in order. Returns the applied argv list for evidence capture."""
    exec_run = runner or (lambda argv: _run(argv, check=True))
    applied: list[list[str]] = []
    for rule in policy.build_rules(
        internal_if=internal_if, upstream_if=upstream_if, internal_subnet=internal_subnet
    ):
        exec_run(rule)  # type: ignore[operator]
        applied.append(rule)
    for rule in ipv6_lockdown_rules():
        try:
            exec_run(rule)  # type: ignore[operator]
            applied.append(rule)
        except Exception:  # noqa: S110 - ip6tables may be absent; sysctl disable_ipv6 is primary
            pass
    return applied


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - container entrypoint, live lab only
    manifest = json.loads(MANIFEST_PATH.read_text())
    policy = GatewayPolicy.from_manifest(manifest)
    internal_if = manifest["internal_if"]
    upstream_if = manifest["upstream_if"]
    subnet = ipaddress.ip_network(manifest["internal_subnet"])
    if not isinstance(subnet, ipaddress.IPv4Network):
        raise ValueError("internal_subnet must be IPv4")
    apply_policy(policy, internal_if=internal_if, upstream_if=upstream_if, internal_subnet=subnet)
    # proxy + DNS servers would start here (see docker/gateway/entrypoint.sh for the live wiring).
    READY_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
    READY_SENTINEL.write_text("ready\n")
    sys.stdout.write("bletiner-gateway: policy applied, fail-closed FORWARD DROP active\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
