# 16. Revision 3.1 Independent Review & Reconciliation Record

**Document ID:** `DOC-16`
**Status:** Implementation baseline for Phases 0–3 (supersedes conflicting statements elsewhere, see §1)
**Date:** 2026-11-14
**Scope:** Independent technical review of the Revision 3.1 documentation suite before implementation.

The Revision 3.1 suite is treated as the product specification. Its technical claims are **not** treated as
proven. This record lists every contradiction, gap, and technically impossible requirement found during review,
and states how each was resolved. Items marked **BLOCKING** or **DECISION REQUIRED** are not silently
worked around; they are surfaced for the product owner.

---

## 1. Source-of-truth precedence

Documents disagree on versions (DOC-00/01 v1.0, DOC-07/10 v2.0.0, DOC-11 v3.0.0, the rest v3.1.0). Where they
conflict, this precedence applies (latest *accepted* architecture decision wins):

1. ADR-016, ADR-017, ADR-018 (DOC-02, status *Accepted (Revision 3.1)*)
2. DOC-14 Claude Code Implementation Brief v3.1.0 (named primary brief by README)
3. DOC-04 Docker & Network Security v3.1.0, DOC-13 v3.1.0
4. DOC-12 Project Structure v3.1.0
5. DOC-11 Roadmap v3.0.0
6. DOC-10 Testing & Acceptance v2.0.0 (oldest; its code samples are illustrative only)
7. This document overrides all of the above **only** for items listed below, each with a reason.

---

## 2. Reconciled network addressing (R-01, R-02)

| Element | Conflicting values found | Reconciled value | Basis |
|---|---|---|---|
| Internal subnet | `172.30.0.0/24` (DOC-04), `172.28.0.0/16` (DOC-10/12) | Per-session `/24` from pool `172.30.0.0/16`; session index N → `172.30.N.0/24` | ADR-016 uses 172.30.0.254; see R-02 |
| Gateway internal IP | `172.30.0.254` (ADR-016, DOC-02/04/09/13/14/15), `172.28.0.2` (DOC-10 Inv. 5), `172.28.0.15` (DOC-10 lab diagram) | `172.30.N.254` (`172.30.0.254` for the first/PoC session) | ADR-016 (accepted) |
| Kali IP | `172.30.0.2` (DOC-04), `172.30.0.10` (DOC-10 §7.10), `172.28.0.10` (DOC-10 lab) | `172.30.N.2` | DOC-04 |
| Gateway external IP | `172.31.0.2` (DOC-02/04) | Production: Docker-assigned on `bletiner-external`. Lab: `198.51.100.2` | See §3 |
| Lab targets | `172.28.0.20/.30/.40` (DOC-10), `192.0.2.10` (old PoC script) | `198.51.100.0/24` (RFC 5737 TEST-NET-2) on an `internal: true` upstream network | §3 |
| Container names | `bletiner-kali-<id>` (ADR-016), `bletiner-inv-<id>` (DOC-04 §4.1/§5.1), `bletiner-kali` (PoC) | `bletiner-kali-<id>`, `bletiner-gateway-<id>`, `bletiner-internal-<id>` | ADR-016 |

**R-02 (contradiction): a fixed subnet cannot be per-session.** Docker refuses to create two networks with
overlapping subnets, so a hard-coded `172.30.0.0/24` makes a second concurrent investigation fail. Resolution:
allocate the lowest free `/24` from `172.30.0.0/16` (`bletiner.network.addressing`). The gateway is always `.254`
and Kali `.2` of that subnet, so ADR-016's `172.30.0.254` holds for session index 0.

---

## 3. Lab design for the live gate (R-21, R-22)

**R-21 (rule violation in spec):** DOC-10 §6.9 and the original PoC script send traffic to public addresses
(`1.1.1.1`, `8.8.8.8`, `93.184.216.34`, `2606:4700:4700::1111`, `example.com`). That conflicts with the
"never test public targets" rule. All of them are replaced.

**R-22 (test validity):** A "packet dropped" test passes trivially if there is simply no route anywhere. It
proves nothing about the gateway's FORWARD policy. Every negative test therefore needs a **leak detector** that
would observably receive the packet if the gateway forwarded it.

Lab topology (all networks `internal: true`; nothing can reach the internet during tests):

```
 bletiner-internal-poc (172.30.0.0/24, internal)      bletiner-lab-upstream-poc (198.51.100.0/24, internal)
 ┌───────────────────────┐   default route   ┌───────────────────────────┐
 │ bletiner-kali-poc     │ ────────────────▶ │ bletiner-gateway-poc      │
 │ 172.30.0.2            │                   │ eth(int) 172.30.0.254     │
 │ cap_drop=ALL, no      │                   │ eth(up)  198.51.100.2     │── default route ──▶ canary 198.51.100.5
 │ NET_ADMIN, ro rootfs  │                   │ iptables FORWARD DROP,    │                     (tcpdump: every packet
 └───────────────────────┘                   │ SNI/Host proxy :8080,     │                      that leaks is logged)
                                             │ DNS filter :5353          │── lab target 198.51.100.10
                                             └───────────────────────────┘    (HTTPS vhosts local-lab.test +
                                                                                unauthorized-tenant.test on ONE IP
                                                                                = shared-CDN simulation)
                                                                            ── dedicated IP 198.51.100.20 (TCP 8443, 9999)
                                                                            ── lab DNS 198.51.100.53 (A + HTTPS/type-65)
```

* The gateway's default route points at the canary. Any packet the gateway forwards to an off-link address
  (`203.0.113.x`, `169.254.169.254`, `172.17.0.1`, …) lands on the canary and is captured. A negative test
  passes only if the client fails **and** the canary saw zero packets for that destination.
* `TC-LAB-00` is a positive control that proves the canary captures leaks. If it fails, every negative verdict
  is invalid and the gate fails.
* Production egress to the real internet (`bletiner-external` as a normal bridge) is deliberately **not**
  exercised by the gate.

---

## 4. Findings

Severity: **BLOCKING** = cannot proceed past the gate without resolution. **DECISION REQUIRED** = needs the
product owner. **RESOLVED** = fixed in implementation with the stated reason.

### R-05 — `scripts/verify_gateway_poc.py` existed but was not a verification tool — RESOLVED (rewritten)
Instruction 2 asked whether the script exists. It did, but:
* Default mode was **simulated**: it printed hard-coded "PASS" results and exited `0` with
  "AUDIT PASS: All 11 security isolation gates verified" **without executing anything**.
* Live mode used `shell=True` (violates DOC-14 rule 6).
* Live verdict logic was inverted for negative tests: `PASS` only if the command exited `0`. So a correctly
  blocked connection (non-zero exit) was reported `FAIL`, and a successful leak was reported `PASS`.
* A timeout counted as `PASS` if the word "dropped" appeared in the expected text.
* `TC-EGRESS-08` stopped the gateway and never restarted it.
* Container names (`bletiner-kali`) matched nothing the spec provisions. Target IPs belonged to no lab.

The script is rewritten (§6). It has no simulation mode. Without a reachable Docker daemon every test is
`NOT RUN` and it exits `2`.

### R-06 — EGRESS-02 did not test SNI — RESOLVED
`curl -H 'Host: unauthorized-tenant.test' https://local-lab.test` sends **SNI = local-lab.test**. The `Host`
header travels *inside* the encrypted tunnel, so a non-MITM gateway can never see it. The gateway would
(correctly) allow the connection. The test exercised domain fronting, not SNI filtering.
Corrected tests:
* `TC-EGRESS-02` — `openssl s_client -connect 198.51.100.10:443 -servername unauthorized-tenant.test` must be
  rejected. A paired positive control in the **same test** sends the identical command with
  `-servername local-lab.test` to the same IP/port and must succeed. Only the SNI differs, so the result isolates
  SNI enforcement. The gateway decision log is captured as evidence.
* `TC-EGRESS-02a` — the same via `curl --resolve unauthorized-tenant.test:443:198.51.100.10`.
* `TC-EGRESS-02b` — ClientHello with **no SNI** (`openssl s_client -noservername`) must be rejected.
* `TC-EGRESS-02c` — plaintext HTTP (port 80) with an out-of-scope `Host` header must get 403. This is where a
  Host-header test is meaningful.
* `TC-EGRESS-02d` — authorized SNI sent to a *different* IP (canary) must be rejected (see R-07).

**Residual risk (documented, not fixable under ADR-014's no-MITM decision):** domain fronting. A client that
sends an authorized SNI and an unauthorized `Host` header inside TLS, to a CDN that routes by `Host`, cannot be
detected without TLS interception. The original EGRESS-02 expected this to be blocked, which is **impossible**
under the accepted architecture.

### R-07 — Authorized SNI to an arbitrary destination IP (spec gap) — RESOLVED
DOC-04 §7.2 says the gateway "relays the encrypted byte stream to the target" once SNI matches, but never binds
the destination IP to the SNI. A tool can connect to *any* IP on port 443 while presenting an authorized SNI.
That gives unrestricted egress to attacker-controlled hosts. Resolution: the proxy forwards only if the original
destination IP is among the addresses the gateway's own scoped resolver returned for that SNI name. Tested by
`TC-EGRESS-02d`.

### R-08 — Transparent redirect makes every IP look open on 80/443 — RESOLVED (with residual)
DOC-04 §8.1 redirects TCP 80/443 for **all** destinations to the proxy. NAT `REDIRECT` happens in `PREROUTING`,
before `FORWARD`, so the gateway completes the TCP handshake for every IP. The matrix row "Raw web port scans to
CDN → BLOCKED (timed out)" is therefore impossible as designed, and scanners would report 80/443 open on the
whole internet. Resolution: `REDIRECT` applies only to destination IPs that in-scope domains resolved to through
the gateway resolver (a dynamic `BLETINER_WEB` nat chain). Every other destination falls through to
`FORWARD DROP`. Residual: for in-scope domain IPs, ports 80/443 are answered by the proxy, so port state is
not authoritative there.

### R-09 — Ambient capabilities for a non-root user as specified are impossible — RESOLVED (owner chose nmap -sT)

> **Decision (2026-11-14, product owner): fall back to `nmap -sT`.** The sandbox adds **zero** capabilities
> (`cap_drop=ALL`, empty `cap_add`), so there is no UID-0 exec and no `CAP_NET_RAW`. Raw SYN scans are
> unavailable by design; tools use TCP connect scans. This is the strictest option and removes the
> `SETUID`/`SETGID` bounding-set trade-off the `setpriv` proposal carried. Implemented in
> `bletiner/container/security.py` (`ADDED_CAPS = ()`, `assert_secure` rejects any added capability including
> `NET_RAW`), the launcher is now a plain non-elevating passthrough, and `TC-CAP-01` asserts every capability
> set is zero **and** that `nmap -sT` still succeeds unprivileged. **DOC-04 §2.3 and DOC-15 CORR-03 must be
> corrected**: their `CapAmb: 0x3000` / ambient-NET_RAW claims no longer describe the build and were wrong
> regardless (`0x3000` includes `CAP_NET_ADMIN`).

The original analysis, retained for the record:
DOC-14 rule 3 and DOC-04 §2.3 require UID 1000 to raise `CapAmb` via `setpriv`/`capsh` under `--user 1000:1000`
and `cap_drop=ALL`. The kernel only allows `PR_CAP_AMBIENT_RAISE` for a capability already in **both** the
permitted and inheritable sets. A non-root container process gets empty permitted/inheritable sets (Docker
≥ 20.10.14, CVE-2022-24769 fix), so the raise fails with `EPERM`. DOC-04's launcher uses `capsh --user=bletiner`,
which additionally needs `CAP_SETUID`/`CAP_SETGID`, also dropped. The Dockerfile's `USER bletiner` and the
launcher therefore contradict each other.

Additional defect: DOC-04 §2.3, DOC-15 CORR-03 and the old PoC script assert `CapAmb: 0000000000003000`. That
mask is `CAP_NET_RAW (bit 13) | CAP_NET_ADMIN (bit 12)`, so it contradicts the zero-`NET_ADMIN` rule. The correct
mask for `NET_RAW | NET_BIND_SERVICE` is **`0x2400`**.

**Proposed resolution (implemented, flagged PROPOSED, not accepted):**
* The container's default user stays `1000:1000`. PID 1 and interactive shells are UID 1000 with **no**
  effective capabilities.
* Bounding set = `NET_RAW, NET_BIND_SERVICE, SETUID, SETGID`. `no-new-privileges` is on and all setuid bits are
  stripped, so a UID-1000 process cannot acquire anything from the bounding set.
* Only the host-initiated agent exec runs the launcher as UID 0 in the container. It immediately does
  `setpriv --reuid=1000 --regid=1000 --clear-groups --inh-caps=-all,+net_raw,+net_bind_service
  --ambient-caps=-all,+net_raw,+net_bind_service -- <argv>`. The tool runs as UID 1000 with `CapAmb=0x2400`.
* Trade-off for the product owner: `SETUID`/`SETGID` sit in the container bounding set, and a UID-0 exec exists
  transiently. Alternatives: (a) accept `nmap -sT` only (no raw sockets, no extra caps; DOC-04 §2.3 fallback),
  or (b) accept this proposal. **Phase 2's capability gate (`TC-CAP-01`) cannot be declared accepted until this
  is decided and verified live.**

### R-10 — `icc=false` on the internal network breaks the gateway path — RESOLVED
DOC-04 §9.3 sets `icc=false` on `bletiner-internal-<id>`. Docker implements that as a host rule dropping all
container-to-container traffic on the bridge, **including Kali → gateway**, so Kali would have no egress at all.
Resolution: ICC stays enabled on the per-session network. It only ever holds Kali and its own gateway.
Cross-investigation isolation comes from separate networks, not ICC.

### R-11 — Docker embedded DNS (127.0.0.11) bypass — RESOLVED (verify live)
On user-defined networks Kali's `resolv.conf` points to Docker's embedded resolver, which forwards upstream
outside the gateway's port-53 redirect. Resolution: Kali is created with `dns=[<gateway_ip>]`, so the embedded
resolver forwards to the gateway filter. Live test `TC-DNS-02` asserts an out-of-scope name queried via
`127.0.0.11` is refused. Probe names use the reserved `.invalid` TLD.

### R-12 — Host bridge IP is on-link to Kali and outside the gateway's control — OPEN (verify live)
On `bletiner-internal-<id>` the Docker host owns `172.30.N.1`. That address is on Kali's directly connected
subnet, so the traffic never crosses the gateway. Docker's internal-network rules filter FORWARD, not host
INPUT. DOC-04's claim "zero route to the host" is unproven, and EGRESS-06 tested the wrong address
(`172.17.0.1`, which *does* route via the gateway). Added `TC-EGRESS-06b` (Kali → `172.30.0.1`). If it fails, a
candidate mitigation (network option `com.docker.network.bridge.inhibit_ipv4`) must be evaluated live. Its
behaviour on `internal` networks is unverified, so it is not enabled blindly.

### R-13 — Missing MASQUERADE — RESOLVED
DOC-04 §8.1 forwards authorized raw-IP traffic but defines no source NAT. Replies from targets would be addressed
to `172.30.N.2`, which is unroutable upstream. Added `POSTROUTING -o <upstream> -j MASQUERADE` (DOC-12 already
names it).

### R-14 — EGRESS-09 depends on Phase 4 code — RESOLVED (split)
DOC-10's EGRESS-09 runs `bletiner.adapters.tools.http_recon_tool` *inside Kali*. That is Phase 4 tool code which
is not installed in the image, so Gate 3 could never pass before Phase 4. Resolution: Layer A (client redirect
hook) is `bletiner.network.redirects` with host-side unit tests (`tests/unit/test_redirects.py`, implementation
correctness only). Gate 3 tests Layer B live: `curl -L` following a 302 from `local-lab.test` to
`unauthorized-tenant.test` must be stopped by the gateway SNI filter. Live Layer A testing moves to Phase 4.

### R-15 — Proxy env vars vs transparent routing (ADR-009 vs ADR-016/018) — RESOLVED
ADR-009 says egress "must route through the gateway proxy (`HTTP_PROXY`…)". ADR-016/018 enforce transparently at
L3 because env vars are voluntary. Resolution: enforcement is transparent. The proxy also accepts explicit
`CONNECT` with identical policy, but proxy env vars are **not** set by default. Tests exercise the transparent
path, which is the one tools cannot opt out of.

### R-16 — Unverifiable image pins — RESOLVED (with open item)
* The Kali digest `sha256:4f87a32b…` in DOC-04 could not be verified and appears to be illustrative. Pinning to
  an unverified digest would make the build fail or, worse, pin an unknown image.
* Exact apt pins on `kali-rolling` (`nmap=7.94+dfsg1-1` etc.) stop resolving as soon as the rolling repo moves on.
* Resolution: the Dockerfile uses the tag and records installed tool versions in `/etc/bletiner/image-manifest.txt`
  at build time. **Open:** pin the digest after the first verified pull
  (`docker buildx imagetools inspect kalilinux/kali-rolling`).
* The PoC image contains only what Phases 2–3 need. Go tools and Tier-4 tools (masscan, sqlmap) are deferred to
  Phase 4 tooling.

### R-17 — Three incompatible package layouts — RESOLVED
DOC-14 uses `bletiner/{models,storage,container,network,…}`. DOC-12 uses
`src/bletiner/{core,docker,scope,infra/gateway,…}`. DOC-10 imports `bletiner.adapters.docker`, `bletiner.domain`,
`bletiner.services.docker_manager`. Resolution: DOC-14 layout (precedence §1). Container images live under
`docker/{kali,gateway,lab}/` per DOC-12, which DOC-14 does not specify. Gateway runtime modules
(`bletiner/network/{policy,sni,dns_filter,proxy,gateway_main}.py`) are stdlib + `dnslib` only, so they can be
copied into the gateway image without the desktop dependencies.

### R-04 — Gate size: 10 vs 11 vs 14 tests — RESOLVED
DOC-10/11/14 say 10 scenarios, DOC-15 and the old script say 11, and DOC-04 §12 says 14. Gate 3 is now defined
as **every test in the script's matrix** (the 10 original EGRESS scenarios, corrected, plus the added
SNI/DNS/capability/route/forwarding/control tests). Each must be `PASS`. The count is printed by the script, not
asserted in prose.

### R-19 — Toolchain — RESOLVED
Neither Poetry nor uv is installed on this workstation. `pyproject.toml` is PEP 621 (hatchling) and works with
Poetry ≥ 2, uv, or pip. Gate commands are run from `.venv/bin/`. PySide6 is an optional `ui` extra because it is
not needed until Phase 5.

### R-20 — No container runtime on the build workstation — BLOCKING
The workstation (macOS 26.5, arm64) has no Docker Engine, Docker Desktop, Colima, OrbStack or Podman. Per DOC-14
§10.3 the live gate cannot run here. All Tier C (empirical) properties remain **PENDING LAB EXECUTION**.

### R-23 — DOC-15 claims phases passed before any code existed — RESOLVED
DOC-15 §4 lists "Phase 0/1/2: PASS" and "Level 2 Implementation Correctness: PASS" while the repository contained
no implementation. These are reclassified as `NOT RUN`. Actual status is tracked in
`docs/reports/PHASE_0-3_REPORT.md`.

### R-24 — Gateway start-up window — OPEN (residual risk)
`net.ipv4.ip_forward` can only be set via Docker `sysctls` at container create, because `/proc/sys` is read-only
inside containers. Between gateway start and iptables application, the default FORWARD policy is ACCEPT.
Mitigations: (1) the gateway is started and confirmed ready **before** Kali exists or has a route. (2)
`ip_forward` is enabled only when the scope contains dedicated IP/CIDR targets; domain-only scopes never forward
at the kernel level. (3) Restart policy is `no`, and a dead gateway is never restarted under a live Kali. The
manager must re-provision. (4) In the lab, the gateway has no upstream default route until after rules are
applied. A production-mode window remains when IP targets are present and someone manually restarts the gateway.

### R-25 — Seccomp profile file not provided — OPEN
DOC-04 §5.1 references `/etc/bletiner/seccomp-profile.json`, which does not exist. Docker's default seccomp
profile is used. A custom profile is a Phase 7 hardening item.

### R-26 — ECH blocking by DNS stripping alone is insufficient — RESOLVED (defense-in-depth)
ADR-017 strips HTTPS/SVCB records, but an `ECHConfig` can be obtained out of band (hard-coded, or fetched over an
allowed channel). The proxy therefore also rejects any ClientHello carrying the `encrypted_client_hello` (0xfe0d)
or legacy ESNI (0xffce) extension. This is unit-tested only. Stock curl/OpenSSL in Kali cannot emit ECH, so it
is not live-testable here.

### R-27 — HTTP keep-alive Host switching — RESOLVED
Host-header inspection on the first request only would let a client send `Host: allowed` and then
`Host: unauthorized` on the same connection. The proxy forwards exactly one request per plaintext connection
(`Connection: close` is forced, and chunked request bodies are rejected), then relays only upstream → client.

### R-28 — Kali restart drops the injected route — NOTED
Restarting the Kali container creates a fresh netns with no default route (fail-closed but broken). The container
manager uses restart policy `no` and re-injects the route on every (re)start.

### R-30 — Two different prioritization formulas — RESOLVED (DOC-08 §5.1 authoritative)
ADR-019 specifies a weighted **sum** (`ws·S + wc·C + wv·V − we·E`), while DOC-08 §5.1 specifies a bounded
**product** (`100·Ws·Wc·Iv·Sr·Cd`). They disagree. DOC-08 §5.1 is implemented
(`bletiner/reporting/prioritization.py`) because it is the only one with complete factor tables and matches the
`findings.priority_score` 0–100 column and the no-discard threshold. ADR-019's "weights are configurable, never
LLM-modified; low scores are never discarded" consequences are preserved. DOC-02 ADR-019 should be updated to
reference §5.1, or vice versa.

### R-31 — Agent runtime built, but gated — RESOLVED (code-enforced)
Mandatory instruction #6 and DOC-14 rule 5 forbid running the autonomous agent against a live sandbox before
Gate 3 passes. Phases 4–8 are implemented as code + deterministic mocked tests (the spec's own Tier B). The
**live** runtime is gated in code: `bletiner/agent/gate.require_gate3_certified` is called by
`InvestigationService.start` (and re-asserted in the E2E suite) and raises unless a genuine passing *live* gate
report exists. A simulated or NOT-RUN report can never certify it (R-05). So the agent code exists and is
tested, but cannot execute against a live gateway on this host.

---

## 5. Requirement → implementation map for Phases 0–3

| Requirement | Implementation | Unit tests (Tier B) | Live test (Tier C) |
|---|---|---|---|
| ADR-016 route injection | `bletiner/network/router.py` | `test_router.py` | `TC-ROUTE-01`, `TC-ROUTE-02` |
| ADR-017 HTTPS/SVCB stripping | `bletiner/network/dns_filter.py` | `test_dns_filter.py` | `TC-DNS-ECH-01` |
| ADR-018 domain vs IP separation | `bletiner/network/policy.py` (rule builder) | `test_policy.py` | `TC-FWD-01`, `TC-FWD-02`, `TC-EGRESS-03` |
| SNI / Host inspection | `bletiner/network/sni.py`, `proxy.py` | `test_sni.py`, `test_proxy.py` | `TC-EGRESS-01/02/02a–d` |
| Fail-closed | topology + `internal: true` | `test_topology.py` | `TC-EGRESS-08` |
| Hardened Kali | `bletiner/container/security.py`, `manager.py` | `test_container_manager.py` | `TC-SBX-01…05` |
| No-cap sandbox + nmap -sT (R-09) | `bletiner/container/security.py` (`ADDED_CAPS=()`) | `test_container_manager.py` | `TC-CAP-01` |
| Structured argv | `bletiner/container/exec_runner.py` | `test_exec_runner.py` | `TC-SBX-05` |
| Two-tier redirects | `bletiner/network/redirects.py` (Layer A) | `test_redirects.py` | `TC-EGRESS-09` (Layer B) |
