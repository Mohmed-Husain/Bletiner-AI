# Test Evidence

Captured gate output. Per DOC-10 §2.1 and DOC-15 §3, verdicts are one of
`PASS` / `FAIL` / `NOT RUN` / `PENDING LAB EXECUTION`, and nothing is `PASS` from documentation or an
unexecuted definition.

- `phase0/gate0.log` — ruff, ruff format, mypy --strict (Tier B).
- `phase1/gate1.log` — storage + model unit tests (Tier B).
- `phase2/gate2.log` — container hardening + exec unit tests (Tier B, mocked Docker).
- `phase3/gate3_tierB_and_live_attempt.log` — network unit tests (Tier B) + the live gate attempt.
- `phase3/gate3_*.json` — machine-readable 6-field evidence from `scripts/verify_gateway_poc.py`.

**Tier C (empirical) status: PENDING LAB EXECUTION.** This workstation has no container runtime
(docs/16 R-20), so the live egress gate reports every scenario `NOT RUN` and exits non-zero. Run
`python scripts/verify_gateway_poc.py --build` on a host with Docker to produce Tier C evidence.
