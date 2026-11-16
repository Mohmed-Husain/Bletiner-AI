# Bletiner AI v0.2.0 — Readiness Overhaul Report

Date: 2026-11-17 (Asia/Calcutta) · Branch: `fix/ready-ui`

## 1. Why this overhaul happened

The project's specification (docs/00-16) was implemented phase-by-phase, but the desktop layer that was added
afterwards was largely a visual mock-up:

- the terminal was a static `QTextEdit` with pasted nmap output;
- progress, badges, sessions and the model list were hardcoded demo data;
- the "investigation" path fell back to a scripted `SimulatedToolExecutor` and canned provider responses;
- the UI bypassed the real SQLite repositories, session lifecycle, findings pipeline and provider discovery;
- the app could not run a real investigation without a valid provider key, and credentials silently vanished
  whenever the running Python lacked `keyring` (the user's runtime did).

Version 0.2.0 removes all of that and connects the UI to real engines.

## 2. What is now real

### Engines
- **Local recon engine** (`bletiner/services/recon.py`, new): deterministic, no LLM, no API key. Runs real
  `dig`, `nmap`, `curl`, `whatweb`, `nikto` and `nuclei` (when installed) inside the Kali sandbox, parses the real output with
  unit-tested pure functions, and produces findings, reports and hashed evidence.
- **AI agent engine**: the pi-inspired agent loop with real provider adapters (DeepSeek/OpenAI/Anthropic/Ollama/
  custom), real Docker tool execution, Gate-3 certification enforced in code. Verified with mock transports and
  a fake Docker container; **no paid live LLM call was made** (see limitations).

### Desktop
- **Real interactive Kali PTY** (`bletiner/ui/terminal_bridge.py` + `terminal_card.py`): `/bin/bash` PTY via the
  Docker SDK exec API, pyte ANSI rendering with 24-bit colour, cursor, scrollback, resize, arrow keys/Ctrl-C/
  Ctrl-D, bracketed paste, reconnect and an offline fallback console. Agent output is shown in a separate
  activity strip so the operator shell is never corrupted.
- **Controller** (`bletiner/ui/controller.py`, rewritten): real readiness probe (Docker, Kali container, Gate 3,
  secret backend, AI configuration, Ollama), real session persistence, pause/resume/stop, real terminal command
  execution, and no simulation fallback anywhere.
- **Sessions & findings**: `SessionStore` (SQLite v2 + per-investigation `session.json`), real session history,
  findings viewer with potential/confirmed filters, Markdown + air-gapped HTML reports, SHA-256 evidence manifests.
- **Panels**: honest provider/model discovery (real `GET /models` / Ollama `/api/tags`, connection tests, capability
  probe), credential storage with a Windows DPAPI fallback when `keyring` is unavailable, real Docker/gate
  diagnostics, target validation with authorized-scope enforcement.
- **UX**: no demo strings anywhere; truthful `checking…` states instead of false negatives; single primary
  Start; real phase progress; clickable findings badges; keyboard shortcuts; dark title bar; no label-background
  bars; no duplicate/lying status pills.
- **Self-healing sandbox**: the app finds a running sandbox, starts a stopped one, or creates `my-kali` from a
  local `bletiner-kali` image.

### Credentials
- `keyring` → Windows DPAPI → in-memory (with an explicit warning). Keys are never written as plaintext.
  The Settings page displays the backend that is actually in use.

### Data safety
- A legacy/incompatible `~/.bletiner/bletiner.db` is renamed to `bletiner.legacy-<timestamp>.db` (never deleted) and
  a fresh v2 database is created; session history keeps working from the per-investigation manifests.

## 3. Verification evidence

| Gate | Result |
| --- | --- |
| `pytest -q` | **461 passed, 2 deselected** (live Docker marker) |
| `mypy` (strict, package) | **Success: no issues found in 80 source files** |
| `ruff check .` | **All checks passed** |
| `ruff format --check .` | **107 files already formatted** |
| Offscreen full-app launch (`python -m bletiner --ui`) | launched cleanly, no fatal errors (15 s smoke run) |
| `python -m bletiner --check` | real readiness JSON: Docker true, gate certified true, container `my-kali`, backend OS keyring |
| End-to-end UI run (`scripts/e2e_ui.py`) | **19/19 checks passed** — real local investigation driven through the real MainWindow: 10 real findings, progress 100%, 44 activity events, reports + evidence manifest + session.json written, interactive terminal round-trip (`echo UI_TERMINAL_OK` echoed by the real shell), findings dialog, session history updated |

The end-to-end run scanned a local deliberately-vulnerable lab container
(`bletiner-lab-target:poc`, HTTP :8080) from inside the Kali sandbox; nothing external was scanned.

Screenshots (1672x941): `.handoff/shots/final-*.png` (idle UI) and `.handoff/shots/e2e-*.png` (completed
investigation, real evidence tree, findings dialog, terminal, sessions history).

## 4. Known limitations / honest caveats

1. **AI engine needs a valid provider key.** The DeepSeek key stored on this machine during the earlier demo
   work is a placeholder and is honestly rejected by the provider (HTTP 401). Local recon needs no key.
2. **No paid live LLM call was executed** during this overhaul. AI provider paths are covered by unit tests with
   fake transports and by real Docker execution of the tool loop; a live call should be smoke-tested by the
   operator after entering a real key.
3. Local recon scans one host at a time; IPv6 targets are unsupported in v1.
4. The AI engine's certified egress gateway requires Gate 3 evidence in `evidence/phase3/`. That evidence is
   intentionally not committed (it is local live-run evidence), so a fresh clone must run the gate before AI mode.
5. Initial runs from the earlier demo remain in `~/.bletiner/investigations` and are displayed as `legacy`
   sessions (their artifacts are real files on disk). They were not deleted automatically — remove them manually
   if you want a clean history.
6. The app assumes Docker Desktop is running; without it the terminal and both engines are disabled with an
   explanatory banner.

---

## 5. Post-release fixes — v0.2.1 (user-reported issues)

The first end-to-end use on the operator's machine surfaced four real bugs. Root causes and fixes:

1. **API key was validated but never stored.** Settings' "Test" button sent a real minimal request with the
   *typed* key and reported success, but did not persist it; Models/AI kept using the old placeholder key
   (`****-key`, HTTP 401). Fix: the button is now **"Test & save"** — on success the key is stored
   (keyring/DPAPI) and settings persisted; on failure nothing is stored and the row says "NOT saved". Typing in a
   key field shows an explicit "unsaved key" state.
2. **Models page showed predefined "curated" ids after a failed discovery,** which looked invented. Fix: a failed
   discovery now shows an honest empty list + the real error and a required fix; the curated fallback is no longer
   auto-populated. Added an explicit **"Use this model id"** field for cases where a provider's model-list
   endpoint is unavailable.
3. **AI investigation started with a rejected key.** Fix: `controller.start()` now runs a **real API-key preflight**
   (one minimal provider request) *before* creating any session or emitting run state. A rejected key raises a
   clean "DeepSeek rejected the API key (HTTP 401...)" message, the UI returns to Idle, and no investigation is
   created. Verified against the real provider with the stale placeholder key: blocked in <1 s, no session.
4. **Pause/Stop did not work during an AI run.** Two independent causes: (a) the bottom-bar Pause/Stop buttons were
   never enabled after construction; (b) the agent loop called the provider HTTP request synchronously on the Qt
   event loop, freezing the whole UI during model generation. Fix: buttons are enabled for the whole live run
   (Stop also while paused); provider calls now run in a worker thread; the loop checks pause/stop at every safe
   boundary (before/after each provider call and each tool call); `stop()` cancels the active task, finalises partial
   findings and emits a stopped outcome. Integration tests cover AI pause→resume and stop→stopped-session.

### What "Local recon" means

Local recon is the **deterministic, non-AI engine**: it runs a fixed, non-destructive playbook with real tools
(`dig` → `nmap -sT -F` → service fingerprint → HTTP headers → `whatweb` → `nikto`, plus `nuclei` when installed) inside
the Kali sandbox, parses the real output with rule-based analyzers, and produces findings/reports/evidence. It
needs Docker + the Kali container only — no provider account, no token cost. "AI agent" instead asks a real LLM to
choose tools and record findings, which needs a saved, validated API key (and the certified egress gate).

### Additional verification after the fixes

| Gate | Result |
| --- | --- |
| `pytest -q` | **473 passed, 2 deselected** |
| `mypy` (strict) | no issues in 80 source files |
| `ruff check` / `format --check` | clean |
| AI preflight against the live provider with the stale key | **blocked before any session**, clean HTTP-401 message |
| AI pause→resume / stop→stopped integration tests | pass (fake provider runs in the real agent loop + real controller path) |
| Local recon end-to-end (headless MainWindow) | re-verified after the fixes (real tools, 10 findings, reports/evidence, terminal round-trip) |

Screenshots for v0.2.1: `.handoff/shots/v021-*.png`.

