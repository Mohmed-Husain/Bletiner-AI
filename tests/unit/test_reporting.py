"""Phase 6 Tier B: lifecycle FSM, prioritization, dual reports, XSS + secret sanitization."""

from __future__ import annotations

import hashlib

import pytest

from bletiner.models.entities import (
    Confidence,
    Finding,
    Severity,
    VerificationStatus,
    new_id,
)
from bletiner.reporting.html_generator import CSP, build_html
from bletiner.reporting.lifecycle import InvalidTransitionError, is_reportable, transition
from bletiner.reporting.markdown_builder import build_markdown
from bletiner.reporting.model import ReportContext
from bletiner.reporting.prioritization import (
    NO_DISCARD_THRESHOLD,
    PriorityFactors,
    compute_priority,
)
from bletiner.reporting.sanitizer import sanitize

VS = VerificationStatus


def _finding(**kw: object) -> Finding:
    base = dict(
        investigation_id=new_id(),
        session_id=new_id(),
        stable_finding_key=hashlib.sha256(b"k").hexdigest(),
        title="T",
        summary="s",
        category="EXPOSED_SERVICE",
        affected_asset="198.51.100.20",
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
        detection_method="nmap",
    )
    base.update(kw)
    return Finding(**base)  # type: ignore[arg-type]


class TestLifecycle:
    def test_valid_path(self) -> None:
        s = VS.DETECTED
        for nxt in (VS.NEEDS_TRIAGE, VS.UNDER_INVESTIGATION, VS.VERIFIED):
            s = transition(s, nxt)
        assert s is VS.VERIFIED and is_reportable(s)

    def test_cannot_skip_triage(self) -> None:
        with pytest.raises(InvalidTransitionError):
            transition(VS.DETECTED, VS.VERIFIED)

    def test_detected_not_reportable(self) -> None:
        assert not is_reportable(VS.DETECTED)

    def test_reopen_verified_for_deep(self) -> None:
        assert transition(VS.VERIFIED, VS.UNDER_INVESTIGATION) is VS.UNDER_INVESTIGATION


class TestPrioritization:
    def test_critical_confirmed_direct_is_max(self) -> None:
        p = compute_priority(
            PriorityFactors(
                Severity.CRITICAL,
                Confidence.CONFIRMED,
                investigation_value=1.0,
                scope_relevance=1.0,
                cost_discount=1.0,
            )
        )
        assert p.value == 100.0

    def test_low_theoretical_is_retained_only(self) -> None:
        p = compute_priority(
            PriorityFactors(
                Severity.LOW, Confidence.LOW, investigation_value=0.2, scope_relevance=0.2, cost_discount=0.5
            )
        )
        assert p.value < NO_DISCARD_THRESHOLD and p.retained_only

    def test_steering_doubles_but_clamps(self) -> None:
        base = PriorityFactors(Severity.MEDIUM, Confidence.MEDIUM)
        boosted = PriorityFactors(Severity.MEDIUM, Confidence.MEDIUM, steering_boost=True)
        assert compute_priority(boosted).value == pytest.approx(
            min(100.0, compute_priority(base).value * 2), abs=0.01
        )

    def test_factors_clamped_to_ranges(self) -> None:
        p = compute_priority(
            PriorityFactors(
                Severity.HIGH,
                Confidence.HIGH,
                investigation_value=5.0,
                scope_relevance=-1.0,
                cost_discount=9.0,
            )
        )
        assert p.components["Iv"] == 1.0 and p.components["Sr"] == 0.1 and p.components["Cd"] == 1.0

    def test_components_exposed(self) -> None:
        p = compute_priority(PriorityFactors(Severity.HIGH, Confidence.MEDIUM))
        assert set(p.components) == {"Ws", "Wc", "Iv", "Sr", "Cd"}


class TestSanitizer:
    @pytest.mark.parametrize(
        "secret",
        [
            "sk-ABCDEF0123456789abcdef",
            "Bearer abcdef123456789",
            "eyJhbG.eyJzdWIi.sig_part_x",
            "api_key=supersecretvalue",
            "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
        ],
    )
    def test_secrets_redacted(self, secret: str) -> None:
        out = sanitize(f"leaked {secret} here")
        assert "[REDACTED]" in out
        assert secret not in out


class TestReports:
    def _ctx(self, findings: tuple[Finding, ...]) -> ReportContext:
        return ReportContext(
            title="Lab Report",
            primary_target="local-lab.test",
            authorized_scope=("local-lab.test",),
            findings=findings,
        )

    def test_markdown_and_html_consistent_counts(self) -> None:
        findings = (
            _finding(severity=Severity.CRITICAL, verification_status=VS.VERIFIED, title="RCE"),
            _finding(severity=Severity.MEDIUM, verification_status=VS.VERIFIED, title="CORS"),
            _finding(
                severity=Severity.LOW, verification_status=VS.DETECTED, title="hidden"
            ),  # not reportable
        )
        ctx = self._ctx(findings)
        md = build_markdown(ctx)
        html = build_html(ctx)
        assert "| CRITICAL | 1 |" in md and "| MEDIUM | 1 |" in md
        assert "RCE" in md and "RCE" in html
        assert "hidden" not in md and "hidden" not in html  # DETECTED excluded

    def test_html_has_csp_and_no_external_refs(self) -> None:
        html = build_html(self._ctx((_finding(verification_status=VS.VERIFIED),)))
        assert CSP in html and "default-src 'none'" in html
        assert "http://" not in html and "https://" not in html  # air-gapped

    @pytest.mark.parametrize(
        "xss",
        [
            "<script>alert(1)</script>",
            '"><img src=x onerror=alert(1)>',
            "</pre><script>evil()</script>",
            "{{7*7}}",
            "javascript:alert(1)",
        ],
    )
    def test_html_neutralizes_xss(self, xss: str) -> None:
        f = _finding(
            verification_status=VS.VERIFIED,
            title=xss,
            summary=xss,
            impact_statement=xss,
            remediation_guidance=xss,
        )
        html = build_html(self._ctx((f,)))
        # No live markup survives: payloads appear only as inert, entity-escaped text.
        assert "<script>" not in html
        assert "<img" not in html
        assert ">49<" not in html  # {{7*7}} template injection is not evaluated

    def test_markdown_neutralizes_html_and_pipes(self) -> None:
        f = _finding(verification_status=VS.VERIFIED, title="a|b<script>", summary="x")
        md = build_markdown(self._ctx((f,)))
        assert "<script>" not in md and "a\\|b" in md

    def test_report_redacts_secret_in_summary(self) -> None:
        f = _finding(verification_status=VS.VERIFIED, summary="token sk-ABCDEF0123456789abcdef leaked")
        assert "sk-ABCDEF0123456789abcdef" not in build_markdown(self._ctx((f,)))
        assert "sk-ABCDEF0123456789abcdef" not in build_html(self._ctx((f,)))

    @pytest.mark.parametrize("field", ["title", "impact_statement", "remediation_guidance", "affected_asset"])
    def test_both_formats_redact_secret_in_every_field(self, field: str) -> None:
        secret = "sk-ABCDEF0123456789abcdef"
        f = _finding(verification_status=VS.VERIFIED, **{field: f"leak {secret} here"})
        ctx = self._ctx((f,))
        # Audit fix: HTML must redact the same fields Markdown does, not just the summary.
        assert secret not in build_markdown(ctx)
        assert secret not in build_html(ctx)

    def test_title_and_target_redacted_in_both(self) -> None:
        secret = "sk-ABCDEF0123456789abcdef"
        ctx = ReportContext(
            title=f"Report {secret}",
            primary_target=f"host-{secret}",
            authorized_scope=("local-lab.test",),
            findings=(_finding(verification_status=VS.VERIFIED),),
        )
        assert secret not in build_markdown(ctx)
        assert secret not in build_html(ctx)
