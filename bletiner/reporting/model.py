"""Report assembly model (DOC-08 section 6/7).

Every untrusted finding field is secret-sanitized ONCE here, in :meth:`safe_reportable`, so the Markdown and
HTML builders consume identical, already-sanitized text. This prevents the field-by-field drift where one
report format redacts a secret and the other does not (audit finding: HTML must not leak secrets that Markdown
redacts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from bletiner.models.entities import Confidence, Finding, Severity, VerificationStatus
from bletiner.reporting.lifecycle import is_reportable
from bletiner.reporting.sanitizer import sanitize

_SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


@dataclass(frozen=True)
class SafeFinding:
    """A finding whose every free-text field has been secret-sanitized. Builders must use only these."""

    finding_id: str
    title: str
    summary: str
    category: str
    affected_asset: str
    affected_port: int | None
    detection_method: str
    impact_statement: str
    remediation_guidance: str
    severity: Severity
    confidence: Confidence
    verification_status: VerificationStatus

    @classmethod
    def from_finding(cls, f: Finding) -> SafeFinding:
        return cls(
            finding_id=f.finding_id,
            title=sanitize(f.title),
            summary=sanitize(f.summary),
            category=sanitize(f.category),
            affected_asset=sanitize(f.affected_asset),
            affected_port=f.affected_port,
            detection_method=sanitize(f.detection_method),
            impact_statement=sanitize(f.impact_statement or ""),
            remediation_guidance=sanitize(f.remediation_guidance or ""),
            severity=f.severity,
            confidence=f.confidence,
            verification_status=f.verification_status,
        )


@dataclass(frozen=True)
class ReportContext:
    title: str
    primary_target: str
    authorized_scope: tuple[str, ...]
    findings: tuple[Finding, ...]
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def reportable(self) -> list[Finding]:
        """Reportable findings, sorted by severity then port (highest first)."""
        items = [f for f in self.findings if f.is_included_in_report and is_reportable(f.verification_status)]
        return sorted(items, key=lambda f: (_SEVERITY_ORDER[f.severity], -(f.affected_port or 0)))

    def safe_reportable(self) -> list[SafeFinding]:
        """Reportable findings with every free-text field secret-sanitized. The only source for builders."""
        return [SafeFinding.from_finding(f) for f in self.reportable()]

    def severity_counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for f in self.reportable():
            counts[f.severity.value] += 1
        return counts

    def safe_title(self) -> str:
        return sanitize(self.title)

    def safe_primary_target(self) -> str:
        return sanitize(self.primary_target)

    def safe_scope(self) -> tuple[str, ...]:
        """Authorized scope entries, secret-sanitized for completeness (same path in both report formats)."""
        return tuple(sanitize(s) for s in self.authorized_scope)
