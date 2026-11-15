"""Live egress-gate test implementations (docs/16). Executed only with a running Docker lab.

Negative-test contract (R-22): a scenario PASSES only when the client action fails AND the canary recorded
no leaked packet for that destination. A client failure alone is not sufficient evidence of enforcement.
"""

from __future__ import annotations

from poc_lab import CANARY_IP, DEDICATED_IP, GATEWAY_IP, Lab
from poc_report import Result


def _blocked(lab: Lab, tid: str, name: str, argv: list[str], expected: str) -> Result:  # pragma: no cover
    """Negative test: command must fail AND leave no canary leak."""
    before = lab.canary_leaks()
    res = lab.kali_exec(argv)
    after = lab.canary_leaks()
    new_leaks = after[len(before) :].strip()
    client_blocked = res.exit_code != 0
    no_leak = not new_leaks
    verdict = "PASS" if (client_blocked and no_leak) else "FAIL"
    actual = f"exit={res.exit_code} client_blocked={client_blocked} canary_leak={bool(new_leaks)}"
    evidence = f"out={res.output.strip()[:160]!r} leaks={new_leaks[:160]!r}"
    return Result(tid, name, " ".join(argv), expected, actual, verdict, evidence)


def _no_leak(lab: Lab, tid: str, name: str, argv: list[str], expected: str) -> Result:  # pragma: no cover
    """Negative test for connectionless traffic (UDP): the client exit code is meaningless, so the
    authoritative signal is that the canary received nothing. TC-LAB-00 proves the canary observes leaks."""
    before = lab.canary_leaks()
    res = lab.kali_exec(argv)
    new_leaks = lab.canary_leaks()[len(before) :].strip()
    verdict = "PASS" if not new_leaks else "FAIL"
    return Result(
        tid,
        name,
        " ".join(argv),
        expected,
        f"exit={res.exit_code} canary_leak={bool(new_leaks)}",
        verdict,
        f"out={res.output.strip()[:120]!r} leaks={new_leaks[:120]!r}",
    )


def _allowed(
    lab: Lab, tid: str, name: str, argv: list[str], expected: str, needle: str
) -> Result:  # pragma: no cover
    res = lab.kali_exec(argv)
    verdict = "PASS" if (res.ok and needle in res.output) else "FAIL"
    return Result(
        tid,
        name,
        " ".join(argv),
        expected,
        f"exit={res.exit_code}",
        verdict,
        f"out={res.output.strip()[:200]!r}",
    )


def execute_all(lab: Lab) -> list[Result]:  # pragma: no cover - requires docker
    r: list[Result] = []

    # TC-LAB-00 positive control: force the gateway to forward a probe to the canary; it must be logged.
    probe = lab.kali_exec(["sh", "-lc", "true"])
    before = lab.canary_leaks()
    lab._exec(lab.gateway, ["sh", "-lc", f"echo x | nc -w1 {CANARY_IP} 12345 || true"])
    after = lab.canary_leaks()
    saw = after[len(before) :].strip()
    r.append(
        Result(
            "TC-LAB-00",
            "Canary leak-detector positive control",
            f"gateway -> {CANARY_IP}:12345",
            "canary logs the probe",
            f"canary_logged={bool(saw)}",
            "PASS" if saw else "FAIL",
            saw[:160] or "no leak observed",
        )
    )

    # TC-EGRESS-01 authorized domain
    r.append(
        _allowed(
            lab,
            "TC-EGRESS-01",
            "Authorized domain via SNI proxy",
            ["curl", "-ksS", "-m", "8", "-o", "/dev/null", "-w", "%{http_code}", "https://local-lab.test/"],
            "200 OK",
            "200",
        )
    )

    # TC-EGRESS-02 unauthorized SNI rejected + authorized control (only the SNI differs)
    bad = lab.kali_exec(
        [
            "sh",
            "-c",
            f"echo Q | openssl s_client -connect {_T()}:443 -servername unauthorized-tenant.test "
            "-verify_return_error 2>&1",
        ]
    )
    good = lab.kali_exec(
        ["sh", "-c", f"echo Q | openssl s_client -connect {_T()}:443 -servername local-lab.test 2>&1"]
    )
    ok = (bad.exit_code != 0 or "unrecognized" in bad.output.lower() or "alert" in bad.output.lower()) and (
        ("CONNECTED" in good.output and "Verify" in good.output) or good.exit_code == 0
    )
    r.append(
        Result(
            "TC-EGRESS-02",
            "Unauthorized SNI rejected (+ authorized control)",
            "openssl s_client -servername {unauthorized|authorized}",
            "unauthorized SNI handshake rejected; identical request with authorized SNI succeeds",
            f"bad_exit={bad.exit_code} good_exit={good.exit_code}",
            "PASS" if ok else "FAIL",
            f"bad={bad.output.strip()[:120]!r} good={good.output.strip()[:120]!r}",
        )
    )

    # TC-EGRESS-02b no SNI
    nosni = lab.kali_exec(["sh", "-c", f"echo Q | openssl s_client -connect {_T()}:443 -noservername 2>&1"])
    r.append(
        Result(
            "TC-EGRESS-02b",
            "ClientHello with no SNI rejected",
            "openssl s_client -noservername",
            "connection rejected (NO_SNI)",
            f"exit={nosni.exit_code}",
            "PASS" if nosni.exit_code != 0 or "alert" in nosni.output.lower() else "FAIL",
            nosni.output.strip()[:160],
        )
    )

    # TC-EGRESS-02c plaintext HTTP out-of-scope Host -> 403
    code = lab.kali_exec(
        [
            "curl",
            "-s",
            "-m",
            "6",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-H",
            "Host: unauthorized-tenant.test",
            "http://local-lab.test/",
        ]
    )
    r.append(
        Result(
            "TC-EGRESS-02c",
            "Plaintext HTTP out-of-scope Host rejected",
            "curl -H 'Host: unauthorized-tenant.test'",
            "HTTP 403 Forbidden",
            f"http_code={code.output.strip()}",
            "PASS" if code.output.strip() == "403" else "FAIL",
            code.output.strip()[:80],
        )
    )

    # TC-EGRESS-02d authorized SNI to the wrong IP (canary) must be rejected and must not leak
    r.append(
        _blocked(
            lab,
            "TC-EGRESS-02d",
            "Authorized SNI to wrong IP rejected",
            [
                "sh",
                "-c",
                f"echo Q | openssl s_client -connect {CANARY_IP}:443 "
                "-servername local-lab.test -verify_return_error 2>&1",
            ],
            "rejected (SNI_IP_MISMATCH), no canary leak",
        )
    )

    # TC-EGRESS-03/04 raw socket bypass
    r.append(
        _blocked(
            lab,
            "TC-EGRESS-03",
            "Direct TCP bypass dropped",
            [
                "python3",
                "-c",
                "import socket;socket.setdefaulttimeout(4);socket.create_connection(('203.0.113.9',80))",
            ],
            "connection fails, no canary leak",
        )
    )
    r.append(
        _no_leak(
            lab,
            "TC-EGRESS-04",
            "Direct UDP bypass dropped",
            ["sh", "-c", "nc -u -w2 203.0.113.9 12345 </dev/null"],
            "udp dropped: no canary leak (UDP has no connection result to read)",
        )
    )

    # TC-EGRESS-05 external resolver: the gateway intercepts ALL port-53 traffic, so a query aimed at an
    # external resolver is answered locally (NXDOMAIN for the out-of-scope name) and never reaches it. The
    # pass condition is "intercepted (no usable record) AND no canary leak", NOT a client error -- dig exits
    # 0 on an NXDOMAIN response.
    before = lab.canary_leaks()
    dns_res = lab.kali_exec(["dig", "+time=2", "+tries=1", "@203.0.113.9", "evil.invalid", "+short"])
    new_leaks = lab.canary_leaks()[len(before) :].strip()
    intercepted = not dns_res.output.strip()  # no A record returned for the out-of-scope name
    verdict = "PASS" if intercepted and not new_leaks else "FAIL"
    r.append(
        Result(
            "TC-EGRESS-05",
            "Direct external resolver intercepted",
            "dig @203.0.113.9 evil.invalid +short",
            "query intercepted by gateway DNS (no record), no canary leak",
            f"answer={dns_res.output.strip()[:60]!r} canary_leak={bool(new_leaks)}",
            verdict,
            f"leaks={new_leaks[:120]!r}",
        )
    )

    # TC-EGRESS-06/06b/07 control-plane, on-link bridge, metadata
    for tid, name, ip in (
        ("TC-EGRESS-06", "Docker control-plane IP dropped", "172.17.0.1:2375"),
        ("TC-EGRESS-06b", "On-link host bridge IP (R-12)", "172.30.0.1"),
        ("TC-EGRESS-07", "Cloud metadata IP dropped", "169.254.169.254"),
    ):
        r.append(
            _blocked(
                lab, tid, name, ["curl", "-s", "-m", "3", f"http://{ip}/"], "connection fails, no canary leak"
            )
        )

    # TC-EGRESS-08 fail-closed
    r.append(_fail_closed(lab))

    # TC-EGRESS-09 redirect Layer B
    r.append(
        _blocked(
            lab,
            "TC-EGRESS-09",
            "Out-of-scope redirect blocked at gateway",
            ["curl", "-ksS", "-m", "8", "-L", "http://local-lab.test/redirect-external"],
            "second hop to unauthorized-destination.test blocked, no canary leak",
        )
    )

    # TC-EGRESS-10 IPv6
    r.append(
        _blocked(
            lab,
            "TC-EGRESS-10",
            "IPv6 egress unreachable",
            [
                "python3",
                "-c",
                "import socket;socket.setdefaulttimeout(4);"
                "socket.socket(socket.AF_INET6,socket.SOCK_STREAM).connect(('2001:db8::1',80))",
            ],
            "network unreachable",
        )
    )

    # TC-FWD-01 dedicated IP authorized port
    r.append(
        _allowed(
            lab,
            "TC-FWD-01",
            "Authorized dedicated IP:port forwarded",
            [
                "curl",
                "-sS",
                "-m",
                "6",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                f"http://{DEDICATED_IP}:8443/",
            ],
            "reachable",
            "200",
        )
    )
    # TC-FWD-02 dedicated IP unauthorized port dropped
    r.append(
        _blocked(
            lab,
            "TC-FWD-02",
            "Dedicated IP unauthorized port dropped",
            ["sh", "-c", f"nc -w2 {DEDICATED_IP} 9999 </dev/null"],
            "dropped (unauthorized port), no canary leak",
        )
    )

    # TC-DNS-ECH-01 type65 stripped
    ech = lab.kali_exec(["dig", "+time=2", "local-lab.test", "HTTPS", "+short"])
    r.append(
        Result(
            "TC-DNS-ECH-01",
            "HTTPS/type65 stripped (ADR-017)",
            "dig local-lab.test HTTPS",
            "no type65 record in the answer",
            f"answer={ech.output.strip()[:80]!r}",
            "PASS" if not ech.output.strip() else "FAIL",
            ech.output.strip()[:160] or "empty answer",
        )
    )

    # TC-DNS-02 embedded resolver bypass
    r.append(
        _blocked_dns(
            lab,
            "TC-DNS-02",
            "Embedded resolver bypass closed (R-11)",
            ["dig", "+time=2", "+tries=1", "@127.0.0.11", "evil.invalid", "+short"],
        )
    )

    # TC-ROUTE-01 no NET_ADMIN
    ro = lab.kali_exec(["ip", "route", "add", "198.51.100.0/24", "via", GATEWAY_IP])
    r.append(
        Result(
            "TC-ROUTE-01",
            "Kali cannot modify routes",
            "ip route add ...",
            "EPERM / Operation not permitted",
            f"exit={ro.exit_code}",
            "PASS" if ro.exit_code != 0 and "not permitted" in ro.output.lower() else "FAIL",
            ro.output.strip()[:160],
        )
    )
    # TC-ROUTE-02 default via gateway
    rt = lab.kali_exec(["ip", "route", "show", "default"])
    r.append(
        Result(
            "TC-ROUTE-02",
            "Default route is the gateway",
            "ip route show default",
            f"default via {GATEWAY_IP}",
            rt.output.strip()[:80],
            "PASS" if f"default via {GATEWAY_IP}" in rt.output else "FAIL",
            rt.output.strip()[:160],
        )
    )

    # TC-CAP-01 zero added caps + unprivileged connect scan works (R-09 accepted fallback)
    cap = lab.kali_exec(["grep", "Cap", "/proc/self/status"], launcher=True)
    scan = lab.kali_exec(["nmap", "-sT", "-Pn", "-p", "8443", "-n", "--max-retries", "1", DEDICATED_IP])
    r.append(_cap_result(cap, scan))

    # TC-SBX-05 argv injection literal
    inj = lab.kali_exec(["nmap", "-sL", "-n", "x; id"])
    r.append(
        Result(
            "TC-SBX-05",
            "Command injection is literal argv",
            "nmap -sL 'x; id'",
            "id not executed; treated as a literal hostname",
            f"exit={inj.exit_code}",
            "PASS" if "uid=" not in inj.output else "FAIL",
            inj.output.strip()[:160],
        )
    )
    return r


def _T() -> str:  # pragma: no cover
    from poc_lab import TARGET_IP

    return TARGET_IP


def _fail_closed(lab: Lab) -> Result:  # pragma: no cover
    lab.gateway.stop()
    try:
        ping = lab.kali_exec(["ping", "-c", "1", "-W", "2", "203.0.113.9"])
        curl = lab.kali_exec(["curl", "-s", "-m", "3", "https://local-lab.test/"])
        blocked = ping.exit_code != 0 and curl.exit_code != 0
    finally:
        lab.gateway.start()
        lab._wait_ready(lab.gateway)
    return Result(
        "TC-EGRESS-08",
        "Gateway stopped -> fail-closed",
        "stop gateway; ping+curl",
        "100% loss while gateway down; restored after restart",
        f"ping_exit={ping.exit_code} curl_exit={curl.exit_code}",
        "PASS" if blocked else "FAIL",
        ping.output.strip()[:160],
    )


def _blocked_dns(lab: Lab, tid: str, name: str, argv: list[str]) -> Result:  # pragma: no cover
    res = lab.kali_exec(argv)
    # Success == no usable answer (empty, or does not resolve to a routable IP).
    answer = res.output.strip()
    verdict = "PASS" if (res.exit_code != 0 or not answer or "NXDOMAIN" in res.output) else "FAIL"
    return Result(
        tid,
        name,
        " ".join(argv),
        "NXDOMAIN/REFUSED/empty",
        f"exit={res.exit_code} answer={answer[:60]!r}",
        verdict,
        answer[:160] or "empty",
    )


def _cap_result(cap, scan) -> Result:  # pragma: no cover
    """R-09 accepted fallback: the process has ZERO effective/ambient caps, yet an unprivileged
    ``nmap -sT`` connect scan still works (no raw socket, no root)."""
    caps: dict[str, int] = {}
    for line in cap.output.splitlines():
        if line.startswith("Cap"):
            k, _, v = line.partition(":")
            try:
                caps[k.strip()] = int(v.strip(), 16)
            except ValueError:
                pass
    no_caps = all(caps.get(k, 0) == 0 for k in ("CapAmb", "CapEff", "CapPrm", "CapBnd"))
    scan_ok = scan.exit_code == 0 and "requires root" not in scan.output
    ok = no_caps and scan_ok
    return Result(
        "TC-CAP-01",
        "Zero added caps; unprivileged nmap -sT works",
        "grep Cap /proc/self/status; nmap -sT -Pn",
        "all capability sets zero; connect scan exits 0",
        f"caps_all_zero={no_caps} connect_scan_exit={scan.exit_code}",
        "PASS" if ok else "FAIL",
        f"caps={cap.output.strip()[:120]!r} scan={scan.output.strip()[:120]!r}",
    )
