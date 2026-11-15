# Bletiner AI — Phases 4–8 Implementation Report

**Date:** 2026-11-15
**Branch:** `feat/phase0-3-sandbox-gateway`
**Scope:** Phases 4 (agent runtime), 5 (UI), 6 (findings + reporting), 7 (recovery), 8 (E2E module).

Read `docs/16_REV31_RECONCILIATION.md` first. This report continues `PHASE_0-3_REPORT.md`.

---

## 1. Headline status

| Tier | Meaning | Status |
|---|---|---|
| A — documentation consistency | contradictions reconciled (incl. R-30, R-31) | DONE |
| B — implementation correctness | code + deterministic mocked tests | **PASS** — 209 unit tests, ruff + mypy --strict clean |
| C — empirical / live | live egress gate + E2E against Docker lab | **PENDING LAB EXECUTION** (no container runtime here) |

The hard constraint (DOC-14 rule 5 / instruction #6): the autonomous agent must not run against a live sandbox
until Gate 3 passes on Docker. Phases 4–8 are therefore built and unit-tested, with the **live** path gated in
code (`require_gate3_certified`). The agent cannot execute against a live gateway on this machine.

## 2. What was built

### Phase 4 — agent runtime & providers
- `bletiner/providers/` — vendor-neutral `AgentMessage`/`ProviderResponse`, OpenAI-compatible adapter (covers
  OpenAI/Ollama/LM Studio/DeepSeek/Groq/Together via `base_url`), Anthropic adapter, deterministic fake.
  Translation is pure; HTTP is behind an injected transport (no network in tests).
- `bletiner/tools/` — static registry with the 4-tier classification. `assert_schedulable` makes Tier 4
  unschedulable in every mode and Tier 3 DEEP-only. Commands are argv templates (injection stays literal).
- `bletiner/agent/` — Plan→Execute→Observe→Analyze→Replan FSM, branching state tree, validated steering,
  loop budgets, structured events (no raw chain-of-thought), and the **Gate-3 guard**.

### Phase 5 — desktop UI
- `bletiner/ui/viewmodels.py` — headless, fully tested: activity feed (redacted, operational-only), finding
  badges, status labels, loopback xterm.js token auth.
- `bletiner/ui/app.py` — thin 3-card PySide6 shell importing Qt/qasync lazily (optional extra).

### Phase 6 — findings & dual reporting
- Lifecycle FSM (illegal transitions rejected), DOC-08 §5.1 bounded-product prioritization (R-30), Markdown +
  air-gapped HTML reports (Jinja2 autoescape, strict CSP, inlined styles), and secret/JWT/key sanitization.
  XSS fuzzing in the test suite.

### Phase 7 — recovery & secrets
- Pure `reconcile()` (reattach / crashed / interrupted / sweep-orphan), idempotent-resume classifier, and a
  keyring-backed secret store with in-memory fallback and always-on redaction.

### Phase 8 — orchestration & E2E
- `InvestigationService` — the single entry point that may start the agent against a live sandbox; enforces the
  Gate-3 guard before provisioning. `DockerToolExecutor` runs argv in Kali as the unprivileged user.
- `tests/integration/test_local_lab.py` — live E2E, skipped unless `BLETINER_LIVE=1` + Docker, and it
  re-asserts Gate-3 certification before any agent run.

## 3. Open items / decisions
- **R-30** DOC-02 ADR-019 vs DOC-08 §5.1 prioritization text should be unified in the docs.
- **R-31 / Tier C** the live gate and E2E remain PENDING until run on Docker.
- Real provider HTTP transport (actual `httpx` calls) is intentionally not wired; adapters translate payloads
  and the transport seam is injected. Wiring a concrete transport is a small, isolated follow-up.
- PySide6 widgets are a thin shell; interactive Qt behaviour (pytest-qt at `offscreen`) is not exercised here.

## 4. Prerequisites before relying on the agent
1. Run `scripts/verify_gateway_poc.py --build` on Docker; all scenarios must PASS (certifies Gate 3).
2. Then the E2E suite and `InvestigationService.start` will run; until then both refuse by design.
