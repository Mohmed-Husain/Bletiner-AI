# Bletiner AI

> **Authorized Security Investigation Workspace** — a real desktop security workbench with a hardened Kali
> sandbox, a deterministic local recon engine, and an optional autonomous LLM agent.

This repository started as an implementation of a large engineering specification (docs/00-16). Version 0.2.0
is the *readiness overhaul*: every demo/simulation path has been removed and the desktop UI is wired to the
real backends. There is no fabricated tool output anywhere in the product.

---

## What actually works

| Capability | Status | Notes |
| --- | --- | --- |
| Real interactive Kali terminal (PTY) | **Working** | Real /bin/bash PTY inside the sandbox container, ANSI rendering, arrow keys/Ctrl-C, resize, reconnect. Falls back to a clearly labelled command console if Docker is unavailable. |
| Local recon engine (no API key, no LLM) | **Working** | Runs real dig, nmap, curl, whatweb, nikto, and nuclei (when installed) inside the Kali container, parses the real output into findings, and generates Markdown + HTML reports with SHA-256 evidence manifests. |
| AI agent engine (autonomous) | **Working with a provider key** | Real provider calls (DeepSeek / OpenAI / Anthropic / Ollama / custom OpenAI-compatible endpoint) driving the pi-inspired agent loop over the real Kali tool executor. Refuses to run without a key or without the certified egress gate. |
| Session history | **Working** | SQLite (~/.bletiner/bletiner.db) + self-contained session.json manifests per investigation. Works even if the database is missing, corrupt, or a legacy schema (the old DB is moved aside, never deleted). |
| Findings + reports + evidence | **Working** | Live findings feed, severity/confidence badges, findings viewer, Markdown and air-gapped HTML reports, per-tool raw output evidence with hashes. |
| Honest settings / credentials | **Working** | OS keyring when available, Windows DPAPI fallback otherwise. The Settings page shows the real backend; keys are never written as plaintext. |
| Provider/model discovery | **Working** | Models are discovered from the provider (GET /models, Ollama /api/tags) and can be connection-tested; no invented model names. Curated fallbacks are explicitly labelled when discovery fails. |
| Egress gateway + scope enforcement | **Implemented, live-certified on this host** | Gate 3 evidence lives in `evidence/phase3/` (local runtime evidence, not committed). The AI engine is code-gated on a genuine live pass. The local recon engine does not run the autonomous agent loop. |
| Sandbox auto-provisioning | **Working** | The app finds a running sandbox, starts a stopped one, or creates a fresh `my-kali` container from a local `bletiner-kali` image. |

> The previous "Deterministic Simulation Lab", scripted tool output, hardcoded demo data, and the fake model
> list have been deleted. If a prerequisite is missing, the UI says so and disables the action.

---

## Quick start (Windows)

Requirements:

- Windows 10/11 with **Docker Desktop** running.
- **Python 3.11+** (3.13 recommended).
- The hardened Kali sandbox image, either already present as `bletiner-kali:latest` or built once from this repo:

  ```powershell
  docker build -f docker/kali/Dockerfile -t bletiner-kali:latest .
  ```

Launch:

```powershell
.\run_ui.bat
```

The launcher creates a local `.venv`, installs dependencies on first run, and opens the workspace. To launch
manually:

```powershell
.\.venv\Scripts\python.exe -m bletiner --ui
```

Check prerequisites without opening the UI:

```powershell
.\.venv\Scripts\python.exe -m bletiner --check
```

This prints a JSON readiness report (Docker daemon, Kali container, Gate 3 certification, secret backend, AI
configuration) and exits non-zero when the local engine cannot run.

### First run

1. Open **Targets**, enter the host/domain/IP you are explicitly authorized to test, confirm the authorization
   checkbox, and add it to the authorized scope (`Save target`).
2. Go to **Investigation**, keep **Local recon** selected, and press **Start investigation**. Tool output
   streams in the real Kali terminal; findings and reports appear as the run completes.
3. Optional AI mode: open **Settings**, paste a provider API key and press **Test & save** (it sends one minimal
   real request and, only on success, stores the key), open **Models**, **Discover models**, select one, then
   switch the engine toggle to **AI agent**. Starting an AI run re-validates the key first: a rejected key never
   starts an investigation.

---

## Architecture (short)

- `bletiner/ui/` — PySide6 desktop shell, controller, real PTY terminal card, sessions/findings widgets, panels.
- `bletiner/services/recon.py` — deterministic local recon engine (pure parsers, unit-tested).
- `bletiner/services/session_store.py` — investigation/session persistence (SQLite + manifests).
- `bletiner/agent/` — pi-inspired agent loop, events, steering, Gate 3 guard.
- `bletiner/container/` — hardened Kali container lifecycle and non-blocking tool executor.
- `bletiner/network/` — fail-closed egress gateway, DNS/TLS policy and scope enforcement.
- `bletiner/providers/` — provider adapters, model discovery, HTTP transport.
- `bletiner/reporting/` — sanitized Markdown/HTML report generation.

All tool execution uses structured argv (no shell strings) as the unprivileged `bletiner` user; the operator
terminal is the only interactive shell, and it runs inside the disposable sandbox container.

---

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ui,dev,secrets]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
```

Read the readiness overhaul evidence: [docs/reports/FINAL_READINESS_REPORT.md](docs/reports/FINAL_READINESS_REPORT.md).

Headless UI rendering (used for visual review in CI-less environments):

```powershell
.\.venv\Scripts\python.exe scripts\render_ui.py
```

---

## Troubleshooting

- **Docker shows Offline** — start Docker Desktop; the readiness probe refreshes every 8 seconds.
- **"Kali sandbox container not found"** — build the image with the command above; the app will create and
  start `my-kali` automatically. To diagnose: `docker ps -a --filter name=my-kali` and `docker images | findstr bletiner`.
- **AI engine disabled** — you need a saved, validated provider key (Settings → Test & save), a selected model
  (Models → Discover models) and the Gate 3 certification in @BT@evidence/phase3/@BT@. The local engine needs none of these.
- **"Key accepted" but AI still says rejected** — the key must be *saved*: press **Test & save** in Settings.
  The AI engine now preflights the key before starting, so a stale/rejected key is reported inline and no run starts.
- **API key rejected** — press **Test & save** in Settings: validation failures are shown and never stored.
  The previous placeholder key is still in the OS vault until you replace it.
- **Database notice** — a legacy `~/.bletiner/bletiner.db` is renamed to `bletiner.legacy-<timestamp>.db` and a
  fresh database is created; session history keeps working from the per-investigation manifests.

---

## Verification harness

`scripts/e2e_ui.py` drives the real MainWindow through a complete local investigation against the
local Docker lab target (`bletiner-lab-target:poc`, HTTP :8080) and asserts findings, reports, evidence,
terminal round-trip and sessions history. It temporarily adds the lab IP to the authorized scope and restores
it afterwards; it never scans external targets.

## Authorization notice

Bletiner AI is designed strictly for authorized security assessments on systems owned by the operator or under
explicit written authorization. Targets must be added to the authorized scope before any scan; the local engine
still executes real network tools and must be used responsibly.