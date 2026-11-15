#!/usr/bin/env python3
"""Bletiner AI - Phase 3 egress gateway live security gate (reconciled; see docs/16).

This REPLACES the Revision 3.1 script, which (docs/16 R-05) defaulted to a simulated mode that printed
hard-coded PASS results, used shell=True, and inverted its negative-test verdict logic.

Guarantees:
  * There is NO simulation mode. With no reachable Docker daemon every test is NOT RUN and the script
    exits 2 (R-05, R-20).
  * Every negative test requires BOTH a client-side failure AND zero leaked packets on the canary (R-22).
  * SNI enforcement is tested with openssl -servername, not a Host header (R-06).
  * Targets are RFC 5737 lab addresses on internal-only networks; nothing public is contacted (R-21).

Usage:
    python scripts/verify_gateway_poc.py            # run against an already-built lab
    python scripts/verify_gateway_poc.py --build    # build images first
    python scripts/verify_gateway_poc.py --keep      # leave the lab up for inspection
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from poc_report import Result, banner, render, summarize, write_artifacts

try:
    from poc_lab import (
        CANARY_IP,
        DEDICATED_IP,
        GATEWAY_IP,
        TARGET_IP,
        Lab,
        LabUnavailable,
    )

    _LAB_IMPORT_ERR: str | None = None
except Exception as exc:
    _LAB_IMPORT_ERR = str(exc)

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence" / "phase3"
MANIFEST = {
    "version": 1,
    "domains": ["local-lab.test", "*.local-lab.test"],
    "web_ports": [80, 443],
    "allow_private_targets": False,
    "ip_targets": [{"cidr": f"{DEDICATED_IP}/32", "tcp_ports": [8443], "udp_ports": []}]
    if "_LAB_IMPORT_ERR" not in dir() or _LAB_IMPORT_ERR is None
    else [],
}


def _not_run(reason: str) -> int:
    tests = _test_catalog()
    results = [
        Result(tid, name, cmd, exp, f"NOT RUN - {reason}", "NOT RUN", "no Docker daemon reachable")
        for tid, name, cmd, exp in tests
    ]
    _emit(results, mode="not-run", reason=reason)
    return 2


def _test_catalog() -> list[tuple[str, str, str, str]]:
    """(test_id, name, command, expected) for reporting even when the lab cannot start."""
    t = TARGET_IP if _LAB_IMPORT_ERR is None else "198.51.100.10"
    d = DEDICATED_IP if _LAB_IMPORT_ERR is None else "198.51.100.20"
    return [
        (
            "TC-LAB-00",
            "Canary leak-detector positive control",
            f"gateway forwards a probe to {CANARY_IP if _LAB_IMPORT_ERR is None else '198.51.100.5'}",
            "canary logs the probe (proves leaks are observable)",
        ),
        (
            "TC-EGRESS-01",
            "Authorized domain via SNI proxy",
            f"kali: curl https://local-lab.test (resolves {t})",
            "200 OK, gateway logs ALLOWED",
        ),
        (
            "TC-EGRESS-02",
            "Unauthorized SNI to shared IP rejected (+ authorized control)",
            f"openssl s_client -servername unauthorized-tenant.test -connect {t}:443",
            "handshake rejected; identical request with authorized SNI succeeds",
        ),
        (
            "TC-EGRESS-02b",
            "ClientHello with no SNI rejected",
            f"openssl s_client -noservername -connect {t}:443",
            "connection rejected (NO_SNI)",
        ),
        (
            "TC-EGRESS-02c",
            "Plaintext HTTP out-of-scope Host rejected",
            "curl -H 'Host: unauthorized-tenant.test' http://local-lab.test/",
            "403 Forbidden",
        ),
        (
            "TC-EGRESS-02d",
            "Authorized SNI to wrong IP rejected",
            f"openssl s_client -servername local-lab.test -connect {CANARY_IP if _LAB_IMPORT_ERR is None else 'x'}:443",
            "rejected (SNI_IP_MISMATCH); no canary leak",
        ),
        (
            "TC-EGRESS-03",
            "Direct TCP bypass dropped",
            "kali: python3 socket.create_connection((203.0.113.9,80))",
            "fails; no canary leak",
        ),
        (
            "TC-EGRESS-04",
            "Direct UDP bypass dropped",
            "kali: nc -u 203.0.113.9 12345",
            "dropped; no canary leak",
        ),
        (
            "TC-EGRESS-05",
            "Direct external resolver intercepted",
            "kali: dig @198.51.100.99 evil.invalid",
            "REFUSED/NXDOMAIN or dropped; no canary leak",
        ),
        (
            "TC-EGRESS-06",
            "Docker control-plane IP dropped",
            "kali: curl http://172.17.0.1:2375/version",
            "fails; no canary leak",
        ),
        (
            "TC-EGRESS-06b",
            "On-link host bridge IP (R-12)",
            "kali: curl http://172.30.0.1/",
            "fails; no canary leak",
        ),
        (
            "TC-EGRESS-07",
            "Cloud metadata IP dropped",
            "kali: curl http://169.254.169.254/latest/",
            "fails; no canary leak",
        ),
        (
            "TC-EGRESS-08",
            "Gateway stopped -> fail-closed",
            "stop gateway; kali: ping/curl",
            "100% loss; restart restores",
        ),
        (
            "TC-EGRESS-09",
            "Out-of-scope redirect blocked at gateway (Layer B)",
            "kali: curl -L http://local-lab.test/redirect-external",
            "second hop blocked; no canary leak",
        ),
        (
            "TC-EGRESS-10",
            "IPv6 egress unreachable",
            "kali: python3 socket AF_INET6 connect",
            "network unreachable",
        ),
        (
            "TC-FWD-01",
            "Authorized dedicated IP:port forwarded",
            f"kali: curl http://{d}:8443/",
            "succeeds (ADR-018 explicit FORWARD)",
        ),
        (
            "TC-FWD-02",
            "Dedicated IP unauthorized port dropped",
            f"kali: nc {d} 9999",
            "dropped; no canary leak",
        ),
        (
            "TC-DNS-ECH-01",
            "HTTPS/type65 stripped (ADR-017)",
            "kali: dig local-lab.test HTTPS",
            "no type65 record in answer",
        ),
        (
            "TC-DNS-02",
            "Embedded resolver bypass closed (R-11)",
            "kali: dig @127.0.0.11 evil.invalid",
            "NXDOMAIN/REFUSED",
        ),
        (
            "TC-ROUTE-01",
            "Kali cannot modify routes (zero NET_ADMIN)",
            "kali(bletiner): ip route add ...",
            "EPERM / Operation not permitted",
        ),
        (
            "TC-ROUTE-02",
            "Default route is the gateway",
            "kali(bletiner): ip route show default",
            f"default via {GATEWAY_IP if _LAB_IMPORT_ERR is None else '172.30.0.254'}",
        ),
        (
            "TC-CAP-01",
            "Zero added caps; unprivileged nmap -sT works (R-09 accepted)",
            "kali: grep Cap /proc/self/status; nmap -sT -Pn",
            "all cap sets zero; TCP connect scan succeeds without root",
        ),
        (
            "TC-SBX-05",
            "Command injection is literal argv",
            "kali: nmap -sV 'x; id'",
            "'id' not executed; treated as hostname",
        ),
    ]


def run_live(lab: Lab) -> list[Result]:  # pragma: no cover - requires docker
    from poc_tests import execute_all

    return execute_all(lab)


def _emit(results: list[Result], *, mode: str, reason: str | None = None) -> None:
    print(banner(mode, reason or f"{len(results)} test cases"))
    for r in results:
        print(render(r))
    total, p, f, n = summarize(results)
    path = write_artifacts(results, EVIDENCE_DIR, mode)
    print(f"\n{'=' * 88}")
    print(f"  TOTAL {total}  PASS {p}  FAIL {f}  NOT RUN {n}")
    print(f"  Evidence: {path}")
    if mode == "not-run":
        print("  GATE 3: NOT RUN - live Docker execution is required (docs/16 R-20). Tier C remains PENDING.")
    elif f == 0 and n == 0:
        print("  GATE 3: PASS - all egress scenarios verified live. Phase 4 may begin.")
    else:
        print("  GATE 3: FAIL - do not begin Phase 4. Diagnose the failures above (DOC-14 section 10).")
    print("=" * 88)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bletiner AI egress gateway live gate")
    parser.add_argument("--build", action="store_true", help="build lab images before provisioning")
    parser.add_argument("--keep", action="store_true", help="leave the lab running for inspection")
    args = parser.parse_args(argv)

    if _LAB_IMPORT_ERR is not None:
        return _not_run(_LAB_IMPORT_ERR)

    try:
        lab = Lab(build=args.build)
    except LabUnavailable as exc:
        return _not_run(str(exc))

    try:
        lab.up(MANIFEST)
        results = run_live(lab)
        lab.save_pcap(EVIDENCE_DIR / "leak.pcap")
    except LabUnavailable as exc:
        return _not_run(str(exc))
    finally:
        if not args.keep:
            lab.down()

    _emit(results, mode="live")
    total, _, fail, not_run = summarize(results)
    return 0 if (fail == 0 and not_run == 0 and total > 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
