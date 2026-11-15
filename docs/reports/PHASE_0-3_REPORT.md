# Bletiner AI — Phases 0–3 Implementation Report

**Date:** 2026-11-14
**Branch:** `feat/phase0-3-sandbox-gateway`
**Scope delivered:** Phase 0 (setup) → Phase 1 (domain + storage) → Phase 2 (hardened Kali sandbox) →
Phase 3 (dual-homed fail-closed egress gateway + rewritten live gate). **Phase 4 (agent loop) NOT started**,
per the mandate: no agent runtime before the live gateway gate passes.

This report follows the mandatory instructions in the request and the Revision 3.1 evidence rules. The
independent review of the documentation is `docs/16_REV31_RECONCILIATION.md`; read it first.

---

## 1. Headline status

| Assurance tier | Meaning | Status |
|---|---|---|
| Tier A — documentation consistency | contradictions reconciled | **DONE** (docs/16) |
| Tier B — implementation correctness | code + unit tests (mocked Docker, pure logic) | **PASS** — 127 tests, ruff + mypy --strict clean |
| Tier C — empirical security properties | live Docker egress gate | **PENDING LAB EXECUTION** — no container runtime on this workstation (R-20) |

**The fail-closed network has NOT been empirically proven on this machine.** It cannot be: macOS 26.5 / arm64
here has no Docker/Podman/Colima/OrbStack. The live gate is built and runnable; it reports every scenario
`NOT RUN` and exits non-zero until run on a Docker host. I did **not** mark any security property `PASS` from
documentation or mocks — that was the central defect of the original artifacts (R-05, R-23).

---

## 2. What was built

### Phase 0 — setup
`pyproject.toml` (PEP 621, works with uv/Poetry≥2/pip — neither uv nor Poetry is installed here, R-19),
`bletiner/__main__.py` CLI, pre-commit config including a `shell=True` / `bash -c` grep hook, strict ruff +
mypy. **Gate 0: PASS** (`evidence/phase0/gate0.log`).

### Phase 1 — domain + storage
Pydantic v2 frozen models (`bletiner/models/`), async SQLite with enforced WAL + foreign keys + transactional
migrations (`bletiner/storage/database.py`), typed repositories, artifact tree with traversal/symlink
protection. Schema is DOC-07's 17-table v2 DDL verbatim, loaded under one exclusive transaction; a serialized
write lock gives the "200 concurrent writes, zero SQLITE_BUSY" guarantee. **Gate 1: PASS** (60 tests,
`evidence/phase1/gate1.log`).

### Phase 2 — hardened Kali sandbox
`bletiner/container/security.py` emits the exact Docker config and `assert_secure()` refuses to create a
container missing any invariant (non-root 1000:1000, read-only root, `cap_drop=ALL`, no `NET_ADMIN`, no
privileged, no docker.sock, no-new-privileges, IPv6 off). `exec_runner.py` enforces structured `argv` and
rejects `shell -c`. `docker/kali/` holds the Dockerfile + ambient-cap launcher. **Gate 2: PASS** (27 tests,
mocked Docker, `evidence/phase2/gate2.log`).

### Phase 3 — dual-homed fail-closed gateway
`bletiner/network/`: per-session subnet allocation, `internal: true` topology, ADR-016 host-side netns route
injection, the iptables policy builder (FORWARD DROP first, ADR-018 domain-vs-IP separation, MASQUERADE,
metadata/RFC1918 drops, QUIC drop), a bounds-checked TLS ClientHello/SNI parser, the ADR-017 DNS filter
(HTTPS/SVCB stripping + rebinding guard), and the transparent SNI/Host proxy decision engine. The gateway
image (`docker/gateway/`) runs these. **Network Tier B: PASS** (38 tests). **Live Gate 3: PENDING** — see §4.

---

## 3. Mandatory-instruction compliance

1. Docs treated as spec, claims not assumed proven — `docs/16`, and R-09 flags an impossible requirement.
2. `scripts/verify_gateway_poc.py` existed but was a fake (printed PASS without executing); **rewritten** (R-05).
3. **EGRESS-02 corrected** to test SNI via `openssl -servername`, not an encrypted-tunnel Host header, with a
   same-IP authorized/unauthorized control pair (R-06). Added NO-SNI, Host-header-on-HTTP, and SNI-to-wrong-IP
   cases.
4. IP/version inconsistencies reconciled to the accepted ADRs (R-01/R-02 table in docs/16 §2).
5. No simulated/mocked test is labelled live validation — enforced by the `tier` field and the gate's
   no-simulation design.
6. Phases executed 0→3 in order; agent loop deliberately absent.
7. Impossible/contradictory requirements documented, not silently worked around (R-09 ambient caps, R-08
   transparent-redirect scanning, R-10 `icc=false`, R-12 on-link host IP).
8. Six-field evidence (Test ID / command / expected / actual / verdict / logs) in `scripts/poc_report.py`.
9. No public/production targets — RFC 5737 addresses on `internal: true` lab networks only (R-21).
10. This per-phase report with changed files, results, unresolved issues, and prerequisites.

---

## 4. Phase 3 gate: why it is PENDING, and how to run it

```bash
python scripts/verify_gateway_poc.py --build   # on a host with Docker
```

It provisions the reconciled lab (docs/16 §3): Kali + gateway on an internal subnet, lab target/canary/DNS on
an internal upstream, gateway default-routed at the **canary leak detector**. Every negative test passes only
if the client fails **and** the canary captured nothing (R-22); `TC-LAB-00` is the positive control proving
the detector works. With no Docker, all 23 scenarios are `NOT RUN` and the script exits 2.

Matrix (all Tier C, currently NOT RUN): TC-LAB-00, TC-EGRESS-01/02/02b/02c/02d/03/04/05/06/06b/07/08/09/10,
TC-FWD-01/02, TC-DNS-ECH-01, TC-DNS-02, TC-ROUTE-01/02, TC-CAP-01, TC-SBX-05.

---

## 5. Unresolved issues / decisions required

- **R-09 (RESOLVED — owner chose `nmap -sT`):** the sandbox now adds **zero** capabilities (`cap_drop=ALL`,
  empty `cap_add`); no UID-0 exec, no `CAP_NET_RAW`. `assert_secure` rejects any added capability, the launcher
  is a non-elevating passthrough, and `TC-CAP-01` checks all cap sets are zero while `nmap -sT` still works.
  **Follow-up:** DOC-04 §2.3 and DOC-15 CORR-03 must be corrected — their ambient-`CAP_NET_RAW` /
  `CapAmb: 0x3000` claims no longer match the build (and `0x3000` wrongly included `CAP_NET_ADMIN`).
- **R-12, R-24 (OPEN, verify live):** on-link host bridge IP reachability, and the gateway start-up
  `ip_forward` window. Tests exist (TC-EGRESS-06b); mitigations may need host-specific tuning.
- **R-16 (OPEN):** pin the Kali image digest after a verified pull; the docs' digest was unverifiable.
- **R-20 (BLOCKING for Tier C):** no Docker here.
- **R-25 (OPEN):** custom seccomp profile deferred to Phase 7; default profile used.

---

## 6. Prerequisites before Phase 4

1. Run the live gate on a Docker host; all scenarios must be `PASS` (DOC-14 rule 5).
2. Correct DOC-04 §2.3 / DOC-15 CORR-03 capability text to match the accepted R-09 no-cap + `nmap -sT` model.
3. Decide R-12/R-24 mitigations from live results.
4. Pin the Kali image digest (R-16).

Until step 1 is green on real Docker, Phase 4 must not begin.
