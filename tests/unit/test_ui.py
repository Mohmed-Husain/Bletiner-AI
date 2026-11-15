"""Phase 5 Tier B: headless UI view-models (no Qt required)."""

from __future__ import annotations

import hashlib

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.models.entities import Confidence, Finding, Severity, VerificationStatus, new_id
from bletiner.ui.app import qt_available
from bletiner.ui.viewmodels import (
    FeedViewModel,
    FindingBadges,
    new_terminal_token,
    status_label,
    token_matches,
)

VS = VerificationStatus


def _finding(sev: Severity, status: VS) -> Finding:
    return Finding(
        investigation_id=new_id(),
        session_id=new_id(),
        stable_finding_key=hashlib.sha256(b"k").hexdigest(),
        title="t",
        summary="s",
        category="C",
        affected_asset="a",
        severity=sev,
        confidence=Confidence.HIGH,
        detection_method="nmap",
        verification_status=status,
    )


class TestFeed:
    def test_filters_non_feed_events(self) -> None:
        feed = FeedViewModel()
        assert feed.ingest(AgentEvent(EventType.OBSERVATION, payload={"x": 1})) is None
        assert feed.lines() == []

    def test_formats_tool_and_finding(self) -> None:
        feed = FeedViewModel()
        feed.ingest(AgentEvent(EventType.TOOL_STARTED, payload={"tool": "nmap_fast", "argv": ["nmap", "x"]}))
        line = feed.ingest(
            AgentEvent(
                EventType.FINDING, payload={"severity": "HIGH", "title": "RCE", "asset": "local-lab.test"}
            )
        )
        assert line is not None and "★ HIGH finding: RCE" in line
        assert any("▶ nmap_fast" in ln for ln in feed.lines())

    def test_policy_block_rendered(self) -> None:
        feed = FeedViewModel()
        line = feed.ingest(AgentEvent(EventType.POLICY_BLOCK, payload={"tool": "masscan", "error": "tier4"}))
        assert line is not None and "⛔ blocked masscan" in line

    def test_redacts_secret_in_generic_event(self) -> None:
        feed = FeedViewModel()
        line = feed.ingest(AgentEvent(EventType.BUDGET, payload={"note": "key sk-ABCDEFGHIJKLMNOP"}))
        assert line is not None and "sk-ABCDEFGHIJKLMNOP" not in line

    def test_bounded_history(self) -> None:
        feed = FeedViewModel(max_lines=3)
        for i in range(10):
            feed.ingest(AgentEvent(EventType.STATE_CHANGED, payload={"state": "PLANNING", "i": i}))
        assert len(feed.lines()) == 3


class TestBadges:
    def test_counts(self) -> None:
        findings = [
            _finding(Severity.CRITICAL, VS.VERIFIED),
            _finding(Severity.CRITICAL, VS.DETECTED),
            _finding(Severity.LOW, VS.NEEDS_TRIAGE),
        ]
        b = FindingBadges.from_findings(findings)
        assert b.by_severity["CRITICAL"] == 2
        assert b.confirmed == 1
        assert b.potential == 2


class TestTerminalToken:
    def test_token_unique_and_matches(self) -> None:
        a, b = new_terminal_token(), new_terminal_token()
        assert a != b
        assert token_matches(a, a)
        assert not token_matches(a, b)


def test_status_label() -> None:
    assert status_label(AgentState.EXECUTING) == "Running tool"


def test_qt_available_is_bool() -> None:
    assert isinstance(qt_available(), bool)
