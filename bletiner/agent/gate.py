"""Gate-3 live-certification guard (DOC-14 rule 5 / section 10, mandatory instruction #6).

The autonomous agent must NOT run against a live sandbox until ``scripts/verify_gateway_poc.py`` has passed
all scenarios on real Docker. This module enforces that in code: the live investigation service calls
:func:`require_gate3_certified` before INITIALIZING; it raises unless a genuine passing report exists.

A report counts as passing only if it was produced in ``live`` mode with every test ``PASS`` and none
``NOT RUN``/``FAIL`` — a simulated or NOT-RUN report can never certify the gate (docs/16 R-05).
"""

from __future__ import annotations

import json
from pathlib import Path

from bletiner.models.errors import PoCVerificationFailedError

DEFAULT_EVIDENCE_DIR = Path("evidence/phase3")


def _load(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return dict(data) if isinstance(data, dict) else None


_MISSING_TS_SENTINEL = "￿"  # sorts after any ISO-8601 timestamp, so a report with no valid
#                                  generated_at is treated as newest and can never be silently hidden
#                                  behind an older valid report (keeps selection fail-closed).


def _recency(path: Path, report: dict[str, object]) -> tuple[str, float]:
    """Sort key: the report's own ``generated_at`` (authoritative), then file mtime as a tie-break.

    Filenames are NOT used (a lexically-later ``gate3_not-run_*`` must never shadow a genuine later
    ``gate3_live_*`` pass — audit finding). A report lacking a valid ``generated_at`` sorts as newest, so a
    malformed report surfaces for the (unchanged) content checks rather than being ignored.
    """
    ts = report.get("generated_at")
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (str(ts) if isinstance(ts, str) and ts else _MISSING_TS_SENTINEL, mtime)


def latest_report(evidence_dir: Path = DEFAULT_EVIDENCE_DIR) -> dict[str, object] | None:
    """Return the most recent gate report by ``generated_at`` (then mtime), never by filename order."""
    if not evidence_dir.is_dir():
        return None
    candidates: list[tuple[tuple[str, float], dict[str, object]]] = []
    for path in evidence_dir.glob("gate3_*.json"):
        report = _load(path)
        if report is not None:
            candidates.append((_recency(path, report), report))
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


def is_gate3_certified(evidence_dir: Path = DEFAULT_EVIDENCE_DIR) -> bool:
    report = latest_report(evidence_dir)
    if report is None:
        return False
    if report.get("mode") != "live":
        return False
    totals = report.get("totals")
    if not isinstance(totals, dict):
        return False
    return (
        bool(report.get("gate_passed"))
        and int(totals.get("fail", 1)) == 0
        and int(totals.get("not_run", 1)) == 0
        and int(totals.get("total", 0)) > 0
    )


def require_gate3_certified(evidence_dir: Path = DEFAULT_EVIDENCE_DIR) -> None:
    """Raise ``PoCVerificationFailedError`` unless a genuine passing live gate report exists."""
    if not is_gate3_certified(evidence_dir):
        raise PoCVerificationFailedError(
            "Gate 3 egress PoC has not passed live on Docker; the autonomous agent must not run against a "
            "live sandbox until `python scripts/verify_gateway_poc.py --build` passes all scenarios "
            "(DOC-14 rule 5 / section 10)."
        )
