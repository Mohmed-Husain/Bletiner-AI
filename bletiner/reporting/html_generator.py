"""Air-gapped HTML report generator (DOC-08 section 7, DOC-14 Phase 6).

Jinja2 with ``autoescape=True`` and a strict Content-Security-Policy (``default-src 'none'``). All styles are
inlined; there are zero external references, so the report renders offline with no network requests. Every
untrusted string is additionally sanitized for secrets before escaping.
"""

from __future__ import annotations

from jinja2 import Environment, select_autoescape
from markupsafe import Markup

from bletiner.reporting.model import ReportContext

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{{ csp }}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 0; padding: 24px; line-height: 1.5; }
h1 { font-size: 24px; } h3 { margin-top: 28px; }
.meta { color: #555; } table { border-collapse: collapse; }
td, th { border: 1px solid #ccc; padding: 4px 10px; }
.sev-CRITICAL { color: #b30000; font-weight: 700; } .sev-HIGH { color: #d1591a; font-weight: 700; }
.sev-MEDIUM { color: #9a7d00; } .sev-LOW { color: #35774f; } .sev-INFO { color: #555; }
.finding { border-left: 4px solid #ccc; padding-left: 14px; margin: 18px 0; }
pre { white-space: pre-wrap; word-break: break-word; }
</style>
</head>
<body>
<h1>{{ title }}</h1>
<p class="meta">Target <code>{{ primary_target }}</code>
 &bull; generated {{ ctx.generated_at.isoformat() }}</p>
<p class="meta">Authorized scope: {% for s in scope %}<code>{{ s }}</code> {% endfor %}</p>
<h2>Summary</h2>
<table><tr><th>Severity</th><th>Count</th></tr>
{% for sev in severities %}<tr><td class="sev-{{ sev }}">{{ sev }}</td><td>{{ counts[sev] }}</td></tr>
{% endfor %}</table>
<h2>Findings</h2>
{% if not findings %}<p>No reportable findings.</p>{% endif %}
{% for f in findings %}
<div class="finding">
<h3>{{ loop.index }}. {{ f.title }}</h3>
<p><span class="sev-{{ f.severity.value }}">{{ f.severity.value }}</span>
 &bull; confidence {{ f.confidence.value }} &bull; status {{ f.verification_status.value }}</p>
<p>Asset: <code>{{ f.affected_asset }}{% if f.affected_port %}:{{ f.affected_port }}{% endif %}</code>
 &bull; {{ f.category }} &bull; {{ f.detection_method }}</p>
<pre>{{ f.summary }}</pre>
{% if f.impact_statement %}<p><strong>Impact:</strong> {{ f.impact_statement }}</p>{% endif %}
{% if f.remediation_guidance %}<p><strong>Remediation:</strong> {{ f.remediation_guidance }}</p>{% endif %}
</div>
{% endfor %}
</body>
</html>
"""


def build_html(ctx: ReportContext) -> str:
    env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
    template = env.from_string(_TEMPLATE)
    # Every field comes from safe_reportable()/safe_* and is already secret-sanitized; autoescape then
    # neutralizes markup. Both layers apply to ALL fields, not just the summary.
    return template.render(
        ctx=ctx,
        title=ctx.safe_title(),
        primary_target=ctx.safe_primary_target(),
        scope=ctx.safe_scope(),
        # Trusted module constant, never user input; render the policy verbatim.
        csp=Markup(CSP),  # noqa: S704
        severities=("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"),
        counts=ctx.severity_counts(),
        findings=ctx.safe_reportable(),
    )
