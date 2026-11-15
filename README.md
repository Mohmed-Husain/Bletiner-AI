# Bletiner AI

> **Autonomous Security Investigation Desktop Application**  
> Built with Python, PySide6, Docker Kali Linux Sandbox, and a Pi-Inspired Agent Runtime.

---

## Overview

**Bletiner AI** is an autonomous desktop application for authorized cybersecurity investigation. It operates inside dedicated, per-session Kali Linux containers, providing human operators with real-time operational feeds, an interactive Kali Linux terminal, structured finding management, and evidence-backed Markdown/HTML reporting.

---

## Architecture Highlights

1. **Pi-Inspired Agent Harness:** Adapted from Earendil Works Pi (`pi-ai` provider protocol and `pi-agent` state machine).
2. **Per-Investigation Kali Sandbox:** One dedicated Docker container and isolated workspace per investigation.
3. **Fail-Closed Egress Gateway:** Dual-homed internal bridge (`internal: true`) and gateway proxy preventing out-of-scope traffic.
4. **Desktop Presentation:** PySide6 with a modern 3-card workspace (Investigation, Kali Linux Terminal, Sessions & Files) and bottom steering control.
5. **Dual Reporting Engine:** Evidence-hashed findings exported simultaneously to Markdown and standalone HTML.

---

## Engineering Documentation Suite

Complete, implementation-ready specifications are available in the [`docs/`](docs/) directory:

- [**docs/README.md**](docs/README.md) — Documentation index and reading order.
- [**docs/14_CLAUDE_CODE_IMPLEMENTATION_BRIEF.md**](docs/14_CLAUDE_CODE_IMPLEMENTATION_BRIEF.md) — Primary implementation brief for Claude Code.
- [**docs/11_IMPLEMENTATION_ROADMAP.md**](docs/11_IMPLEMENTATION_ROADMAP.md) — 9-phase canonical engineering roadmap.
- [**docs/15_DOCUMENTATION_AUDIT.md**](docs/15_DOCUMENTATION_AUDIT.md) — Quality Gate audit certifying all requirements.
- [**docs/16_REV31_RECONCILIATION.md**](docs/16_REV31_RECONCILIATION.md) — Independent review: contradictions found and how each was resolved. **Read this before the code.**
- [**scripts/verify_gateway_poc.py**](scripts/verify_gateway_poc.py) — Live egress gate (rewritten; no simulation mode).

---

## Implementation Status (Phases 0–3)

The `feat/phase0-3-sandbox-gateway` branch implements the foundation: project setup, domain + SQLite
persistence, the hardened Kali sandbox, and the dual-homed fail-closed egress gateway. **The autonomous agent
loop (Phase 4) is intentionally not built** until the live gateway gate passes.

An independent review (`docs/16`) reconciled version/IP contradictions and flagged requirements that are
impossible or insecure as written (e.g. non-root ambient capabilities, the mislabelled SNI test). The original
`verify_gateway_poc.py` printed hard-coded PASS results without executing anything; it has been rewritten into
a real live gate with no simulation mode.

| Tier | What | Status |
|---|---|---|
| A — docs consistency | contradictions reconciled | DONE |
| B — implementation | code + 127 unit tests, ruff + `mypy --strict` clean | PASS |
| C — empirical security | live Docker egress gate | **PENDING LAB EXECUTION** (no container runtime on the build host) |

See [`docs/reports/PHASE_0-3_REPORT.md`](docs/reports/PHASE_0-3_REPORT.md) for the full report.

### Developer quickstart

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

```bash
ruff check . && ruff format --check . && mypy && pytest
```

```bash
python scripts/verify_gateway_poc.py --build   # live egress gate; needs a running Docker daemon
```

---

## License & Authorization Notice

Bletiner AI is designed strictly for authorized security assessments on systems owned by the operator or under explicit written authorization.
