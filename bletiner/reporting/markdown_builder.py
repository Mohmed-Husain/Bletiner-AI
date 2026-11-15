"""Markdown report generator (DOC-08). Deterministic, air-gapped, no external references.

Consumes only ``ctx.safe_reportable()`` (already secret-sanitized), then applies Markdown-control escaping.
"""

from __future__ import annotations

from bletiner.reporting.model import ReportContext


def _md(text: str) -> str:
    """Neutralize Markdown/HTML control characters in already-secret-sanitized text."""
    return text.replace("|", "\\|").replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")


def build_markdown(ctx: ReportContext) -> str:
    counts = ctx.severity_counts()
    lines: list[str] = [
        f"# {_md(ctx.safe_title())}",
        "",
        f"- **Primary target:** `{_md(ctx.safe_primary_target())}`",
        f"- **Authorized scope:** {', '.join(f'`{_md(s)}`' for s in ctx.safe_scope()) or 'n/a'}",
        f"- **Generated:** {ctx.generated_at.isoformat()}",
        "",
        "## Summary",
        "",
        "| Severity | Count |",
        "| --- | --- |",
    ]
    lines += [f"| {sev} | {counts[sev]} |" for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")]
    lines += ["", "## Findings", ""]

    reportable = ctx.safe_reportable()
    if not reportable:
        lines.append("_No reportable findings._")
    for i, f in enumerate(reportable, 1):
        asset = f"{_md(f.affected_asset)}" + (f":{f.affected_port}" if f.affected_port else "")
        lines += [
            f"### {i}. {_md(f.title)}",
            "",
            f"- **Severity:** {f.severity.value} | **Confidence:** {f.confidence.value} | "
            f"**Status:** {f.verification_status.value}",
            f"- **Asset:** `{asset}`",
            f"- **Category:** {_md(f.category)} | **Detection:** {_md(f.detection_method)}",
            "",
            _md(f.summary),
            "",
        ]
        if f.impact_statement:
            lines += [f"**Impact:** {_md(f.impact_statement)}", ""]
        if f.remediation_guidance:
            lines += [f"**Remediation:** {_md(f.remediation_guidance)}", ""]
    return "\n".join(lines).rstrip() + "\n"
