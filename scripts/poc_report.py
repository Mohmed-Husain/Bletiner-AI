"""Six-field evidence reporting for the egress gate (DOC-10 section 2.1; DOC-15 section 3).

No test is ever ``PASS`` from documentation or an unexecuted definition. A verdict comes from a captured
exit code, matched output, and the leak-detector state. When Docker is unavailable every test is ``NOT RUN``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Result:
    test_id: str
    name: str
    command: str
    expected: str
    actual: str
    verdict: str  # PASS | FAIL | NOT RUN
    evidence: str
    tier: str = "C"


SEP = "=" * 88


def banner(mode: str, lab_summary: str) -> str:
    return (
        f"{SEP}\n  BLETINER AI - EGRESS GATEWAY LIVE SECURITY GATE (reconciled, docs/16)\n"
        f"  Mode: {mode}\n  {lab_summary}\n{SEP}"
    )


def render(r: Result) -> str:
    return (
        f"\n[{r.test_id}] {r.name}  (Tier {r.tier})\n"
        f"  Command:  {r.command}\n"
        f"  Expected: {r.expected}\n"
        f"  Actual:   {r.actual}\n"
        f"  Verdict:  {r.verdict}\n"
        f"  Evidence: {r.evidence}"
    )


def summarize(results: list[Result]) -> tuple[int, int, int, int]:
    total = len(results)
    p = sum(1 for r in results if r.verdict == "PASS")
    f = sum(1 for r in results if r.verdict == "FAIL")
    n = sum(1 for r in results if r.verdict == "NOT RUN")
    return total, p, f, n


def write_artifacts(results: list[Result], out_dir: Path, mode: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    total, p, f, n = summarize(results)
    doc = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "totals": {"total": total, "pass": p, "fail": f, "not_run": n},
        "gate_passed": f == 0 and n == 0 and total > 0,
        "results": [asdict(r) for r in results],
    }
    path = out_dir / f"gate3_{mode}_{stamp}.json"
    path.write_text(json.dumps(doc, indent=2))
    return path
