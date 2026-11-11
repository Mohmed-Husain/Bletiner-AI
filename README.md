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
- [**scripts/verify_gateway_poc.py**](scripts/verify_gateway_poc.py) — 11-point egress security validation test suite.

---

## License & Authorization Notice

Bletiner AI is designed strictly for authorized security assessments on systems owned by the operator or under explicit written authorization.
