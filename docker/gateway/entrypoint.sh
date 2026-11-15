#!/bin/sh
# Gateway entrypoint. ORDER IS THE FAIL-CLOSED CONTRACT (docs/16 R-24):
#   1. FORWARD policy DROP is applied first (inside apply_policy, rule 0);
#   2. the full rule set is applied;
#   3. only then do the proxy + DNS services start and the ready sentinel is written.
# The container manager does not inject Kali's default route until this sentinel exists, so Kali can never
# egress past an unconfigured gateway.
set -eu

INTERNAL_IF="${BLETINER_INTERNAL_IF:-eth0}"
UPSTREAM_IF="${BLETINER_UPSTREAM_IF:-eth1}"

# ip_forward is set via Docker create-time sysctl (/proc/sys is read-only here); only meaningful when the
# scope has dedicated IP targets. Domain-only scopes never forward at L3.
echo "[gateway] applying fail-closed policy (FORWARD DROP first)"
python3 -m bletiner.network.gateway_main

echo "[gateway] starting scoped DNS resolver (:5353) and transparent proxy (:8080)"
exec python3 -m bletiner.network.gateway_run \
    --internal-if "$INTERNAL_IF" --upstream-if "$UPSTREAM_IF"
