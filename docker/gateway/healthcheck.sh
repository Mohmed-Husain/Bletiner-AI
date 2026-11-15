#!/bin/sh
set -eu
[ -f /run/bletiner/gateway.ready ] || exit 1
iptables -w -C FORWARD -j DROP 2>/dev/null || exit 1   # fail-closed rule still present
exit 0
